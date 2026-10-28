# -*- coding: utf-8 -*-
"""
S10 · 三源脂批统一索引（含跨源去重 + 按来源隔离）
====================================================

用户决策
--------
  Q1-C：癸酉本 223 条**全部收录**，并可按来源过滤
  Q2-B：脂批大全 9,156 条**收录 + 跨源去重**
  Q3  ：批语**按真实来源版本隔离**（不再固定塞进 fuqinju）

数据盘点（实测）
----------------
| 数据源 | 条数 | 唯一（去重后） | 来源文件 |
|:---|---:|---:|:---|
| 抚琴居校本·脂批 | 3,234 | 3,217 | clean/pizhu_redecoded.jsonl |
| 脂批大全 | 9,156 | 7,380 | zhiyanzhai_pizhu/pizhu.jsonl |
| 癸酉本·批语 | 223 | 223 | guiyouben/pizhu.jsonl |
| **合计** | **12,613** | **9,401** | |

重叠情况：
  抚琴居 ∩ 脂批大全 = 1,416（这两源同源度高，都是脂评本系统）
  抚琴居 ∩ 癸酉本   = 1
  脂批大全 ∩ 癸酉本 = 3

去重策略
--------
  归一化文本指纹 = 去空白、去标点后的文本
  **保留优先级**：抚琴居校本 > 癸酉本 > 脂批大全
  理由：抚琴居校本有 PDF 码点重解码的版本归属（最可信）；
        癸酉本是该书独有批语（不可由别处替代）；脂批大全作补充。

隔离设计
--------
  rowkey = `<ver>|pizhu|<hui:03d>|<chunk_id>`
    fuqinju|pizhu|021|xxx     抚琴居校本脂批
    guiyouben|pizhu|091|xxx   癸酉本批语
  （脂批大全并入哪个 ver？→ 见下方 SOURCE_VER 映射）

  倒排 key = `<ver>|pizhu|<bigram>`

运行（master 容器内）：
    docker exec master bash -lc "cd /workspace/pipeline && python3 s10_1_unify_pizhu.py"
"""
import os
import re
import sys
import json
import time
import base64
import hashlib
import collections
import subprocess

sys.stdout.reconfigure(encoding="utf-8")

CORPUS = "/workspace/corpus"          # 容器内语料挂载点（见下方 prepare 说明）
PIPE = "/workspace/pipeline"
OUT = os.path.join(PIPE, "out")
MODEL_DIR = "/data/opt/models/bge-small-zh-v1.5"
MODEL_ID = "bge-small-zh-v1.5"
EMB_DIM = 512
MAX_LEN = 512
BATCH = 32
STRATEGY_REV = "v3-pizhu3src"

HDFS_BASE = "/hlm"
T_CHUNKS = "hlm_chunks"
T_VECTORS = "hlm_vectors"
T_TERMS = "hlm_terms"

# ------------------------------------------------------------
# 来源 → 隔离版本
# ------------------------------------------------------------
# 抚琴居校本脂批：批语来自各脂本（甲戌/庚辰/蒙府…），但**收录于抚琴居校本**，
#   所以隔离版本用 fuqinju。
# 脂批大全：同样是脂评本系统批语，但来源是独立汇编（gitbook + 脂批大全），
#   **不属于任何正文版本的校本**，单独给一个隔离版本 "zhipi"（脂批汇集）。
# 癸酉本批语：属于癸酉本，隔离版本 guiyouben。
SRC_FUQINJU = "fuqinju"
SRC_ZHIPI = "zhipi"
SRC_GUIYOU = "guiyouben"

# ============================================================
# 重要：zhipi 是否参与「选某版本正文」时的检索？
#   选 fuqinju  → 正文 fuqinju + 脂批 fuqinju + 脂批 zhipi（都是脂评本系统，互补）
#   选 guiyouben→ 正文 guiyouben + 脂批 guiyouben
#   选 chenggao → 正文 chenggao（程高本无脂批；也不应带脂评本批语）
#   不限版本    → 全部
# ============================================================


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


def norm_fp(t):
    """文本指纹：去空白、去标点"""
    t = re.sub(r'\s+', '', str(t or ''))
    return re.sub(r'[，。；：、！？「」『』（）()\[\]【】""\'\'…—·\-,.;:!?]', '', t)


def sha1(s):
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def load_jsonl(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def parse_list(v):
    """字段可能是 list 也可能是 JSON 字符串"""
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        try:
            x = json.loads(v)
            return x if isinstance(x, list) else ([v] if v else [])
        except Exception:
            return [v] if v else []
    return []


# ============================================================
# 1) 载入三源并归一化
# ============================================================
def load_sources():
    """返回统一的 pizhu 记录列表"""
    out = []

    # ---- 源 A：抚琴居校本脂批 ----
    # s12 修复后：优先用 fuqinju/pizhu.jsonl（含 1,315 条截断修复）
    # 回退到 clean/pizhu_redecoded.jsonl（未修复版本）
    p_fixed = os.path.join(CORPUS, "fuqinju", "pizhu.jsonl")
    p_orig = os.path.join(CORPUS, "clean", "pizhu_redecoded.jsonl")
    p = p_fixed if os.path.exists(p_fixed) else p_orig
    log(f"  抚琴居脂批来源: {os.path.basename(os.path.dirname(p))}/{os.path.basename(p)}")
    rows = load_jsonl(p)
    for r in rows:
        vers = parse_list(r.get("versions_legend")) or parse_list(r.get("versions"))
        out.append({
            "ver": SRC_FUQINJU,
            "src_tag": "抚琴居校本",
            "text": (r.get("text") or "").strip(),
            "hui": int(r.get("hui") or 0),
            "huimu": r.get("huimu") or "",
            "ptype": (parse_list(r.get("ptypes_legend")) or
                      parse_list(r.get("ptypes")) or [r.get("ptype") or ""])[0],
            "versions": vers,
            "page": r.get("page"),
            "origin": "pizhu_redecoded",
            "prio": 1,                      # 优先级最高
        })
    log(f"  源A 抚琴居校本脂批: {len(rows):,}")

    # ---- 源 B：脂批大全 ----
    p = os.path.join(CORPUS, "zhiyanzhai_pizhu", "pizhu.jsonl")
    rows = load_jsonl(p)
    for r in rows:
        vers = parse_list(r.get("versions"))
        out.append({
            "ver": SRC_ZHIPI,
            "src_tag": "脂批大全",
            "text": (r.get("text") or "").strip(),
            "hui": int(r.get("hui") or 0),
            "huimu": r.get("huimu") or "",
            "ptype": (parse_list(r.get("ptypes")) or [r.get("ptype") or ""])[0],
            "versions": vers,
            "page": None,
            "origin": "zhiyanzhai_pizhu",
            "prio": 3,
        })
    log(f"  源B 脂批大全: {len(rows):,}")

    # ---- 源 C：癸酉本批语 ----
    p = os.path.join(CORPUS, "guiyouben", "pizhu.jsonl")
    rows = load_jsonl(p)
    for r in rows:
        out.append({
            "ver": SRC_GUIYOU,
            "src_tag": "癸酉本",
            "text": (r.get("text") or "").strip(),
            "hui": int(r.get("hui") or 0),
            "huimu": r.get("huimu") or "",
            "ptype": r.get("ptype") or "",
            "versions": ["癸酉本"],
            "page": None,
            "origin": r.get("source") or "guiyouben",   # 保留细分来源
            "prio": 2,
        })
    log(f"  源C 癸酉本批语: {len(rows):,}")

    return out


# ============================================================
# 2) 跨源去重
# ============================================================
def dedup(records):
    """
    按归一化文本指纹去重，保留优先级最高的那条。
    保留时把其他来源的信息合并进 `alt_sources`，不丢信息。
    """
    buckets = collections.defaultdict(list)
    for r in records:
        if len(norm_fp(r["text"])) < 2:
            continue
        buckets[norm_fp(r["text"])].append(r)

    merged = []
    n_dup = 0
    for fp, group in buckets.items():
        group.sort(key=lambda x: x["prio"])
        keep = dict(group[0])
        others = group[1:]
        n_dup += len(others)
        if others:
            keep["alt_sources"] = [
                {"ver": o["ver"], "src_tag": o["src_tag"], "origin": o["origin"]}
                for o in others
            ]
            # 合并版本标注（去重）
            vs = list(keep.get("versions") or [])
            for o in others:
                for v in (o.get("versions") or []):
                    if v not in vs:
                        vs.append(v)
            keep["versions"] = vs
        keep["fp"] = fp
        merged.append(keep)
    log(f"  去重: {len(records):,} → {len(merged):,}（合并重复 {n_dup:,}）")
    return merged


# ============================================================
# 3) 切 chunk（一条批语 = 一个 chunk）
# ============================================================
def make_chunks(records):
    chunks = []
    for i, r in enumerate(records):
        text = r["text"]
        hui = r["hui"]
        # chunk_id = sha1(ver | hui | fp前24 | STRATEGY_REV)[:16]
        # 「改策略/换来源都会变」——这个设计本身没问题。
        cid = sha1(f"{r['ver']}|{hui:03d}|{r['fp'][:24]}|{STRATEGY_REV}")[:16]
        chunks.append({
            "chunk_id": cid,
            "version": r["ver"],
            "source": "pizhu",
            "src_tag": r["src_tag"],
            "origin": r["origin"],
            "alt_sources": r.get("alt_sources", []),
            "hui": hui,
            "huimu": r["huimu"],
            "para_start": -1,
            "para_end": -1,
            "seg": i,
            "char_count": len(text),
            "strategy_rev": STRATEGY_REV,
            "partition": f"pizhu_{r['ver']}_hui_{min(108, max(1, hui)):03d}",
            "pizhu_versions": r.get("versions") or [],
            "pizhu_ptype": r.get("ptype") or "",
            "pizhu_page": r.get("page"),
            "text": text,
        })

    # ---- S40b：消解 chunk_id 碰撞 ----
    #
    # 问题：fp 是归一化文本的前 24 字符。一条批语在不同批型里可能被切成
    #   两条，前缀相同、尾部不同：
    #     seg=991 侧批 152 字「又是李嬷，事有凑巧…」
    #     seg=993 眉批 128 字「又是李嬷，事有凑巧…」（前 24 字一样）
    #   → fp[:24] 相同 → cid 相同 → 写 HBase 时后覆盖先
    #   实测 129 组冲突，丢 152 条脂批（含 1 条回末总评）。
    #
    # 修法：**只给冲突的那些**加 _{seg} 后缀，不冲突的保持原 id。
    #   这样与已发布的 deploy/data/*.jsonl 完全兼容（那些数据就是这样修的），
    #   且不影响任何已有向量（向量的 chunk_id 也是同一套后缀）。
    _by = collections.defaultdict(list)
    for c in chunks:
        _by[c["chunk_id"]].append(c)
    _n_fix = 0
    for _cid, _items in _by.items():
        if len(_items) > 1:
            for _c in _items:
                _c["chunk_id"] = f"{_cid}_{_c['seg']}"
                _n_fix += 1
    if _n_fix:
        log(f"  chunk_id 碰撞: {len([k for k,v in _by.items() if len(v)>1])} 组，"
            f"加后缀 {_n_fix} 条")

    return chunks


# ============================================================
# 4) 向量化
# ============================================================
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


def hbase_conn():
    import happybase
    return happybase.Connection("master", port=9090, timeout=600000,
                                table_prefix=b"", table_prefix_separator=b"")


# ============================================================
def main():
    t_all = time.time()
    if not os.path.exists(PIPE):
        print("必须在 master 容器内运行")
        sys.exit(1)

    # 语料挂载点检查
    if not os.path.isdir(CORPUS):
        print(f"⚠ 缺 {CORPUS}")
        print("  请在宿主机执行：")
        print(r'  docker cp "D:\Programming\Red\hlm_corpus\clean\pizhu_redecoded.jsonl" master:/workspace/corpus/clean/')
        print(r'  docker cp "D:\Programming\Red\hlm_corpus\zhiyanzhai_pizhu\pizhu.jsonl" master:/workspace/corpus/zhiyanzhai_pizhu/')
        print(r'  docker cp "D:\Programming\Red\hlm_corpus\guiyouben\pizhu.jsonl" master:/workspace/corpus/guiyouben/')
        sys.exit(1)

    log("=" * 70)
    log("S10 三源脂批统一索引")
    log("=" * 70)

    # ---- 1) 载入 + 去重 ----
    log("载入三源...")
    recs = load_sources()
    log(f"  合计 {len(recs):,}")
    log("跨源去重...")
    recs = dedup(recs)

    # ---- 2) 统计 ----
    by_ver = collections.Counter(r["ver"] for r in recs)
    log(f"按隔离版本: {dict(by_ver)}")
    by_src = collections.Counter(r["src_tag"] for r in recs)
    log(f"按来源: {dict(by_src)}")

    # ---- 3) 切 chunk ----
    chunks = make_chunks(recs)
    log(f"chunk 数: {len(chunks):,}")

    # ---- 4) 本地 + HDFS ----
    os.makedirs(OUT, exist_ok=True)
    local = os.path.join(OUT, "pizhu_3src.jsonl")
    chunks.sort(key=lambda c: (c["version"], c["hui"], c["seg"]))
    with open(local, "w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    log(f"本地: {local}  {os.path.getsize(local)/1048576:.2f} MB")

    subprocess.run(["hdfs", "dfs", "-mkdir", "-p", f"{HDFS_BASE}/warehouse/pizhu3src"],
                   capture_output=True)
    subprocess.run(["hdfs", "dfs", "-put", "-f", local,
                    f"{HDFS_BASE}/warehouse/pizhu3src/pizhu_3src.jsonl"],
                   capture_output=True)
    log(subprocess.run(["hdfs", "dfs", "-ls", "-h", f"{HDFS_BASE}/warehouse/pizhu3src"],
                       capture_output=True, text=True).stdout.strip())

    # ---- 5) 向量化 ----
    log("向量化...")
    encode = get_encoder()
    vecs = []
    t0 = time.time()
    for s in range(0, len(chunks), BATCH):
        batch = chunks[s:s + BATCH]
        embs = encode([c["text"] for c in batch])
        for c, v in zip(batch, embs):
            vecs.append((c, v.tobytes()))
        if (s // BATCH) % 20 == 0:
            el = time.time() - t0
            log(f"  {len(vecs)}/{len(chunks)}  {len(vecs)/max(el,1e-9):.1f}/s")
    log(f"向量化完成: {len(vecs):,} 条  耗时 {time.time()-t0:.1f}s")

    vpath = os.path.join(OUT, "pizhu_3src_vectors.jsonl")
    with open(vpath, "w", encoding="utf-8") as f:
        for c, blob in vecs:
            f.write(json.dumps({
                "chunk_id": c["chunk_id"], "version": c["version"],
                "source": "pizhu", "model_id": MODEL_ID, "dim": EMB_DIM,
                "vec_b64": base64.b64encode(blob).decode("ascii"),
            }, ensure_ascii=False) + "\n")
    subprocess.run(["hdfs", "dfs", "-mkdir", "-p",
                    f"{HDFS_BASE}/derived/vectors3ver/model={MODEL_ID}"], capture_output=True)
    subprocess.run(["hdfs", "dfs", "-put", "-f", vpath,
                    f"{HDFS_BASE}/derived/vectors3ver/model={MODEL_ID}/pizhu_3src_vectors.jsonl"],
                   capture_output=True)
    log(f"HDFS 向量: {os.path.getsize(vpath)/1048576:.2f} MB")

    # ---- 6) 清旧脂批 + 写新 ----
    c = hbase_conn()
    tb = c.table(T_CHUNKS)
    tbv = c.table(T_VECTORS)
    tbt = c.table(T_TERMS)

    log("清理旧脂批 chunk/vector/倒排...")
    for tname, t in (("chunks", tb), ("vectors", tbv)):
        keys = []
        for k, d in t.scan(batch_size=500):
            parts = k.decode().split("|")
            if len(parts) >= 4 and parts[1] == "pizhu":
                keys.append(k)
        with t.batch(batch_size=500) as b:
            for k in keys:
                b.delete(k)
        log(f"  {tname}: 删除 {len(keys):,} 行")
    # 旧脂批倒排 key: <ver>|pizhu|<term>
    tkeys = []
    for k, d in tbt.scan(batch_size=500):
        parts = k.decode().split("|")
        if len(parts) >= 3 and parts[1] == "pizhu":
            tkeys.append(k)
    if tkeys:
        with tbt.batch(batch_size=500) as b:
            for k in tkeys:
                b.delete(k)
        log(f"  terms: 删除 {len(tkeys):,} 行旧脂批倒排")
    time.sleep(3)

    # 写 chunk
    log("写 chunk...")
    t0 = time.time()
    with tb.batch(batch_size=200):
        for ch in chunks:
            rk = f"{ch['version']}|pizhu|{ch['hui']:03d}|{ch['chunk_id']}".encode()
            tb.put(rk, {
                b"d:text": ch["text"].encode("utf-8"),
                b"d:char_count": str(ch["char_count"]).encode(),
                b"d:source": b"pizhu",
                b"m:hui": str(ch["hui"]).encode(),
                b"m:huimu": ch["huimu"].encode("utf-8"),
                b"m:seg": str(ch["seg"]).encode(),
                b"m:version": ch["version"].encode(),
                b"m:strategy_rev": STRATEGY_REV.encode(),
                b"m:partition": ch["partition"].encode(),
                b"m:src_tag": ch["src_tag"].encode("utf-8"),
                b"m:origin": ch["origin"].encode("utf-8"),
                b"m:pizhu_versions": json.dumps(ch["pizhu_versions"],
                                                ensure_ascii=False).encode("utf-8"),
                b"m:pizhu_ptype": (ch["pizhu_ptype"] or "").encode("utf-8"),
                b"m:pizhu_page": str(ch["pizhu_page"] or "").encode(),
                b"m:alt_sources": json.dumps(ch["alt_sources"],
                                             ensure_ascii=False).encode("utf-8"),
            })
    log(f"  {T_CHUNKS} 写入 {len(chunks):,} 行  耗时 {time.time()-t0:.1f}s")

    # 写向量
    log("写向量...")
    t0 = time.time()
    with tbv.batch(batch_size=200):
        for ch, blob in vecs:
            rk = f"{ch['version']}|pizhu|{ch['hui']:03d}|{ch['chunk_id']}".encode()
            tbv.put(rk, {
                b"v:emb": blob, b"m:model_id": MODEL_ID.encode(),
                b"m:dim": str(EMB_DIM).encode(), b"m:hui": str(ch["hui"]).encode(),
                b"m:source": b"pizhu", b"m:version": ch["version"].encode(),
            })
    log(f"  {T_VECTORS} 写入 {len(vecs):,} 行  耗时 {time.time()-t0:.1f}s")

    # 写倒排（按隔离版本分组）
    log("写倒排...")
    t0 = time.time()
    byver = collections.defaultdict(list)
    for ch in chunks:
        byver[ch["version"]].append(ch)
    for ver, vchunks in byver.items():
        postings = collections.defaultdict(list)
        for ch in vchunks:
            for bg, tf in bigrams(ch["text"]).items():
                postings[bg].append([ch["chunk_id"], tf])
        with tbt.batch(batch_size=100) as b:
            for term, plist in postings.items():
                b.put(f"{ver}|pizhu|{term}".encode(), {
                    b"p:postings": json.dumps(plist, ensure_ascii=False).encode("utf-8"),
                    b"p:df": str(len(plist)).encode(),
                    b"p:source": b"pizhu",
                })
        log(f"  {ver:10s} 脂批词项 {len(postings):,}")
    log(f"  倒排完成  耗时 {time.time()-t0:.1f}s")

    # ---- 7) 校验 ----
    log("-- 校验 --")
    cnt = collections.Counter()
    for k, d in tb.scan(batch_size=500):
        p = k.decode().split("|")
        cnt[(p[0], p[1] if len(p) >= 4 else "?")] += 1
    log(f"  {T_CHUNKS} 总行数: {sum(cnt.values()):,}")
    for (v, s), n in sorted(cnt.items()):
        log(f"    {v:10s}/{s:6s}: {n:5d}")

    # 抽样
    for ver in (SRC_FUQINJU, SRC_ZHIPI, SRC_GUIYOU):
        for k, d in tb.scan(row_prefix=f"{ver}|pizhu|".encode(), limit=1):
            log(f"  抽样 {ver}: {k.decode()}")
            log(f"    src_tag={d.get(b'm:src_tag',b'').decode()} "
                f"text={d[b'd:text'].decode()[:40]}")

    c.close()

    # 落统计
    with open(os.path.join(OUT, "pizhu3src_stats.json"), "w", encoding="utf-8") as f:
        json.dump({
            "strategy_rev": STRATEGY_REV,
            "n_total_chunks": len(chunks),
            "by_version": dict(by_ver),
            "by_source_tag": dict(by_src),
        }, f, ensure_ascii=False, indent=2)
    log(f"全部完成，总耗时 {time.time()-t_all:.1f}s")


if __name__ == "__main__":
    main()
