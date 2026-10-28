# -*- coding: utf-8 -*-
"""
从 HDFS 事实源重建 HBase 脂批索引（S40b）
=========================================

背景
----
HBase 元数据损坏后已清空重建，5 张表重新建好，
s7_2 已导入正文 chunk/vector/term（3,270 条正文）。

本脚本补导入**脂批**（8,510 条）：
    /hlm/warehouse/pizhu3src/pizhu_3src.jsonl          4.6 MB
    /hlm/derived/vectors3ver/.../pizhu_3src_vectors.jsonl  23.7 MB

关键：**不重新向量化**，直接读 HDFS 上已有的向量快照。
      原因：向量化 8,510 条要 4 分钟，而快照就在 HDFS 上（R-510 的设计红利）。

设计与 s7_2 一致：
    rowkey = <ver>|<source>|<hui:03d>|<cid>
    chunk 表 families: d(数据), m(元数据)
    vector 表 families: v(向量), m(元数据)

运行（master 容器内）：
    docker exec master bash -lc "python3 /workspace/pipeline/s40_import_pizhu.py"
"""
import base64
import json
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

import happybase

HDFS = "hdfs://master:9000"
HDFS_CHUNKS = "/hlm/warehouse/pizhu3src/pizhu_3src.jsonl"
HDFS_VECS = "/hlm/derived/vectors3ver/model=bge-small-zh-v1.5/pizhu_3src_vectors.jsonl"
LOCAL_CHUNKS = "/tmp/pizhu_3src.jsonl"
LOCAL_VECS = "/tmp/pizhu_3src_vectors.jsonl"

T_CHUNKS = "hlm_chunks"
T_VECTORS = "hlm_vectors"
T_TERMS = "hlm_terms"


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def fetch(hdfs_path, local_path):
    """从 HDFS 拉到本地临时文件（happybase 不能直接读 HDFS）"""
    if os.path.exists(local_path) and os.path.getsize(local_path) > 1024:
        log(f"  复用本地 {local_path} ({os.path.getsize(local_path)/1048576:.1f} MB)")
        return local_path
    log(f"  从 HDFS 拉取 {hdfs_path} ...")
    r = subprocess.run(
        ["hdfs", "dfs", "-get", "-f", hdfs_path, local_path],
        capture_output=True, text=True,
    )
    if r.returncode != 0 and not os.path.exists(local_path):
        log(f"  ✗ 拉取失败: {r.stderr[:200]}")
        return None
    log(f"  ✓ {local_path} ({os.path.getsize(local_path)/1048576:.1f} MB)")
    return local_path


def main():
    t0 = time.time()
    c = happybase.Connection(
        "localhost", 9090, table_prefix=b"",
        table_prefix_separator=b"", timeout=120000,
    )
    tb = c.table(T_CHUNKS)
    tbv = c.table(T_VECTORS)

    # ---- 1) chunks ----
    p = fetch(f"{HDFS}{HDFS_CHUNKS}", LOCAL_CHUNKS)
    if not p:
        return 1

    log("读脂批 chunk ...")
    recs = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                recs.append(json.loads(line))
    log(f"  {len(recs):,} 条")

    # 统计分布
    dist = {}
    for r in recs:
        k = f"{r.get('version')}/{r.get('source', 'pizhu')}"
        dist[k] = dist.get(k, 0) + 1
    log(f"  分布: {dist}")

    # ---- 2) 写 chunk ----
    log(f"写 {T_CHUNKS} ...")
    batch = tb.batch(batch_size=500)
    n = 0
    for r in recs:
        cid = r.get("chunk_id")
        if not cid:
            continue
        ver = r.get("version") or ""
        src = r.get("source") or "pizhu"
        hui = int(r.get("hui") or 0)
        rowkey = f"{ver}|{src}|{hui:03d}|{cid}".encode()

        data = {
            b"d:text": (r.get("text") or "").encode(),
            b"d:char_count": str(r.get("char_count") or len(r.get("text") or "")).encode(),
        }
        meta = {
            b"m:version": ver.encode(),
            b"m:source": src.encode(),
            b"m:hui": str(hui).encode(),
            b"m:huimu": str(r.get("huimu") or "").encode(),
            b"m:para_start": str(r.get("para_start") or 0).encode(),
            b"m:para_end": str(r.get("para_end") or 0).encode(),
            b"m:seg": str(r.get("seg") or 0).encode(),
            b"m:pizhu_versions": json.dumps(r.get("pizhu_versions") or [], ensure_ascii=False).encode(),
            b"m:pizhu_page": str(r.get("pizhu_page") or "").encode(),
            b"m:src_tag": str(r.get("src_tag") or "").encode(),
            b"m:origin": str(r.get("origin") or "").encode(),
            b"m:pizhu_ptype": str(r.get("pizhu_ptype") or "").encode(),
            b"m:partition": str(r.get("partition") or "").encode(),
            b"m:strategy_rev": str(r.get("strategy_rev") or "").encode(),
        }
        batch.put(rowkey, {**data, **meta})
        n += 1
    batch.send()
    log(f"  ✓ {T_CHUNKS} 写入 {n:,} 行")

    # ---- 3) vectors ----
    p = fetch(f"{HDFS}{HDFS_VECS}", LOCAL_VECS)
    if not p:
        return 1

    log("读脂批向量 ...")
    vecs = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                vecs.append(json.loads(line))
    log(f"  {len(vecs):,} 条")

    log(f"写 {T_VECTORS} ...")
    batch = tbv.batch(batch_size=500)
    n = 0
    for r in vecs:
        cid = r.get("chunk_id")
        if not cid:
            continue
        ver = r.get("version") or ""
        src = r.get("source") or "pizhu"
        hui = int(r.get("hui") or 0)
        rowkey = f"{ver}|{src}|{hui:03d}|{cid}".encode()

        vb64 = r.get("vec_b64") or r.get("emb_b64") or ""
        if not vb64:
            continue
        batch.put(rowkey, {
            b"v:vec_b64": vb64.encode(),
            b"v:dim": str(r.get("dim") or 512).encode(),
            b"m:model": (r.get("model") or "bge-small-zh-v1.5").encode(),
        })
        n += 1
    batch.send()
    log(f"  ✓ {T_VECTORS} 写入 {n:,} 行")

    # ---- 4) 校验 ----
    log("-- 校验 --")
    for t, tab in ((T_CHUNKS, tb), (T_VECTORS, tbv)):
        cnt = sum(1 for _ in tab.scan(batch_size=2000))
        log(f"  {t} 总行数: {cnt:,}")

    log(f"全部完成，耗时 {time.time()-t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
