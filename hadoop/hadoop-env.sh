#!/usr/bin/env bash
# =============================================================
# Hadoop 环境变量
# =============================================================
# 关键：必须显式设 JAVA_HOME
#   eclipse-temurin 镜像的 JAVA_HOME 是 /opt/java/openjdk，
#   但 Hadoop 的 hadoop-env.sh 里有默认值会被覆盖，所以要重设。
# =============================================================

export JAVA_HOME="${JAVA_HOME:-/opt/java/openjdk}"

# 日志与 PID 目录（容器内 /logs 可挂载出来）
export HADOOP_LOG_DIR=/logs/hadoop
export HADOOP_PID_DIR=/tmp/hadoop-pids

# 单节点小内存：限制堆大小，避免容器 OOM
export HDFS_NAMENODE_OPTS="-Xmx512m -Xms256m"
export HDFS_DATANODE_OPTS="-Xmx384m -Xms192m"

# 关闭 IPv6 优先（容器内 DNS 解析可能返回 IPv6 导致连接失败）
export HADOOP_OPTS="$HADOOP_OPTS -Djava.net.preferIPv4Stack=true"

# 用 root 跑（单机开发环境，避免 UID 问题）
export HDFS_NAMENODE_USER=root
export HDFS_DATANODE_USER=root
export HDFS_SECONDARYNAMENODE_USER=root
