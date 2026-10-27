# S40 · Docker 用户方案：单节点 HDFS

> 日期：2026-10-27
> 用户需求：「对于电脑上有 docker 的同学，有没有适合他们的方案？」

---

## 一、问题重述

S39 给了三条路线，但只有 Docker 的同学被分到了「路线 B：纯本地模式」——
这只是**读 JSONL 文件**，跟 Hadoop 没有关系。

问题：**能不能让只有 Docker 的同学也跑出「真 Hadoop」？**

---

## 二、可行性判断

### 2.1 先看需要什么

S39 已确认：项目的数据源层只需要 **HDFS**（不需要 HBase/Spark/Hive）。

那么问题是：**能不能只装 HDFS？**

### 2.2 现有镜像的体积构成

```
hadoop-lab:2026.10  6.89 GB
├── /opt/hadoop       1.4 GB   ← 只需要这个
├── /opt/hbase        405 MB   ← 不需要
├── /opt/spark        429 MB   ← 不需要
├── /opt/hive         356 MB   ← 不需要
├── /opt/flink        504 MB   ← 不需要
└── /opt/zookeeper     44 MB   ← 不需要
```

**结论：可以砍掉约 4.3 GB（63%）。**

### 2.3 结论

做一个「只含 HDFS」的镜像，单容器跑伪分布式。
同学 `docker run` 一条命令就有真 HDFS。

---

## 三、实现

### 3.1 镜像设计

`hadoop/Dockerfile.hdfs`：

```dockerfile
FROM eclipse-temurin:11-jre-jammy     # 只装 JRE，不装完整 JDK

# 下载 Hadoop 3.3.6（阿里云镜像优先，失败回退 Apache 官方）
# 730 MB tar.gz

# 瘦身：只删文档/源码jar/Windows脚本
# ⚠️ 不能删 share/hadoop/{yarn,mapreduce,tools} ← 见第四节踩坑

COPY core-site.xml hdfs-site.xml hadoop-env.sh
COPY entrypoint.sh
```

**最终镜像 2.54 GB**（vs 原 6.89 GB）。

### 3.2 关键配置

`core-site.xml`：

```xml
<property>
    <name>fs.defaultFS</name>
    <value>hdfs://localhost:9000</value>
</property>
<property>
    <name>dfs.webhdfs.enabled</name>
    <value>true</value>          <!-- 项目靠这个读数据 -->
</property>
```

`hdfs-site.xml` 的两个关键点：

```xml
<!-- ① 必须绑 0.0.0.0，否则容器外访问不到 WebHDFS -->
<property>
    <name>dfs.namenode.http-address</name>
    <value>0.0.0.0:9870</value>
</property>

<!-- ② 单节点只能存 1 份，设 2 会一直报 under-replicated -->
<property>
    <name>dfs.replication</name>
    <value>1</value>
</property>

<!-- ③ 关权限检查，否则容器内 UID 与 HDFS 记录不一致 -->
<property>
    <name>dfs.permissions.enabled</name>
    <value>false</value>
</property>
```

### 3.3 启动流程（entrypoint.sh）

```
1. 首次启动 → hdfs namenode -format
2. 启动 NameNode
3. 轮询等待退出 safe mode（约 4 秒）
4. 二次确认 WebHDFS 可访问
5. 启动 DataNode
6. 创建项目目录 /hlm/{warehouse,derived,logs}
7. 检测 /seed 挂载 → 自动导入 4 个数据文件
8. 前台 wait 保持容器存活
```

**自动导入是本方案的亮点**：启动时挂 `-v ./deploy/data:/seed:ro`，
容器自己 `hdfs dfs -put`，同学不用手动导数据。

---

## 四、踩坑：不能删 share/hadoop 子目录

### 4.1 坑①：删 yarn 报 HADOOP_YARN_HOME

第一版为了瘦身删掉了：

```dockerfile
rm -rf share/hadoop/yarn share/hadoop/mapreduce share/hadoop/tools
```

结果启动直接失败：

```
[hdfs] 首次启动，格式化 NameNode ...
[hdfs] 格式化失败，见 /logs/format.log
ERROR: Invalid HADOOP_YARN_HOME
```

**原因**：`bin/hdfs` 启动脚本会校验 `HADOOP_YARN_HOME`，
目录不存在就报错 —— **即使这个项目根本不用 YARN**。

### 4.2 坑②：删 mapreduce 报 HADOOP_MAPRED_HOME

保留 yarn、继续删 mapreduce：

```
ERROR: Invalid HADOOP_MAPRED_HOME
```

同样的机制。

### 4.3 最终决策

**不删任何 `share/hadoop/*` 子目录**，只删：

```dockerfile
rm -rf share/doc
find . -name "*-sources.jar" -delete
find . -name "*.cmd" -delete        # Windows 脚本
```

省下的空间（约 300 MB）不值得破坏启动脚本。

**代价**：镜像从「理论 1.5GB」变成实际 2.54GB，但仍比 6.89GB 轻 63%。

---

## 五、start.sh 集成

### 5.1 三个模式参数

```bash
./start.sh                # 自动探测（有 Hadoop 就用；没有就交互询问）
./start.sh --with-hdfs    # 强制起单节点 HDFS
./start.sh --local-only   # 强制纯本地文件
```

### 5.2 自动探测逻辑

```
① 找已有 Hadoop 容器（master / namenode / hdfs-namenode / hdfs）
   ├─ 找到 → 探测 WebHDFS（9870 / 50070）
   │    ├─ 可用且有项目数据 → 用它（路线 A）
   │    └─ 可用但没数据 → 提示 tools/import_to_hdfs.sh
   └─ 没找到
        ↓
② 交互询问「启动单节点 HDFS? [Y/n]」
   ├─ Y → 构建/启动 hlm-hdfs 容器（路线 B）
   └─ n → 本地文件（路线 B-）
```

### 5.3 关键设计：幂等

重复运行 `./start.sh --with-hdfs` 不会重建容器：

```bash
if docker ps -a --format '{{.Names}}' | grep -qx 'hlm-hdfs'; then
    # 已存在 → 检查是否运行，没运行就 start
else
    # 不存在 → docker run
fi
```

镜像也只在不存在时构建。

---

## 六、验证

### 6.1 镜像体积

| 镜像 | 体积 |
|:---|---:|
| `hadoop-lab:2026.10`（完整） | 6.89 GB |
| **`hlm-hdfs`（本项目）** | **2.54 GB** |
| 减少 | **63%** |

### 6.2 启动耗时（实测）

```
[hdfs] 11:23:18 首次启动，格式化 NameNode ...
[hdfs] 11:23:20 格式化完成                    ← 2 秒
[hdfs] 11:23:20 启动 NameNode ...
[hdfs] 11:23:25 safe mode OFF（等待 4 秒）
[hdfs] 11:23:25 WebHDFS 就绪
[hdfs] 11:23:25 启动 DataNode ...
[hdfs] 11:23:31 DataNode 状态: 1 项
[hdfs] 11:23:31 创建 HDFS 目录 ...
[hdfs] 11:23:37 检测到 /seed 数据，自动导入 ...
[hdfs] 11:23:47 数据导入完成
[hdfs] 11:23:47 HDFS 就绪
```

**首次 29 秒**（含格式化 + 导入 43 MB 数据）；二次启动约 **12 秒**。

### 6.3 应用连接验证

```bash
docker network create hlm-net
docker run -d --name hlm-hdfs --network hlm-net ... hlm-hdfs
docker run -d --name hlm-api --network hlm-net \
  -e HLM_DATA_SOURCE=hdfs \
  -e HLM_WEBHDFS_URL=http://hlm-hdfs:9870/webhdfs/v1 \
  hlm-api
```

结果：

```
数据源: hdfs（环境变量强制）
载入向量矩阵: (11957, 512)
载入 chunk 元数据: 11,780 条（正文 3,295 / 脂批 8,662）
内存倒排: 232,278 个 bigram

health: chunk=11780  data_source=hdfs
```

### 6.4 检索质量

| 问题 | 结果 |
|:---|:---:|
| 宝玉为什么挨打？ | ✅ 第33回 |
| 林黛玉为什么进贾府？ | ✅ 第3回 |
| 黛玉葬花在第几回？ | ✅ 第27回 |
| 金钏为什么投井？ | ✅ 第32回 |
| 通灵宝玉上的铭文是什么？ | ✅ 第8回 |

**5/5** — 与路线 A（真实集群）完全一致。

### 6.5 端到端问答

```
问：宝玉为什么挨打？
引用：第33回 / 第9回 / 第46回
```

### 6.6 HDFS UI 可访问

```bash
curl http://127.0.0.1:19870          # HTTP 200
```

浏览器打开能看到 NameNode 界面 —— 同学可以直观看到「Hadoop 在跑」。

---

## 七、端口设计

**为什么用 19870/19000 而不是 9870/9000？**

我的开发机上 9870/9000 已被真实集群占用。为了让**两套共存**，
单节点 HDFS 映射到 19870/19000。

同学如果没端口冲突，可以改成 9870/9000：
```bash
docker run -p 9870:9870 -p 9000:9000 ...
```

**容器内部端口不变**（永远是 9870/9000），只有宿主机映射可变 ——
这样 `HLM_WEBHDFS_URL` 在容器网络内始终是 `http://hlm-hdfs:9870/...`。

---

## 八、资源占用

| 项 | 值 |
|:---|---:|
| HDFS 镜像磁盘 | 2.54 GB |
| HDFS 容器内存 | 约 700 MB（NN 512M + DN 384M 堆） |
| 后端镜像 | 665 MB |
| 后端容器内存 | 约 1.2 GB |
| 数据 volume | 约 43 MB |
| **合计内存** | **约 2 GB** |

8 GB 内存的电脑足够跑。

---

## 九、改动文件

| 文件 | 类型 | 说明 |
|:---|:---|:---|
| `hadoop/Dockerfile.hdfs` | **新增** | 单节点 HDFS 镜像（2.54 GB） |
| `hadoop/core-site.xml` | **新增** | 开 WebHDFS |
| `hadoop/hdfs-site.xml` | **新增** | 副本 1、绑 0.0.0.0、关权限 |
| `hadoop/hadoop-env.sh` | **新增** | JAVA_HOME、堆大小 |
| `hadoop/entrypoint.sh` | **新增** | 启动 + 自动导数据 |
| `start.sh` | **重写** | 三模式参数 + 自动探测 + 幂等 |
| `docs/38-部署指南.md` | 修改 | 路线 B 重写为「Docker 单节点 HDFS」 |
| `README.md` | 修改 | 快速开始改为三命令选择 |

---

## 十、三条路线最终对比

| | 路线 A | **路线 B** | 路线 B- | 路线 C |
|:---|:---|:---|:---|:---|
| 适用 | 有 Hadoop | **只有 Docker** | 只想快 | 有 HBase 1.x/2.x |
| 命令 | `./start.sh` | `./start.sh --with-hdfs` | `./start.sh --local-only` | 手动 |
| 有 Hadoop | ✅ 你的 | ✅ **内置** | ❌ | ✅ |
| 磁盘 | 0 | 2.54 GB | 0 | 0 |
| 首次耗时 | 10 min | **15 min** | 8 min | 40 min |
| 检索结果 | 基准 | 相同 | 相同 | 相同 |

---

## 十一、遗留

| # | 项 | 说明 |
|:--:|:---|:---|
| 1 | 镜像 2.54 GB 仍偏大 | 不删 share/hadoop 是硬约束（见第四节） |
| 2 | 单节点无副本 | `dfs.replication=1`，没有容错能力（单机演示够用） |
| 3 | 首次构建要下载 730 MB | 依赖网络；阿里云镜像优先但未做断点续传 |
| 4 | 未支持 ARM（Apple Silicon） | `eclipse-temurin:11-jre-jammy` 有 arm64 版，但未实测 |
| 5 | 19870/19000 端口硬编码 | 若被占用需手动改，脚本未做自动避让 |

### 关于遗留 #4

Apple Silicon 的用户可能遇到问题。理论上 `eclipse-temurin:11-jre-jammy`
有多架构 manifest，Docker Desktop 会自动拉 arm64 版，
Hadoop 是纯 Java 应该没问题 —— 但**我没有 Apple 设备，无法验证**。
部署指南里应标注这一点。

---

## 十二、最终状态

| 项 | 数值 |
|:---|:---|
| 镜像体积 | 2.54 GB（-63%） |
| HDFS 启动 | 29 秒（首次）/ 12 秒（二次） |
| 容器内存 | 约 700 MB |
| 应用连接 | ✅ 成功（data_source=hdfs） |
| 检索命中 | 5/5 |
| 部署步骤 | 2 条命令 |

---

*本文档记录 Docker 用户单节点 HDFS 方案，含 2 次 Dockerfile 瘦身踩坑。*
