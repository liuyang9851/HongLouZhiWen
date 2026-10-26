# -*- coding: utf-8 -*-
"""
S5 补强 · 把脂批纳入 RAG 索引
==============================

**这是 S5 评测暴露出来的真实缺口**：
  推理型 r015「脂批对理解《红楼梦》有什么价值？」得 1 分，
  系统诚实回答「语料中未找到」——行为正确，但能力缺失。

根因：`hlm_chunks` 只索引了正文（1,035 chunk），
      `hlm_pizhu`（3,234 条脂批）从来没进过 RAG 索引。

本脚本做三件事：
  1. 把 3,234 条脂批切成 chunk（一条批语 = 一个 chunk，天然边界清晰）
  2. 用同一个模型向量化
  3. 写进同一个 HBase 表（加 source 字段区分 text/pizhu）

设计要点
--------
- **溯源字段扩展**：chunk 增加 `source`（text/pizhu）+ `pizhu_versions`（脂批版本）
- **rowkey 加 source 前缀**：`<ver>|<source>|<hui:03d>|<chunk_id>`，
  这样按 source 扫描很方便，也不会和正文 chunk 撞
- **chunk_id 加 source**：避免与正文 ID 冲突
- 脂批的 `versions_legend` 是**版本证据**，要保留进元数据（对应「版本可追溯」）

运行（master 容器内）：
    docker exec master bash -lc "cd /workspace/pipeline && python3 s5_4_add_pizhu.py"
"""
import os
import sys
import json
import time
import base64
import hashlib
import collections

sys.stdout.reconfigure(encoding="utf-8")

PIPE = "/workspace/pipeline"
OUT = os.path.join(PIPE, "out")
MODEL_DIR = "/data/opt/models/bge-small-zh-v1.5"
MODEL_ID = "bge-small-zh-v1.5"
EMB_DIM = 512
MAX_LEN = 512
BATCH = 32
VERSION = "fuqinju"
STRATEGY_REV = "v1-pizhu"

HDFS_BASE = "/hlm"
HDFS_PIZHU = f"{HDFS_BASE}/warehouse/pizhu/{VERSION}"
T_CHUNKS = "hlm_chunks"
T_VECTORS = "hlm_vectors"
T_TERMS = "hlm_terms"


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


# ---------------- bigram（与正文索引一致） ----------------
STOP = set("的了在是我有和就不人都一这中大为上个国到说们")


def bigrams(text):
    chars = [c for c in text if '\u4e00' <= c <= '\u9fff']
    out = collections.Counter()
    for i in range(len(chars) - 1):
        a, b = chars[i], chars[i + 1]
        if a in STOP and b in STOP:
            continue
        out[a + b] += 1
    return out


# ---------------- HBase ----------------
def conn():
    import happybase
    return happybase.Connection("master", port=9090, timeout=600000,
                                table_prefix=b"", table_prefix_separator=b"")


# ---------------- 读脂批 ----------------
PIZHU_SRC = "/workspace/pipeline/../码点核对/pizhu_redecoded.jsonl"


def load_pizhu():
    """
    读脂批。优先用「码点重解码」版本（唯一可信的版本归属来源）。

    为什么不用 clean/pizhu.jsonl：
      那里面 version/ptype/conf 三个字段是旧解码程序猜的，
      官方说明已明确「应全部停用，改用 pizhu_redecoded.jsonl 的 versions_legend」。
    """
    candidates = [
        "/workspace/pipeline/pizhu_redecoded.jsonl",
        "/workspace/pipeline/../码点核对/pizhu_redecoded.jsonl",
    ]
    path = None
    for c in candidates:
        if os.path.exists(c):
            path = c
            break
    if not path:
        raise SystemExit("找不到 pizhu_redecoded.jsonl，请先 docker cp 进容器")

    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    log(f"读入脂批: {len(rows):,} 条  ({path})")
    return rows


def make_chunk_id(hui, idx, text):
    """chunk_id = sha1('pizhu|hui|idx|text_hash|strategy_rev')[:16]"""
    h = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
    key = f"pizhu|{hui:03d}|{idx:04d}|{h}|{STRATEGY_REV}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


# ---------------- 向量化 ----------------
def get_encoder():
    import onnxruntime as ort
    import numpy as np
    import re

    vocab = {}
    for i, line in enumerate(open(os.path.join(MODEL_DIR, "vocab.txt"), encoding="utf-8")):
        vocab[line.rstrip("\n")] = i
    cls_id, sep_id = vocab.get("[CLS]", 101), vocab.get("[SEP]", 102)
    unk_id, pad_id = vocab.get("[UNK]", 100), vocab.get("[PAD]", 0)
    sess = ort.InferenceSession(os.path.join(MODEL_DIR, "model.onnx"),
                                providers=["CPUExecutionProvider"])

    def encode(texts):
        seqs = []
        for t in texts:
            toks = re.findall(r"[\u4e00-\u9fff]|[a-z]+|[0-9]+|[^\sa-z0-9\u4e00-\u9fff]",
                              t.lower())
            seqs.append([cls_id] + [vocab.get(x, unk_id) for x in toks][:MAX_LEN - 2] + [sep_id])
        maxlen = max(len(s) for s in seqs)
        ids = np.full((len(seqs), maxlen), pad_id, dtype=np.int64)
        attn = np.zeros((len(seqs), maxlen), dtype=np.int64)
        tt = np.zeros((len(seqs), maxlen), dtype=np.int64)
        for i, s in enumerate(seqs):
            ids[i, :len(s)] = s
            attn[i, :len(s)] = 1
        hidden = sess.run(None, {"input_ids": ids, "attention_mask": attn,
                                 "token_type_ids": tt})[0]
        mask = attn[:, :, None].astype(np.float32)
        emb = (hidden * mask).sum(axis=1) / np.maximum(mask.sum(axis=1), 1e-9)
        emb = emb / np.maximum(np.linalg.norm(emb, axis=1, keepdims=True), 1e-9)
        return emb.astype(np.float32)

    return encode


def main():
    if not os.path.exists("/workspace/pipeline"):
        print("必须在 master 容器内运行")
        sys.exit(1)

    t_all = time.time()

    # ---- 1) 构造 pizhu chunk ----
    rows = load_pizhu()
    chunks = []
    for i, r in enumerate(rows):
        text = (r.get("text") or "").strip()
        if len(text) < 2:
            continue
        hui = r.get("hui") or 0
        cid = make_chunk_id(hui, i, text)
        chunks.append({
            "chunk_id": cid,
            "version": VERSION,
            "source": "pizhu",
            "hui": hui,
            "huimu": r.get("huimu") or "",
            "para_start": -1,
            "para_end": -1,
            "seg": i,
            "char_count": len(text),
            "strategy_rev": STRATEGY_REV,
            "partition": f"pizhu_hui_{min(80, max(1, hui)):03d}",
            "pizhu_versions": r.get("versions_legend") or [],
            "pizhu_page": r.get("page"),
            "text": text,
        })
    log(f"构造脂批 chunk: {len(chunks):,} 条")

    # ---- 2) 写本地 + HDFS ----
    os.makedirs(OUT, exist_ok=True)
    local = os.path.join(OUT, "pizhu_chunks.jsonl")
    with open(local, "w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    log(f"本地: {local}  {os.path.getsize(local) / 1048576:.2f} MB")

    import subprocess
    subprocess.run(["hdfs", "dfs", "-mkdir", "-p", HDFS_PIZHU], capture_output=True)
    r = subprocess.run(["hdfs", "dfs", "-put", "-f", local,
                        f"{HDFS_PIZHU}/pizhu_chunks.jsonl"],
                       capture_output=True, text=True)
    log(f"HDFS: {r.returncode == 0 and 'OK' or r.stderr[:200]}")
    log(subprocess.run(["hdfs", "dfs", "-ls", "-h", HDFS_PIZHU],
                       capture_output=True, text=True).stdout.strip())

    # ---- 3) 向量化 ----
    log("向量化 ...")
    encode = get_encoder()
    vecs = []
    t0 = time.time()
    for s in range(0, len(chunks), BATCH):
        batch = chunks[s:s + BATCH]
        embs = encode([c["text"] for c in batch])
        for c, v in zip(batch, embs):
            vecs.append((c["chunk_id"], v.tobytes()))
    log(f"向量化完成: {len(vecs):,} 条  耗时 {time.time() - t0:.1f}s")

    # HDFS 快照
    vpath = os.path.join(OUT, "pizhu_vectors.jsonl")
    with open(vpath, "w", encoding="utf-8") as f:
        for cid, blob in vecs:
            f.write(json.dumps({
                "chunk_id": cid, "version": VERSION, "source": "pizhu",
                "model_id": MODEL_ID, "dim": EMB_DIM,
                "vec_b64": base64.b64encode(blob).decode("ascii"),
            }, ensure_ascii=False) + "\n")
    subprocess.run(["hdfs", "dfs", "-mkdir", "-p",
                    f"{HDFS_BASE}/derived/vectors/{VERSION}/model={MODEL_ID}"],
                   capture_output=True)
    subprocess.run(["hdfs", "dfs", "-put", "-f", vpath,
                    f"{HDFS_BASE}/derived/vectors/{VERSION}/model={MODEL_ID}/pizhu_vectors.jsonl"],
                   capture_output=True)
    log(f"HDFS 向量快照: {os.path.getsize(vpath) / 1048576:.2f} MB")

    # ---- 4) 写 HBase ----
    log("写 HBase ...")
    c = conn()
    tb = c.table(T_CHUNKS)
    t0 = time.time()
    with tb.batch(batch_size=200):
        for ch in chunks:
            # rowkey 加 source 段：fuqinju|pizhu|<hui>|<cid>
            rk = f"{VERSION}|pizhu|{ch['hui']:03d}|{ch['chunk_id']}".encode()
            tb.put(rk, {
                b"d:text": ch["text"].encode("utf-8"),
                b"d:char_count": str(ch["char_count"]).encode(),
                b"d:source": b"pizhu",
                b"m:hui": str(ch["hui"]).encode(),
                b"m:huimu": ch["huimu"].encode("utf-8"),
                b"m:seg": str(ch["seg"]).encode(),
                b"m:version": VERSION.encode(),
                b"m:strategy_rev": STRATEGY_REV.encode(),
                b"m:partition": ch["partition"].encode(),
                b"m:pizhu_versions": json.dumps(ch["pizhu_versions"],
                                                ensure_ascii=False).encode("utf-8"),
                b"m:pizhu_page": str(ch["pizhu_page"] or "").encode(),
            })
    log(f"  {T_CHUNKS} 写入 {len(chunks):,} 行 pizhu  耗时 {time.time() - t0:.1f}s")

    tbv = c.table(T_VECTORS)
    t0 = time.time()
    with tbv.batch(batch_size=200):
        for (cid, blob), ch in zip(vecs, chunks):
            rk = f"{VERSION}|pizhu|{ch['hui']:03d}|{cid}".encode()
            tbv.put(rk, {
                b"v:emb": blob,
                b"m:model_id": MODEL_ID.encode(),
                b"m:dim": str(EMB_DIM).encode(),
                b"m:hui": str(ch["hui"]).encode(),
                b"m:source": b"pizhu",
            })
    log(f"  {T_VECTORS} 写入 {len(vecs):,} 行 pizhu  耗时 {time.time() - t0:.1f}s")

    # 倒排
    tbt = c.table(T_TERMS)
    postings = collections.defaultdict(list)
    for ch in chunks:
        for bg, tf in bigrams(ch["text"]).items():
            postings[bg].append([ch["chunk_id"], tf])
    t0 = time.time()
    n = 0
    with tbt.batch(batch_size=100):
        for term, plist in postings.items():
            # 词项 key 加 pizhu 段，避免覆盖正文的 posting
            tbt.put(f"{VERSION}|pizhu|{term}".encode(), {
                b"p:postings": json.dumps(plist, ensure_ascii=False).encode("utf-8"),
                b"p:df": str(len(plist)).encode(),
                b"p:source": b"pizhu",
            })
            n += 1
    log(f"  {T_TERMS} 写入 {n:,} 个 pizhu 词项  耗时 {time.time() - t0:.1f}s")

    # ---- 5) 校验 ----
    log("-- 校验 --")
    cnt_chunks = sum(1 for _ in tb.scan(batch_size=500))
    cnt_vec = sum(1 for _ in tbv.scan(batch_size=500))
    log(f"  {T_CHUNKS} 总行数: {cnt_chunks:,}（正文 1,035 + 脂批 {len(chunks):,}）")
    log(f"  {T_VECTORS} 总行数: {cnt_vec:,}")

    # 抽样
    for k, d in tb.scan(row_prefix=f"{VERSION}|pizhu|".encode(), limit=1):
        log(f"  抽样 rowkey = {k.decode()}")
        log(f"    text = {d[b'd:text'].decode()[:60]}")
        log(f"    versions = {d.get(b'm:pizhu_versions', b'').decode()}")

    c.close()
    log(f"全部完成，总耗时 {time.time() - t_all:.1f}s")


if __name__ == "__main__":
    main()
