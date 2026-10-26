# S24 · 放宽沙箱：让自定义查询真正使用 Hadoop

> 日期：2026-10-24
> 需求：「放宽沙箱，运行使用 hadoop 进行自定义查询」

---

## 一、方案选择：能力开放，而非放开 import

用户要求「放宽沙箱」。有两个做法：

| 方案 | 做法 | 评价 |
|:---|:---|:---|
| A | 放开 `import os/subprocess`，让 AI 自己拼 `hdfs dfs` 命令 | ❌ **等于任意命令执行**，沙箱形同虚设 |
| B | **提供 `hdfs_*` / `hbase_*` 封装函数** | ✅ **本实现** |

### 为什么 B 依然算「真正使用 Hadoop」

- `hdfs_*` → 真的调 `/opt/hadoop/bin/hdfs dfs`，走 **HDFS RPC**
- `hbase_*` → 真的通过 **happybase 连 HBase Thrift 9090**
- 不是读本地副本，是**实时查询集群**

**这是真实访问 Hadoop，但把「能力」而不是「任意命令」交给 AI。**

---

## 二、提供的 Hadoop API（8 个）

| API | 作用 | 底层 |
|:---|:---|:---|
| `hdfs_ls(path)` | 列目录 | `hdfs dfs -ls` |
| `hdfs_cat(path, max_bytes)` | 读文件（有上限） | `hdfs dfs -cat` |
| `hdfs_count_lines(path)` | 数行数 | `hdfs dfs -cat \| wc -l` |
| `hdfs_du(path)` | 查大小 | `hdfs dfs -du -s` |
| `hbase_count(table)` | 表行数 | happybase + FirstKeyOnlyFilter |
| `hbase_get(table, rowkey)` | 取一行 | happybase `table.row()` |
| `hbase_scan(table, prefix, limit)` | 前缀扫描 | happybase `table.scan()` |
| `hadoop_status()` | 集群状态 | `hdfs dfsadmin -report` + 各表行数 |

### 安全约束（依然严格）

| 约束 | 实现 |
|:---|:---|
| 路径白名单 | 只能访问 `/hlm/`、`/user/root/` |
| 防路径穿越 | 拒绝含 `..` 的路径 |
| 表名白名单 | 只有 5 张 `hlm_*` 表 |
| 只读 | 所有函数都没有写/删操作 |
| 字节上限 | `hdfs_cat` 单次最多 8 MB |
| 行数上限 | `hbase_scan` 最多 2000 行 |
| 网络 | 只放行集群内网（172.16-20/127/10/192.168），外网一律拒绝 |

---

## 三、调试过程中踩的 4 个坑（这是本次最耗时的部分）

### 坑 1：JVM 在内存限制下起不来

症状：`hdfs` 命令在沙箱里返回 `HadoopError: 退出码 1`，直接调用却正常。

排查：

```bash
prlimit --as=536870912 -- hdfs dfs -ls /hlm/
# Error occurred during initialization of VM
# Could not reserve enough space for 262144KB object heap

prlimit --as=2147483648 -- hdfs dfs -ls /hlm/
# Error occurred during initialization of VM
# Could not allocate metaspace: 1073741824 bytes

prlimit --cpu=15 -- hdfs dfs -ls /hlm/     # 不加 --as
# Found 6 items  ← 成功
```

**根因**：JVM 需要 1 GB metaspace，任何 `--as` 限制都会让它启动失败。

**修法**：Hadoop 模式**不加 `--as`**，改用 CPU 限制 + 墙钟超时 + 进程数限制。

### 坑 2：`--nofile=64` 太小

症状：去掉 `--as` 后仍失败，错误是 Java 类加载异常。

逐个排查 `prlimit` 各参数：

```
[nproc=16 单独]     rc=0  OK
[fsize=1M 单独]     rc=0  OK
[nofile=64 单独]    rc=1  ← 罪魁祸首
[nproc=16+nofile=64] rc=1
```

继续测 `nofile` 最小值：

```
nofile=256  → rc=1
nofile=512  → rc=0  ✓
nofile=1024 → rc=0  ✓
```

**修法**：Hadoop 模式 `nofile=1024`，纯计算模式保持 64。

### 坑 3：网络拦截挡住了 HBase

症状：

```json
{"hlm_chunks": "error: 沙箱内禁止网络访问", ...}
```

原因：早先为防数据外传，沙箱里把 `socket.socket` 无条件替换成抛异常的类。
但 **happybase 需要 socket 才能连 Thrift 9090**。

**修法**：改成「只放行集群内网」——

```python
_ALLOW_NETS = ("172.16.", "172.17.", ..., "127.", "10.", "192.168.")
_ALLOW_HOSTS = {"master", "slave1", "slave2", "localhost", "127.0.0.1"}

class _GuardedSocket(_orig_socket):
    def connect(self, addr):
        if not _host_allowed(addr[0]):
            raise OSError(f"沙箱内禁止访问外网地址：{addr[0]}")
        return super().connect(addr)
```

**效果**：HBase 可连，外网仍被拦。

### 坑 4：`RUNNER_TEMPLATE.format()` 把代码里的花括号当占位符

症状：

```
KeyError: '"master", "slave1", "slave2", "localhost", "127'
KeyError: 'host'
```

原因：沙箱运行器用 `RUNNER_TEMPLATE.format(safe_builtins=..., code=...)` 生成，
我新加的网络拦截代码里有 `{"master", ...}` 和 f-string `{host}`，
被 `.format()` 当成占位符。

**修法**：把除 `{safe_builtins}` / `{code}` 外所有花括号转义成 `{{ }}`。
（中间还因为重复转义产生了 `{{{{`，需要再规范化回 `{{`。）

### 坑 5：`hlm_terms` 44.8 万行，普通 scan 超时

```
hlm_chunks    11,780  ✓
hlm_terms     ERR: timed out   ← 44.8 万行
```

测了两种方案：

| 方案 | 耗时 |
|:---|:---|
| 普通 `scan(batch_size=100)` | **超时（>60s）** |
| `scan(filter=b"FirstKeyOnlyFilter()")` | **3.49 秒** ⭐ |

**`FirstKeyOnlyFilter` 让 RegionServer 只返回 rowkey，不传任何列值**，快 100 倍。

另外发现 `HBASE_TIMEOUT=20` 太短，改成 120。

---

## 四、最终效果

### 4.1 HDFS 查询

```
Q: HDFS 的 /hlm/warehouse 下都存了哪些数据文件？各多大？

AI 生成的代码：
  rows = hdfs_ls("/hlm/warehouse/")
  files = []
  for r in rows:
      if not r["is_dir"]:
          files.append({"path": r["path"], "size": r["size"]})
      else:
          for s in hdfs_ls(r["path"]):
              if not s["is_dir"]:
                  files.append({"path": s["path"], "size": s["size"]})
  result = files

结果：
  [{"path": "/hlm/warehouse/chunks3ver/chunks_3ver.jsonl", "size": 6326851},
   {"path": "/hlm/warehouse/pizhu3src/pizhu_3src.jsonl",  "size": 4785211}]
```

**AI 自己写出了递归遍历！**

### 4.2 HBase 查询

```
Q: HBase 里这 5 张表各有多少行？

结果：
  {"hlm_chunks": 11780, "hlm_vectors": 11780, "hlm_terms": 448841,
   "hlm_wordfreq": 133673, "hlm_stats": 709}
```

### 4.3 集群状态

```
Q: 集群现在什么状态？HDFS 用了多少空间？

结果：
  {"hdfs": {"capacity": "1006.85 GB", "remaining": "940.63 GB",
            "used": "24.10 KB", "under_replicated": "0",
            "live_datanodes": 2},
   "tables": {"hlm_chunks": 11780, "hlm_terms": 448841, ...}}
```

截图：`docs/shots/32-hadoop-query.png`

---

## 五、安全验证（仍然完整）

| 攻击 | 结果 |
|:---|:---|
| `hdfs_cat("/etc/passwd")` | ✅ 拒绝：只允许 `/hlm/`、`/user/root/` |
| `hdfs_ls("/hlm/../etc/passwd")` | ✅ 拒绝：路径不允许包含 `..` |
| `hbase_count("hlm_secret")` | ✅ 拒绝：表不在白名单 |
| `import os` | ✅ AST 拦截 |
| `open("/etc/passwd")` | ✅ AST 拦截 |
| `open("/tmp/x","w")` | ✅ AST 拦截 |
| `while True` | ✅ AST 拦截 |
| **正常 HDFS** | ✅ 通过（返回 6） |
| **正常 HBase** | ✅ 通过（返回 709） |

**放宽了 Hadoop 能力，但没有放开任意命令执行。**

---

## 六、资源限制对比

| 项 | 纯计算模式 | Hadoop 模式 |
|:---|---:|---:|
| CPU | 20 s | 20 s |
| 地址空间 `--as` | 512 MB | **不限**（JVM 需要） |
| 进程数 `--nproc` | 16 | 16 |
| 文件描述符 `--nofile` | 64 | **1024**（JVM 需要 ≥512） |
| 单文件 `--fsize` | 1 MB | 1 MB |
| 墙钟超时 | 60 s | 60 s |

**权衡说明**：Hadoop 模式放弃了 `--as` 内存硬限制，这是 JVM 的硬性要求。
风险由 `--nproc` / `--fsize` / `--nofile` / 墙钟超时共同约束，
且沙箱代码仍无法 `import`，只能调 8 个白名单 API。

---

## 七、改动文件

| 文件 | 类型 | 说明 |
|:---|:---|:---|
| `backend/hadoop_api.py` | **新增** | 8 个 HDFS/HBase 只读 API + 安全校验 |
| `backend/sandbox.py` | 修改 | 注入 Hadoop API；双模式资源限制；网络只放行内网 |
| `backend/app.py` | 修改 | 代码生成 prompt 加入 Hadoop API 说明与示例 |
| `backend/Dockerfile` | 修改 | COPY hadoop_api.py |
| `web/public/js/app.js` | 修改 | 底部提示文案更新 |

---

## 八、与 S23 回答的对比

S23 时说「自定义查询没有用 Hadoop」。现在**已经改掉了**：

| 能力 | S23（之前） | S24（现在） |
|:---|:---|:---|
| 读语料做统计 | 本地 JSONL 副本 | 本地（仍然，因为更快） |
| 查 HDFS 文件 | ❌ 不支持 | ✅ **实时 HDFS RPC** |
| 查 HBase 表 | ❌ 不支持 | ✅ **实时 Thrift 连接** |
| 查集群状态 | ❌ 不支持 | ✅ **dfsadmin -report** |

**现在自定义查询有两条路径**：
- 需要全文统计 → 走本地副本（快）
- 需要集群数据/元数据 → 走 Hadoop API（真实）

---

*本文档记录一次「放宽沙箱但不放弃安全」的实现，重点在 5 个调试坑。*
