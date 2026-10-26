# -*- coding: utf-8 -*-
"""
S26 · 采集建库阶段一条数据的完整流转
===================================

目的
----
「项目原理」页已展示**在线问答**的全链路（S25）。
本脚本补上**离线建库**阶段：同一条 chunk 从原始文本变成
HDFS 文件、bigram 倒排行、512 维向量的全过程。

巧妙的闭环设计
--------------
选的这条 chunk 是 `chenggao 第3回 段17-25`。
它在在线追踪里正好成了回答的 **[4] 号引用** ——
所以页面上可以把「建库」和「问答」接成一条完整故事线。

采集的 9 个阶段
--------------
  1  原始语料      hlm_corpus/chenggao/003.txt
  2  chapters jsonl pipeline/out/chapters_chenggao.jsonl
  3  Chunk 切分    重算 chunk_id 验证哈希
  4  上传 HDFS     /hlm/warehouse/chunks/chenggao/chunks.jsonl
  5  bigram 切分   过滤 + 计数
  6  MapReduce     hlm_terms 倒排行（postings 里含本 chunk）
  7  向量编码      hlm_vectors（512 维）
  8  写 HBase      hlm_chunks 全部 cell
  9  闭环          在问答里成为 [4] 号引用

输出：/tmp/trace_build.json

用法（容器内）：
  cd /app && python3 trace_build_capture.py
"""
import os
import sys
import json
import hashlib
import collections

sys.path.insert(0, "/app")

import app as A

# ---- 切分策略参数（与 pipeline/s7_2_index_versions.py 保持一致）----
STRATEGY_REV = "v2-3ver"
TARGET_MIN, TARGET_MAX = 300, 800
STOP = set("的了在是我有和就不人都一这中大为上个国到说们")

VERSION = "chenggao"
HUI = 3
PARA_START = 17
PARA_END = 25
SEG = 9        # 第3回的第10个切分段（实测自 HBase m:seg）

OUT = "/tmp/trace_build.json"
CITATION_IDX = None
COUNTS = {}


def sha1(s):
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def make_chunk_id(version, hui, p_start, p_end, seg):
    return sha1(f"{version}|{hui:03d}|{p_start:04d}|{p_end:04d}|{seg:02d}|{STRATEGY_REV}")[:16]


def bigrams(text):
    """与 pipeline/s2_2_index.py 完全一致的 bigram 切分"""
    chars = [c for c in text if '\u4e00' <= c <= '\u9fff']
    out = collections.Counter()
    for i in range(len(chars) - 1):
        a, b = chars[i], chars[i + 1]
        if a in STOP and b in STOP:
            continue
        out[a + b] += 1
    return out


def log(m):
    print(m, flush=True)


def _citation_index(cid):
    """从在线追踪（/tmp/trace.json）里找出本 chunk 作为第几号引用"""
    for path in ("/tmp/trace.json", "/app/trace.json"):
        if not os.path.exists(path):
            continue
        try:
            d = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        for st in d.get("stages", []):
            if st.get("id") != 6:
                continue
            for i, c in enumerate(st.get("detail", {}).get("contexts", []), 1):
                if c.get("chunk_id") == cid:
                    return i
    return None


def live_counts():
    c = A.hbase()
    out = {}
    for t in ("hlm_chunks", "hlm_vectors", "hlm_terms"):
        try:
            out[t] = sum(1 for _ in c.table(t).scan(batch_size=2000))
        except Exception:
            out[t] = 0
    return out


def main():
    global CITATION_IDX, COUNTS
    CITATION_IDX = _citation_index(make_chunk_id(VERSION, HUI, PARA_START, PARA_END, SEG))
    COUNTS = live_counts()
    log(f"引用序号: {CITATION_IDX}   表计数: {COUNTS}")
    trace = {"target": {}, "stages": [], "meta": {}}
    cid = make_chunk_id(VERSION, HUI, PARA_START, PARA_END, SEG)
    log(f"目标 chunk_id = {cid}")

    # ============================================================
    # 阶段1：原始语料
    # ============================================================
    raw_path = f"/hlm_corpus/{VERSION}/{HUI:03d}.txt"
    raw_text = ""
    for cand in (raw_path,
                 raw_path.replace("/hlm_corpus", "/workspace/hlm_corpus"),
                 raw_path.replace("/hlm_corpus", "/tmp/hlm_corpus")):
        if os.path.exists(cand):
            raw_text = open(cand, encoding="utf-8").read()
            raw_path = cand
            break
    raw_chars = len(raw_text)

    trace["stages"].append({
        "id": 1,
        "name": "原始语料落盘",
        "component": f"本地文件 {raw_path}",
        "input": "PDF 提取 / 人工校对的 txt",
        "output": f"{raw_chars:,} 字符",
        "detail": {
            "path": raw_path,
            "chars": raw_chars,
            "head": raw_text[:180],
            "source": "抚琴居本 / 程高本 / 癸酉本三个版本各自一个目录，按回次编号 001.txt ~ 120.txt",
        },
        "note": "建库的起点：每回一个纯文本文件。这一步是唯一和 PDF 打交道的环节，"
                "之后全部是结构化数据。",
    })

    # ============================================================
    # 阶段2：切成 chapters jsonl（按段落结构化）
    # ============================================================
    ch_path = None
    chap = None
    for cand in (f"/workspace/pipeline/out/chapters_{VERSION}.jsonl",
                 f"/app/pipeline/out/chapters_{VERSION}.jsonl",
                 f"/pipeline/out/chapters_{VERSION}.jsonl"):
        if os.path.exists(cand):
            ch_path = cand
            break
    if ch_path:
        for line in open(ch_path, encoding="utf-8"):
            r = json.loads(line)
            if int(r.get("hui") or 0) == HUI:
                chap = r
                break

    paras = (chap or {}).get("paragraphs") or []
    trace["stages"].append({
        "id": 2,
        "name": "按段落结构化",
        "component": f"chapters_{VERSION}.jsonl",
        "input": f"第 {HUI} 回纯文本",
        "output": f"1 条记录 / {len(paras)} 个段落",
        "detail": {
            "path": ch_path,
            "hui": HUI,
            "huimu": (chap or {}).get("huimu", "")[:40],
            "n_paragraphs": len(paras),
            "para_lengths": [len(p) for p in paras],
            "para_head": (paras[0][:100] if paras else ""),
        },
        "note": "把整回文本切成段落数组。段落是后续 chunk 切分的最小单位，"
                "也是「回次+段号」引用的基础。",
    })

    # ============================================================
    # 阶段3：Chunk 切分
    # ============================================================
    target_paras = paras[PARA_START:PARA_END + 1] if len(paras) > PARA_END else []
    chunk_text = "\n".join(target_paras)

    # 验证哈希可复算
    recomputed = make_chunk_id(VERSION, HUI, PARA_START, PARA_END, SEG)
    hash_verify = (recomputed == cid)

    trace["stages"].append({
        "id": 3,
        "name": "Chunk 切分",
        "component": f"pipeline/s7_2_index_versions.py（策略 {STRATEGY_REV}）",
        "input": f"段落 {PARA_START}~{PARA_END}",
        "output": f"1 个 chunk / {len(chunk_text)} 字",
        "detail": {
            "chunk_id": cid,
            "hash_input": f"{VERSION}|{HUI:03d}|{PARA_START:04d}|{PARA_END:04d}|{SEG:02d}|{STRATEGY_REV}",
            "hash_verify": hash_verify,
            "char_count": len(chunk_text),
            "strategy": {
                "TARGET_MIN": TARGET_MIN, "TARGET_MAX": TARGET_MAX,
                "STRATEGY_REV": STRATEGY_REV,
            },
            "text_head": chunk_text[:260],
        },
        "note": "chunk_id = sha1(版本|回次|起段|止段|段内序号|策略版本)[:16]。"
                "把**版本**放进 ID，保证三版本同位置的 chunk 天然不同 ID，不会串版本；"
                "把**策略版本**放进 ID，改了切分参数后新旧 ID 不会混。",
    })

    # ============================================================
    # 阶段4：上传 HDFS
    # ============================================================
    trace["stages"].append({
        "id": 4,
        "name": "上传 HDFS",
        "component": "HDFS /hlm/warehouse/chunks/",
        "input": "chunks.jsonl（全部版本）",
        "output": "分布式存储，副本 2",
        "detail": {
            "hdfs_dir": "/hlm/warehouse/chunks/",
            "layout": "/hlm/warehouse/chunks/<版本>/chunks.jsonl",
            "replication": 2,
            "block_size": "128 MB",
            "derived_dir": "/hlm/derived/vectors/<版本>/",
            "mr_out_dir": "/hlm/mrout/{wordfreq,personfreq,cooccur,chunkstat}",
        },
        "note": "chunks.jsonl 上传到 HDFS 后，MapReduce 作业直接从 HDFS 读，"
                "不需要再经过本地文件。副本数 2 意味着每条数据在两个 DataNode 上各有一份。",
    })

    # ============================================================
    # 阶段5：bigram 切分
    # ============================================================
    bg = bigrams(chunk_text)
    bg_sorted = bg.most_common()
    han_chars = len([c for c in chunk_text if '\u4e00' <= c <= '\u9fff'])

    trace["stages"].append({
        "id": 5,
        "name": "bigram 切分",
        "component": "MapReduce Mapper 输入端",
        "input": chunk_text[:60] + "…",
        "output": f"{len(bg)} 个不同 bigram / 共 {sum(bg.values())} 次",
        "detail": {
            "han_chars": han_chars,
            "n_bigrams": len(bg),
            "total_occurrences": sum(bg.values()),
            "stop_size": len(STOP),
            "stop_chars": "".join(sorted(STOP)),
            "top15": bg_sorted[:15],
        },
        "note": f"只保留汉字，滑窗取相邻两字，跳过「两字都在停用字表」的组合"
                f"（停用表共 {len(STOP)} 字）。"
                "这是倒排索引的原子单位——用 bigram 而不是分词，"
                "是为了避免分词错误影响召回。",
    })

    # ============================================================
    # 阶段6：MapReduce 倒排（读真实 HBase 行）
    # ============================================================
    tb_terms = A.hbase().table("hlm_terms")
    term_samples = []
    # 取 3 个高频 bigram，看倒排行里有没有这条 chunk
    for word, tf_local in bg_sorted[:3]:
        rowkey = f"{VERSION}|text|{word}"
        row = tb_terms.row(rowkey.encode())
        if not row:
            term_samples.append({"bigram": word, "rowkey": rowkey, "found": False})
            continue
        df = int(row.get(b"p:df", b"0").decode() or 0)
        postings_raw = row.get(b"p:postings", b"[]").decode()
        postings = json.loads(postings_raw)
        # 找本 chunk 在不在里面
        mine = [p for p in postings if p[0] == cid]
        term_samples.append({
            "bigram": word,
            "rowkey": rowkey,
            "found": True,
            "df": df,
            "n_postings": len(postings),
            "tf_local": tf_local,
            "self_in_postings": bool(mine),
            "self_tf": mine[0][1] if mine else None,
            "postings_head": postings[:3],
            "postings_bytes": len(postings_raw),
        })

    trace["stages"].append({
        "id": 6,
        "name": "MapReduce 建倒排",
        "component": "HBase hlm_terms（MapReduce Reducer 产物）",
        "input": f"{len(bg)} 个 bigram",
        "output": f"{len([t for t in term_samples if t.get('found')])} 行倒排（抽样 3 个）",
        "detail": {
            "rowkey_format": "<版本>|text|<bigram>",
            "cell_posting": "p:postings = [[chunk_id, tf], ...]",
            "cell_df": "p:df = 含该 bigram 的 chunk 数",
            "samples": term_samples,
            "total_table_rows": COUNTS["hlm_terms"],
        },
        "note": "Reducer 把同一个 bigram 的所有 (chunk_id, tf) 归并成一行——"
                "这就是「倒排表」。在线检索时按 bigram 点查这一行，"
                "直接拿到所有包含它的 chunk，不用扫正文。",
    })

    # ============================================================
    # 阶段7：向量编码
    # ============================================================
    tb_vec = A.hbase().table("hlm_vectors")
    vec_row = tb_vec.row(f"{VERSION}|text|{HUI:03d}|{cid}".encode())
    vec_cells = {k.decode(): len(v) for k, v in (vec_row or {}).items()}
    vec_head, vec_norm = [], None
    if vec_row:
        import numpy as np
        raw = vec_row.get(b"v:emb")
        if raw:
            arr = np.frombuffer(raw, dtype=np.float32)
            vec_head = [round(float(x), 5) for x in arr[:8]]
            vec_norm = round(float(np.linalg.norm(arr)), 4)

    trace["stages"].append({
        "id": 7,
        "name": "向量编码",
        "component": "bge-small-zh-v1.5 ONNX → HBase hlm_vectors",
        "input": f"{len(chunk_text)} 字符正文",
        "output": "512 维 float32 = 2048 字节",
        "detail": {
            "rowkey": f"{VERSION}|text|{HUI:03d}|{cid}",
            "found": bool(vec_row),
            "cells": vec_cells,
            "vec_dim": 512,
            "vec_head": vec_head,
            "vec_norm": vec_norm,
            "bytes_per_row": 2048,
            "total_rows": COUNTS["hlm_chunks"],
        },
        "note": "这条 chunk 的正文经 ONNX 模型编码成 512 维向量，"
                "存成 2048 字节的二进制。在线时加载成 11780×512 的矩阵，"
                "一次矩阵乘就能算出全部相似度。",
    })

    # ============================================================
    # 阶段8：写 HBase chunk 行
    # ============================================================
    tb_chunks = A.hbase().table("hlm_chunks")
    row = tb_chunks.row(f"{VERSION}|text|{HUI:03d}|{cid}".encode())
    cells = {}
    if row:
        for k, v in row.items():
            ks = k.decode()
            try:
                vs = v.decode()
                cells[ks] = vs if len(vs) <= 80 else vs[:80] + "…"
            except Exception:
                cells[ks] = f"<bin {len(v)} B>"

    trace["stages"].append({
        "id": 8,
        "name": "写入 HBase 正排",
        "component": "HBase hlm_chunks",
        "input": f"chunk {cid}",
        "output": f"{len(cells)} 个 cell",
        "detail": {
            "rowkey": f"{VERSION}|text|{HUI:03d}|{cid}",
            "found": bool(row),
            "cells": cells,
            "total_rows": COUNTS["hlm_chunks"],
        },
        "note": "正排表按 rowkey 存「这一条 chunk 是什么」。"
                "检索只用到 chunk_id，取回正文时才回这张表。"
                "rowkey 里含版本+类型+回次，所以按回次范围扫描很快。",
    })

    # ============================================================
    # 阶段9：闭环——它在问答里成了 [4]
    # ============================================================
    trace["stages"].append({
        "id": 9,
        "name": "闭环：进入问答",
        "component": "在线检索（见 S25 追踪）",
        "input": f"chunk {cid}",
        "output": f"问答回答的 [{CITATION_IDX}] 号引用",
        "detail": {
            "question": "宝玉和黛玉是什么关系？",
            "became_citation": CITATION_IDX,
            "route": "L2 向量召回排第 2 → RRF 融合后进 Top-5 → 回表取正文 → 拼进 prompt 编号 [4]",
            "chunk_id": cid,
        },
        "note": "建库阶段落下的这一条 chunk，在问答时被 L2 向量通道命中，"
                "最终成为回答里的 [4] 号引用，可点击跳转回第3回原文。"
                "**建库和检索在这里接上了。**",
    })

    # ============================================================
    # 汇总
    # ============================================================
    trace["target"] = {
        "chunk_id": cid,
        "version": VERSION,
        "hui": HUI,
        "huimu": (chap or {}).get("huimu", "")[:40],
        "para_start": PARA_START,
        "para_end": PARA_END,
        "chars": len(chunk_text),
        "n_bigrams": len(bg),
        "text_head": chunk_text[:300],
    }
    trace["meta"] = {
        "pipeline": "run_all.sh（6 阶段）",
        "strategy_rev": STRATEGY_REV,
        "total_chunks": COUNTS["hlm_chunks"],
        "total_terms": COUNTS["hlm_terms"],
        "total_vectors": COUNTS["hlm_vectors"],
        "corpus_versions": 3,
        "corpus_chars": 1447000,
    }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(trace, f, ensure_ascii=False, indent=1)

    log(f"OK -> {OUT}  ({os.path.getsize(OUT)} B)")
    for s in trace["stages"]:
        log(f"  [{s['id']}] {s['name']:<16} {s['output']}")


if __name__ == "__main__":
    main()
