# -*- coding: utf-8 -*-
"""
S38 · 本地模式（免 Hadoop 部署）
=====================================

背景
----
项目原本强依赖 HBase：
  · load_chunks()   → scan hlm_chunks 表
  · load_vectors()  → scan hlm_vectors 表
  · search_keyword()→ 查 hlm_terms 倒排表

同学想部署时，装一套 HBase 集群成本过高（Docker + 3 节点 + 初始化）。
本模块提供**本地文件回退**：读 JSONL 快照，用内存倒排索引。

设计
----
  环境变量 HLM_STORAGE=local   → 走本模块（免 Hadoop）
  环境变量 HLM_STORAGE=hbase   → 走原有 HBase（默认，答辩用）
  未设置                         → 先试 HBase，失败自动降级到 local

本地文件（放在 /app/data/ 或 $HLM_DATA_DIR）：
  chunks_3ver.jsonl          正文 chunk（3,295 条）
  pizhu_3src.jsonl           脂批 chunk（8,662 条）
  vectors_3ver.jsonl         正文向量（512 维 float32，base64）
  pizhu_3src_vectors.jsonl   脂批向量

与 HBase 模式的差异（必须诚实说明）
-----------------------------------
  ✅ 检索逻辑完全相同（同样的 bigram、同样的 RRF 融合）
  ✅ 数据完全相同（同一批 JSONL 导出）
  ❌ HBase 的分布式存储/并发能力不体现（单机内存版）
  ❌ HDFS 相关功能（问答日志上传）不可用

即：**功能对齐，架构不同**。部署指南里必须写清这一点，
否则同学会误以为"RAG 就长这样"，而看不到 Hadoop 的价值。
"""
import os
import json
import math
import struct
import base64
import threading
from typing import Dict, List, Optional

# ---- 路径 ----
DATA_DIR = os.getenv("HLM_DATA_DIR", "/app/data")

# ---- 内存缓存 ----
_lock = threading.Lock()
_chunks: Dict[str, Dict] = {}
_index: Dict[str, List] = {}       # {bigram: [[cid, tf], ...]}
_df: Dict[str, int] = {}           # {bigram: 全库文档频率}
_DF_BY_VER: Dict[str, Dict[str, int]] = {}   # {"ver|src": {bigram: 该版本内df}}
_N_BY_VER: Dict[str, int] = {}               # {"ver|src": chunk 总数}
_vec_mat = None                    # numpy 矩阵
_vec_ids: List[str] = []
_vec_srcs: List[str] = []
_loaded = False


def _log(m):
    print(m, flush=True)


def _path(name):
    """本地文件路径（仅 local 源有意义，供报错信息用）"""
    return os.path.join(DATA_DIR, name)


def _read_jsonl(name):
    """
    按名字逐条产出 JSON 对象（S39）。

    数据来源由 data_source.resolve_source() 决定：
      · hdfs   → WebHDFS 流式读取（版本无关，推荐）
      · local  → 本地 JSONL 文件
    统一接口，上层 load_all() 不关心具体来源。
    """
    import data_source
    try:
        for rec in data_source.read_jsonl(name):
            yield rec
    except FileNotFoundError:
        _log(f"  ⚠ 数据文件不可得: {name}")
        return


# ============================================================
# bigram（与 HBase 模式完全一致，含 STOP 过滤）
# ============================================================
STOP = set("的了在是我有和就不人都一这中大为上个国到说们")


def bigrams(text: str):
    chars = [c for c in text if '\u4e00' <= c <= '\u9fff']
    out = {}
    for i in range(len(chars) - 1):
        a, b = chars[i], chars[i + 1]
        if a in STOP and b in STOP:
            continue
        out[a + b] = out.get(a + b, 0) + 1
    return out


# ============================================================
# 载入
# ============================================================
def load_all():
    """载入 chunk + 倒排 + 向量（幂等，只做一次）"""
    global _loaded, _vec_mat, _vec_ids, _vec_srcs
    if _loaded:
        return True

    with _lock:
        if _loaded:
            return True

        # ---- 1) chunk ----
        n_text = n_pz = 0
        for fn, src in (("chunks_3ver.jsonl", "text"),
                        ("pizhu_3src.jsonl", "pizhu")):
            for rec in _read_jsonl(fn):
                cid = rec.get("chunk_id")
                if not cid:
                    continue
                _chunks[cid] = {
                    "chunk_id": cid,
                    "rowkey": f"{rec.get('version')}|{src}|{rec.get('hui')}|{cid}",
                    "source": src,
                    "version": rec.get("version") or "",
                    "hui": int(rec.get("hui") or 0),
                    "huimu": rec.get("huimu") or "",
                    "para_start": int(rec.get("para_start") or 0),
                    "para_end": int(rec.get("para_end") or 0),
                    "seg": int(rec.get("seg") or 0),
                    "char_count": int(rec.get("char_count") or len(rec.get("text") or "")),
                    "text": rec.get("text") or "",
                    "pizhu_versions": rec.get("pizhu_versions") or [],
                    "pizhu_page": rec.get("pizhu_page") or "",
                    "src_tag": rec.get("src_tag") or "",
                    "origin": rec.get("origin") or "",
                    "pizhu_ptype": rec.get("pizhu_ptype") or "",
                    "partition": rec.get("partition") or "",
                    "strategy_rev": rec.get("strategy_rev") or "",
                }
                if src == "text":
                    n_text += 1
                else:
                    n_pz += 1

        if not _chunks:
            _log(f"  ⚠ 本地模式：{DATA_DIR} 下没有 chunk 数据")
            return False

        # ---- 2) 倒排索引（内存）----
        # S31 的改进一并保留：huimu 也纳入索引
        #
        # S38 关键修正：df 必须**按版本分别统计**
        #   HBase 的倒排表是 <ver>|text|<bigram> 分版本存的，
        #   p:df 只统计**该版本内**的 chunk 数（如 fuqinju 内 117）。
        #   若本地版把所有版本合在一起算 df（fuqinju+chenggao+guiyouben），
        #   df 会偏大 2~3 倍 → IDF 偏小 → 泛用词权重虚高 → 排序错乱。
        #   实测差异：HBase kw=9.66 vs 本地 kw=2.38（差 3 倍）。
        _df_by_ver: Dict[str, Dict[str, int]] = {}
        for cid, c in _chunks.items():
            idx_text = (c.get("text") or "") + " " + (c.get("huimu") or "")
            ver = c.get("version") or "?"
            src = c.get("source") or "text"
            key = f"{ver}|{src}"
            dfv = _df_by_ver.setdefault(key, {})
            for bg, tf in bigrams(idx_text).items():
                _index.setdefault(bg, []).append([cid, tf, key])
                dfv[bg] = dfv.get(bg, 0) + 1
        _DF_BY_VER = _df_by_ver
        # 每个 版本|来源 的 chunk 总数（IDF 的分母基准）
        _cnt_by_ver: Dict[str, int] = {}
        for c in _chunks.values():
            k = f"{c.get('version') or '?'}|{c.get('source') or 'text'}"
            _cnt_by_ver[k] = _cnt_by_ver.get(k, 0) + 1
        _N_BY_VER = _cnt_by_ver
        _df.clear()
        for bg, plist in _index.items():
            _df[bg] = len(plist)

        # ---- 3) 向量 ----
        try:
            import numpy as np
            ids, mats, srcs = [], [], []
            for fn, src in (("vectors_3ver.jsonl", "text"),
                            ("pizhu_3src_vectors.jsonl", "pizhu")):
                for rec in _read_jsonl(fn):
                    cid = rec.get("chunk_id")
                    # 实测字段名：vec_b64（也兼容 emb/vector 两种写法）
                    b64 = rec.get("vec_b64") or rec.get("emb") or rec.get("vector")
                    if not cid or not b64:
                        continue
                    raw = base64.b64decode(b64)
                    vec = struct.unpack(f"<{len(raw)//4}f", raw)
                    ids.append(cid)
                    mats.append(vec)
                    srcs.append(src)
            if mats:
                _vec_mat = np.asarray(mats, dtype="float32")
                z = np.linalg.norm(_vec_mat, axis=1, keepdims=True)
                z[z == 0] = 1.0
                _vec_mat = _vec_mat / z            # L2 归一，点积=余弦
                _vec_ids = ids
                _vec_srcs = srcs
                _log(f"  载入向量矩阵: {_vec_mat.shape}")
        except Exception as e:
            _log(f"  ⚠ 向量载入失败（向量检索将不可用）: {e}")

        _loaded = True
        _log(f"  载入 chunk 元数据: {len(_chunks):,} 条"
             f"（正文 {n_text:,} / 脂批 {n_pz:,}）")
        _log(f"  内存倒排: {len(_index):,} 个 bigram")
        return True


# ============================================================
# 对外接口（签名与 HBase 版对齐）
# ============================================================
def get_chunks() -> Dict[str, Dict]:
    load_all()
    return _chunks


def search_keyword(query: str, topk: int = 30,
                   version: Optional[str] = None) -> tuple:
    """
    返回 (results, n_grams, n_hit)，与 HBase 版 search_keyword 一致。

    打分逻辑与 HBase 版保持一致：
      ① IDF 加权（S31）
      ② 只惩罚超短 chunk + 脂批降权（S32）
    """
    load_all()
    if not _chunks:
        return [], 0, 0

    qgrams = list(bigrams(query).keys())
    scores: Dict[str, float] = {}
    hit = 0
    N_CHUNKS = len(_chunks)

    for bg in qgrams:
        plist = _index.get(bg)
        if not plist:
            continue
        hit += 1
        # 按版本分别算 IDF（与 HBase 的 p:df 语义一致）
        _idf_cache: Dict[str, float] = {}
        for cid, tf, vkey in plist:
            idf = _idf_cache.get(vkey)
            if idf is None:
                df = _DF_BY_VER.get(vkey, {}).get(bg, 0)
                n_ver = _N_BY_VER.get(vkey, N_CHUNKS)
                idf = 1.0 + math.log((n_ver + 1.0) / (df + 1.0))
                _idf_cache[vkey] = idf
            scores[cid] = scores.get(cid, 0) + idf * (1.0 + min(tf, 2) * 0.1)

    if qgrams:
        n = len(qgrams)
        scores = {k: v / n for k, v in scores.items()}

    # 长度归一化 + 脂批降权（与 S32 一致）
    _MIN_LEN = 60
    _SRC_W = {"text": 1.0, "pizhu": 0.6}
    adj = {}
    for cid, sc in scores.items():
        c = _chunks.get(cid) or {}
        L = len(c.get("text") or "")
        s = c.get("source") or "text"
        w = _SRC_W.get(s, 1.0)
        if 0 < L < _MIN_LEN:
            w *= (L / _MIN_LEN)
        adj[cid] = sc * w

    ranked = sorted(adj.items(), key=lambda x: -x[1])[:topk]
    return ([{"chunk_id": c, "score": round(v, 4)} for c, v in ranked],
            len(qgrams), hit)


def search_vector(query: str, topk: int = 30,
                  version: Optional[str] = None, encoder=None) -> List[Dict]:
    """向量检索（余弦相似度）。encoder 由调用方传入（避免重复加载 ONNX）"""
    load_all()
    if _vec_mat is None or encoder is None or not _vec_ids:
        return []
    try:
        qv = encoder([query])[0]
    except Exception:
        return []
    import numpy as np
    qv = np.asarray(qv, dtype="float32")
    nrm = np.linalg.norm(qv)
    if nrm == 0:
        return []
    qv = qv / nrm
    sims = _vec_mat @ qv

    out = []
    for i in np.argsort(-sims)[:max(topk * 4, 60)]:
        cid = _vec_ids[i]
        c = _chunks.get(cid)
        if not c:
            continue
        # 版本过滤（与 HBase 版一致）
        if version:
            src = c.get("source")
            if src == "text" and c.get("version") != version:
                continue
            if src == "pizhu":
                pv = c.get("pizhu_versions") or []
                if version == "fuqinju" and not any(x in ("fuqinju", "zhipi") for x in pv):
                    continue
                if version == "guiyouben" and "guiyouben" not in pv:
                    continue
                if version == "chenggao":
                    continue
        out.append({"chunk_id": cid, "score": round(float(sims[i]), 4)})
        if len(out) >= topk:
            break
    return out


def table(name: str):
    """
    兼容层：HBase 模式下 `hbase().table("hlm_xxx")` 的调用点，
    在本地模式下走这里。仅支持 hlm_chunks / hlm_vectors 的最小用法。
    """
    if name == "hlm_chunks":
        return _LocalTable("chunks")
    if name == "hlm_vectors":
        return _LocalTable("vectors")
    raise RuntimeError(f"本地模式不支持表: {name}")


class _LocalTable:
    """模拟 happybase Table 的最小接口（scan/row），供 load_chunks 等函数复用"""

    def __init__(self, kind):
        self.kind = kind

    def scan(self, batch_size=500):
        load_all()
        if self.kind == "chunks":
            for c in _chunks.values():
                rk = c.get("rowkey", "").encode()
                yield rk, {
                    b"d:text": (c.get("text") or "").encode(),
                    b"d:source": (c.get("source") or "text").encode(),
                    b"m:version": (c.get("version") or "").encode(),
                    b"m:hui": str(c.get("hui") or 0).encode(),
                    b"m:huimu": (c.get("huimu") or "").encode(),
                    b"m:para_start": str(c.get("para_start") or 0).encode(),
                    b"m:para_end": str(c.get("para_end") or 0).encode(),
                    b"m:seg": str(c.get("seg") or 0).encode(),
                    b"d:char_count": str(c.get("char_count") or 0).encode(),
                    b"m:pizhu_versions": json.dumps(c.get("pizhu_versions") or [],
                                                    ensure_ascii=False).encode(),
                    b"m:pizhu_page": (c.get("pizhu_page") or "").encode(),
                    b"m:src_tag": (c.get("src_tag") or "").encode(),
                    b"m:origin": (c.get("origin") or "").encode(),
                    b"m:pizhu_ptype": (c.get("pizhu_ptype") or "").encode(),
                    b"m:partition": (c.get("partition") or "").encode(),
                    b"m:strategy_rev": (c.get("strategy_rev") or "").encode(),
                }
        elif self.kind == "vectors":
            import numpy as np
            if _vec_mat is None:
                return
            for i, cid in enumerate(_vec_ids):
                yield (f"vec|{cid}".encode(), {
                    b"v:emb": _vec_mat[i].tobytes(),
                })

    def row(self, key):
        return None
