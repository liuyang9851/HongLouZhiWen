# -*- coding: utf-8 -*-
"""
S29-3 · 重建脂批的 RAG 索引（彻底修：让检索也用完整脂批）
========================================================

为什么必须重建
--------------
S28/S29 只修了**阅读界面**的展示数据。RAG 索引里仍是旧截断文本：

    hlm_chunks   fuqinju|pizhu  3,219 行   ← 旧（截断率 35.9%）
    hlm_vectors  fuqinju|pizhu  3,219 行
    hlm_terms    fuqinju|pizhu 35,497 行

所以 AI 回答里引用脂批时，引到的仍是断句。

影响范围（实测 rowkey 分布）
-----------------------------
  zhipi|pizhu      5,069   ← 脂批大全，另一个来源，不动
  fuqinju|pizhu    3,219   ← **本次重建**
  guiyouben|pizhu    222   ← 癸酉本，不动
  chenggao|text    1,689   ← 正文，不动
  fuqinju|text     1,051
  guiyouben|text     530

关键设计：脂批倒排是**独立行** `<ver>|pizhu|<bigram>`，
与正文 `<ver>|text|<bigram>` 分开存 → 重建不会碰正文索引。

做法
----
  1. 备份旧数据到本地（可回滚）
  2. 删除 fuqinju|pizhu| 前缀的全部行（3 张表）
  3. 用 pizhu_v5.jsonl（4,563 条，截断 0.1%）重新构造 chunk
     chunk_id = sha1("pizhu|回次|序号|文本哈希8|策略版本")[:16]
  4. ONNX 向量化（bge-small-zh-v1.5，512 维）
  5. 写回 hlm_chunks / hlm_vectors / hlm_terms
  6. 校验：行数、抽样文本、倒排 df

运行（hlm-api 容器内）：
  python3 s29_3_rebuild_pizhu_index.py [--dry-run]
"""
import os
import re
import sys
import json
import time
import base64
import hashlib
import collections

sys.stdout.reconfigure(encoding="utf-8")

MODEL_DIR = "/app/models/bge-small-zh-v1.5"
MODEL_ID = "bge-small-zh-v1.5"
EMB_DIM = 512
MAX_LEN = 512
BATCH = 32

VERSION = "fuqinju"
STRATEGY_REV = "v2-pizhu"          # 升版：文本已变，旧 id 不再可用
SRC = "/tmp/pizhu_v5.jsonl"
HUIMU_SRC = "/tmp/pizhu_old.jsonl"   # 仅用来取 huimu（回目名）
BACKUP_DIR = "/tmp/pizhu_index_backup"

T_CHUNKS = "hlm_chunks"
T_VECTORS = "hlm_vectors"
T_TERMS = "hlm_terms"
PREFIX = f"{VERSION}|pizhu|"

STOP = set("的了在是我有和就不人都一这中大为上个国到说们")


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def bigrams(text):
    chars = [c for c in text if '\u4e00' <= c <= '\u9fff']
    out = collections.Counter()
    for i in range(len(chars) - 1):
        a, b = chars[i], chars[i + 1]
        if a in STOP and b in STOP:
            continue
        out[a + b] += 1
    return out


def make_chunk_id(hui, idx, text):
    h = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
    key = f"pizhu|{hui:03d}|{idx:04d}|{h}|{STRATEGY_REV}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def conn():
    import happybase
    return happybase.Connection("master", port=9090, timeout=600000,
                                table_prefix=b"", table_prefix_separator=b"")


def get_encoder():
    import onnxruntime as ort
    import numpy as np

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
    dry = "--dry-run" in sys.argv
    t_all = time.time()

    if not os.path.exists(SRC):
        raise SystemExit(f"缺少 {SRC}，请先把 pizhu_v5.jsonl 拷进容器")

    # ---------- 1) 读新数据 ----------
    rows = [json.loads(l) for l in open(SRC, encoding="utf-8")]
    log(f"读入 pizhu_v5: {len(rows):,} 条")

    # 回目名（旧的 pizhu_redecoded 有 huimu，新数据没有）
    huimu_map = {}
    if os.path.exists(HUIMU_SRC):
        for l in open(HUIMU_SRC, encoding="utf-8"):
            r = json.loads(l)
            if r.get("hui") and r.get("huimu") and r["hui"] not in huimu_map:
                huimu_map[r["hui"]] = r["huimu"]
        log(f"回目名映射: {len(huimu_map)} 个回次")
    else:
        log("⚠ 无 huimu 源，回目名留空")

    # ---------- 2) 构造 chunk ----------
    chunks = []
    for i, r in enumerate(rows):
        text = (r.get("text") or "").strip()
        if len(text) < 2:
            continue
        hui = r.get("hui") or 0
        chunks.append({
            "chunk_id": make_chunk_id(hui, i, text),
            "hui": hui,
            "huimu": huimu_map.get(hui, ""),
            "seg": i,
            "text": text,
            "char_count": len(text),
            "pizhu_versions": r.get("versions_legend") or [],
            "pizhu_page": r.get("page"),
            "kind": r.get("kind", "inline"),
        })
    log(f"构造 chunk: {len(chunks):,} 条，总字 {sum(c['char_count'] for c in chunks):,}")

    c = conn()

    # ---------- 3) 备份 + 统计旧数据 ----------
    tb, tbv, tbt = c.table(T_CHUNKS), c.table(T_VECTORS), c.table(T_TERMS)
    old_keys = {"chunks": [], "vectors": [], "terms": []}
    log("扫描旧数据 ...")
    for k, d in tb.scan(row_prefix=PREFIX.encode()):
        old_keys["chunks"].append(k)
    for k, d in tbv.scan(row_prefix=PREFIX.encode()):
        old_keys["vectors"].append(k)
    for k, d in tbt.scan(row_prefix=PREFIX.encode()):
        old_keys["terms"].append(k)
    log(f"  旧: chunks={len(old_keys['chunks']):,} vectors={len(old_keys['vectors']):,} terms={len(old_keys['terms']):,}")

    if not dry:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        # 备份抽样 200 条（全量备份太大，留抽样即可验证回滚路径）
        with open(os.path.join(BACKUP_DIR, "sample_chunks.jsonl"), "w", encoding="utf-8") as f:
            for k, d in tb.scan(row_prefix=PREFIX.encode(), limit=200):
                f.write(json.dumps({"rowkey": k.decode(),
                                    "text": d.get(b"d:text", b"").decode("utf-8", "replace")},
                                   ensure_ascii=False) + "\n")
        log(f"  备份抽样 → {BACKUP_DIR}/sample_chunks.jsonl")

    if dry:
        log("--dry-run：到此为止")
        c.close()
        return

    # ---------- 4) 删除旧行 ----------
    log("删除旧行 ...")
    t0 = time.time()
    for name, table, keys in (("chunks", tb, old_keys["chunks"]),
                              ("vectors", tbv, old_keys["vectors"]),
                              ("terms", tbt, old_keys["terms"])):
        with table.batch(batch_size=500) as b:
            for k in keys:
                b.delete(k)
        log(f"  {name}: 删除 {len(keys):,} 行")
    log(f"删除完成 耗时 {time.time()-t0:.1f}s")

    # ---------- 5) 向量化 ----------
    log("向量化 ...")
    encode = get_encoder()
    vecs = []
    t0 = time.time()
    for s in range(0, len(chunks), BATCH):
        batch = chunks[s:s + BATCH]
        embs = encode([x["text"] for x in batch])
        for ch, v in zip(batch, embs):
            vecs.append((ch["chunk_id"], v.tobytes()))
        if (s // BATCH) % 20 == 0:
            log(f"  {s + len(batch):,}/{len(chunks):,}")
    log(f"向量化完成: {len(vecs):,} 条  耗时 {time.time()-t0:.1f}s")

    # ---------- 6) 写 chunks ----------
    log("写 hlm_chunks ...")
    t0 = time.time()
    with tb.batch(batch_size=200) as b:
        for ch in chunks:
            rk = f"{VERSION}|pizhu|{ch['hui']:03d}|{ch['chunk_id']}".encode()
            b.put(rk, {
                b"d:text": ch["text"].encode("utf-8"),
                b"d:char_count": str(ch["char_count"]).encode(),
                b"d:source": b"pizhu",
                b"m:hui": str(ch["hui"]).encode(),
                b"m:huimu": ch["huimu"].encode("utf-8"),
                b"m:seg": str(ch["seg"]).encode(),
                b"m:version": VERSION.encode(),
                b"m:strategy_rev": STRATEGY_REV.encode(),
                b"m:partition": f"pizhu_hui_{min(80, max(1, ch['hui'])):03d}".encode(),
                b"m:pizhu_versions": json.dumps(ch["pizhu_versions"],
                                                ensure_ascii=False).encode("utf-8"),
                b"m:pizhu_page": str(ch["pizhu_page"] or "").encode(),
                b"m:pizhu_kind": ch["kind"].encode(),
            })
    log(f"  hlm_chunks 写入 {len(chunks):,} 行  耗时 {time.time()-t0:.1f}s")

    # ---------- 7) 写 vectors ----------
    log("写 hlm_vectors ...")
    t0 = time.time()
    with tbv.batch(batch_size=200) as b:
        for (cid, blob), ch in zip(vecs, chunks):
            rk = f"{VERSION}|pizhu|{ch['hui']:03d}|{cid}".encode()
            b.put(rk, {
                b"v:emb": blob,
                b"m:model_id": MODEL_ID.encode(),
                b"m:dim": str(EMB_DIM).encode(),
                b"m:hui": str(ch["hui"]).encode(),
                b"m:source": b"pizhu",
            })
    log(f"  hlm_vectors 写入 {len(vecs):,} 行  耗时 {time.time()-t0:.1f}s")

    # ---------- 8) 写倒排 ----------
    log("写 hlm_terms（倒排）...")
    postings = collections.defaultdict(list)
    for ch in chunks:
        for bg, tf in bigrams(ch["text"]).items():
            postings[bg].append([ch["chunk_id"], tf])
    t0 = time.time()
    with tbt.batch(batch_size=100) as b:
        for term, plist in postings.items():
            b.put(f"{VERSION}|pizhu|{term}".encode(), {
                b"p:postings": json.dumps(plist, ensure_ascii=False).encode("utf-8"),
                b"p:df": str(len(plist)).encode(),
                b"p:source": b"pizhu",
            })
    log(f"  hlm_terms 写入 {len(postings):,} 个词项  耗时 {time.time()-t0:.1f}s")

    # ---------- 9) 校验 ----------
    log("-- 校验 --")
    n_ch = sum(1 for _ in tb.scan(row_prefix=PREFIX.encode()))
    n_vec = sum(1 for _ in tbv.scan(row_prefix=PREFIX.encode()))
    n_tr = sum(1 for _ in tbt.scan(row_prefix=PREFIX.encode()))
    log(f"  {T_CHUNKS}  {VERSION}|pizhu  行数: {n_ch:,}  (期望 {len(chunks):,})")
    log(f"  {T_VECTORS} {VERSION}|pizhu  行数: {n_vec:,}  (期望 {len(vecs):,})")
    log(f"  {T_TERMS}   {VERSION}|pizhu  词项: {n_tr:,}  (期望 {len(postings):,})")

    total_ch = sum(1 for _ in tb.scan(batch_size=1000))
    total_vec = sum(1 for _ in tbv.scan(batch_size=1000))
    log(f"  {T_CHUNKS} 全表: {total_ch:,}   {T_VECTORS} 全表: {total_vec:,}")

    log("  抽样 3 条：")
    for k, d in tb.scan(row_prefix=PREFIX.encode(), limit=3):
        t = d.get(b"d:text", b"").decode("utf-8", "replace")
        log(f"    {k.decode()}")
        log(f"      {t[:70]}{'…' if len(t) > 70 else ''}")
        log(f"      versions={d.get(b'm:pizhu_versions', b'').decode()}")

    log("  倒排抽样（宝玉）：")
    r = tbt.row(f"{VERSION}|pizhu|宝玉".encode())
    if r:
        pl = json.loads(r[b"p:postings"].decode())
        log(f"    df={r.get(b'p:df', b'').decode()}  postings 前3={pl[:3]}")

    c.close()
    log(f"全部完成，总耗时 {time.time()-t_all:.1f}s")


if __name__ == "__main__":
    main()
