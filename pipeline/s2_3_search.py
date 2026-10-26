# -*- coding: utf-8 -*-
"""
S2 数据管道 · 阶段 3：检索验证（三路混合检索）
===============================================

目的：证明 S2 写入 HDFS/HBase 的数据**真的能检索**，而不是只写进去了。

三路检索（对应需求 R-104）
-------------------------
  L1 关键词（HBase hlm_terms，bigram 倒排）
  L2 向量  （HBase hlm_vectors，numpy 余弦暴力检索）
  L3 图谱  （HBase hlm_kg，留给 S2-4；本轮先做 L1/L2 融合）

融合：RRF（Reciprocal Rank Fusion），k=60
     score = Σ 1/(k + rank_i)
     RRF 不需要各路分数量纲一致，是工业界最常用的无参数融合法。

运行：docker exec master bash -lc "cd /workspace/pipeline && python3 s2_3_search.py --q '贾宝玉的丫鬟有谁'"
"""
import os
import sys
import json
import time
import argparse
import base64
import subprocess
import collections

sys.stdout.reconfigure(encoding="utf-8")

MODEL_DIR = "/data/opt/models/bge-small-zh-v1.5"
EMB_DIM = 512
MAX_LEN = 512
RRF_K = 60
VERSION = "fuqinju"

T_CHUNKS = "hlm_chunks"
T_VECTORS = "hlm_vectors"
T_TERMS = "hlm_terms"


def log(msg):
    print(msg, flush=True)


# ---------------- 中文 bigram（与写入端一致） ----------------
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


# ---------------- 别名表（与前端一致） ----------------
ALIAS = {
    "贾宝玉": ["贾宝玉", "宝玉", "宝二爷", "怡红公子", "绛洞花王", "富贵闲人", "混世魔王"],
    "林黛玉": ["林黛玉", "黛玉", "颦儿", "颦卿", "林妹妹", "潇湘妃子"],
    "薛宝钗": ["薛宝钗", "宝钗", "宝姐姐", "蘅芜君"],
    "王熙凤": ["王熙凤", "凤姐儿", "凤姐", "凤辣子", "琏二奶奶", "凤丫头"],
    "史湘云": ["史湘云", "湘云", "云妹妹", "枕霞旧友"],
    "贾母": ["贾母", "老太太", "史太君", "老祖宗"],
    "贾探春": ["贾探春", "探春", "三姑娘", "蕉下客"],
    "袭人": ["袭人", "花袭人", "珍珠"],
    "晴雯": ["晴雯"],
    "平儿": ["平儿", "平姑娘"],
    "香菱": ["香菱", "甄英莲", "英莲", "秋菱"],
}


def hbase_conn():
    """
    连 Thrift。必须 table_prefix=b"" + table_prefix_separator=b""。

    实测坑：happybase 1.3.0 里 table_prefix 拼接是
        prefix + separator + name
    默认 prefix=None→b"_"、separator=b"_"，会拼成 `_hlm_chunks`。
    两个都设成 b"" 才等价于「不加前缀」。
    """
    import happybase
    return happybase.Connection("master", port=9090, timeout=600000,
                                table_prefix=b"", table_prefix_separator=b"")


# ---------------- 向量化（与写入端同模型、同 tokenizer） ----------------
_tokenizer_cache = {}


def get_encoder():
    if "f" in _tokenizer_cache:
        return _tokenizer_cache["f"]
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

    def tokenize(text):
        text = text.lower()
        toks = re.findall(r"[\u4e00-\u9fff]|[a-z]+|[0-9]+|[^\sa-z0-9\u4e00-\u9fff]", text)
        ids = []
        for t in toks:
            if t in vocab:
                ids.append(vocab[t])
                continue
            ids.append(unk_id)
        return ids

    def encode(texts):
        seqs = [[cls_id] + tokenize(t)[:MAX_LEN - 2] + [sep_id] for t in texts]
        maxlen = max(len(s) for s in seqs)
        input_ids = np.full((len(seqs), maxlen), pad_id, dtype=np.int64)
        attn = np.zeros((len(seqs), maxlen), dtype=np.int64)
        tt = np.zeros((len(seqs), maxlen), dtype=np.int64)
        for i, s in enumerate(seqs):
            input_ids[i, :len(s)] = s
            attn[i, :len(s)] = 1
        hidden = sess.run(None, {
            "input_ids": input_ids, "attention_mask": attn, "token_type_ids": tt,
        })[0]
        mask = attn[:, :, None].astype(np.float32)
        emb = (hidden * mask).sum(axis=1) / np.maximum(mask.sum(axis=1), 1e-9)
        emb = emb / np.maximum(np.linalg.norm(emb, axis=1, keepdims=True), 1e-9)
        return emb.astype(np.float32)

    _tokenizer_cache["f"] = encode
    return encode


# ---------------- L1：关键词（bigram 倒排） ----------------
def search_keyword(conn, query, topk=20):
    """
    对 query 抽 bigram，查倒排表，用「命中的 bigram 数 + tf 加权」打分。
    这等价于一个简化的 BM25（少了 IDF，但已足够做候选召回）。
    """
    qgrams = bigrams(query)
    if not qgrams:
        return []
    tbt = conn.table(T_TERMS)
    scores = collections.Counter()
    hit_terms = 0
    for bg in qgrams:
        row = tbt.row(f"{VERSION}|{bg}".encode())
        if not row:
            continue
        hit_terms += 1
        postings = json.loads(row[b"p:postings"].decode("utf-8"))
        for cid, tf in postings:
            # 命中一个 bigram 记 1 分，tf 最多再加 1（避免长文本刷分）
            scores[cid] += 1.0 + min(tf, 2) * 0.1
    # 归一化到「命中率」，便于人工判断
    if qgrams:
        for cid in scores:
            scores[cid] = scores[cid] / len(qgrams)
    ranked = scores.most_common(topk)
    return [{"chunk_id": c, "score": round(s, 4), "src": "kw"} for c, s in ranked], \
           len(qgrams), hit_terms


# ---------------- L2：向量（numpy 余弦暴力检索） ----------------
def search_vector(conn, query, topk=20):
    """实测：2 万条 × 512 维暴力检索 < 1 ms；本项目 1,035 条更快。"""
    import numpy as np
    encode = get_encoder()
    qv = encode([query])[0]                       # 已 L2 归一化

    tbv = conn.table(T_VECTORS)
    ids, vecs = [], []
    for k, d in tbv.scan(batch_size=500):
        blob = d[b"v:emb"]
        if len(blob) != EMB_DIM * 4:
            continue
        ids.append(k.decode().split("|", 2)[2])
        vecs.append(np.frombuffer(blob, dtype=np.float32))
    if not ids:
        return [], 0
    M = np.vstack(vecs)                            # (N, 512)
    # 向量写库前已归一化；为稳妥再归一化一次
    M = M / np.maximum(np.linalg.norm(M, axis=1, keepdims=True), 1e-9)
    sims = M @ qv
    idx = np.argpartition(-sims, min(topk, len(sims) - 1))[:topk]
    idx = idx[np.argsort(-sims[idx])]
    return [{"chunk_id": ids[i], "score": round(float(sims[i]), 4), "src": "vec"}
            for i in idx], len(ids)


# ---------------- RRF 融合 ----------------
def rrf_fuse(kw_res, vec_res, k=RRF_K):
    score = collections.defaultdict(float)
    detail = collections.defaultdict(dict)
    for rank, r in enumerate(kw_res, 1):
        score[r["chunk_id"]] += 1.0 / (k + rank)
        detail[r["chunk_id"]]["kw"] = r["score"]
        detail[r["chunk_id"]]["kw_rank"] = rank
    for rank, r in enumerate(vec_res, 1):
        score[r["chunk_id"]] += 1.0 / (k + rank)
        detail[r["chunk_id"]]["vec"] = r["score"]
        detail[r["chunk_id"]]["vec_rank"] = rank
    fused = sorted(score.items(), key=lambda x: -x[1])
    return [{"chunk_id": cid, "rrf": round(s, 6), **detail[cid]} for cid, s in fused]


# ---------------- 主流程 ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--q", required=True, help="查询语句")
    ap.add_argument("--topk", type=int, default=5)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    conn = hbase_conn()
    q = args.q

    # 别名展开：实测「贾宝玉」全名在正文里只出现 2 次，必须展开
    expanded = [q]
    for canon, names in ALIAS.items():
        if canon in q:
            for n in names:
                if n not in q:
                    expanded.append(q.replace(canon, n))
    log(f"查询      : {q}")
    if len(expanded) > 1:
        log(f"别名展开  : {len(expanded)} 种写法（{expanded[1]} ...）")

    t0 = time.time()
    # L1：对每个展开写法都查，取并集（取最高分）
    kw_all = {}
    total_grams = 0
    total_hit = 0
    for qq in expanded:
        res, ng, nh = search_keyword(conn, qq, topk=30)
        total_grams += ng
        total_hit += nh
        for r in res:
            cid = r["chunk_id"]
            if cid not in kw_all or r["score"] > kw_all[cid]["score"]:
                kw_all[cid] = r
    kw_res = sorted(kw_all.values(), key=lambda x: -x["score"])[:30]
    t1 = time.time()

    vec_res, n_vec = search_vector(conn, q, topk=30)
    t2 = time.time()

    fused = rrf_fuse(kw_res, vec_res)
    top = fused[:args.topk]
    t3 = time.time()

    # ---- 回表取正文 ----
    # 坑：happybase 的 scan(filter=) 是 Thrift filterString（服务端过滤器语法），
    # 不是 Python 函数。传 lambda 会报 TDecodeException。
    # 教学规模下直接全表扫一遍建内存索引（1,035 行，毫秒级）。
    tbc = conn.table(T_CHUNKS)
    id2row = {}
    for k, d in tbc.scan(batch_size=500):
        cid = k.decode().split("|", 2)[2]
        id2row[cid] = (k, d)
    log("")
    log(f"L1 关键词: {len(kw_res)} 命中 / {total_hit} 个 bigram 命中 (共 {total_grams})  "
        f"耗时 {(t1 - t0) * 1000:.1f} ms")
    log(f"L2 向量  : {n_vec} 条候选  top-1 sim={vec_res[0]['score'] if vec_res else 0}  "
        f"耗时 {(t2 - t1) * 1000:.1f} ms")
    log(f"RRF 融合 : {len(fused)} 条  耗时 {(t3 - t2) * 1000:.1f} ms")
    log(f"总耗时   : {(t3 - t0) * 1000:.1f} ms")
    log("")
    log("=" * 78)
    log(f"Top-{args.topk} 融合结果")
    log("=" * 78)

    for i, r in enumerate(top, 1):
        cid = r["chunk_id"]
        # 用 rowkey 前缀扫描找该 chunk（chunk_id 不含 hui，需要查元数据）
        # 优化：直接从 chunks 表扫（教学规模可接受）；S4 会加 chunk_id→rowkey 映射表
        row = id2row.get(cid)
        log("")
        log(f"[{i}] RRF={r['rrf']:.6f}  kw={r.get('kw', '-')} (rank {r.get('kw_rank', '-')})  "
            f"vec={r.get('vec', '-')} (rank {r.get('vec_rank', '-')})")
        if row:
            k, d = row
            rk = k.decode()
            is_pz = "|pizhu|" in rk
            log(f"    rowkey : {rk}")
            if is_pz:
                # 脂批 chunk 没有段落号（para_start = -1），改用版本标注
                try:
                    vers = json.loads(d.get(b"m:pizhu_versions", b"[]").decode() or "[]")
                except Exception:
                    vers = []
                log(f"    类型   : 脂批 · 版本 {('/'.join(vers) or '未标注')} · "
                    f"第 {d[b'm:hui'].decode()} 回")
            else:
                log(f"    位置   : 第 {d[b'm:hui'].decode()} 回  "
                    f"段 {d[b'm:para_start'].decode()}-{d[b'm:para_end'].decode()}  "
                    f"({d[b'm:partition'].decode()})")
            log(f"    回目   : {d[b'm:huimu'].decode()}")
            txt = d[b"d:text"].decode("utf-8").replace("\n", "")
            log(f"    正文   : {txt[:150]}...")
        else:
            log(f"    chunk_id={cid}（未能回表，可能扫描过滤未命中）")

    log("")
    log("=" * 78)
    conn.close()


if __name__ == "__main__":
    main()
