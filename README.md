# 红楼智问 · HongLouMeng-QA

> 基于 **Hadoop 生态** 的《红楼梦》可追溯 RAG 知识问答系统
> 三版本正文 + 三源脂批 + 人物关系图谱，每句回答都能跳转到原文段落

---

## 一句话概括

把《红楼梦》三版本正文（167.8 万字）、三源脂批（8,510 条）、350 人物关系图谱
做成一个**每条结论都能追溯到原文**的知识问答系统。
存储与计算全部落在 Hadoop 生态（HDFS / HBase / MapReduce / Hive / Spark）。

---

## 架构

```
┌─────────────────────────────────────────────────────────────┐
│  浏览器前端（原生 ES Module，8 个视图）                        │
└───────────────────────────┬─────────────────────────────────┘
                            │ HTTP / SSE
┌───────────────────────────▼─────────────────────────────────┐
│  FastAPI 后端                                                 │
│  · 三路混合检索（关键词 bigram + 向量 + 图谱）                 │
│  · RRF 融合（k=60）+ 同回去重                                  │
│  · 三档知识策略（语料 / 模型知识兜底 / 精确统计转自定义查询）     │
│  · 沙箱自定义查询（AST 白名单 + prlimit + 网络守卫）            │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│  Hadoop 生态（Docker Compose 三节点：master/slave1/slave2）    │
│                                                              │
│  HDFS   /hlm/{warehouse,derived,mrout,manifest,logs}         │
│  MapReduce  词频 / 共现 / 人物频次 / chunk 统计                │
│  HBase  hlm_chunks / hlm_vectors / hlm_terms /               │
│         hlm_wordfreq / hlm_stats        （60.6 万行）          │
│  Hive 数仓 · Spark · Flink · Storm · ZooKeeper                │
└──────────────────────────────────────────────────────────────┘
```

### 索引规模

| 项 | 数量 |
|:---|---:|
| 正文文本块 | 3,270 |
| 脂批文本块 | 8,510 |
| **索引总 chunk** | **11,780** |
| 向量维度 | 512（bge-small-zh-v1.5，本地 ONNX）|
| HBase 总行数 | 606,783 |
| 人物关系图谱 | 350 节点 / 675 边 / 120 种关系 |

---

## 目录结构

```
红楼智问/
├── backend/            FastAPI 后端
│   ├── app.py              主应用（检索、问答、三档策略）
│   ├── sandbox.py          沙箱（AST 白名单 + prlimit + 网络守卫）
│   ├── hadoop_api.py       HDFS / HBase 访问封装
│   ├── query_api.py        语料读取
│   └── Dockerfile
├── web/                前端（零框架，原生 ES Module）
│   ├── public/js/          app.js / chart.js / chart3d.js / md.js …
│   ├── public/css/         4 个样式表
│   ├── data/               派生的展示数据（部分由 scripts 生成）
│   └── server.js           静态服务 + API 反向代理
├── pipeline/           数据管道（S2–S15）
│   ├── s2_*.py             切分 / 建索引 / 检索 / 分区对比
│   ├── s10_1_unify_pizhu.py    ★ 三源脂批统一入 HBase（当前版本）
│   ├── s3_mapreduce.py      MapReduce 作业
│   └── run_all.sh
├── scripts/            构建与维护脚本
│   ├── s28_1_build_graph_v2.py      四源合并建图谱
│   ├── s32_verify_diagram_numbers.py 数字一致性校验
│   ├── s27_restore_after_reboot.py   一键恢复
│   └── s29_*.py / s31_*.py           字号批量缩放
├── docs/               34 份阶段文档 + 69 张截图
└── eval/               评测集与结果
```

---

## 快速开始

### 1. 启动 Hadoop 集群

```bash
cd D:\Programming\hadoop\cluster
docker compose -f compose.yaml up -d

# HBase Thrift 需手动启动（容器重启后必做）
docker exec -d master bash -lc "hbase thrift start > /logs/hbase-thrift.log 2>&1"
```

### 2. 配置密钥

```bash
cp backend/.env.example backend/.env
# 编辑 backend/.env，填入 DEEPSEEK_API_KEY
```

### 3. 启动后端

```bash
docker build -t hlm-rag-api:6.1 backend
docker run -d --name hlm-api --network hadoop-lab_cluster \
  -p 127.0.0.1:8000:8000 --restart unless-stopped \
  --env-file backend/.env \
  -v "$PWD/pipeline/out:/workspace/pipeline/out" \
  hlm-rag-api:6.1
```

### 4. 启动前端

```bash
cd web
HLM_WEB_PORT=5173 node server.js
# 打开 http://127.0.0.1:5173
```

> 端口用 5173 是因为 Windows 的 Hyper-V 可能保留 3000 段。

### 一键恢复（重启电脑后）

```bash
python scripts/s27_restore_after_reboot.py
```

---

## 核心设计

### 三路混合检索

| 路 | 数据来源 | 作用 |
|:---|:---|:---|
| L1 关键词 | HBase `hlm_terms`（联合 bigram 倒排）| 精确词命中 |
| L2 向量 | HBase `hlm_vectors`（512 维）| 语义召回 |
| L3 图谱 | `person_roles` + 共现 | 人物关系路径 |

RRF 融合（k=60）后按「同回最多 2 正文 + 1 批语」去重，保证引用分散。

### 三档知识策略

```
检索 → 判定问题类型 + 检索质量 → 分流
  ├─ 语料充分     → 基于片段回答 + 引用
  ├─ 元知识型     → 模型自身知识兜底 + 明确标注「非原文」
  ├─ 精确统计型   → 不猜数字，引导用 🔬 自定义查询
  └─ 超出范围     → 软拒答
```

### 沙箱自定义查询

用户问「HBase 里各表多少行」这类问题，系统会让 LLM **写代码**去查：

```
LLM 生成代码 → AST 白名单检查 → 子进程（prlimit + timeout）
  → 网络守卫（仅允许集群内网）→ 结果 → LLM 解读
```

五层防护：AST 白名单 → 受限 builtins → 数据入口白名单 → 资源限制 → 网络守卫。

---

## 主要界面

| 视图 | 说明 |
|:---|:---|
| 欢迎页 | 项目概览 + 程序架构图（SVG）|
| 项目原理 | 一次问答的全链路真实数据 |
| **AI 问答** | 核心：检索证据 + 引用 + 三档策略 |
| 原文阅读 | 三版本切换 + 脂批侧栏 + 划选提问 + 引用跳转 |
| 统计信息 | MapReduce 真实产出 + 每张图可「就此提问」|
| 人物关系 | 350 节点力导向 / 3D / 和弦图 |
| 癸酉本探究 | 后 28 回专题 |
| 语料版本 | 三版本规模 + 质量规则 Q1–Q8 |

---

## 数据来源与许可

| 数据 | 许可 |
|:---|:---|
| 正文 / 脂批 | 公版（曹雪芹，保护期已过）|
| 评测集 | CC-BY-4.0，须署名 CongJ-Pan |
| 人物关系三元组 | 3 个 GitHub 开源来源，注明出处 |
| **87 版剧照（15 张圆形头像）** | **受版权保护，仅限课堂合理使用（见下）** |
| 改琦《红楼梦图咏》（备选头像） | 公有领域（Wikimedia Commons, PD-Art）|

依《著作权法》第二十四条，本项目仅用于课堂教学，不出版发行。

### ⚠️ 关于 87 版剧照

`web/public/img/avatars87/` 下的 15 张人物头像取自 1987 年央视版《红楼梦》剧照，
著作权归原权利人所有。本项目属于**课堂合理使用**：

- 仅用于学校课堂演示，**不公开、不盈利、不出版**
- 只取 15 张用于关系图节点标识，不替代原作、不影响作品正常使用
- 已注明出处

**若本项目需要公开（开源发布 / 参赛 / 上线），必须移除该目录**，
并改用 `web/public/img/avatars/`（改琦《红楼梦图咏》，公有领域）。
切换只需改 `scripts/s37_1_build_main_relations.py` 里的 `AVATAR_DIR` 一行。

---

## 开发约定

### 数据一致性

改动索引或图谱后，务必跑：

```bash
python scripts/s32_verify_diagram_numbers.py --fix
```

它会校验 JS 里的硬编码数字与 HBase 实测值是否一致，并检查 `strategy_rev` 是否被旧脚本污染。

### 索引重建（唯一正确方式）

```bash
docker exec master bash -lc "cd /workspace/pipeline && python3 s10_1_unify_pizhu.py"
```

> ⚠️ 不要跑 `s5_4_add_pizhu.py`（旧版，写 `v2-pizhu`，会污染索引）。

---

## 文档

`docs/` 下按阶段编号：

- `01-需求分析说明书.md` / `02-界面设计说明书.md`
- `03`–`05`：S2 数据管道 / S3 MapReduce / S5 评测
- `06-项目审阅文档.md` / `07-课堂演示文档.md`
- `08`–`37`：各阶段问题修复记录

---

*课程项目 · 大数据原理与应用*
