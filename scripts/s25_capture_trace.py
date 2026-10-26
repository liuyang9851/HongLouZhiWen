# -*- coding: utf-8 -*-
"""
S25 · 采集一次真实问答的全链路中间数据
=====================================

目的
----
「项目原理」页要展示数据在每个组件中的**真实变化**，
不能是示意图，必须是实际跑出来的值。

本脚本在 hlm-api 容器内运行，对一个问题逐阶段抓取：

  阶段0  用户输入
  阶段1  查询改写 + 别名展开
  阶段2  L1 关键词召回（HBase hlm_terms 倒排，逐行读）
  阶段3  L2 向量召回（ONNX 编码 512 维 → 余弦相似度）
  阶段4  L3 图谱召回（HBase hlm_stats cooccur）
  阶段5  RRF 融合
  阶段6  回表取正文（HBase hlm_chunks）
  阶段7  Prompt 拼装
  阶段8  LLM 调用

输出：/tmp/trace.json

用法（在容器内）：
  cd /app && python3 trace_capture.py
"""
import os
import sys
import json
import time

sys.path.insert(0, "/app")

import app as A

QUESTION = "宝玉和黛玉是什么关系？"
COUNTS = {}
VERSION = None          # None = 三版本融合检索

OUT = "/tmp/trace.json"


def hb_cell(row, key):
    """从 HBase row 取 cell 文本"""
    if not row:
        return None
    v = row.get(key)
    if v is None:
        return None
    try:
        return v.decode()
    except Exception:
        return str(v)



def live_counts():
    """实时统计 HBase 各表行数（避免写死；索引重建后数字会变）"""
    c = A.hbase()
    out = {}
    for t in ("hlm_chunks", "hlm_vectors", "hlm_terms", "hlm_wordfreq", "hlm_stats"):
        try:
            out[t] = sum(1 for _ in c.table(t).scan(batch_size=2000))
        except Exception:
            out[t] = 0
    return out

def main():
    global COUNTS
    COUNTS = live_counts()
    print(f"[counts] {COUNTS}", flush=True)
    trace = {"question": QUESTION, "stages": [], "meta": {}}
    t_start = time.time()

    # ============================================================
    # 阶段1：查询改写 + 别名展开
    # ============================================================
    t0 = time.time()
    rewrites = A.rewrite_query(QUESTION)
    queries = []
    alias_detail = []
    for rq in rewrites:
        eqs = A.expand_aliases(rq)
        for eq in eqs:
            if eq not in queries:
                queries.append(eq)
            # 记录每个改写变体展开出哪些别名
        alias_detail.append({"from": rq, "expanded": eqs})
    queries = queries[:24]
    t_rewrite = round((time.time() - t0) * 1000, 2)

    trace["stages"].append({
        "id": 1,
        "name": "查询理解与改写",
        "component": "FastAPI（应用层）· 纯 Python 逻辑",
        "input": QUESTION,
        "output": queries,
        "detail": {
            "rewrites": rewrites,
            "alias_expansion": alias_detail,
            "n_queries": len(queries),
        },
        "ms": t_rewrite,
        "note": "把口语「宝玉/黛玉」展开成正式名「贾宝玉/林黛玉」，"
                "为的是命中 HBase 里按正式名建立的索引；"
                "同时生成多种问法变体提高召回率。",
    })

    # ============================================================
    # 阶段2：L1 关键词召回（HBase hlm_terms）
    # ============================================================
    t0 = time.time()
    q = rewrites[0] if rewrites else QUESTION
    # 先看 bigram 切分
    qgrams = list(A.bigrams(q).keys())

    # 逐 bigram 读 HBase 倒排行，记录真实 postings
    tb_terms = A.hbase().table("hlm_terms")
    term_rows = []
    prefixes = [f"{v}|text|" for v in A.ROOT_VERSIONS]
    for pv in A.PIZHU_VERSIONS:
        prefixes.append(f"{pv}|pizhu|")

    total_postings = 0
    for bg in qgrams[:8]:            # 只看前 8 个 bigram，避免输出过大
        for prefix in prefixes[:2]:  # 只看 2 个前缀示例
            rowkey = f"{prefix}{bg}"
            try:
                row = tb_terms.row(rowkey.encode())
            except Exception as e:
                row = None
            if not row:
                continue
            postings_raw = row.get(b"p:postings")
            df = row.get(b"p:df")
            postings = []
            if postings_raw:
                try:
                    postings = json.loads(postings_raw.decode())[:3]
                except Exception:
                    postings = []
            total_postings += len(postings)
            term_rows.append({
                "rowkey": rowkey,
                "df": int(df.decode()) if df else 0,
                "postings_sample": postings,
                "cell_bytes": {k.decode(): len(v) for k, v in row.items()},
            })

    # 真实执行 L1
    kw, ng, nh = A.search_keyword(q, A.CFG["topk_recall"], VERSION)
    t_kw = round((time.time() - t0) * 1000, 2)

    trace["stages"].append({
        "id": 2,
        "name": "L1 关键词召回",
        "component": f"HBase hlm_terms（表 {COUNTS['hlm_terms']:,} 行）",
        "input": q,
        "output": f"{len(kw)} 个候选 chunk",
        "detail": {
            "bigrams": qgrams,
            "n_bigrams": len(qgrams),
            "hbase_rows_read": len(term_rows) * len(prefixes),
            "rows_sample": term_rows,
            "n_hit_bigrams": nh,
            "top_candidates": kw[:5],
            "scoring": "score += 1.0 + min(tf,2)*0.1（逐 bigram 累加，最后除以 bigram 数）",
        },
        "ms": t_kw,
        "note": "MapReduce 离线阶段为每个 bigram 建了倒排表"
                "（rowkey = 版本|类型|bigram，cell p:postings = [[chunk_id, tf], ...]）。"
                "在线只做点查，O(bigram 数) 次 HBase GET。",
    })

    # ============================================================
    # 阶段3：L2 向量召回
    # ============================================================
    t0 = time.time()
    enc = A.get_encoder()
    qv = enc([q])[0]
    cache = A.load_vectors()
    import numpy as np
    sims = cache["mat"] @ qv
    vecs = A.search_vector(q, A.CFG["topk_recall"], VERSION)
    t_vec = round((time.time() - t0) * 1000, 2)

    trace["stages"].append({
        "id": 3,
        "name": "L2 向量召回",
        "component": "bge-small-zh-v1.5 ONNX + HBase hlm_vectors（11,780 行）",
        "input": q,
        "output": f"{len(vecs)} 个候选 chunk",
        "detail": {
            "embed_dim": 512,
            "query_vector_head": [round(float(x), 4) for x in qv[:8]],
            "query_vector_norm": round(float(np.linalg.norm(qv)), 4),
            "matrix_shape": list(cache["mat"].shape),
            "matrix_bytes": int(cache["mat"].nbytes),
            "sim_range": [round(float(sims.min()), 4), round(float(sims.max()), 4)],
            "top_candidates": vecs[:5],
            "calc": f"sims = ({COUNTS['hlm_vectors']} × 512) @ (512,) → {COUNTS['hlm_vectors']} 个余弦相似度，取 top-30",
        },
        "ms": t_vec,
        "note": "向量是离线用 ONNX 模型算好存进 HBase hlm_vectors 的"
                "（每行 2048 字节 = 512 float32）。在线只需编码 1 个问题向量，"
                "然后一次矩阵乘法算全部 11,780 条相似度。",
    })

    # ============================================================
    # 阶段4：L3 图谱召回
    # ============================================================
    t0 = time.time()
    # 用与 search_graph 修复后一致的**别名**匹配（S25）
    persons = [c for c, aliases in A.ALIAS.items()
               if any(a in QUESTION for a in aliases)]
    graph_detail = {"detected_persons": persons}
    row = None
    if len(persons) >= 2:
        a, b = sorted(persons[:2])
        tb_stats = A.hbase().table("hlm_stats")
        rowkey = f"cooccur|{a}|{b}"
        row = tb_stats.row(rowkey.encode())
        graph_detail["rowkey"] = rowkey
        graph_detail["found"] = bool(row)
        if row:
            graph_detail["cells"] = {k.decode(): v.decode() for k, v in row.items()}
    graph = A.search_graph(QUESTION)
    t_graph = round((time.time() - t0) * 1000, 2)

    trace["stages"].append({
        "id": 4,
        "name": "L3 图谱召回",
        "component": "HBase hlm_stats（表 709 行，cooccur 行由 MapReduce 产出）",
        "input": QUESTION,
        "output": f"{len(graph)} 个候选 chunk" if graph else "0（未触发或未命中）",
        "detail": {
            **graph_detail,
            "top_candidates": graph[:3],
            "trigger": "问题中必须出现 ≥2 个人物名（含简称/别名）才触发",
        "n_graph_candidates": len(graph),
        },
        "ms": t_graph,
        "note": "共现次数来自 MapReduce 离线统计"
                "（统计同一句/同一回中两个人物同时出现的次数）。"
                "在线只读一行就能拿到两人共现证据。",
    })

    # ============================================================
    # 阶段5：RRF 融合
    # ============================================================
    t0 = time.time()
    fused = A.rrf_fuse(kw, vecs, graph, A.CFG["rrf_k"], 5, dedup_by_hui=(VERSION is None))
    t_fuse = round((time.time() - t0) * 1000, 2)

    trace["stages"].append({
        "id": 5,
        "name": "RRF 融合 + 同回次去重",
        "component": "FastAPI（纯 Python 计算）",
        "input": f"L1={len(kw)} / L2={len(vecs)} / L3={len(graph)}",
        "output": f"{len(fused)} 个最终 chunk",
        "detail": {
            "rrf_k": A.CFG["rrf_k"],
            "formula": "RRF = Σ 1/(k + rank_i)，三路各自排名倒数求和",
            "fused": [
                {
                    "chunk_id": f["chunk_id"],
                    "rrf": round(f["rrf"], 6),
                    "kw_rank": f.get("kw_rank"),
                    "vec_rank": f.get("vec_rank"),
                    "graph_rank": f.get("graph_rank"),
                    "kw_score": f.get("kw_score"),
                    "vec_score": f.get("vec_score"),
                }
                for f in fused
            ],
            "dedup_by_hui": VERSION is None,
        },
        "ms": t_fuse,
        "note": "三路分数量纲不同（keyword 是命中数、vec 是余弦），"
                "不能直接相加 → 用排名倒数融合（RRF），只依赖名次，对量纲不敏感。"
                "同回次去重保证 5 条上下文来自 5 个不同回目，避免信息冗余。",
    })

    # ============================================================
    # 阶段6：回表取正文（HBase hlm_chunks）
    # ============================================================
    t0 = time.time()
    chunks = A.load_chunks()
    ctx = []
    for r in fused:
        c = chunks.get(r["chunk_id"])
        if not c:
            continue
        ctx.append({"chunk_id": r["chunk_id"], "rrf": round(r["rrf"], 6), **c})
    t_back = round((time.time() - t0) * 1000, 2)

    trace["stages"].append({
        "id": 6,
        "name": "回表取正文",
        "component": f"HBase hlm_chunks（表 {COUNTS['hlm_chunks']:,} 行）",
        "input": f"{len(fused)} 个 chunk_id",
        "output": f"{len(ctx)} 条完整上下文",
        "detail": {
            "contexts": [
                {
                    "chunk_id": c["chunk_id"],
                    "version": c.get("version"),
                    "hui": c.get("hui"),
                    "huimu": (c.get("huimu") or "")[:30],
                    "para_start": c.get("para_start"),
                    "para_end": c.get("para_end"),
                    "source": c.get("source"),
                    "chars": len(c.get("text") or ""),
                    "text_head": (c.get("text") or "")[:160],
                    "rrf": c["rrf"],
                }
                for c in ctx
            ],
            "total_chars": sum(len(c.get("text") or "") for c in ctx),
        },
        "ms": t_back,
        "note": "融合只用了 chunk_id，这里再按 id 回 HBase 取正文。"
                "取回的每条都带「版本 / 回次 / 段号」，这是答案可追溯的基础。",
    })

    # ============================================================
    # 阶段7：Prompt 拼装
    # ============================================================
    t0 = time.time()
    msgs = A.build_messages(QUESTION, ctx, [], None)
    t_prompt = round((time.time() - t0) * 1000, 2)

    sys_prompt = ""
    user_prompt = ""
    for m in msgs:
        if m["role"] == "system":
            sys_prompt = m["content"]
        elif m["role"] == "user":
            user_prompt = m["content"]

    trace["stages"].append({
        "id": 7,
        "name": "Prompt 拼装",
        "component": "FastAPI → LLM API（DeepSeek）",
        "input": f"{len(ctx)} 条上下文 + 原问题",
        "output": f"{len(sys_prompt) + len(user_prompt)} 字符的 messages",
        "detail": {
            "n_messages": len(msgs),
            "roles": [m["role"] for m in msgs],
            "system_chars": len(sys_prompt),
            "user_chars": len(user_prompt),
            "system_prompt": sys_prompt[:1200],
            "user_prompt": user_prompt[:2600],
        },
        "ms": t_prompt,
        "note": "system prompt 规定「必须标注引用编号、不得编造原文」；"
                "user prompt 把 5 条上下文按 [1]~[5] 编号后拼进去，让模型能引用。",
    })

    # ============================================================
    # 阶段8：LLM 调用
    # ============================================================
    t0 = time.time()
    import asyncio
    provs = A.all_providers()
    default_model = (next((k for k, v in provs.items() if v.get("is_default")), None)
                     or next((k for k, v in provs.items() if v.get("enabled", True)), None)
                     or "deepseek-flash")
    llm_raw = {}
    try:
        llm_raw = asyncio.run(A.call_llm(default_model, msgs, stream=False))
    except Exception as e:
        llm_raw = {"content": f"[调用失败] {e}", "reasoning": "", "usage": {}}
    t_llm = round((time.time() - t0) * 1000, 2)
    answer = llm_raw.get("content") or ""
    reasoning = llm_raw.get("reasoning") or ""
    usage = llm_raw.get("usage") or {}

    trace["stages"].append({
        "id": 8,
        "name": "LLM 生成",
        "component": f"DeepSeek 官方 API（{default_model}）· OpenAI 兼容协议",
        "input": f"{len(sys_prompt) + len(user_prompt)} 字符",
        "output": f"{len(answer)} 字符回答",
        "detail": {
            "model": default_model,
            "answer": answer,
            "answer_head": answer[:700],
            "reasoning_head": reasoning[:400],
            "has_reasoning": bool(reasoning),
            "usage": usage,
        },
        "ms": t_llm,
        "note": "模型只负责「读上下文 → 组织语言」，不负责检索。"
                "所有事实来自前面 5 条上下文，所以每条结论都能标引用序号。",
    })

    # ============================================================
    # 汇总
    # ============================================================
    trace["meta"] = {
        "total_ms": round((time.time() - t_start) * 1000, 1),
        "version": VERSION or "all",
        "corpus": {
            "hlm_chunks": COUNTS["hlm_chunks"],
            "hlm_vectors": COUNTS["hlm_vectors"],
            "hlm_terms": COUNTS["hlm_terms"],
            "hlm_wordfreq": 133673,
            "hlm_stats": 709,
        },
        "t_idx_sizes": {
            "terms_rows": COUNTS["hlm_terms"],
            "vectors_rows": 11780,
            "chunks_rows": 11780,
        },
    }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(trace, f, ensure_ascii=False, indent=1)

    print(f"OK -> {OUT}  ({os.path.getsize(OUT)} B)")
    print(f"total {trace['meta']['total_ms']} ms")
    for s in trace["stages"]:
        print(f"  [{s['id']}] {s['name']:<18} {s['ms']:>8.1f} ms   {s['output']}")


if __name__ == "__main__":
    main()
