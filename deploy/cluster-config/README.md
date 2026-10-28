# cluster-config —— 集群配置备份

> 这份目录是 `D:\Programming\hadoop\cluster\` 的**关键配置文件副本**，
> 放进项目仓库是为了让部署流程可追溯、可复现。

## 文件说明

| 文件 | 作用 |
|:---|:---|
| `compose.yaml` | 集群编排（master + slave1 + slave2）|
| `compose.override.yaml` | S40 修复覆盖层（挂载下面两个文件）|
| `entrypoint-fixed.sh` | 修复版启动脚本（服务分级 + 自动重拉）|
| `hdfs-site-fixed.xml` | 副本数 1 + 放宽心跳 + 缩短 safemode |

## 为什么需要 override 层

原始 `entrypoint.sh` 是 **COPY 进镜像**的（`/usr/local/bin/cluster-entrypoint`），
修改它必须重建 6.89 GB 镜像。用 compose override 挂载新版本，几秒就能生效。

## 部署方法

```bash
# 1) 把本目录文件复制到集群目录
cp deploy/cluster-config/* D:/Programming/hadoop/cluster/

# 2) entrypoint-fixed.sh 必须也放到 workspace（因为要挂载到容器 /workspace）
cp deploy/cluster-config/entrypoint-fixed.sh D:/Programming/hadoop/cluster/workspace/cluster-entrypoint-fixed.sh

# 3) 用 override 启动
cd D:/Programming/hadoop/cluster
docker compose -f compose.yaml -f compose.override.yaml up -d
```

## entrypoint 改了什么

原版最后是：

```bash
wait -n "${children[@]}"      # 任一子进程退出 → 整个容器退出
```

这导致 HBase Master 崩溃会带走 NameNode 和 ZooKeeper，
slave 随之崩溃，形成死循环。

修复版改成**服务分级 + 监控循环**：

| 级别 | 服务 | 崩溃后果 |
|:---|:---|:---|
| **核心** | NameNode、ZooKeeper、HMaster（master）；DataNode（slave） | 容器退出，交给 Docker 重启 |
| **边缘** | YARN RM/NM、JobHistory、Spark Master/Worker、**RegionServer** | 记日志 + 自动重拉（最多 3 次），容器不退 |

**RegionServer 降为边缘**是关键：它崩了不会带走 DataNode，
HDFS 才能保持可用，才有机会修 HBase meta。

## hdfs-site.xml 改了什么

| 项 | 原值 | 新值 | 原因 |
|:---|:---|:---|:---|
| `dfs.replication` | 2 | **1** | 只剩 1 个 DataNode 时 HBase 写不进去（NoRouteToHostException）|
| `dfs.namenode.heartbeat.recheck-interval` | 默认 300000 | 600000 | 容器重启期间避免误判 DataNode 死亡 |
| `dfs.heartbeat.interval` | 默认 3 | 10 | 同上 |
| `dfs.namenode.safemode.min.datanodes` | 默认 1 | 1 | 明确要求，避免 0 DataNode 时卡住 |
| `dfs.namenode.safemode.extension` | 默认 30000 | 10000 | 缩短等待，加快启动 |

**代价**：副本数 1 没有冗余，DataNode 挂掉会丢数据。
单机演示环境可接受（数据在 `deploy/data/` 和单节点 `hlm-hdfs` 都有备份）。

## healthcheck 改了什么

原版要求四个端口全通：

```yaml
# slave 原版
nc -z slave1 9864 && nc -z slave1 8042 && nc -z slave1 16020 && nc -z slave1 8081
```

但 `16020`（RegionServer）和 `8081`（Spark Worker）现在是**边缘服务**，
不保证一直在线，所以健康检查必然失败（实测 `:8081 FAIL`）。

改为只查真正必需的服务：

```yaml
# slave 新版
nc -z slave1 9864 && nc -z slave1 8042        # DataNode + NodeManager

# master 新版（去掉 7077 Spark Master）
nc -z master 9000 && nc -z master 8088 && nc -z master 16000
```

## 环境变量开关

| 变量 | 作用 | 用途 |
|:---|:---|:---|
| `HLM_NO_HBASE=1` | master 只跑 NameNode | 清空 `/hbase` 前用 |
| `HLM_NO_REGIONSERVER=1` | slave 只跑 DataNode | 修 HBase meta 时用 |

示例：

```bash
# 只起 HDFS（供清理 /hbase）
HLM_NO_HBASE=1 docker compose -f compose.yaml -f compose.override.yaml up -d master
HLM_NO_REGIONSERVER=1 ... up -d slave1 slave2

# 清理完再完整启动
HLM_NO_HBASE=0 HLM_NO_REGIONSERVER=0 ... up -d
```

## 关联文档

- `docs/42-S40H-HBase元数据损坏修复.md` —— 完整排查过程
- `docs/38-部署指南.md` —— 三条部署路线
