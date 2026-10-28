# S40 · HBase 元数据损坏修复记录

> 日期：2026-10-28
> 触发：Docker Desktop 隔夜重启后，集群 HBase 完全无法启动

---

## 一、症状

```
docker compose up -d 后：
  master  → 启动 30 秒后 Exited (1)
  slave1  → 启动 50 秒后 Exited (1)
  slave2  → 启动 50 秒后 Exited (1)
  循环往复，日志为空
```

`hlm-api` 连不上 HBase，`chunk=-1`。

---

## 二、根因链（逐层排查）

### 2.1 表层：容器反复退出

`entrypoint.sh` 最后是：

```bash
wait -n "${children[@]}"
result=$?
kill "${children[@]}" 2>/dev/null || true
exit "$((result == 0 ? 1 : result))"
```

**任意一个子进程退出 → 整个容器退出**。

master 容器有 4 个子进程（NameNode、HMaster、ZK、Spark Master），
任一崩溃都会带走整个容器。

### 2.2 中层：HBase Master 为什么崩

```
java.net.NoRouteToHostException: No route to host
  → 写 HDFS 时没有可用 DataNode
  → 因为 slave 容器退出了
  → 因为 RegionServer 崩了
  → 因为 ZK 连接被拒
  → 因为 master 容器退出了（ZK 在 master 上）
  → 死循环
```

### 2.3 深层：RegionServer 为什么崩

```
ERROR regionserver.HRegion: Memstore data size is 18466 in region hbase:meta,,1.1588230740
java.lang.RuntimeException: HRegionServer Aborted
```

`hbase:meta` 表损坏。

### 2.4 最深层：meta 为什么损坏

Master 日志：

```
hbase:meta,,1.1588230740 is NOT online;
state={1588230740 state=OPEN, ts=..., server=slave2...,16020,1793182714121};
Master startup cannot progress, in holding-pattern until region onlined.
```

`1793182714121` 是**昨晚崩溃前那个 RegionServer 的启动时间戳**，
而这个实例早已不存在。

**这条陈旧记录存在于两个地方**：
1. ZooKeeper 的 `/hbase/meta-region-server` 节点
2. HDFS 的 `/hbase/MasterData`（HBase 2.x 的 Master 状态存储）

---

## 三、最关键的坑：卷名不匹配

排查中出现了一个致命误导：

```bash
# 我一直在清这个卷
docker run --rm -v master-data:/data ... rm -rf /data/zookeeper

# 但容器实际挂载的是这个卷
docker inspect master --format '{{range .Mounts}}{{.Name}}{{end}}'
# → hadoop-lab_master-data    ← compose 项目名前缀！
```

**compose 会自动给卷加项目前缀**：`master-data` → `hadoop-lab_master-data`。

我用 `docker run -v master-data:/data` 创建了一个**同名的空卷**，
清空的全是那个空卷。真实的 ZK 数据（8 MB）一直没被动过。

这是整个排查耗时最长的原因——每次"清空 ZK"都看起来执行成功了，
但 HBase 依然读到旧状态。

**教训**：

```bash
# 永远先确认实际卷名
docker inspect <container> --format '{{range .Mounts}} {{.Name}} -> {{.Destination}}{{println}}{{end}}'
```

---

## 四、修复步骤

### 4.1 修 entrypoint（服务隔离）

新增两个开关，把服务分级：

| 级别 | 服务 | 崩溃后果 |
|:---|:---|:---|
| **核心** | NameNode(master)、ZK(master)、HMaster(master)、DataNode(slave) | 容器退出（让 Docker 重启） |
| **边缘** | YARN RM/NM、JobHistory、Spark Master/Worker、**RegionServer** | 只记日志 + 自动重拉（最多 3 次） |

**RegionServer 从核心降为边缘**是关键：它崩了不会带走 DataNode，
HDFS 才能保持可用，才有机会修 meta。

新增环境变量：
- `HLM_NO_HBASE=1` —— master 只跑 NameNode（用于清空 `/hbase`）
- `HLM_NO_REGIONSERVER=1` —— slave 只跑 DataNode

### 4.2 修副本数（消除写失败）

镜像里 `dfs.replication=2`，但只剩 1 个 DataNode 时 HBase 写不进去：

```
NoRouteToHostException: No route to host
```

改为 1（挂载 `hdfs-site-fixed.xml`）：

```xml
<property><name>dfs.replication</name><value>1</value></property>
<!-- 同时放宽 DataNode 失联判定 -->
<property><name>dfs.namenode.heartbeat.recheck-interval</name><value>600000</value></property>
<property><name>dfs.heartbeat.interval</name><value>10</value></property>
<!-- 缩短 safemode 等待 -->
<property><name>dfs.namenode.safemode.min.datanodes</name><value>1</value></property>
<property><name>dfs.namenode.safemode.extension</name><value>10000</value></property>
```

### 4.3 清空并重建 HBase

```bash
# 1) 停全部
docker stop master slave1 slave2

# 2) 清 ZK（用正确的卷名！）
docker run --rm -v hadoop-lab_master-data:/data hadoop-lab:2026.10 \
    -c "rm -rf /data/zookeeper"

# 3) 起 master（只跑 NameNode）
HLM_NO_HBASE=1 docker compose -f compose.yaml -f compose.override.yaml up -d master

# 4) 起 slave（只跑 DataNode，给 HDFS 提供存储）
HLM_NO_REGIONSERVER=1 ... up -d slave1 slave2

# 5) 退出 safemode，删 /hbase
docker exec master hdfs dfsadmin -safemode leave
docker exec master hdfs dfs -rm -r -f -skipTrash /hbase

# 6) 停 master，用完整模式重启（此时 ZK 和 HDFS 都干净）
docker stop master
HLM_NO_HBASE=0 ... up -d

# 7) 等 HBase 初始化完成（约 90 秒）
docker exec master hbase shell -n <<< "status"
# → 1 active master, 2 servers, 0 dead
```

### 4.4 重建 5 张表

```bash
docker exec master python3 /workspace/pipeline/s40_rebuild_tables.py
```

### 4.5 导入数据

```bash
# chunks + vectors + terms（重新向量化，107 秒）
docker exec master bash -lc "cd /workspace/pipeline && python3 s7_2_index_versions.py"

# 脂批（从 HDFS 快照读，不重新向量化，4 秒）
docker exec master python3 /workspace/pipeline/s40_import_pizhu.py

# stats + wordfreq（从 MapReduce 输出读，127 秒）
docker exec master bash -lc "cd /workspace/pipeline && python3 s3_to_hbase_hive.py"
```

**注意**：`s7_2` 需要 `onnxruntime`，master 容器默认没装：

```bash
docker exec master pip3 install onnxruntime
```

---

## 五、恢复结果

| 表 | 行数 | 状态 |
|:---|---:|:---|
| hlm_chunks | 11,780 | ✅ |
| hlm_vectors | 11,780 | ✅ |
| hlm_terms | 369,939 | ✅ |
| hlm_stats | 161 | ✅ |
| hlm_wordfreq | 133,673 | ✅ |

**数据说明**：
- 正文 chunk 3,270（chenggao 1,689 / fuqinju 1,051 / guiyouben 530）
- 脂批 chunk 8,510（fuqinju 3,221 / zhipi 5,219 / guiyouben 222）
- 合计 11,780 —— 与损坏前**完全一致**

检索验证 5/5 命中：

| 问题 | 结果 |
|:---|:---:|
| 宝玉为什么挨打？ | ✅ 第33回 |
| 林黛玉为什么进贾府？ | ✅ 第3回 |
| 黛玉葬花在第几回？ | ✅ 第27回 |
| 金钏为什么投井？ | ✅ 第32回 |
| 通灵宝玉上的铭文是什么？ | ✅ 第8回 |

---

## 六、改动文件

| 文件 | 类型 | 说明 |
|:---|:---|:---|
| `cluster/entrypoint-fixed.sh` | 新增 | 服务分级 + 自动重拉 + 依赖等待 |
| `cluster/hdfs-site-fixed.xml` | 新增 | 副本数 1、放宽心跳、缩短 safemode |
| `cluster/compose.override.yaml` | 新增 | 挂载上述两个文件 |
| `pipeline/s40_rebuild_tables.py` | 新增 | 重建 5 张表 |
| `pipeline/s40_import_pizhu.py` | 新增 | 从 HDFS 快照导入脂批 |

**注意**：`entrypoint-fixed.sh` 必须放在 `cluster/workspace/` 下
（因为 compose 把 `./workspace` 挂到容器 `/workspace`）。

---

## 七、后续修复（S40b 补充）

### 7.1 healthcheck 误报 unhealthy

**现象**：slave1/slave2 一直显示 `unhealthy`，但服务实际正常。

**根因**：healthcheck 要求四个端口全通：

```
:9864 DataNode       ✓
:8042 NodeManager    ✓
:16020 RegionServer  ✓（监听在 IPv6）
:8081 Spark Worker   ✗ 未运行
```

RegionServer 和 Spark Worker 已降为**边缘服务**，不保证一直在线，
所以健康检查必然失败。

**修复**：

```yaml
# slave：只查真正必需的
nc -z slave1 9864 && nc -z slave1 8042

# master：去掉 7077（Spark Master 也是边缘服务）
nc -z master 9000 && nc -z master 8088 && nc -z master 16000
```

### 7.2 HBase Thrift 不自动启动

**现象**：每次容器重建后，所有检索返回 500：

```
TTransportException: TTransportException(type=1,
  message="Could not connect to ('master', 9090)")
```

**根因**：HBase Thrift（9090）**不是 HBase Master 的子进程**。
`hbase thrift start` 需要单独启动，容器重启不会自动带起。

**修复**：在 `entrypoint-fixed.sh` 的 master 分支加后台守护：

```bash
(
    while true; do
        sleep 30
        if ! nc -z localhost 9090 2>/dev/null; then
            log "  Thrift 9090 未监听，自动拉起"
            nohup hbase thrift start > /logs/hbase-thrift.log 2>&1 &
            sleep 25
        fi
    done
) &
```

实测日志：

```
[cluster] 11:33:04   HBase Thrift 守护已启动（30 秒检查一次）
[cluster] 11:33:34   Thrift 9090 未监听，自动拉起
→ 8 秒后就绪
```

### 7.3 dead servers 残留

**现象**：`hbase shell` 的 `status` 显示 `2 dead servers`。

**实际情况**：这是历史会话残留，容器重建后 HBase 自动清理了。当前为 `0 dead servers`。

如果要手动清，可以用：

```bash
echo "status 'simple'"   # 先确认是哪些
# HBase 2.x 会在 RegionServer 泄漏检测超时后自动清理
```

---

## 八、遗留问题

| # | 问题 | 影响 |
|:--:|:---|:---|
| 1 | `entrypoint-fixed.sh` 未进镜像 | 依赖 compose override 挂载。重建镜像后需重新集成 |
| 2 | Hive 未启动 | `s3` 建 Hive 外部表被跳过，SQL 存在 `/tmp/hlm_hive.sql` |
| 3 | ZK 数据每轮崩溃会累积 | 长期运行需监控 `/data/zookeeper` 大小 |
| 4 | Thrift 守护有 30 秒窗口 | 容器刚起时可能有 30~55 秒检索失败窗口 |

---

## 九、经验教训

### 8.1 `wait -n` 是容器化多服务的大坑

```bash
# 错误：任一服务死 → 整容器退出
wait -n "${children[@]}"

# 正确：区分核心/边缘，核心退出才退，边缘自动重拉
while true; do
    check_critical_services || exit 1
    restart_optional_services
    sleep 5
done
```

### 8.2 compose 卷名必须用 `docker inspect` 确认

compose 会自动给卷加**项目名前缀**：`master-data` → `hadoop-lab_master-data`。

```bash
# 错：假设卷名就是 compose 里写的
-v master-data:/data        # 会创建一个同名的空卷

# 对：先看实际挂载
docker inspect master --format '{{range .Mounts}}{{.Name}} -> {{.Destination}}{{println}}{{end}}'
```

**这次排查最耗时的就是这个**：用错卷名清了 8 次 ZK 都是空操作，
真实的 8 MB ZK 数据（含 HBase meta 的陈旧注册）从未被动过。

### 8.3 容器启动顺序要靠显式等待，不能靠 sleep

```bash
# 错：sleep 10 然后祈祷 master 起来了
sleep 10

# 对：DNS + 端口双重探测，带超时
wait_for() {
    while [ $n -lt $timeout ]; do
        getent hosts "$host" >/dev/null 2>&1 || { sleep 2; continue; }
        nc -z "$host" "$port" && return 0
        sleep 2
    done
    return 1
}
```

### 8.4 边缘服务不能进 healthcheck

把某服务降为"边缘"（允许崩溃重拉）后，**必须同时把它从 healthcheck 里移除**，
否则容器会一直显示 unhealthy。

这是"服务分级"的配套动作，容易漏。

### 8.5 非子进程的守护服务要单独保活

HBase Thrift、HiveServer2 这类**不是主进程子进程**的服务，
容器重启后不会自动起。需要在 entrypoint 里加轮询守护。

判断方法：
```bash
# 看进程树，是否挂在主进程下
docker exec <c> ps -ef --forest | grep -A2 hbase
```

### 8.6 HBase 的 meta 状态存在三处，必须同时清

1. HDFS `/hbase/data/hbase/meta/`（表数据）
2. HDFS `/hbase/MasterData/`（Master 状态）
3. ZooKeeper `/hbase/meta-region-server`（meta 位置注册）

**只清一个不够**，Master 会从剩下的地方读到旧状态。

---

*本文档记录 2026-10-28 HBase 元数据损坏的完整排查与修复过程。*
