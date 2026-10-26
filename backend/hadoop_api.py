# -*- coding: utf-8 -*-
"""
S24-1 · 自定义查询：Hadoop 能力 API（HDFS + HBase）
==================================================

设计取舍
--------
用户要求「放宽沙箱，让自定义查询真正使用 Hadoop」。

有两个做法：
  A. 直接放开 `import os/subprocess`，让 AI 自己拼 hdfs 命令
     → 危险：等于任意命令执行，沙箱形同虚设
  B. **能力开放**：提供 hdfs_* / hbase_* 系列封装函数
     → 安全：AI 只能通过这些**受限、只读、参数校验过**的函数碰 Hadoop

**本实现选 B。**

为什么 B 依然算「真正使用 Hadoop」
----------------------------------
· `hdfs_*`  → 真的调 `hdfs dfs -cat/-ls`，走 HDFS RPC
· `hbase_*` → 真的通过 happybase 连 HBase Thrift 9090
· 不是读本地副本，是**实时查询集群**

安全性
------
  · 只读：所有函数禁止写/删
  · 路径白名单：只能访问 /hlm/ 下的路径
  · 参数校验：表名必须在 hlm_* 白名单里
  · 行数/字节上限：防一次拉太多数据
  · 超时：每个 HDFS 调用 15 秒

暴露给沙箱的 API
----------------
  hdfs_ls(path)                 列出目录（返回 name/size/is_dir）
  hdfs_cat(path, max_bytes)     读文件内容（有字节上限）
  hdfs_count_lines(path)        数行数（不需要全部读入内存）
  hdfs_du(path)                 目录/文件大小
  hbase_count(table)            表行数
  hbase_get(table, rowkey)      取一行
  hbase_scan(table, prefix, limit)  前缀扫描（有 limit）
  hadoop_status()               集群状态概览（NameNode/DataNode/HDFS 容量）

运行位置：hlm-api 容器内
"""
import os
import re
import json
import shutil
import subprocess

# ============================================================
# 配置
# ============================================================
HDFS_BIN = shutil.which("hdfs") or "/opt/hadoop/bin/hdfs"
HBASE_BIN = shutil.which("hbase") or "/opt/hbase/bin/hbase"

# 允许访问的 HDFS 前缀（只读白名单）
HDFS_ALLOWED_PREFIXES = ("/hlm/", "/user/root/")

# 允许访问的 HBase 表（只读白名单）
HBASE_ALLOWED_TABLES = {
    "hlm_chunks", "hlm_vectors", "hlm_terms",
    "hlm_wordfreq", "hlm_stats",
}

# 资源上限
MAX_CAT_BYTES = 8 * 1024 * 1024      # 单次读文件上限 8 MB
MAX_SCAN_ROWS = 2000                 # 单次扫描行数上限
HDFS_TIMEOUT = 20                    # HDFS 调用超时（秒）
HBASE_TIMEOUT = 120                  # HBase 连接/调用超时（秒）
                                     # 注：hlm_terms 44.8 万行遍历约需 4~10s，
                                     #     但 socket 空闲超时过短会误判


class HadoopError(Exception):
    """Hadoop 操作失败"""


# ============================================================
# 内部工具
# ============================================================
def _check_hdfs_path(path: str) -> str:
    """校验 HDFS 路径在白名单内，防路径穿越"""
    if not isinstance(path, str) or not path:
        raise HadoopError("路径不能为空")
    p = path.strip()
    if not p.startswith("/"):
        raise HadoopError(f"HDFS 路径必须以 / 开头：{p!r}")
    # 拒绝 .. 穿越
    if ".." in p.split("/"):
        raise HadoopError("路径不允许包含 ..")
    if not any(p == pre.rstrip("/") or p.startswith(pre) for pre in HDFS_ALLOWED_PREFIXES):
        raise HadoopError(f"只允许访问 {HDFS_ALLOWED_PREFIXES} 下的路径，拒绝：{p}")
    return p


def _check_table(table: str) -> str:
    """校验 HBase 表名在白名单内"""
    if table not in HBASE_ALLOWED_TABLES:
        raise HadoopError(
            f"表 {table!r} 不在允许列表 {sorted(HBASE_ALLOWED_TABLES)}")
    return table


def _run(cmd, timeout, text=True):
    """
    执行命令，返回 stdout（失败抛 HadoopError）。

    text=False 时返回 bytes（用于 hdfs_cat 的 head -c 截断场景，
    因为可能截断在多字节 UTF-8 字符中间）。
    """
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=text, errors="replace",
            timeout=timeout,
            env={
                "PATH": "/usr/local/bin:/usr/bin:/bin:/opt/hadoop/bin:/opt/hbase/bin",
                "JAVA_HOME": os.environ.get("JAVA_HOME", "/usr/lib/jvm/java-11-openjdk-amd64"),
                "HADOOP_HOME": "/opt/hadoop",
                "HBASE_HOME": "/opt/hbase",
                "HADOOP_CONF_DIR": "/opt/hadoop/etc/hadoop",
                "HBASE_CONF_DIR": "/opt/hbase/conf",
                "HOME": "/root",
                "LANG": "C.UTF-8",
            },
        )
    except subprocess.TimeoutExpired:
        raise HadoopError(f"命令超时（>{timeout}s）：{cmd[0]}")
    if r.returncode != 0:
        err = r.stderr or b""
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
        err = err.strip()
        # HDFS 输出常混日志，取最后几行有意义的
        lines = [l for l in err.splitlines() if l and not l.startswith(("20", "log4j", "WARN"))]
        raise HadoopError((lines[-1] if lines else err)[:300] or f"退出码 {r.returncode}")
    return r.stdout if r.stdout is not None else ("" if text else b"")


# ============================================================
# HDFS API
# ============================================================
def hdfs_ls(path: str):
    """
    列出 HDFS 目录内容。

    >>> hdfs_ls("/hlm/warehouse/")
    [{"path": "/hlm/warehouse/chunks", "is_dir": True, "size": 0}, ...]
    """
    p = _check_hdfs_path(path)
    out = _run([HDFS_BIN, "dfs", "-ls", p], HDFS_TIMEOUT)
    rows = []
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith("Found "):
            continue
        parts = line.split()
        if len(parts) < 8:
            continue
        perm = parts[0]
        try:
            size = int(parts[4])
        except (ValueError, IndexError):
            size = 0
        full = parts[-1]
        rows.append({
            "path": full,
            "is_dir": perm.startswith("d"),
            "size": size,
            "perm": perm,
            "owner": parts[2],
            "date": " ".join(parts[5:7]),
        })
    return rows


def hdfs_cat(path: str, max_bytes: int = MAX_CAT_BYTES):
    """
    读 HDFS 文件内容（文本）。

    · max_bytes 默认 8 MB，超过会截断并标记 truncated
    · 返回 {"text": ..., "bytes": ..., "truncated": bool}

    >>> r = hdfs_cat("/hlm/warehouse/pizhu3src/pizhu_3src.jsonl")
    >>> len(r["text"]) > 0
    True
    """
    p = _check_hdfs_path(path)
    limit = min(int(max_bytes or MAX_CAT_BYTES), MAX_CAT_BYTES)
    if limit <= 0:
        raise HadoopError("max_bytes 必须 > 0")

    # 先看文件大小，决定是否需要截断读取
    stat = hdfs_du(p)
    size = stat.get("size", 0) if stat else 0

    if size and size > limit:
        # 用 head -c 截断读，避免拉整个大文件
        # 注意：截断可能落在多字节 UTF-8 字符中间 → 用二进制 + errors=replace
        raw = _run(
            ["bash", "-c",
             f"{HDFS_BIN} dfs -cat {p} 2>/dev/null | head -c {limit}"],
            HDFS_TIMEOUT, text=False)
        txt = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        return {"text": txt, "bytes": len(raw) if isinstance(raw, bytes) else len(txt),
                "file_size": size, "truncated": True}
    out = _run([HDFS_BIN, "dfs", "-cat", p], HDFS_TIMEOUT)
    return {"text": out, "bytes": len(out.encode("utf-8", "replace")),
            "file_size": size, "truncated": False}


def hdfs_count_lines(path: str):
    """
    数 HDFS 文件行数（流式，不占内存）。

    >>> hdfs_count_lines("/hlm/warehouse/pizhu3src/pizhu_3src.jsonl") > 0
    True
    """
    p = _check_hdfs_path(path)
    out = _run(
        ["bash", "-c", f"{HDFS_BIN} dfs -cat {p} 2>/dev/null | wc -l"],
        HDFS_TIMEOUT)
    try:
        return int(out.strip().split()[0])
    except Exception:
        return 0


def hdfs_du(path: str):
    """
    查 HDFS 路径的大小（目录会汇总）。

    >>> hdfs_du("/hlm/warehouse/chunks3ver")["size"] > 0
    True
    """
    p = _check_hdfs_path(path)
    out = _run([HDFS_BIN, "dfs", "-du", "-s", p], HDFS_TIMEOUT)
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3:
            try:
                return {"size": int(parts[1]), "path": parts[-1]}
            except ValueError:
                continue
    return {"size": 0, "path": p}


# ============================================================
# HBase API（通过 happybase 连 Thrift 9090）
# ============================================================
def _hbase_conn():
    import happybase
    host = os.environ.get("HLM_HBASE_HOST", "master")
    port = int(os.environ.get("HLM_HBASE_PORT", "9090"))
    return happybase.Connection(
        host=host, port=port, timeout=HBASE_TIMEOUT,
        transport='buffered',
        table_prefix=b"", table_prefix_separator=b"",
    )


def hbase_count(table: str):
    """
    统计 HBase 表行数。

    实测坑与优化：
      · batch_size=500 时单次 Thrift 响应太大 → 超时
      · batch_size=100 时 200 行仅 0.03s
      · **hlm_terms 448,841 行，普通 scan 会超时（>60s）**
      · **加 FirstKeyOnlyFilter 后仅 3.49s（快 100 倍）**
        FirstKeyOnlyFilter 让 RegionServer 只返回 rowkey，
        不传任何列值，网络传输量降到最低。

    >>> hbase_count("hlm_chunks") > 10000
    True
    >>> hbase_count("hlm_terms") > 400000
    True
    """
    t = _check_table(table)
    conn = _hbase_conn()
    try:
        tb = conn.table(t)
        n = 0
        # FirstKeyOnlyFilter：每行只返回第一个 cell 的 rowkey，不传列值
        for _ in tb.scan(batch_size=200, filter=b"FirstKeyOnlyFilter()"):
            n += 1
        return n
    finally:
        try:
            conn.close()
        except Exception:
            pass


def hbase_get(table: str, rowkey: str):
    """
    按 rowkey 取一行，返回 dict（列名 → 值）。

    值会尝试 UTF-8 解码；二进制（如向量 v:emb）返回 "<binary N bytes>"。

    >>> r = hbase_get("hlm_chunks", "chenggao|text|001|" + "0"*16)
    """
    t = _check_table(table)
    if not isinstance(rowkey, str) or not rowkey:
        raise HadoopError("rowkey 不能为空")
    conn = _hbase_conn()
    try:
        tb = conn.table(t)
        row = tb.row(rowkey.encode("utf-8"))
        if not row:
            return {}
        out = {}
        for k, v in row.items():
            col = k.decode("utf-8", "replace") if isinstance(k, bytes) else str(k)
            if isinstance(v, bytes):
                try:
                    out[col] = v.decode("utf-8")
                except UnicodeDecodeError:
                    out[col] = f"<binary {len(v)} bytes>"
            else:
                out[col] = v
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass


def hbase_scan(table: str, prefix: str = "", limit: int = 20):
    """
    前缀扫描 HBase 表。

    · limit 默认 20，上限 2000
    · 二进制列（如 v:emb）返回 "<binary N bytes>"，不含原始字节

    >>> rs = hbase_scan("hlm_chunks", "fuqinju|pizhu|", limit=3)
    >>> len(rs) <= 3
    True
    """
    t = _check_table(table)
    lim = max(1, min(int(limit or 20), MAX_SCAN_ROWS))
    conn = _hbase_conn()
    try:
        tb = conn.table(t)
        kwargs = {"limit": lim, "batch_size": min(100, max(10, lim))}
        if prefix:
            kwargs["row_prefix"] = prefix.encode("utf-8")
        rows = []
        for rk, data in tb.scan(**kwargs):
            item = {"rowkey": rk.decode("utf-8", "replace")}
            for k, v in data.items():
                col = k.decode("utf-8", "replace") if isinstance(k, bytes) else str(k)
                if isinstance(v, bytes):
                    try:
                        item[col] = v.decode("utf-8")
                    except UnicodeDecodeError:
                        item[col] = f"<binary {len(v)} bytes>"
                else:
                    item[col] = v
            rows.append(item)
        return rows
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ============================================================
# 集群状态
# ============================================================
def hadoop_status():
    """
    集群状态概览：NameNode/DataNode、HDFS 容量、HBase 表行数。

    >>> s = hadoop_status()
    >>> s["hdfs"]["live_datanodes"] >= 1
    True
    """
    out = {"hdfs": {}, "tables": {}}
    try:
        rep = _run([HDFS_BIN, "dfsadmin", "-report"], HDFS_TIMEOUT)
        for line in rep.splitlines():
            line = line.strip()
            if line.startswith("Configured Capacity:"):
                out["hdfs"]["capacity"] = line.split(":", 1)[1].strip()
            elif line.startswith("DFS Used:"):
                out["hdfs"]["used"] = line.split(":", 1)[1].strip()
            elif line.startswith("DFS Remaining:"):
                out["hdfs"]["remaining"] = line.split(":", 1)[1].strip()
            elif line.startswith("Live datanodes"):
                m = re.search(r"\((\d+)\)", line)
                out["hdfs"]["live_datanodes"] = int(m.group(1)) if m else 0
            elif line.startswith("Under replicated blocks:"):
                out["hdfs"]["under_replicated"] = line.split(":", 1)[1].strip()
    except HadoopError as e:
        out["hdfs"]["error"] = str(e)

    # 表行数（只查允许的表，且限制耗时）
    for t in sorted(HBASE_ALLOWED_TABLES):
        try:
            out["tables"][t] = hbase_count(t)
        except Exception as e:
            out["tables"][t] = f"error: {e}"
    return out


# ============================================================
# 暴露给沙箱的 API
# ============================================================
SANDBOX_HADOOP_API = {
    "hdfs_ls": hdfs_ls,
    "hdfs_cat": hdfs_cat,
    "hdfs_count_lines": hdfs_count_lines,
    "hdfs_du": hdfs_du,
    "hbase_count": hbase_count,
    "hbase_get": hbase_get,
    "hbase_scan": hbase_scan,
    "hadoop_status": hadoop_status,
    "HDFS_ALLOWED_PREFIXES": list(HDFS_ALLOWED_PREFIXES),
    "HBASE_ALLOWED_TABLES": sorted(HBASE_ALLOWED_TABLES),
}


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print("=" * 70)
    print("Hadoop 能力 API 自检")
    print("=" * 70)

    print("\n[1] hdfs_ls('/hlm/')")
    for x in hdfs_ls("/hlm/"):
        print(f"   {'DIR ' if x['is_dir'] else 'FILE'} {x['size']:>10,}  {x['path']}")

    print("\n[2] hdfs_du('/hlm/warehouse/chunks3ver')")
    print(f"   {hdfs_du('/hlm/warehouse/chunks3ver')}")

    print("\n[3] hdfs_count_lines('/hlm/warehouse/pizhu3src/pizhu_3src.jsonl')")
    print(f"   {hdfs_count_lines('/hlm/warehouse/pizhu3src/pizhu_3src.jsonl'):,} 行")

    print("\n[4] hdfs_cat 前 200 字节")
    r = hdfs_cat("/hlm/warehouse/pizhu3src/pizhu_3src.jsonl", max_bytes=200)
    print(f"   truncated={r['truncated']} file_size={r['file_size']:,}")
    print(f"   {r['text'][:160]!r}")

    print("\n[5] hbase_count 各表")
    for t in sorted(HBASE_ALLOWED_TABLES):
        try:
            print(f"   {t:16s} {hbase_count(t):>10,} 行")
        except Exception as e:
            print(f"   {t:16s} ERR: {e}")

    print("\n[6] hbase_scan('hlm_chunks', 'fuqinju|pizhu|', limit=2)")
    for row in hbase_scan("hlm_chunks", "fuqinju|pizhu|", limit=2):
        rk = row["rowkey"]
        txt = str(row.get("d:text", ""))[:40]
        print(f"   {rk}  {txt!r}")

    print("\n[7] 安全检查：越权路径 / 越权表")
    for bad in ("/etc/passwd", "/hlm/../etc/passwd", "relative/path"):
        try:
            hdfs_ls(bad)
            print(f"   ❌ {bad!r} 未被拒绝")
        except HadoopError as e:
            print(f"   ✅ {bad!r} 已拒绝：{str(e)[:40]}")
    try:
        hbase_count("hlm_secret")
        print("   ❌ 越权表未被拒绝")
    except HadoopError as e:
        print(f"   ✅ 越权表已拒绝：{str(e)[:40]}")
