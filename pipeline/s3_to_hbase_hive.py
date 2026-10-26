# -*- coding: utf-8 -*-
"""
S3 · 把 MapReduce 结果写入 HBase + 建 Hive 外部表
===================================================

对应需求
  R-506  MapReduce 统计结果回写 HDFS + HBase
  R-507  统计结果用 Hive 提供 SQL 查询

HBase 表
  hlm_stats    rowkey=<metric>|<key>   列族 s:  (value)
    metric = personfreq / cooccur / chunkstat
  hlm_wordfreq rowkey=<bigram>         列族 f:cnt

Hive 外部表（映射 HDFS 上的 TSV）
  hlm_word_freq(bigram string, cnt int)
  hlm_person_freq(name string, cnt int)
  hlm_cooccur(a string, b string, cnt int)
  hlm_chunk_stat(hui int, n_chunk int, n_bytes int, n_chars int)

运行：docker exec master bash -lc "cd /workspace/pipeline && python3 s3_to_hbase_hive.py"
"""
import os
import sys
import time
import subprocess
import collections

sys.stdout.reconfigure(encoding="utf-8")

PIPE = "/workspace/pipeline"
HDFS_OUT = "/hlm/mrout"

T_STATS = "hlm_stats"
T_WF = "hlm_wordfreq"
RAG_TABLES = "hlm_rag_tables"      # 记录表清单，避免用 hbase shell 探测


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def shell_db(cmd):
    """执行 hbase shell 命令（写文件后执行，避免 bash 展开花括号）"""
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".rb", delete=False,
                                     encoding="utf-8") as f:
        f.write(cmd + "\n")
        p = f.name
    try:
        r = subprocess.run(["hbase", "shell", "-n", p], capture_output=True, text=True)
        return r.stdout + r.stderr
    finally:
        try:
            os.unlink(p)
        except OSError:
            pass


def table_exists(name):
    import json
    out = shell_db("list")
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("=>") and "[" in line:
            try:
                return name in json.loads(line.split("=>", 1)[1].strip())
            except Exception:
                pass
    return False


def ensure_table(name, families):
    if table_exists(name):
        log(f"  表 {name} 已存在")
        return
    fams = ", ".join(f"{{NAME => '{f}', VERSIONS => 1}}" for f in families)
    out = shell_db(f"create '{name}', {fams}")
    log(f"  建表 {name}  families={families}")
    if "ERROR" in out.upper():
        log(f"    ⚠ {out.strip()[-200:]}")
    time.sleep(2)


def conn():
    import happybase
    return happybase.Connection("master", port=9090, timeout=600000,
                                table_prefix=b"", table_prefix_separator=b"")


def read_tsv(name, ncol):
    p = os.path.join(PIPE, "out", name)
    if not os.path.exists(p):
        return []
    rows = []
    with open(p, encoding="utf-8", errors="replace") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= ncol:
                rows.append(parts[:ncol])
    return rows


def main():
    if not os.path.exists("/workspace/pipeline"):
        print("必须在 master 容器内运行")
        sys.exit(1)

    c = conn()
    ensure_table(T_STATS, ["s"])
    ensure_table(T_WF, ["f"])

    # ---- 1) 词频 → hlm_wordfreq ----
    wf = read_tsv("wordfreq.tsv", 2)
    log(f"词频条数: {len(wf):,}")
    t0 = time.time()
    t = c.table(T_WF)
    n = 0
    with t.batch(batch_size=500):
        for w, cnt in wf:
            t.put(f"fuqinju|{w}".encode(), {b"f:cnt": cnt.encode()})
            n += 1
    log(f"  {T_WF}: 写入 {n:,} 行  耗时 {time.time() - t0:.1f}s")

    # ---- 2) 其余统计 → hlm_stats ----
    ts = c.table(T_STATS)
    counts = {}
    for metric, fname, ncol in [
        ("personfreq", "personfreq.tsv", 2),
        ("cooccur", "cooccur.tsv", 2),
        ("chunkstat", "chunkstat.tsv", 4),
    ]:
        rows = read_tsv(fname, ncol)
        t0 = time.time()
        n = 0
        with ts.batch(batch_size=500):
            for parts in rows:
                if metric == "chunkstat":
                    hui, nc, nb, nch = parts
                    rk = f"{metric}|{int(hui):03d}".encode()
                    ts.put(rk, {b"s:n_chunk": nc.encode(),
                                b"s:n_bytes": nb.encode(),
                                b"s:n_chars": nch.encode()})
                elif metric == "cooccur":
                    pair, cnt = parts
                    rk = f"{metric}|{pair}".encode()
                    ts.put(rk, {b"s:cnt": cnt.encode()})
                else:
                    name, cnt = parts
                    rk = f"{metric}|{name}".encode()
                    ts.put(rk, {b"s:cnt": cnt.encode()})
                n += 1
        counts[metric] = n
        log(f"  {T_STATS}[{metric}]: 写入 {n:,} 行  耗时 {time.time() - t0:.1f}s")

    # ---- 3) 校验 ----
    log("-- 校验 --")
    log(f"  {T_WF}: {sum(1 for _ in c.table(T_WF).scan(batch_size=500)):,} 行")
    log(f"  {T_STATS}: {sum(1 for _ in c.table(T_STATS).scan(batch_size=500)):,} 行")
    # 抽样
    for k, d in c.table(T_STATS).scan(limit=2):
        log(f"  抽样 {k.decode()} -> { {kk.decode(): vv.decode() for kk, vv in d.items()} }")
    c.close()

    # ---- 4) Hive 外部表 ----
    log("-- 建 Hive 外部表（映射 HDFS TSV）--")
    hive_sql = f"""
DROP TABLE IF EXISTS hlm_word_freq;
CREATE EXTERNAL TABLE hlm_word_freq (bigram STRING, cnt INT)
ROW FORMAT DELIMITED FIELDS TERMINATED BY '\\t'
STORED AS TEXTFILE LOCATION '{HDFS_OUT}/wordfreq';

DROP TABLE IF EXISTS hlm_person_freq;
CREATE EXTERNAL TABLE hlm_person_freq (name STRING, cnt INT)
ROW FORMAT DELIMITED FIELDS TERMINATED BY '\\t'
STORED AS TEXTFILE LOCATION '{HDFS_OUT}/personfreq';

DROP TABLE IF EXISTS hlm_cooccur;
CREATE EXTERNAL TABLE hlm_cooccur (a STRING, b STRING, cnt INT)
ROW FORMAT DELIMITED FIELDS TERMINATED BY '\\t'
STORED AS TEXTFILE LOCATION '{HDFS_OUT}/cooccur';

DROP TABLE IF EXISTS hlm_chunk_stat;
CREATE EXTERNAL TABLE hlm_chunk_stat (hui INT, n_chunk INT, n_bytes INT, n_chars INT)
ROW FORMAT DELIMITED FIELDS TERMINATED BY '\\t'
STORED AS TEXTFILE LOCATION '{HDFS_OUT}/chunkstat';

SHOW TABLES;
"""
    sql_path = "/tmp/hlm_hive.sql"
    with open(sql_path, "w", encoding="utf-8") as f:
        f.write(hive_sql)

    # 检查 HiveServer2 是否运行
    chk = subprocess.run(["bash", "-lc",
                          "python3 -c \"import socket;s=socket.socket();s.settimeout(2);"
                          "print('OPEN' if s.connect_ex(('master',10000))==0 else 'CLOSED');s.close()\""],
                         capture_output=True, text=True)
    log(f"  HiveServer2 (10000): {chk.stdout.strip()}")
    if "CLOSED" in chk.stdout:
        log("  HiveServer2 未启动，跳过建表（可用 /workspace/start-hive.sh 启动）")
        log("  Hive SQL 已保存到 " + sql_path + "，供演示时手动执行")
        return

    r = subprocess.run(
        ["beeline", "-u", "jdbc:hive2://master:10000", "-n", "root",
         "-f", sql_path],
        capture_output=True, text=True)
    out = r.stdout + r.stderr
    for line in out.splitlines():
        if any(k in line for k in ("hlm_", "No rows", "Error", "OK", "Time taken")):
            log("  " + line.strip()[:160])

    # Hive SQL 查询验证
    log("-- Hive SQL 验证（Top-5 词频）--")
    r2 = subprocess.run(
        ["beeline", "-u", "jdbc:hive2://master:10000", "-n", "root",
         "--outputformat=tsv2", "-e",
         "SELECT bigram, cnt FROM hlm_word_freq ORDER BY cnt DESC LIMIT 5"],
        capture_output=True, text=True)
    for line in (r2.stdout + r2.stderr).splitlines():
        if line.strip() and not line.startswith(("SLF4J", "log4j", "Warning")):
            log("  " + line.strip()[:120])


if __name__ == "__main__":
    main()
