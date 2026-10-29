# 红楼智问 · HongLouZhiWen

> 基于 **Hadoop 生态** 的《红楼梦》可追溯 RAG 知识问答系统
> 三版本正文 + 三源脂批 + 人物关系图谱，每句回答都能跳转到原文段落

**项目地址**：https://github.com/liuyang9851/HongLouZhiWen

---

## 一句话概括

把《红楼梦》三版本正文（166.5 万字）、三源脂批（8,662 条）、350 人物关系图谱
做成一个**每条结论都能追溯到原文**的知识问答系统。
存储与计算全部落在 Hadoop 生态（HDFS / HBase / MapReduce / Hive / Spark）。

---

## 🚀 快速开始

### 前置条件

| 依赖 | 版本 | 说明 |
|:---|:---|:---|
| Docker | 任意近期版本 | 跑集群和后端容器 |
| Node.js | **≥ 20** | 跑前端（`start.sh` 会校验） |
| DeepSeek API Key | — | 填进 `backend/.env` |

### 三步跑起来

```bash
# 1. 配置 API Key
cp backend/.env.example backend/.env
#    编辑 backend/.env，填入 DEEPSEEK_API_KEY

# 2. 一条命令启动（自动探测环境）
./start.sh

# 3. 打开浏览器
#    http://127.0.0.1:5173
```

`start.sh` 会自动判断你的环境该走哪条路，不需要你提前决定。

### 四种启动模式

```bash
./start.sh --cluster       # 完整 Hadoop 集群（HBase），需已有集群
./start.sh --with-hdfs     # 单节点 HDFS（只有 Docker 时用这个）
./start.sh --local-only    # 纯本地文件（最快，不体现 Hadoop）
./start.sh                 # 自动探测：有集群用集群，没有就问你要不要起单节点 HDFS

./start.sh --status        # 只看状态，不启动
./start.sh --stop          # 停止全部服务
./start.sh --help          # 查看用法
```

| 模式 | 数据源 | 需要 Hadoop | 冷启动耗时 |
|:---|:---|:---:|:---:|
| `--cluster` | HBase（完整集群） | ✅ 已有集群 | 11~20 秒 |
| `--with-hdfs` | 单节点 HDFS | ✅ 脚本自带 | 1~2 分钟 |
| `--local-only` | 本地 JSONL | ❌ | 30 秒 |

**四种模式的检索结果完全相同**（实测 5/5 命中一致）。选哪个只影响启动开销，
不影响效果。

### 启动脚本做了什么

`start.sh` 不是简单的 `docker compose up`，它按顺序处理这些事：

1. 检查 Docker、Node.js 版本，以及 `backend/.env` 里的 Key 是否还是占位符
2. **按模式准备数据源**
   - 集群模式：起 compose、等 HDFS 退出 safemode、等 HMaster 就绪
   - 单节点：构建/复用 `hlm-hdfs` 镜像，等数据导入完成
   - 本地：确认 `deploy/data/` 完整
3. **处理 HBase Thrift** —— 它不是 HMaster 的子进程，容器重启后不会自动起，
   脚本会主动检测并拉起（这是最容易踩的坑之一）
4. 构建/复用后端镜像，启动 `hlm-api` 容器
5. **等后端加载到 11,932 chunk** —— 每 15 秒报一次实际值，加载不满会报错并给出定位命令
6. 启动前端
7. 打印所有服务的访问地址

失败时的提示是具体的，不是一句「启动失败」。比如 chunk 数对不上会直接告诉你
差多少条、最可能是 chunk_id 碰撞、以及该验证哪个文件。

### Windows / WSL 用户注意

前端的启动方式和别的服务不一样，这点值得单独说。

**问题**：WSL2 的 localhost 转发只对 Windows → WSL 单向有效。在 WSL 里起 node，
Windows 浏览器访问 `127.0.0.1:5173` 会失败 —— 即使 node 监听 `0.0.0.0` 也一样。

**做法**：`start.sh` 检测到 WSL 环境后，会调用 `start-web.ps1` 在 **Windows 侧**启动前端。
这个脚本用 `schtasks` 让进程完全脱离，父 shell 退出也不会带走前端。

**验证方式**：不要用 `curl` 验前端。WSL 访问不到 Windows 侧的 5173（反向隔离），
curl 必然失败但前端其实是好的。以 `start-web.ps1` 的退出码为准。
`--status` 里用 `tasklist` 查 Windows 侧进程，也是这个原因。

两个脚本里都有一处容易踩的 cmd 陷阱：`set VAR=value && next` 会把 `&&` 前的空格
带进变量值，导致 `HLM_BACKEND="http://127.0.0.1:8000 "`，所有代理请求报
`Failed to parse URL`。必须写 `set VAR=value&& next`。

### 启动后能访问什么

```
前端界面   http://127.0.0.1:5173
后端 API   http://127.0.0.1:8000/docs
```

集群模式下还有：

```
HDFS UI    http://127.0.0.1:9870
HBase UI   http://127.0.0.1:16010
YARN UI    http://127.0.0.1:18088
JobHistory http://127.0.0.1:19888
```

### 排查

```bash
docker logs -f hlm-api          # 后端日志
cat web/_srv.err                # 前端错误（Windows 侧）
./start.sh --status             # 一览所有服务
docker exec master python3 /tmp/check_hbase_tables.py   # 逐表校验 HBase
```

---

## 架构

```
┌─────────────────────────────────────────────────────────────┐
│  浏览器前端（原生 ES Module，9 个视图）                        │
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
│  HDFS   /hlm/{warehouse,derived,mrin,mrout,manifest,logs}    │
│  MapReduce  词频 / 共现 / 人物频次 / chunk 统计                │
│  HBase  hlm_chunks / hlm_vectors / hlm_terms /               │
│         hlm_wordfreq / hlm_stats        （52.8 万行）          │
│  Hive 数仓 · Spark · ZooKeeper                                │
└──────────────────────────────────────────────────────────────┘
```

> master 只做管理，不存数据块。实际 DataNode 只有 slave1 和 slave2 两个，
> 所以副本数上限是 2 —— 这一条和下面的「已知问题」有关。

### 索引规模

| 项 | 数量 |
|:---|---:|
| 正文文本块 | 3,270 |
| 脂批文本块 | 8,662 |
| **索引总 chunk** | **11,932** |
| 向量维度 | 512（bge-small-zh-v1.5，本地 ONNX）|
| HBase 总行数 | 527,637 |
| 人物关系图谱 | 350 节点 / 673 边 |

---

## 目录结构

```
红楼智问/
├── start.sh            一键启动（四种模式 + stop/status/help）
├── start-web.ps1       Windows 侧前端启动器（WSL 环境专用）
├── backend/            FastAPI 后端
│   ├── app.py              主应用（检索、问答、三档策略）
│   ├── data_source.py      数据源抽象（hdfs / local / auto 三层降级）
│   ├── storage_local.py    本地模式存储
│   ├── sandbox.py          沙箱（AST 白名单 + prlimit + 网络守卫）
│   ├── hadoop_api.py       HDFS / HBase 访问封装
│   ├── query_api.py        语料读取
│   ├── Dockerfile              集群模式（装 happybase 等）
│   └── Dockerfile.standalone   本地/单节点模式
├── web/                前端（零框架，原生 ES Module，9 个视图）
│   ├── public/js/          app.js / page_*.js / chart*.js / md.js
│   ├── public/css/         4 个样式表
│   ├── data/               派生的展示数据 + 360 个章节 JSON + 脂批
│   └── server.js           静态服务 + API 反向代理
├── pipeline/           数据管道（27 个脚本）
│   ├── s10_1_unify_pizhu.py    ★ 三源脂批统一入 HBase（当前版本）
│   ├── s7_2_index_versions.py  三版本正文切分 + 向量化 + 入表
│   ├── s3_mapreduce.py         MapReduce 作业
│   ├── s40_*.py                HBase 重建与脂批导入（故障修复用）
│   └── job*_mapper.py / job*_reducer.py
├── scripts/            构建与维护脚本（33 个）
│   ├── s28_1_build_graph_v2.py       四源合并建图谱
│   ├── s32_verify_diagram_numbers.py 数字一致性校验
│   └── s27_restore_after_reboot.py   一键恢复
├── hadoop/             单节点 HDFS 镜像（Dockerfile.hdfs + 配置）
├── tools/              运维工具
│   ├── check_hbase_tables.py   逐表校验行数
│   ├── import_to_hdfs.sh       把数据导入已有集群
│   └── start_cluster.sh        启动 Hadoop 集群
├── docs/               72 份阶段文档 + 95 张截图
└── eval/               评测集与结果
```

---

## 核心设计

### 三路混合检索

| 路 | 数据来源 | 作用 |
|:---|:---|:---|
| L1 关键词 | HBase `hlm_terms`（联合 bigram 倒排）| 精确词命中 |
| L2 向量 | HBase `hlm_vectors`（512 维）| 语义召回 |
| L3 图谱 | `hlm_stats` 共现 + 人物关系 | 人物关系路径 |

RRF 融合（k=60）后按「同回最多 2 正文 + 1 批语」去重，保证引用分散。

### 三档知识策略

```
检索 → 判定问题类型 + 检索质量 → 分流
  ├─ 语料充分     → 基于片段回答 + 引用
  ├─ 元知识型     → 模型自身知识兜底 + 明确标注「非原文」
  ├─ 精确统计型   → 不猜数字，引导用 🔬 自定义查询
  └─ 超出范围     → 软拒答（不调模型，0.1 ms）
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

9 个视图（`web/public/js/app.js` 的 `VIEWS` 数组）：

| 路由 | 视图 | 说明 |
|:---|:---|:---|
| `#/home` | 欢迎页 | 项目概览 + 程序架构图（手写 SVG） |
| `#/principle` | 项目原理 | 一次问答拆成 8 个阶段，展示真实中间数据 |
| `#/chat` | **AI 问答** | 核心：三栏布局 + 检索证据面板 + `[n]` 引用 |
| `#/reader` | 原文阅读 | 三版本切换 + 脂批侧栏 + 划选提问 + 引用跳转 |
| `#/stats` | 统计信息 | MapReduce 真实产出 + 每张图可「就此提问」 |
| `#/graph` | 人物关系 | 350 节点力导向 / 和弦图 / 头像子图 |
| `#/guiyou` | 癸酉本探究 | 后 28 回专题，含方法错误复盘 |
| `#/corpus` | 语料版本 | 三版本规模 + 质量规则 Q1–Q8 |
| `#/ops` | 系统运维 | Hadoop 组件状态灯 + 存储规划 |

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

## 已知问题

以下三条是当前系统实际的缺口，不是「以后再说」的优化：

| # | 问题 | 影响 |
|:--:|:---|:---|
| 1 | **HDFS 副本数为 1**（S40 故障时临时改的，新旧文件不一致） | `chunks3ver` / `pizhu3src` / `vectors3ver` 三份新数据只有 1 副本，任一 DataNode 挂掉就读不回来；改回 2 只需改 `hdfs-site.xml` 再 `hdfs dfs -setrep -w 2` |
| 2 | **8,662 条脂批向量读不出来** | 导入脚本写 `v:vec_b64`，检索代码读 `v:emb`，列名不一致。向量通道实际只覆盖正文 3,270 条 |
| 3 | **`hlm_stats` 精度降级** | 重建后从 709 行降到 161 行，L3 图谱召回查的是摘要行而非完整共现矩阵 |

此外：

- L3 图谱召回是 O(N) 全扫描，511 ms 属于浪费，应该离线建「人物 → 块」正排表
- 评测集只有 52 条，推理型 15 条没有人工评分，样本量撑不起强结论
- 只在一个模型（deepseek-flash）上验证过

首页架构图上有两处标注尚未同步：`hlm_terms` 写 451,817（实测 369,939）、
`hlm_stats` 写 709（实测 161），以及图底「8 个视图」（实际 9 个）。

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

### chunk 数必须是 11,932

这是 S40b 修复 129 组 chunk_id 碰撞后的正确值。历史上写 11,780 的文档
都带着「丢过 152 条脂批」的伤 —— 不是「少了」，是「曾经丢过」。
`start.sh` 会校验这个数，对不上就报错。

---

## 文档

`docs/` 下按阶段编号，共 72 份：

- `01-需求分析说明书.md` / `02-界面设计说明书.md`
- `03`–`05`：S2 数据管道 / S3 MapReduce / S5 评测
- `06-项目审阅文档.md` / `07-课堂演示文档.md`
- `08`–`42`：各阶段问题修复记录
- `42-S40H-HBase元数据损坏修复.md`：一次真实故障的完整排查过程
- `38-部署指南.md`：三条部署路线 + 版本兼容性 + 常见问题

---

*课程项目 · 大数据原理与应用 · 2026 年秋*
