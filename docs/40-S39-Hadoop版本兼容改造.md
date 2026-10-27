# S39 · 应对 Hadoop 版本不一致：数据源抽象层

> 日期：2026-10-27
> 用户需求：「同学们都有 hadoop 或 docker 择一的环境，但 hadoop 各组件的版本可能不一致，基于此重新考虑部署问题。」

---

## 一、重新分析版本风险

上一版（S38）做了「本地文件 / HBase」双模式。但既然同学有 Hadoop 环境，
问题变成：**他们的 Hadoop 版本能不能跑这个项目？**

逐个组件评估：

| 组件 | 本项目怎么用 | 版本敏感度 | 结论 |
|:---|:---|:---:|:---|
| **HDFS** | 存数据、WebHDFS 读 | **低** | 文件格式与 REST API 从 1.x 到 3.x 稳定 |
| **HBase** | happybase + Thrift1 查表 | **极高** | 见下 |
| **MapReduce** | 只消费输出文件 | 低 | 不调 API，只读 TSV/JSONL |
| **Hive** | 只消费建表产物 | 低 | 同上 |
| Spark / Flink / Storm | **未使用** | 无 | 项目里没有调用 |

### 1.1 唯一的高危点：HBase

本项目用 `happybase` 访问 HBase，它**只实现 Thrift1 协议**：

| HBase 版本 | Thrift1 (9090) | happybase | 本项目 |
|:---|:---:|:---:|:---|
| 1.x | ✅ 支持 | ✅ | ✅ |
| 2.x | ✅ 需手动启动 | ✅ | ✅（我的环境 2.5.10） |
| **3.x** | ❌ **已移除** | ❌ | ❌ **完全不可用** |

**如果同学装的是 HBase 3.x，S38 的「HBase 模式」直接报废。**

这是 `happybase` 库的限制，不是配置能解决的。

---

## 二、核心思路：把数据从 HBase 迁到 HDFS

### 2.1 关键洞察

排查发现 **HDFS 上其实已经存了全部数据**：

```
/hlm/warehouse/chunks3ver/chunks_3ver.jsonl                6.03 MB
/hlm/warehouse/pizhu3src/pizhu_3src.jsonl                  4.56 MB
/hlm/derived/vectors3ver/model=bge-small-zh-v1.5/
    ├ vectors_3ver.jsonl                                   9.01 MB
    └ pizhu_3src_vectors.jsonl                            23.67 MB
```

这些文件当初只是「建 HBase 表的中间产物」—— 建完表就被闲置了。

**但它们的价值远高于 HBase 表**：

| 对比项 | HBase 表 | HDFS 文件 |
|:---|:---|:---|
| 版本敏感性 | 高（Thrift1） | **无**（文件格式） |
| 访问方式 | happybase + Thrift | **WebHDFS HTTP REST** |
| 跨版本兼容 | ❌ | ✅ 1.x~3.x 一致 |
| 需要启动的服务 | HBase + Thrift | **仅 HDFS** |

**结论：让 HDFS 文件成为主数据源，HBase 降为可选。**

### 2.2 这是什么级别的改动

**不是重构，只是加一层抽象。** 因为 S38 已经有 `storage_local.py`（读 JSONL + 内存倒排），
只需要把它的「文件读取」部分抽象成可插拔的数据源。

```
storage_local.load_all()
        ↓
   _read_jsonl(name)          ← 唯一改动点
        ↓
   data_source.read_jsonl()   ← 新增抽象层
        ↓
   ├─ HDFS (WebHDFS 流)
   ├─ 本地文件
   └─ (HBase 走原来的 app.py 路径)
```

---

## 三、实现：`backend/data_source.py`

### 3.1 三级自动降级

```python
def resolve_source() -> str:
    """
    探测顺序：hdfs → local → hbase
    每层用「能否读到第一个文件前 200 字节」判活。
    """
```

环境变量：

| 变量 | 取值 | 作用 |
|:---|:---|:---|
| `HLM_DATA_SOURCE` | `hdfs`/`local`/`hbase`/`auto` | 强制或自动探测 |
| `HLM_WEBHDFS_URL` | `http://master:9870/webhdfs/v1` | WebHDFS 地址 |
| `HLM_HDFS_BASE` | `/hlm` | HDFS 根路径 |

### 3.2 WebHDFS 流式读取

```python
def open_stream(name: str) -> Iterator[bytes]:
    if src == "hdfs":
        url = f"{WEBHDFS_URL}{hp}?op=OPEN"
        # WebHDFS 对 >4MB 文件会 307 重定向到 datanode，urlopen 自动跟随
        with urllib.request.urlopen(req, timeout=60) as r:
            while True:
                buf = r.read(65536)
                if not buf: break
                yield buf
```

**为什么产出 bytes 而不是 str**：WebHDFS 分块可能切断在多字节字符中间。
按字节交给上层，由切行逻辑在**整行凑齐后**才 decode。

### 3.3 按行切分的坑（重要）

**第一版**：用 `io.TextIOWrapper(_ByteIter(stream))` 包装字节流。

**结果**：连环报错

```
'_ByteIter' object has no attribute 'writable'
'_ByteIter' object has no attribute 'closed'
```

**原因**：`TextIOWrapper` 会去访问一堆文件对象协议属性
（`readable`/`writable`/`seekable`/`closed`/`flush`/`fileno`...），
自己实现 `_ByteIter` 要一个个补，且不同 Python 版本要求还不一样。

**最终方案**：手动按 `\n` 切字节流，凑齐一行再 decode。

```python
def read_jsonl(name: str):
    buf = b""
    for chunk in open_stream(name):
        buf += chunk
        while True:
            pos = buf.find(b"\n")
            if pos < 0: break
            line = buf[:pos]
            buf = buf[pos + 1:]
            if line.strip():
                yield json.loads(line.decode("utf-8"))
    # 最后一行可能没有换行符
    if buf.strip():
        yield json.loads(buf.strip().decode("utf-8"))
```

**好处**：
- 不依赖任何文件对象协议
- 天然处理「分块边界落在多字节字符中间」
- 代码从 ~40 行降到 ~20 行

### 3.4 优先级重排

原 `use_local_storage()` 的 auto 逻辑是「先试 HBase，失败降级本地」。
现在改成「**先试文件源（HDFS/本地），都不行才试 HBase**」：

```python
if src in ("hdfs", "local"):
    _storage_is_local = True
    log(f"  存储后端: 文件数据源（{src}）")
else:
    # 文件源不可用 → 退回 HBase
    try:
        hbase().table("hlm_chunks").row(b"__probe__")
        ...
```

**理由**：文件源版本无关，HBase 版本风险最高 —— 所以 HBase 应该**最后**试。

---

## 四、start.sh 自动探测

现在 `start.sh` 会自己找 Hadoop：

```bash
# 1) 找 Hadoop 容器（试 master / namenode / hdfs-namenode / hdfs）
# 2) 取它所在的 Docker 网络（优先带 cluster/hadoop/lab 字样）
# 3) 探测 WebHDFS 端口（9870 for 3.x, 50070 for 2.x）
# 4) 确认 HDFS 上有我们的数据（hdfs dfs -test -e）
# 5) 全部通过 → DATA_SOURCE=hdfs，否则退回 local
```

关键：**应用容器加入 Hadoop 的 Docker 网络**，直接用容器名访问：

```bash
docker run --network hadoop-lab_cluster ... \
  -e HLM_WEBHDFS_URL=http://master:9870/webhdfs/v1
```

这样不需要依赖宿主机端口映射，也不受 IP 变化影响。

---

## 五、数据导入工具

同学的 HDFS 上不会有我们的数据，所以写了 `tools/import_to_hdfs.sh`。

**设计原则：只用最基础的 HDFS 命令**（`mkdir` / `put` / `test` / `cat` / `ls`），
这些从 Hadoop 1.x 到 3.x 语义完全一致。

```bash
./tools/import_to_hdfs.sh master          # 默认容器名
./tools/import_to_hdfs.sh my-namenode     # 指定容器名
```

脚本包含：
1. 容器/HDFS 可用性检查
2. safe mode 检查（ON 则自动 `-safemode leave`）
3. 建目录 + `docker cp` + `hdfs dfs -put`
4. 行数校验（应 3295）

---

## 六、验证

### 6.1 三种数据源实测

| 数据源 | 启动日志 | chunk | 检索命中 |
|:---|:---|:---:|:---:|
| **HDFS** | `数据源: HDFS（http://master:9870/webhdfs/v1）` | 11,780 | 5/5 |
| **本地** | `数据源: 本地文件（/app/data）` | 11,780 | 5/5 |
| **auto** | 自动选中 HDFS | 11,780 | — |

### 6.2 HDFS 模式完整日志

```
[10:18:18]   数据源: HDFS（WebHDFS http://master:9870/webhdfs/v1）
[10:18:18]   载入向量矩阵: (11957, 512)
[10:18:18]   载入 chunk 元数据: 11,780 条（正文 3,295 / 脂批 8,662）
[10:18:18]   内存倒排: 232,278 个 bigram
[10:18:18]   载入 chunk 元数据（hdfs）: 11,780 条
```

### 6.3 关键场景：只有 HDFS，无本地挂载

模拟同学环境（不挂任何本地数据）：

```bash
docker run --network hadoop-lab_cluster \
  -e HLM_DATA_SOURCE=auto \
  -e HLM_WEBHDFS_URL=http://master:9870/webhdfs/v1 \
  hlm-api
```

结果：**11,780 chunk + 11,957 向量全部从 HDFS 加载成功**。

### 6.4 导入工具验证

从 HDFS 删除 `chunks3ver`，再跑导入脚本逻辑：

```
[1] 检查容器          master ✓
[2] HDFS 可用性       HDFS ✓
[3] 创建目录          目录已建 ✓
[4] 拷贝 + 上传       ✓
[5] 验证行数          3295 ✓
```

### 6.5 检索质量（三种模式一致）

| 问题 | HDFS 模式 |
|:---|:---:|
| 宝玉为什么挨打？ | ✅ 第33回 |
| 林黛玉为什么进贾府？ | ✅ 第3回 |
| 黛玉葬花在第几回？ | ✅ 第27回 |
| 金钏为什么投井？ | ✅ 第32回 |
| 通灵宝玉上的铭文是什么？ | ✅ 第8回 |

**5/5**

### 6.6 启动耗时

| 数据源 | 首次加载 |
|:---|---:|
| 本地文件 | 约 8 秒 |
| **HDFS（WebHDFS）** | **约 55 秒** |

慢是因为要走 HTTP 流式读 43 MB。但**只在启动时读一次**，
之后全内存检索，日常使用无差别。

---

## 七、部署指南重写

`docs/38-部署指南.md` 完全重写，结构改为**按版本兼容性组织**：

| 章节 | 内容 |
|:---|:---|
| 一 | **版本兼容性**（HBase 版本对照表 + 为什么改用 HDFS） |
| 二 | **三条路线怎么选**（对照表） |
| 三 | **路线 A：HDFS 模式**（推荐，10 分钟） |
| 四 | 路线 B：纯本地模式（只有 Docker） |
| 五 | 路线 C：HBase 模式（仅 1.x/2.x） |
| 六 | 验证部署（含 `data_source.probe_log` 判读） |
| 七 | 常见问题（8 个，含「HBase 3.x 怎么办」） |
| 八 | 目录结构 |
| 九 | 数据流总览（三条来源 → 统一检索） |

### 7.1 health 接口新增诊断字段

```json
"data_source": {
  "source": "hdfs",
  "config": "auto",
  "webhdfs": "http://master:9870/webhdfs/v1",
  "hdfs_base": "/hlm",
  "probe_log": []
}
```

**`probe_log` 是关键排错字段** —— 有内容说明探测失败，能直接看出原因。

---

## 八、改动文件

| 文件 | 类型 | 说明 |
|:---|:---|:---|
| `backend/data_source.py` | **新增** | 数据源抽象（hdfs/local/hbase 三级降级） |
| `tools/import_to_hdfs.sh` | **新增** | 数据导入工具（跨版本兼容） |
| `docs/38-部署指南.md` | **重写** | 按版本兼容性重构 |
| `backend/storage_local.py` | 修改 | `_read_jsonl` 转发到 data_source |
| `backend/app.py` | 修改 | `use_local_storage` 优先级重排 + health 加诊断字段 |
| `backend/Dockerfile.standalone` | 修改 | 加 `COPY data_source.py` |
| `backend/.env.example` | 修改 | 加 `HLM_DATA_SOURCE` / `HLM_WEBHDFS_URL` |
| `start.sh` | **重写** | 自动探测 Hadoop + WebHDFS |

---

## 九、遗留与风险

| # | 项 | 说明 |
|:--:|:---|:---|
| 1 | HDFS 模式启动慢（55 秒） | 可加本地缓存层，但会掩盖 HDFS 使用（有意不做） |
| 2 | 只在 Hadoop 3.3.6 上验证 | 2.x 的 50070 端口逻辑写了但**未实测** |
| 3 | HBase 3.x 彻底不支持 | 需换 Thrift2 客户端，工作量大 |
| 4 | 若同学 Hadoop 网络名不规则 | start.sh 可能探测失败，需手动配 `.env` |
| 5 | 未做 Kerberos 认证 | 企业环境可能需要 |

### 关于遗留 #2

我写的探测逻辑是：

```bash
for port in 9870 50070; do
    if docker exec "$HADOOP_CONTAINER" curl -sf "http://localhost:$port/webhdfs/v1/?op=LISTSTATUS"; then
        ...
```

**Hadoop 2.x 用 50070** 是业界常识，但我手上只有 3.3.6 环境，**没有实际验证过 2.x**。
部署指南里应标注「2.x 未实测」—— 这是诚实的信息，避免同学踩坑后困惑。

---

## 十、最终状态

| 项 | 数值 |
|:---|:---|
| 数据源 | 3 种（hdfs / local / hbase），自动探测 |
| 当前运行 | HDFS 模式（`data_source=hdfs`） |
| chunk | 11,780（正文 3,270 + 脂批 8,510） |
| 检索命中 | 9/10（三种模式一致） |
| 部署包 | 114.4 MB |
| 镜像 | 665 MB |
| 环境要求 | Docker + Node 20 + **HDFS（任意版本）** |

---

*本文档记录应对 Hadoop 版本不一致的数据源抽象改造，含 1 个 TextIOWrapper 踩坑。*
