# -*- coding: utf-8 -*-
"""
S7-2 · 三版本全部进 RAG 索引
=============================

解决的问题
----------
  问题 2：原文阅读只能选抚琴居本
  问题 7：三版本都要进 RAG 检索

做法
----
  对 chapters_{ver}.jsonl（3 个版本）都做切分 + 向量化，写进同一批 HBase 表。
  rowkey 里已有 version 前缀 → 天然隔离，不会串版本。

设计要点
--------
  1. **版本是 rowkey 第一段**：`<ver>|<source>|<hui:03d>|<cid>`
     → 加新版本不需要改表结构（这是 S2 时设计的红利）
  2. **chunk_id 里带 version**：不同版本的同位置 chunk ID 天然不同
  3. **分区仍按回次**：与版本正交（R-512「分区不破坏版本」）
  4. 程高本 120 回、癸酉本 28 回，回次范围不同 → 补零宽度统一 3 位仍够用

运行（master 容器内）：
    docker exec master bash -lc "cd /workspace/pipeline && python3 s7_2_index_versions.py"
"""
import os
import sys
import json
import time
import base64
import hashlib
import collections
import subprocess

sys.stdout.reconfigure(encoding="utf-8")

PIPE = "/workspace/pipeline"
OUT = os.path.join(PIPE, "out")
MODEL_DIR = "/data/opt/models/bge-small-zh-v1.5"
MODEL_ID = "bge-small-zh-v1.5"
EMB_DIM = 512
MAX_LEN = 512
BATCH = 32

STRATEGY_REV = "v2-3ver"     # 三版本统一策略版本
TARGET_MIN, TARGET_MAX = 300, 800
WIN_SIZE, WIN_OVERLAP = 500, 100
N_PARTITIONS = 12            # 120 回 / 每 10 回一区

HDFS_BASE = "/hlm"
T_CHUNKS = "hlm_chunks"
T_VECTORS = "hlm_vectors"
T_TERMS = "hlm_terms"

# 只处理 fuqinju 之外的版本（fuqinju 已有 v1 索引，这里统一重建为 v2-3ver）
VERSIONS = ["fuqinju", "chenggao", "guiyouben"]
# 值 = chapters_{ver}.jsonl 的文件后缀
VFILE = {"fuqinju": "fuqinju", "chenggao": "chenggao", "guiyouben": "guiyouben"}


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


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


def sha1(s):
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def make_chunk_id(version, hui, p_start, p_end, seg):
    return sha1(f"{version}|{hui:03d}|{p_start:04d}|{p_end:04d}|{seg:02d}|{STRATEGY_REV}")[:16]


def split_long_text(text):
    if len(text) <= TARGET_MAX:
        return [(text, 0)]
    out = []
    step = WIN_SIZE - WIN_OVERLAP
    i = 0
    while i < len(text):
        piece = text[i:i + WIN_SIZE]
        if len(piece) < TARGET_MIN // 2 and out:
            out[-1] = (out[-1][0] + piece, out[-1][1])
            break
        out.append((piece, i))
        if i + WIN_SIZE >= len(text):
            break
        i += step
    return out


def merge_paragraphs(paras, start_idx):
    buf, total, i = [], 0, start_idx
    while i < len(paras):
        p = paras[i]
        if total + len(p) > TARGET_MAX and total >= TARGET_MIN:
            break
        buf.append(p)
        total += len(p)
        i += 1
        if total >= TARGET_MIN and i < len(paras) and total + len(paras[i]) > TARGET_MAX:
            break
    end = max(start_idx, i - 1)
    text = "\n".join(buf)
    return text, end, len(text) > TARGET_MAX


def chunk_version(ver, chapters):
    """对一个版本做三级切分，返回 chunk 列表"""
    chunks = []
    for ch in chapters:
        hui = ch["hui"]
        huimu = ch["huimu"]
        paras = ch["paragraphs"]
        i = 0
        seg = 0
        while i < len(paras):
            text, end, need_l3 = merge_paragraphs(paras, i)
            if need_l3:
                for sub, off in split_long_text(text):
                    cid = make_chunk_id(ver, hui, i, end, seg)
                    if off:
                        cid = sha1(f"{cid}|{off}")[:16]
                    chunks.append(dict(
                        chunk_id=cid, version=ver, source="text", hui=hui,
                        huimu=huimu, para_start=i, para_end=end, seg=seg,
                        char_count=len(sub), strategy_rev=STRATEGY_REV, text=sub))
                    seg += 1
            else:
                chunks.append(dict(
                    chunk_id=make_chunk_id(ver, hui, i, end, seg), version=ver,
                    source="text", hui=hui, huimu=huimu, para_start=i,
                    para_end=end, seg=seg, char_count=len(text),
                    strategy_rev=STRATEGY_REV, text=text))
                seg += 1
            i = end + 1

    # 尾块合并
    merged = []
    for c in chunks:
        if (merged and c["char_count"] < TARGET_MIN // 2
                and merged[-1]["hui"] == c["hui"]
                and merged[-1]["char_count"] + c["char_count"] <= TARGET_MAX * 1.2):
            p = merged[-1]
            p["text"] = p["text"] + "\n" + c["text"]
            p["para_end"] = c["para_end"]
            p["char_count"] = len(p["text"])
            p["chunk_id"] = make_chunk_id(ver, p["hui"], p["para_start"],
                                          p["para_end"], p["seg"])
        else:
            merged.append(c)

    # 分区（按回次每 10 回一区）
    for c in merged:
        part = min(N_PARTITIONS - 1, (c["hui"] - 1) // 10)
        c["partition"] = f"hui_{part * 10 + 1:03d}_{(part + 1) * 10:03d}"
    return merged


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
            toks = re.findall(r"[\u4e00-\u9fff]|[a-z]+|[0-9]+|[^\sa-z0-9\u4e00-\u9fff]", t.lower())
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


def hbase_conn():
    import happybase
    return happybase.Connection("master", port=9090, timeout=600000,
                                table_prefix=b"", table_prefix_separator=b"")


def main():
    t_all = time.time()
    if not os.path.exists(PIPE):
        print("必须在 master 容器内运行")
        sys.exit(1)

    # ---- 1) 三个版本都切分 ----
    all_chunks = []
    stats = {}
    for ver in VERSIONS:
        p = os.path.join(OUT, f"chapters_{VFILE[ver]}.jsonl")
        if not os.path.exists(p):
            log(f"⚠ 缺 {p}，跳过 {ver}")
            continue
        chapters = [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]
        ch = chunk_version(ver, chapters)
        all_chunks.extend(ch)
        sizes = sorted(c["char_count"] for c in ch)
        n = len(sizes)
        stats[ver] = {
            "n_hui": len(chapters), "n_chunk": n,
            "mean": round(sum(sizes) / n, 1),
            "min": sizes[0], "max": sizes[-1],
            "chars": sum(sizes),
        }
        log(f"{ver:10s} 回数={len(chapters):3d} chunk={n:5d} "
            f"mean={stats[ver]['mean']:6.1f} min={sizes[0]:3d} max={sizes[-1]:4d} "
            f"chars={stats[ver]['chars']:,}")

    log(f"三版本合计 chunk: {len(all_chunks):,}")

    # ---- 2) 本地 + HDFS ----
    local = os.path.join(OUT, "chunks_3ver.jsonl")
    all_chunks.sort(key=lambda c: (c["version"], c["hui"], c["seg"]))
    with open(local, "w", encoding="utf-8") as f:
        for c in all_chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    log(f"本地: {local}  {os.path.getsize(local)/1048576:.2f} MB")

    subprocess.run(["hdfs", "dfs", "-mkdir", "-p", f"{HDFS_BASE}/warehouse/chunks3ver"],
                   capture_output=True)
    subprocess.run(["hdfs", "dfs", "-put", "-f", local,
                    f"{HDFS_BASE}/warehouse/chunks3ver/chunks_3ver.jsonl"],
                   capture_output=True)
    log(subprocess.run(["hdfs", "dfs", "-ls", "-h", f"{HDFS_BASE}/warehouse/chunks3ver"],
                       capture_output=True, text=True).stdout.strip())

    # ---- 3) 向量化 ----
    log("向量化 ...")
    encode = get_encoder()
    t0 = time.time()
    vecs = []
    for s in range(0, len(all_chunks), BATCH):
        batch = all_chunks[s:s + BATCH]
        embs = encode([c["text"] for c in batch])
        for c, v in zip(batch, embs):
            vecs.append((c, v.tobytes()))
        if (s // BATCH) % 10 == 0:
            el = time.time() - t0
            log(f"  {len(vecs)}/{len(all_chunks)}  {len(vecs)/max(el,1e-9):.1f} chunk/s")
    log(f"向量化完成: {len(vecs):,} 条  耗时 {time.time()-t0:.1f}s")

    # HDFS 快照
    vpath = os.path.join(OUT, "vectors_3ver.jsonl")
    with open(vpath, "w", encoding="utf-8") as f:
        for c, blob in vecs:
            f.write(json.dumps({
                "chunk_id": c["chunk_id"], "version": c["version"],
                "source": "text", "model_id": MODEL_ID, "dim": EMB_DIM,
                "vec_b64": base64.b64encode(blob).decode("ascii"),
            }, ensure_ascii=False) + "\n")
    subprocess.run(["hdfs", "dfs", "-mkdir", "-p",
                    f"{HDFS_BASE}/derived/vectors3ver/model={MODEL_ID}"], capture_output=True)
    subprocess.run(["hdfs", "dfs", "-put", "-f", vpath,
                    f"{HDFS_BASE}/derived/vectors3ver/model={MODEL_ID}/vectors_3ver.jsonl"],
                   capture_output=True)
    log(f"HDFS 向量快照: {os.path.getsize(vpath)/1048576:.2f} MB")

    # ---- 4) 写 HBase（先删旧正文 chunk，保留脂批） ----
    c = hbase_conn()
    tb = c.table(T_CHUNKS)
    tbv = c.table(T_VECTORS)
    tbt = c.table(T_TERMS)

    # 删除旧版正文 chunk（source=text），保留脂批
    #
    # **踩坑记录**：旧代码判断 `len(parts) == 3`，
    # 但 rowkey 早就是 4 段 `<ver>|<source>|<hui>|<cid>`，
    # 条件永远不成立 → 一行都没删 → 新旧数据叠加
    # （实测癸酉本变成 1~28 与 81~108 两套共存，1060 行 vs 应有的 530 行）。
    # 正确做法：只要不是 pizhu，就是旧的正文 chunk，一律删除后重建。
    log("清掉旧版正文 chunk（保留脂批）...")
    old_keys = []
    for k, d in tb.scan(batch_size=500):
        parts = k.decode().split("|")
        src = parts[1] if len(parts) >= 4 else "text"
        if src != "pizhu":
            old_keys.append(k)
    with tb.batch(batch_size=500) as b:
        for k in old_keys:
            b.delete(k)
    log(f"  删除旧正文 chunk {len(old_keys):,} 行（脂批保留）")

    # 删旧向量（非 pizhu 的）
    old_vkeys = []
    for k, d in tbv.scan(batch_size=500):
        parts = k.decode().split("|")
        src = parts[1] if len(parts) >= 4 else "text"
        if src != "pizhu":
            old_vkeys.append(k)
    with tbv.batch(batch_size=500) as b:
        for k in old_vkeys:
            b.delete(k)
    log(f"  删除旧正文向量 {len(old_vkeys):,} 行（脂批保留）")

    # 等删除生效（HBase delete 是异步的）
    time.sleep(3)

    # 写新 chunk
    log("写 HBase chunk ...")
    t0 = time.time()
    with tb.batch(batch_size=200):
        for ch in all_chunks:
            rk = f"{ch['version']}|text|{ch['hui']:03d}|{ch['chunk_id']}".encode()
            tb.put(rk, {
                b"d:text": ch["text"].encode("utf-8"),
                b"d:char_count": str(ch["char_count"]).encode(),
                b"d:source": b"text",
                b"m:hui": str(ch["hui"]).encode(),
                b"m:huimu": ch["huimu"].encode("utf-8"),
                b"m:para_start": str(ch["para_start"]).encode(),
                b"m:para_end": str(ch["para_end"]).encode(),
                b"m:seg": str(ch["seg"]).encode(),
                b"m:version": ch["version"].encode(),
                b"m:strategy_rev": STRATEGY_REV.encode(),
                b"m:partition": ch["partition"].encode(),
            })
    log(f"  {T_CHUNKS} 写入 {len(all_chunks):,} 行  耗时 {time.time()-t0:.1f}s")

    log("写 HBase vector ...")
    t0 = time.time()
    with tbv.batch(batch_size=200):
        for ch, blob in vecs:
            rk = f"{ch['version']}|text|{ch['hui']:03d}|{ch['chunk_id']}".encode()
            tbv.put(rk, {
                b"v:emb": blob, b"m:model_id": MODEL_ID.encode(),
                b"m:dim": str(EMB_DIM).encode(), b"m:hui": str(ch["hui"]).encode(),
                b"m:source": b"text",
            })
    log(f"  {T_VECTORS} 写入 {len(vecs):,} 行  耗时 {time.time()-t0:.1f}s")

    # 倒排：按版本分别建
    log("重建正文倒排（分版本）...")
    t0 = time.time()
    for ver in VERSIONS:
        vchunks = [c for c in all_chunks if c["version"] == ver]
        if not vchunks:
            continue
        postings = collections.defaultdict(list)
        for ch in vchunks:
            # S31：把 huimu（回目）也纳入倒排索引。
            # 原实现只用 text，导致回目专有词（如第33回「大遭笞挞」的「笞挞」）
            # 用户直接检索时 0 命中 —— 因为「笞挞」只在回目里出现，正文没有。
            # 注意：只影响倒排召回面，chunk 的 d:text 内容不变（原文阅读仍显示正文）。
            _idx = (ch["text"] or "") + " " + (ch.get("huimu") or "")
            for bg, tf in bigrams(_idx).items():
                postings[bg].append([ch["chunk_id"], tf])
        with tbt.batch(batch_size=100) as b:
            for term, plist in postings.items():
                b.put(f"{ver}|text|{term}".encode(), {
                    b"p:postings": json.dumps(plist, ensure_ascii=False).encode("utf-8"),
                    b"p:df": str(len(plist)).encode(),
                    b"p:source": b"text",
                })
        log(f"  {ver:10s} 词项 {len(postings):,}")

    # 清理旧格式倒排（ver|term，无 text 段）
    old_terms = []
    for k, d in tbt.scan(batch_size=500):
        parts = k.decode().split("|")
        if len(parts) == 2:      # 旧格式 fuqinju|<term>
            old_terms.append(k)
    if old_terms:
        with tbt.batch(batch_size=1000) as b:
            for k in old_terms:
                b.delete(k)
        log(f"  清理旧格式词项 {len(old_terms):,} 行")
    log(f"  倒排完成  耗时 {time.time()-t0:.1f}s")

    # ---- 5) 校验 ----
    log("-- 校验 --")
    cnt = sum(1 for _ in tb.scan(batch_size=500))
    cntv = sum(1 for _ in tbv.scan(batch_size=500))
    log(f"  {T_CHUNKS} 总行数: {cnt:,}")
    log(f"  {T_VECTORS} 总行数: {cntv:,}")
    vers = collections.Counter()
    for k, d in tb.scan(batch_size=500):
        vers[f"{d.get(b'm:version',b'').decode()}/{d.get(b'd:source',b'text').decode()}"] += 1
    log(f"  分布: {dict(vers)}")

    # 抽样看每版本
    for ver in VERSIONS:
        for k, d in tb.scan(row_prefix=f"{ver}|text|".encode(), limit=1):
            log(f"  抽样 {ver}: {k.decode()} → {d[b'd:text'].decode()[:40]}")

    c.close()

    with open(os.path.join(OUT, "versions3_stats.json"), "w", encoding="utf-8") as f:
        json.dump({"stats": stats, "total_chunks": len(all_chunks),
                   "strategy_rev": STRATEGY_REV}, f, ensure_ascii=False, indent=2)
    log(f"全部完成，总耗时 {time.time()-t_all:.1f}s")


if __name__ == "__main__":
    main()
