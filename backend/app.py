# -*- coding: utf-8 -*-
"""
S4 · RAG 后端服务
==================

对应需求
  R-101  多轮对话（会话上下文）
  R-102  检索增强生成（无检索不生成）
  R-103  答案强制引用溯源（版本+回次+段号+字符区间）
  R-104  三路混合检索（关键词 + 向量 + RRF）
  R-106  流式输出（SSE）
  R-107  思考过程可选展示（DeepSeek thinking）
  R-109  问答记录持久化
  R-111  拒答与不确定性表达
  R-601  默认 DeepSeek，严格按官方协议
  R-602  多模型可切换
  R-604  Embedding 固定用本地 ONNX（bge-small-zh-v1.5，512 维），不依赖外部 embedding API
  R-607  失败降级

运行：以容器方式接入 hadoop-lab_cluster 网络
  见 pipeline/run_backend.sh

架构
----
  前端(Node) --HTTP--> 本服务(容器内)
                          ├─ 检索：HBase(hlm_terms/vectors/chunks) + numpy
                          ├─ 生成：DeepSeek / 智谱 / OpenAI 兼容
                          └─ 日志：HDFS（问答记录）+ HBase（可查索引）
"""
import os
import math
import sys
import json
import time
import uuid
import asyncio
import hashlib
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone, timedelta

import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, Field
import httpx

sys.stdout.reconfigure(encoding="utf-8")

# ============================================================
# 配置（全部可用环境变量覆盖，便于部署与演示）
# ============================================================
CFG = {
    # S38：存储后端（local = 免 Hadoop 部署 / hbase = 完整集群）
    "storage": os.getenv("HLM_STORAGE", "auto"),
    "data_dir": os.getenv("HLM_DATA_DIR", "/app/data"),
    "hbase_host": os.getenv("HLM_HBASE_HOST", "master"),
    "hbase_port": int(os.getenv("HLM_HBASE_PORT", "9090")),
    "model_dir": os.getenv("HLM_MODEL_DIR", "/data/opt/models/bge-small-zh-v1.5"),
    "emb_dim": int(os.getenv("HLM_EMB_DIM", "512")),
    "version": os.getenv("HLM_VERSION", "fuqinju"),
    "rrf_k": int(os.getenv("HLM_RRF_K", "60")),
    "topk_recall": int(os.getenv("HLM_TOPK_RECALL", "30")),
    "topk_context": int(os.getenv("HLM_TOPK_CONTEXT", "5")),
    "qa_log_dir": os.getenv("HLM_QA_LOG", "/workspace/pipeline/out/qa_logs"),
}

# ============================================================
# 模型提供方配置
# ============================================================
# 问题 5 的修复：原来只有硬编码的下拉列表，用户不能自己填 API 网址/密钥/模型名。
# 现在支持两种「协议类型」：
#
#   kind="openai"      —— OpenAI 兼容协议（DeepSeek / 智谱 / 通义 / Moonshot /
#                         Ollama / vLLM / one-api 网关 等都用这个）
#                         POST {base_url}/chat/completions
#                         Authorization: Bearer {api_key}
#
#   kind="antigravity" —— Antigravity 协议（Google 系）。
#                         端点 {base_url}/v1beta/models/{model}:generateContent
#                         请求体 {"contents":[{"role","parts":[{"text"}]}]}
#                         响应 candidates[0].content.parts[].text
#                         支持 SSE 流式（?alt=sse）
#
# 用户自定义配置持久化到 /app/providers.json（容器内），
# 通过 POST /providers 增删改，GET /providers 读。

PROVIDERS_FILE = os.getenv("HLM_PROVIDERS_FILE", "/app/providers.json")

# 内置默认（DeepSeek 官方为默认，符合要求）
PROVIDERS = {
    "deepseek-flash": {
        "kind": "openai",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-flash",
        "label": "DeepSeek · deepseek-flash",
        "key_env": "DEEPSEEK_API_KEY",
    },
    "deepseek-v4-pro": {
        "kind": "openai",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-pro",
        "label": "DeepSeek · deepseek-v4-pro",
        "key_env": "DEEPSEEK_API_KEY",
    },
    "glm-4-flash": {
        "kind": "openai",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
        "label": "智谱 · glm-4-flash",
        "key_env": "ZHIPU_API_KEY",
    },
}

# 用户自定义的 provider（从文件加载，覆盖内置同 id 项）
CUSTOM_PROVIDERS: Dict[str, Dict] = {}


def load_custom_providers():
    """从 JSON 文件加载用户自定义模型配置"""
    global CUSTOM_PROVIDERS
    if not os.path.exists(PROVIDERS_FILE):
        CUSTOM_PROVIDERS = {}
        return
    try:
        with open(PROVIDERS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        CUSTOM_PROVIDERS = data if isinstance(data, dict) else {}
        log(f"载入自定义模型 {len(CUSTOM_PROVIDERS)} 个: {list(CUSTOM_PROVIDERS)}")
    except Exception as e:
        log(f"读 {PROVIDERS_FILE} 失败: {e}")
        CUSTOM_PROVIDERS = {}


def save_custom_providers():
    try:
        os.makedirs(os.path.dirname(PROVIDERS_FILE), exist_ok=True)
        with open(PROVIDERS_FILE, "w", encoding="utf-8") as f:
            json.dump(CUSTOM_PROVIDERS, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        log(f"写 {PROVIDERS_FILE} 失败: {e}")
        return False


def all_providers() -> Dict[str, Dict]:
    """内置 + 自定义合并（自定义优先）"""
    merged = dict(PROVIDERS)
    merged.update(CUSTOM_PROVIDERS)
    return merged


def resolve_key(p: Dict) -> str:
    """
    取密钥。优先级：
      1. provider 自身带的 api_key（用户在前端填的，存在 providers.json）
      2. key_env 指定的环境变量
    """
    if p.get("api_key"):
        return p["api_key"]
    env = p.get("key_env")
    return os.getenv(env, "") if env else ""

DEFAULT_MODEL = os.getenv("HLM_DEFAULT_MODEL", "deepseek-flash")

# 进入 RAG 索引的全部语料版本（问题 7：三版本都进检索）
ROOT_VERSIONS = ["fuqinju", "chenggao", "guiyouben"]

# 脂批的隔离版本（s10 三源统一后新增）
#   fuqinju   → 抚琴居校本内的脂批（3,217 条，PDF 码点重解码，版本归属最可信）
#   guiyouben → 癸酉本自带批语（222 条，含后28回内嵌批 / 回前批 / 回末评 / 前80回摘录）
#   zhipi     → 脂批大全（5,754 条，独立汇编，补前两源之缺）
PIZHU_VERSIONS = ["fuqinju", "guiyouben", "zhipi"]

VERSION_LABELS = {
    "fuqinju": "抚琴居校本",
    "chenggao": "程高本",
    "guiyouben": "癸酉本",
    "zhipi": "脂批大全",
}

# 「选中某个正文版本时，哪些脂批来源参与检索」
#   fuqinju  选中 → 抚琴居脂批 + 脂批大全（都是脂评本系统，互补）
#   guiyouben 选中 → 癸酉本批语（癸酉本独有）
#   chenggao 选中 → 无（程高本是删改本，没有脂批）
PIZHU_SOURCES_FOR = {
    "fuqinju": ["fuqinju", "zhipi"],
    "guiyouben": ["guiyouben"],
    "chenggao": [],
    None: PIZHU_VERSIONS,
}

app = FastAPI(title="红楼智问 RAG API", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ============================================================
# HBase / 向量 / 分词
# ============================================================
import threading

_conn = None
_conn_lock = threading.Lock()
_chunk_cache: Dict[str, Dict[str, Any]] = {}
_vec_cache: Dict[str, Any] = {"ids": None, "mat": None}
_encoder = None


def _new_conn():
    import happybase
    # 关键：table_prefix=b"" + table_prefix_separator=b""，否则会拼成 _hlm_xxx
    return happybase.Connection(
        CFG["hbase_host"], port=CFG["hbase_port"], timeout=600000,
        table_prefix=b"", table_prefix_separator=b"")


def hbase(reset: bool = False):
    """
    拿 HBase 连接。

    **踩坑记录（重要）**：
      happybase 的 Connection 底层是 thriftpy2 的 TSocket，**不是线程安全的**。
      FastAPI 默认线程池里多个请求共享同一个连接 →
      `TTransportException: TSocket read 0 bytes`（Thrift 流被并发写坏）。
      实测表现：单个请求正常，一并发（或上一个请求异常后）就 500。

    修法：
      1. 加锁保护「首次创建」
      2. reset=True 时丢弃坏连接、重建
      3. 调用方用 with_hbase_retry() 包一层，失败自动重连重试
    """
    global _conn
    with _conn_lock:
        if reset and _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
            _conn = None
        if _conn is None:
            _conn = _new_conn()
        return _conn


def with_hbase_retry(fn, retries: int = 3):
    """
    执行一个用到 HBase 的函数，遇到 Thrift 传输异常自动重连重试。

    为什么需要：Thrift 连接一旦坏掉（网络抖动、服务重启、并发写坏），
    后续所有调用都会失败，必须显式重连。
    """
    last = None
    for i in range(retries):
        try:
            return fn()
        except Exception as e:
            msg = str(e)
            if ("TTransportException" in msg or "TSocket" in msg
                    or "EOF" in msg or "Broken pipe" in msg):
                last = e
                log(f"HBase 连接异常，重连重试 {i + 1}/{retries}: {msg[:80]}")
                hbase(reset=True)
                time.sleep(0.4 * (i + 1))
                continue
            raise
    raise last if last else RuntimeError("HBase retry failed")


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


def get_encoder():
    """本地 ONNX bge-small-zh 编码器（懒加载 + 缓存）"""
    global _encoder
    if _encoder is not None:
        return _encoder
    import onnxruntime as ort
    import re

    vocab = {}
    for i, line in enumerate(open(os.path.join(CFG["model_dir"], "vocab.txt"),
                                  encoding="utf-8")):
        vocab[line.rstrip("\n")] = i
    cls_id, sep_id = vocab.get("[CLS]", 101), vocab.get("[SEP]", 102)
    unk_id, pad_id = vocab.get("[UNK]", 100), vocab.get("[PAD]", 0)
    sess = ort.InferenceSession(os.path.join(CFG["model_dir"], "model.onnx"),
                                providers=["CPUExecutionProvider"])
    MAX_LEN = 512

    def encode(texts):
        seqs = []
        for t in texts:
            toks = re.findall(r"[\u4e00-\u9fff]|[a-z]+|[0-9]+|[^\sa-z0-9\u4e00-\u9fff]",
                              t.lower())
            ids = [cls_id] + [vocab.get(x, unk_id) for x in toks][:MAX_LEN - 2] + [sep_id]
            seqs.append(ids)
        maxlen = max(len(s) for s in seqs)
        input_ids = np.full((len(seqs), maxlen), pad_id, dtype=np.int64)
        attn = np.zeros((len(seqs), maxlen), dtype=np.int64)
        tt = np.zeros((len(seqs), maxlen), dtype=np.int64)
        for i, s in enumerate(seqs):
            input_ids[i, :len(s)] = s
            attn[i, :len(s)] = 1
        hidden = sess.run(None, {"input_ids": input_ids,
                                 "attention_mask": attn,
                                 "token_type_ids": tt})[0]
        mask = attn[:, :, None].astype(np.float32)
        emb = (hidden * mask).sum(axis=1) / np.maximum(mask.sum(axis=1), 1e-9)
        emb = emb / np.maximum(np.linalg.norm(emb, axis=1, keepdims=True), 1e-9)
        return emb.astype(np.float32)

    _encoder = encode
    return _encoder


# ============================================================
# S38：存储后端切换（local / hbase）
# ============================================================
def _data_source_info():
    """数据源探测详情（供 /health 展示，便于排错）"""
    try:
        import data_source
        return data_source.describe()
    except Exception as e:
        return {"source": "unknown", "error": str(e)}


def use_local_storage() -> bool:
    """
    是否走「文件数据源」路径（HDFS 或 本地文件），而非 HBase。

    返回值语义：
      True  → 用 data_source 层读 JSONL（HDFS 流 或 本地文件）
      False → 用 HBase 表

    判定优先级（S39 重写）：
      1. HLM_STORAGE=local  → 强制文件路径
      2. HLM_STORAGE=hbase  → 强制 HBase
      3. auto（默认）：
         a) 先看 data_source 能否解析出 hdfs / local
            —— 能就用文件路径（**版本无关，最稳**）
         b) 都不行才试 HBase（版本风险高）

    为什么把 HBase 降为最后兜底：
      同学环境里 HBase 版本不一致的风险最高（3.x 已移除 Thrift1，
      happybase 完全连不上）。而 HDFS 的 WebHDFS 是稳定 HTTP 接口，
      本地文件更不用说。所以文件路径优先。
    """
    mode = (CFG.get("storage") or "auto").lower()
    if mode == "local":
        return True
    if mode == "hbase":
        return False

    # auto：优先文件数据源
    global _storage_probed, _storage_is_local
    if not _storage_probed:
        _storage_probed = True
        try:
            import data_source
            src = data_source.resolve_source()
        except Exception as e:
            src = "none"
            log(f"  数据源探测异常: {e}")

        if src in ("hdfs", "local"):
            _storage_is_local = True
            log(f"  存储后端: 文件数据源（{src}）")
        else:
            # 文件源不可用 → 退回 HBase
            try:
                hbase().table("hlm_chunks").row(b"__probe__")
                _storage_is_local = False
                log("  存储后端: HBase（文件源不可用，已退回 HBase）")
            except Exception as e:
                _storage_is_local = True
                log(f"  存储后端: 文件数据源（HBase 也不可用: {type(e).__name__}）")
    return _storage_is_local


_storage_probed = False
_storage_is_local = False


def _local():
    """懒加载本地存储模块"""
    import storage_local
    return storage_local


def load_chunks():
    """全量载入 chunk 元数据（1,035 行，内存常驻）"""
    global _chunk_cache
    if _chunk_cache:
        return _chunk_cache
    # S38/S39：文件类数据源（HDFS 或 本地文件）直接读 JSONL
    if use_local_storage():
        _chunk_cache = _local().get_chunks()
        import data_source
        src = data_source.resolve_source()
        log(f"载入 chunk 元数据（{src}）: {len(_chunk_cache):,} 条")
        return _chunk_cache
    def _scan_chunks():
        tb = hbase().table("hlm_chunks")
        return list(tb.scan(batch_size=500))

    for k, d in with_hbase_retry(_scan_chunks):
        # rowkey 有两种：
        #   正文  fuqinju|001|<chunk_id>            （3 段）
        #   脂批  fuqinju|pizhu|001|<chunk_id>      （4 段，S5 补强后新增）
        parts = k.decode().split("|")
        # 两种 rowkey：
        #   <ver>|text|<hui>|<cid>   正文（三版本）
        #   <ver>|pizhu|<hui>|<cid>  脂批
        if len(parts) >= 4:
            source = parts[1]
            cid = parts[3]
        else:
            source = d.get(b"d:source", b"text").decode() or "text"
            cid = parts[-1]
        try:
            pv = json.loads(d.get(b"m:pizhu_versions", b"[]").decode() or "[]")
        except Exception:
            pv = []
        _chunk_cache[cid] = {
            "chunk_id": cid,
            "rowkey": k.decode(),
            "source": source,
            "pizhu_versions": pv,
            "pizhu_page": d.get(b"m:pizhu_page", b"").decode(),
            "src_tag": d.get(b"m:src_tag", b"").decode(),
            "origin": d.get(b"m:origin", b"").decode(),
            "pizhu_ptype": d.get(b"m:pizhu_ptype", b"").decode(),
            "version": d.get(b"m:version", b"").decode(),
            "hui": int(d.get(b"m:hui", b"0").decode() or 0),
            "huimu": d.get(b"m:huimu", b"").decode(),
            "para_start": int(d.get(b"m:para_start", b"0").decode() or 0),
            "para_end": int(d.get(b"m:para_end", b"0").decode() or 0),
            "seg": int(d.get(b"m:seg", b"0").decode() or 0),
            "char_count": int(d.get(b"d:char_count", b"0").decode() or 0),
            "partition": d.get(b"m:partition", b"").decode(),
            "strategy_rev": d.get(b"m:strategy_rev", b"").decode(),
            "text": d.get(b"d:text", b"").decode("utf-8"),
        }
    log(f"载入 chunk 元数据: {len(_chunk_cache):,} 条")
    return _chunk_cache


def load_vectors():
    """向量常驻内存（把 S2 的 383 ms 降到 <5 ms）"""
    global _vec_cache
    if _vec_cache["mat"] is not None:
        return _vec_cache
    # S38：本地模式直接用内存矩阵
    if use_local_storage():
        m = _local()
        m.load_all()
        if m._vec_mat is not None:
            _vec_cache = {"ids": m._vec_ids, "mat": m._vec_mat,
                          "srcs": m._vec_srcs, "vers": None}
            log(f"载入向量矩阵（本地）: {m._vec_mat.shape}")
            return _vec_cache
        _vec_cache = {"ids": None, "mat": None}
        return _vec_cache
    def _scan_vectors():
        tb = hbase().table("hlm_vectors")
        return list(tb.scan(batch_size=500))

    ids, mats, srcs, vers = [], [], [], []
    for k, d in with_hbase_retry(_scan_vectors):
        blob = d.get(b"v:emb")
        if not blob or len(blob) != CFG["emb_dim"] * 4:
            continue
        parts = k.decode().split("|")
        if len(parts) >= 4:
            srcs.append(parts[1])
            cid = parts[3]
        else:
            srcs.append("text")
            cid = parts[-1]
        ids.append(cid)
        mats.append(np.frombuffer(blob, dtype=np.float32))
        vers.append(parts[0] if len(parts) >= 4 else CFG["version"])
    if not ids:
        return {"ids": [], "mat": None}
    M = np.vstack(mats)
    M = M / np.maximum(np.linalg.norm(M, axis=1, keepdims=True), 1e-9)
    _vec_cache = {"ids": ids, "mat": M, "srcs": srcs, "vers": vers}
    log(f"载入向量矩阵: {M.shape}")
    return _vec_cache


# ============================================================
# 检索三路
# ============================================================
ALIAS = {
    "贾宝玉": ["贾宝玉", "宝玉", "宝二爷", "怡红公子", "绛洞花王", "富贵闲人", "混世魔王"],
    "林黛玉": ["林黛玉", "黛玉", "颦儿", "颦卿", "林妹妹", "潇湘妃子"],
    "薛宝钗": ["薛宝钗", "宝钗", "宝姐姐", "蘅芜君"],
    "王熙凤": ["王熙凤", "凤姐儿", "凤姐", "凤辣子", "琏二奶奶", "凤丫头"],
    "史湘云": ["史湘云", "湘云", "云妹妹", "枕霞旧友"],
    "贾母": ["贾母", "老太太", "史太君", "老祖宗"],
    "贾探春": ["贾探春", "探春", "三姑娘", "蕉下客"],
    "贾迎春": ["贾迎春", "迎春", "二姑娘", "菱洲"],
    "贾惜春": ["贾惜春", "惜春", "四姑娘", "藕榭"],
    "李纨": ["李纨", "大奶奶", "稻香老农", "宫裁"],
    "袭人": ["袭人", "花袭人", "珍珠"],
    "晴雯": ["晴雯"],
    "平儿": ["平儿", "平姑娘"],
    "鸳鸯": ["鸳鸯"],
    "紫鹃": ["紫鹃"],
    "香菱": ["香菱", "甄英莲", "英莲", "秋菱"],
    "贾琏": ["贾琏", "琏二爷"],
    "秦可卿": ["秦可卿", "可卿", "秦氏"],
    "贾雨村": ["贾雨村", "雨村"],
    "甄士隐": ["甄士隐", "士隐"],
}


# 这些是「物件/专名」，里面虽含人名但**不是人名**，不能被别名展开
PROTECTED_TERMS = [
    "通灵宝玉", "通灵玉", "宝玉挨打", "宝玉生日", "宝玉出家",
    "宝二爷", "怡红公子", "绛洞花王", "混世魔王", "富贵闲人",
]


def expand_aliases(text: str) -> List[str]:
    """
    别名展开：「贾宝玉」→ 7 种写法。不做这个，按人名检索几乎必然 0 命中。

    实测坑：「通灵宝玉上的铭文是什么」被展开成「宝玉/宝二爷/怡红公子」，
    召回全是宝玉的日常段落，把「通灵宝玉」这个真正的主角丢了。
    修法：先把受保护的专有名词替换成占位符，展开后再换回来。
    """
    # 1) 保护区
    slots = {}
    work = text
    for i, term in enumerate(PROTECTED_TERMS):
        if term in work:
            ph = f"\u0001P{i}\u0001"
            slots[ph] = term
            work = work.replace(term, ph)

    out = [text]
    for canon, names in ALIAS.items():
        if canon in work:
            for n in names:
                if n not in work:
                    v = work.replace(canon, n)
                    # 还原保护区
                    for ph, term in slots.items():
                        v = v.replace(ph, term)
                    if v != text and v not in out:
                        out.append(v)

    # 2) 若整句被保护完（没有任何可展开人名），至少保留原句
    return out[:8]


# 语料 chunk 总数（用于 IDF 归一；启动后由 health 校验）
N_CHUNKS = int(os.getenv("HLM_N_CHUNKS", "11780"))


def search_keyword(query: str, topk: int = 30,
                   version: Optional[str] = None) -> List[Dict]:
    # S38：本地模式用内存倒排
    if use_local_storage():
        return _local().search_keyword(query, topk, version)
    qgrams = list(bigrams(query).keys())

    def _do():
        tb = hbase().table("hlm_terms")
        scores: Dict[str, float] = {}
        hit = 0
        # 倒排 key 格式：<ver>|text|<bigram>  和  <ver>|pizhu|<bigram>
        # version=None 时扫全部三个版本（融合检索）；指定版本时只扫该版本 + 脂批
        vers = [version] if version else ROOT_VERSIONS
        prefixes = []
        for v in vers:
            prefixes.append(f"{v}|text|")
        # 脂批按来源隔离（s10 三源统一后）：
        #   选 fuqinju   → fuqinju|pizhu| + zhipi|pizhu|
        #   选 guiyouben → guiyouben|pizhu|
        #   选 chenggao  → 无
        #   不限版本      → 全部三源
        for pv in PIZHU_SOURCES_FOR.get(version, PIZHU_VERSIONS):
            prefixes.append(f"{pv}|pizhu|")
        for bg in qgrams:
            for prefix in prefixes:
                row = tb.row(f"{prefix}{bg}".encode())
                if not row:
                    continue
                hit += 1
                # S31：IDF 加权
                # 原实现所有 bigram 权重都是 1.0，导致泛用词（宝玉 df=596）
                # 与情节专有词（打死 df=24）等权，泛用词主导排序。
                # 改为 idf = log(1 + N/df)，稀有词权重高。
                df = int(row.get(b"p:df", b"0").decode() or 0)
                idf = 1.0 + math.log((N_CHUNKS + 1.0) / (df + 1.0))
                for cid, tf in json.loads(row[b"p:postings"].decode()):
                    scores[cid] = scores.get(cid, 0) + idf * (1.0 + min(tf, 2) * 0.1)
        return scores, hit

    scores, hit = with_hbase_retry(_do)
    if qgrams:
        n = len(qgrams)
        scores = {k: v / n for k, v in scores.items()}

    # S32：长度归一化 + 脂批降权
    #
    # 两个独立问题（实测数据）：
    #   ① 超短 chunk 的 bigram 密度虚高
    #      正文平均 546 字，脂批平均 30 字，差 18 倍。
    #      「宝玉为什么挨打」曾让一个 11 字的 chunk 排第一。
    #   ② 脂批是"评点"不是"情节"
    #      「宝钗为什么搬出大观园」曾让一条脂批（第16回）排第一，
    #      而真正讲这事的第77回正文没进 Top-3。
    #
    # 修法（单侧，不做完整 BM25，避免双重惩罚）：
    #   · 短 chunk 线性打折（阈值 60 字）
    #   · 脂批再乘一个整体系数 0.6
    _chunks = load_chunks()
    _MIN_LEN = 60
    _SRC_W = {"text": 1.0, "pizhu": 0.6}
    _adj = {}
    for cid, sc in scores.items():
        c = _chunks.get(cid) or {}
        _L = len(c.get("text") or "")
        _s = c.get("source") or "text"
        _w = _SRC_W.get(_s, 1.0)
        if 0 < _L < _MIN_LEN:
            _w *= (_L / _MIN_LEN)
        _adj[cid] = sc * _w
    scores = _adj

    ranked = sorted(scores.items(), key=lambda x: -x[1])[:topk]
    return [{"chunk_id": c, "score": round(s, 4)} for c, s in ranked], len(qgrams), hit


def search_vector(query: str, topk: int = 30,
                  version: Optional[str] = None) -> List[Dict]:
    """
    向量检索。version 非空时只在该版本的 chunk 里检索
    （脂批 source=pizhu 始终参与，因为只有抚琴居本有脂批）。
    """
    cache = load_vectors()
    if cache["mat"] is None:
        return []
    # S38：本地模式用 storage_local 的检索（保留版本过滤逻辑）
    if use_local_storage():
        return _local().search_vector(query, topk, version,
                                      encoder=get_encoder())
    qv = get_encoder()([query])[0]
    sims = cache["mat"] @ qv

    srcs = cache.get("srcs") or ["text"] * len(cache["ids"])
    vers = cache.get("vers") or [CFG["version"]] * len(cache["ids"])

    # 掩码：目标版本正文 + 该版本允许的脂批来源
    if version:
        allowed_pz = set(PIZHU_SOURCES_FOR.get(version, []))
        mask = np.array([
            (v == version and s != "pizhu") or (s == "pizhu" and v in allowed_pz)
            for v, s in zip(vers, srcs)
        ])
        sims = np.where(mask, sims, -2.0)

    k = min(topk, len(sims))
    idx = np.argpartition(-sims, k - 1)[:k]
    idx = idx[np.argsort(-sims[idx])]
    return [{"chunk_id": cache["ids"][i], "score": round(float(sims[i]), 4),
             "source": srcs[i], "version": vers[i]}
            for i in idx if sims[i] > -1.5]


def search_graph(query: str, topk: int = 10) -> List[Dict]:
    """
    L3 图谱检索：问「X 和 Y 是什么关系」时，从 hlm_stats 的 cooccur 找证据。
    S4 阶段先用共现数据（真实 MapReduce 产出），S5 换成完整 KG。

    S25 修复：原实现是 `c in query`（c 是**正式名**），
    但用户口语常用简称（"宝玉"而非"贾宝玉"），
    导致「宝玉和黛玉是什么关系？」这类问题永远检测不到人物、图谱通道形同虚设。
    改为按**别名表**匹配：任一别名出现在问句中，即认定该人物。
    """
    persons = [c for c, aliases in ALIAS.items()
               if any(a in query for a in aliases)]
    if len(persons) < 2:
        return []
    a, b = sorted(persons[:2])
    # S38：本地模式没有 hlm_stats 表 → 用 chunk 扫描直接算共现
    if use_local_storage():
        aliases_a0 = ALIAS.get(a, [a])
        aliases_b0 = ALIAS.get(b, [b])
        chunks0 = load_chunks()
        hits0 = []
        for cid, c in chunks0.items():
            if (c.get("source") or "text") != "text":
                continue
            text = c.get("text") or ""
            if any(x in text for x in aliases_a0) and any(x in text for x in aliases_b0):
                hits0.append(cid)
        # S38：score 口径与 HBase 一致（cnt/100，cnt=同时提到两人的 chunk 数）
        # 原来给固定 0.5 会导致 graph 通道权重失真：
        #   HBase「宝玉|黛玉」cnt=237 → score=2.37（强）
        #   本地固定 0.5（弱）→ 第3回被其它通道顶掉，Top1 错成第49回
        cnt0 = len(hits0)
        sc0 = round(cnt0 / 100.0, 4)
        return [{"chunk_id": c, "score": sc0} for c in hits0[:topk]]

    tb = hbase().table("hlm_stats")
    row = tb.row(f"cooccur|{a}|{b}".encode())
    if not row:
        return []
    cnt = int(row.get(b"s:cnt", b"0").decode() or 0)
    # 把共现次数转成"伪 chunk"参与融合：找到同时提到两人的 chunk
    #
    # S25 修复②：原实现用 `a in c["text"]`（a 是正式名"林黛玉"），
    # 但正文绝大多数地方写的是简称"黛玉"，正式名很少连续出现，
    # 结果即使共现行存在（如「林黛玉|贾宝玉」cnt=237）也匹配不到任何 chunk。
    # 改为：正文中命中两人各自的**任一别名**即算同时提到。
    aliases_a = ALIAS.get(a, [a])
    aliases_b = ALIAS.get(b, [b])
    chunks = load_chunks()
    hits = []
    for cid, c in chunks.items():
        text = c.get("text") or ""
        if any(x in text for x in aliases_a) and any(x in text for x in aliases_b):
            hits.append({"chunk_id": cid, "score": round(cnt / 100.0, 4)})
            if len(hits) >= topk:
                break
    return hits


def rrf_fuse(kw, vec, graph, k=60, topk=5, dedup_by_hui: bool = True):
    """
    RRF 融合。

    **方案 B：同回次去重**（用户选定）
    ------------------------------------
    问题：不限版本时，同一段落会在 3 个版本各召回一次，
          占掉 Top-5 里 2~3 个名额，实际只有 1~2 个不同段落可用。
    做法：同一个 (hui, para 范围) 只保留 RRF 最高的那个版本，
          其余版本作为 `other_versions` 附在保留项上（供前端做「跨版本对照」）。
    注意：脂批不参与去重（它没有段落号，且与正文是不同性质的内容）。
    """
    score: Dict[str, float] = {}
    detail: Dict[str, Dict] = {}
    for src, res in (("kw", kw), ("vec", vec), ("graph", graph)):
        for rank, r in enumerate(res, 1):
            cid = r["chunk_id"]
            score[cid] = score.get(cid, 0) + 1.0 / (k + rank)
            detail.setdefault(cid, {})[src] = r["score"]
            detail[cid][f"{src}_rank"] = rank

    ordered = sorted(score.items(), key=lambda x: -x[1])

    if not dedup_by_hui:
        fused = ordered[:topk]
        return [{"chunk_id": c, "rrf": round(s, 6), **detail[c]} for c, s in fused]

    # ============================================================
    # 方案 B 去重（用户选定），三条规则
    # ============================================================
    # 实测发现光按「同回次同段落范围」去重不够：
    #   问「莫失莫忘 仙寿恒昌」→ Top-5 里 3 条是**第8回的脂批**，
    #   脂批没有段落号，各自 chunk_id 不同 → 不会被去重，直接刷满名额。
    #
    # 三条规则：
    #   ① 同回次 + 同段落范围 → 只留 RRF 最高的版本（跨版本去重）
    #   ② 同一回最多贡献 2 个「正文」chunk（避免单回刷满）
    #   ③ 同一回最多贡献 1 个「脂批」chunk（脂批是佐证，不该占主要名额）
    #      —— 且脂批总数上限 = topk 的一半，保证正文占主导
    chunks = load_chunks()
    kept = []                          # [[cid, rrf, others, key], ...]
    seen: Dict[tuple, int] = {}        # 跨版本去重 key -> kept 下标
    per_hui_text: Dict[int, int] = {}
    per_hui_pz: Dict[int, int] = {}
    n_pz_total = 0
    MAX_TEXT_PER_HUI = 2
    MAX_PZ_PER_HUI = 1
    MAX_PZ_TOTAL = max(1, topk // 2)

    for cid, sc in ordered:
        c = chunks.get(cid)
        if not c:
            continue
        is_pz = (c.get("source") == "pizhu" or c.get("para_start", 0) < 0)
        hui = c.get("hui") or 0

        if is_pz:
            key = ("pizhu", cid)                       # 脂批不参与跨版本去重
            if per_hui_pz.get(hui, 0) >= MAX_PZ_PER_HUI:
                continue                                # 规则③
            if n_pz_total >= MAX_PZ_TOTAL:
                continue                                # 规则③上限
        else:
            key = (hui, c.get("para_start"), c.get("para_end"))
            if per_hui_text.get(hui, 0) >= MAX_TEXT_PER_HUI:
                # 已够本回正文名额：若是**不同版本**的同段落，仍记录为 other_versions
                if key in seen:
                    idx = seen[key]
                    kept_ver = chunks.get(kept[idx][0], {}).get("version")
                    if c.get("version") != kept_ver:
                        kept[idx][2].append({
                            "chunk_id": cid,
                            "version": c.get("version"),
                            "version_label": VERSION_LABELS.get(c.get("version"), c.get("version")),
                            "rrf": round(sc, 6),
                        })
                continue                                # 规则②

        if key in seen:
            idx = seen[key]
            # 只记录「不同版本」的对照；同版本重复 chunk 直接丢弃
            kept_ver = chunks.get(kept[idx][0], {}).get("version")
            if c.get("version") != kept_ver:
                kept[idx][2].append({
                    "chunk_id": cid,
                    "version": c.get("version"),
                    "version_label": VERSION_LABELS.get(c.get("version"), c.get("version")),
                    "rrf": round(sc, 6),
                })
            continue

        if len(kept) >= topk:
            continue
        seen[key] = len(kept)
        if is_pz:
            per_hui_pz[hui] = per_hui_pz.get(hui, 0) + 1
            n_pz_total += 1
        else:
            per_hui_text[hui] = per_hui_text.get(hui, 0) + 1
        kept.append([cid, sc, [], key])

    out = []
    for cid, sc, others, key in kept:
        others_sorted = sorted(others, key=lambda x: -x["rrf"])
        out.append({
            "chunk_id": cid, "rrf": round(sc, 6), **detail[cid],
            "other_versions": others_sorted,
        })
    return out


# ------------------------------------------------------------
# 查询改写（Query Rewrite）
# ------------------------------------------------------------
# 为什么要做：实测「林黛玉为什么进贾府」用纯关键词+向量检索，
# Top-5 全是第29/30/23/13回，**第3回（正文明写黛玉进府的段落）一条没进**。
# 原因：问题是"问原因"，正文是"叙述过程"，字面/语义都不直接重合。
#
# 修法：把「为什么X」改写成「X 的具体过程关键词」，
# 用同义扩展把"问原因的抽象问题"落到"有具体场景的检索词"上。
# 这是 RAG 的标准做法（HyDE / query expansion 的轻量版）。
#
# 注意：这是规则改写，不调 LLM（省 token、可解释、可测）。
# S5 会加一路「LLM 改写」并对比效果。
REWRITE_RULES = [
    # ---- 情节锚点：现代口语 → 原文用词 ----
    # 这类最容易出问题：用户用现代词，原文用文言词，直接检索必然 0 命中

    # 「宝玉挨打」= 第33回
    # ⚠️ 注意：回目写「大遭笞挞」，但**正文里没有「笞挞」二字**
    #    （正文用的是「板子」「打死」「大板」「着实打死」）
    #    而倒排索引只对正文建，不含回目 → 扩展词必须用正文真实用词。
    #    实测：chunks.jsonl 中「笞挞」仅出现在 huimu 字段，text 字段 0 次。
    (("挨打", "被打", "挨了打", "打了一顿", "毒打"),
     ["板子", "打死", "着实打死", "大板", "棍", "动弹不得"]),

    # 「被撵」= 撵出去 / 逐出 / 打发出去
    (("被撵", "撵出去", "赶出去", "被赶", "逐出"),
     ["撵出去", "撵了", "逐出", "打发出去", "撵走"]),

    # 「抄检大观园」= 第74回
    (("抄检", "搜检", "查抄"),
     ["抄检大观园", "查抄", "搜检", "绣春囊", "王善保家的"]),

    # ---- 具体事件（触发词足够具体，不会误伤）----
    # 「进贾府」必须同时出现「进」和「贾府/荣府」才算，不能只凭「为什么」
    (("进贾府", "进荣府", "进京", "投奔", "初进荣国府"),
     ["到了都中", "进入神京", "弃舟登岸", "外祖母", "轿子", "荣府门前"]),

    # ---- 结局类 ----
    (("结局", "后来怎样", "最后怎样", "下场", "最终"),
     ["死", "亡", "出家", "嫁", "散", "终"]),

    # ---- 人物身份（具体词，非泛化）----
    (("丫鬟", "丫头", "侍女"),
     ["丫鬟", "丫头", "大丫鬟", "小丫头", "屋里人"]),

    # ---- 性格 ----
    (("性格", "为人", "脾气"),
     ["笑道", "冷笑道", "叹道", "性子", "脾气", "为人"]),

    # ---- 关系 ----
    (("什么关系", "亲戚关系", "怎么称呼"),
     ["亲戚", "表", "堂", "嫁", "娶", "母", "父", "兄弟"]),
]

# ⚠️ 已删除：原来有一条
#     (("为什么", "为何", "原因", "缘由"), ["进","入","到","接","送","投","归","缘由"])
#   这条把「为什么」当成触发词，导致所有原因类问题都被注入同一批无关词，
#   实测「宝玉为什么挨打」「宝钗为什么搬出大观园」「晴雯为什么被撵」
#   全部被带偏到第67回。原因类问题不存在统一的扩展词。

# 场景扩展：问题里出现这些，补对应回次的锚点词
SCENE_HINTS = {
    "进贾府": ["黛玉方进入房时", "弃舟登岸", "进入荣府", "到了都中"],
    "进京": ["都中", "神京", "荣府"],
    # 注意：不能简单用 "宝玉" 做键 —— 会命中「通灵宝玉」「宝玉挨打」等专名，
    # 导致召回偏移。这里用更具体的触发词。
    "宝玉的日常": ["怡红院", "袭人", "晴雯", "麝月"],
    "黛玉的": ["潇湘馆", "林姑娘", "林妹妹", "颦儿"],
}

# 物件/器物类：这类问题该按「物」检索，不该按「人」检索
OBJECT_HINTS = {
    "通灵宝玉": ["通灵宝玉", "莫失莫忘", "仙寿恒昌", "那块玉", "美玉", "镌着"],
    "金锁": ["金锁", "不离不弃", "芳龄永继", "璎珞", "项圈"],
    "金麒麟": ["金麒麟", "麒麟"],
    "冷香丸": ["冷香丸", "癞头和尚", "药"],
    "风月宝鉴": ["风月宝鉴", "宝鉴", "镜子"],
}


KEYWORD_SYS_PROMPT = """你是《红楼梦》文本检索专家。用户会给你一个问题，
你要提取用于全文检索的**关键词**。

【任务】
把用户问题转换成一组检索词，要求：
1. **用原文可能出现的词**，不要用现代口语
   · 「挨打」→「笞挞」「板子」「打死」（第33回正文用「板子」「打死」）
   · 「被撵」→「撵出去」「逐出」「打发出去」
   · 「死了」→「没了」「去世」「亡故」「殡天」
2. **补全专名**：简称 → 全名
   · 宝玉 → 贾宝玉　黛玉 → 林黛玉　凤姐 → 王熙凤　宝钗 → 薛宝钗
3. **补上可能相关的人物/物件/地点**（帮助定位情节）
   · 「宝玉挨打」→ 贾政、金钏、琪官、忠顺王府
4. 关键词 3~8 个，每个 1~4 字
5. 不要输出解释性长句，只要词

【输出格式】严格输出 JSON，不要任何其他文字：
{"keywords": ["词1", "词2", "词3"], "reason": "一句话说明检索思路"}

【示例】
问题：宝玉为什么挨打？
{"keywords": ["板子", "打死", "贾政", "金钏", "琪官"], "reason": "第33回宝玉被贾政笞挞，起因是琪官与金钏两事"}
"""


async def gen_keywords(question: str, model_id: str,
                       timeout: float = 8.0) -> List[str]:
    """
    由 LLM 生成检索关键词。失败/超时返回空列表（调用方用规则改写兜底）。
    """
    import asyncio
    try:
        out = await asyncio.wait_for(
            call_llm(model_id,
                     [{"role": "system", "content": KEYWORD_SYS_PROMPT},
                      {"role": "user", "content": f"问题：{question}"}],
                     stream=False, thinking=False, temperature=0.1),
            timeout=timeout)
        raw = (out.get("content") or "").strip()
        # 容错：可能被 ```json 包裹
        raw = _re.sub(r"^```(?:json)?\s*|\s*```$", "", raw).strip()
        m = _re.search(r"\{.*\}", raw, _re.S)
        if not m:
            return []
        obj = json.loads(m.group(0))
        kws = obj.get("keywords") or []
        # 清洗：只保留中文，长度 1~8
        clean = []
        for k in kws:
            k = str(k).strip()
            if 1 <= len(k) <= 8 and _re.fullmatch(r"[\u4e00-\u9fff]+", k):
                clean.append(k)
        return clean[:8]
    except asyncio.TimeoutError:
        log(f"关键词生成超时（>{timeout}s），降级到规则改写")
        return []
    except Exception as e:
        log(f"关键词生成失败: {type(e).__name__}: {e}")
        return []


# ============================================================
# S33：关键词缓存（LRU + TTL + 落盘）
# ============================================================
# 为什么要缓存：
#   每次问答都调一次 LLM 生成关键词，约 0.8~1.2s。
#   但同一问题（或近似问题）会反复出现，尤其：
#     · 用户重复提问 / 刷新重问
#     · 多轮对话里同一实体反复出现
#     · 演示时反复走同一批示例问题
#
# 缓存策略：
#   · key = 规范化后的问题（去空白、去标点）
#   · LRU 上限 KEYWORD_CACHE_MAX（默认 500）
#   · TTL KEYWORD_CACHE_TTL（默认 7 天）
#   · 落盘到 keyword_cache.json，重启后仍有效
#   · 只缓存非空结果（LLM 失败不写缓存，下次重试）

import time as _time

KEYWORD_CACHE_MAX = int(os.getenv("HLM_KW_CACHE_MAX", "500"))
KEYWORD_CACHE_TTL = int(os.getenv("HLM_KW_CACHE_TTL", str(7 * 86400)))
_KEYWORD_CACHE_PATH = os.getenv("HLM_KW_CACHE_PATH",
                                "/workspace/pipeline/out/keyword_cache.json")

_kw_cache: Dict[str, Dict] = {}      # {norm_q: {"kws": [...], "ts": 秒}}
_KW_HIT = 0                          # 缓存命中次数（供 /health 观察）
_KW_MISS = 0                         # 未命中次数
_kw_cache_loaded = False
_kw_cache_dirty = 0


def _norm_q(q: str) -> str:
    """
    问题规范化（作为缓存 key）。

    注意：**不能过度归一**。
      · 去空白、去标点 → 安全
      · 去「为什么/是什么」→ 危险（「宝玉为什么挨打」vs「宝玉怎么挨打」可能答案不同）
    所以只做最小归一。
    """
    q = (q or "").strip()
    q = _re.sub(r"[\s\u3000]+", "", q)
    q = _re.sub(r"[？?！!。，,、；;：:]+$", "", q)
    return q


def _kw_cache_load():
    """懒加载磁盘缓存"""
    global _kw_cache_loaded, _kw_cache
    if _kw_cache_loaded:
        return
    _kw_cache_loaded = True
    try:
        if os.path.exists(_KEYWORD_CACHE_PATH):
            with open(_KEYWORD_CACHE_PATH, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                _kw_cache = d
                log(f"关键词缓存载入: {len(_kw_cache)} 条")
    except Exception as e:
        log(f"关键词缓存载入失败（忽略）: {e}")


def _kw_cache_save(force=False):
    """
    写盘。

    策略：
      · 缓存条目 ≤ 50 → 每次都写（文件小，开销可忽略，保证不丢）
      · 条目 > 50    → 每 20 次新写入刷一次（减少 IO）
    """
    global _kw_cache_dirty
    _kw_cache_dirty += 1
    if not force and len(_kw_cache) > 50 and _kw_cache_dirty < 20:
        return
    try:
        os.makedirs(os.path.dirname(_KEYWORD_CACHE_PATH), exist_ok=True)
        # 先写临时文件再 rename，避免写一半崩溃损坏
        tmp = _KEYWORD_CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_kw_cache, f, ensure_ascii=False)
        os.replace(tmp, _KEYWORD_CACHE_PATH)
        _kw_cache_dirty = 0
    except Exception as e:
        log(f"关键词缓存写盘失败（忽略）: {e}")


def _kw_cache_get(q: str):
    """返回缓存的 keywords，未命中/过期返回 None"""
    _kw_cache_load()
    k = _norm_q(q)
    ent = _kw_cache.get(k)
    if not ent:
        return None
    if _time.time() - ent.get("ts", 0) > KEYWORD_CACHE_TTL:
        _kw_cache.pop(k, None)
        return None
    return ent.get("kws")


def _kw_cache_put(q: str, kws: List[str]):
    """写缓存（LRU 淘汰）"""
    _kw_cache_load()
    if not kws:
        return
    k = _norm_q(q)
    _kw_cache[k] = {"kws": kws, "ts": int(_time.time())}
    # LRU 淘汰：超上限时按 ts 最旧的删
    if len(_kw_cache) > KEYWORD_CACHE_MAX:
        ordered = sorted(_kw_cache.items(), key=lambda x: x[1].get("ts", 0))
        for kk, _ in ordered[:len(_kw_cache) - KEYWORD_CACHE_MAX]:
            _kw_cache.pop(kk, None)
    _kw_cache_save()


def gen_keywords_sync(question: str, model_id: str,
                      timeout: float = 8.0) -> List[str]:
    """
    同步包装（retrieve 是同步函数，需要用它）。

    两种情况：
      · 无 running loop（脚本/测试）→ asyncio.run
      · 有 running loop（FastAPI）→ 新线程里 run，避免 "already running" 错误

    S33：先查缓存。命中直接返回，不打 LLM（省 0.8~1.2s）。
    """
    import asyncio
    import concurrent.futures

    # ---- 缓存命中 ----
    cached = _kw_cache_get(question)
    if cached is not None:
        global _KW_HIT, _KW_MISS
        _KW_HIT += 1
        log(f"  关键词缓存命中: {cached}")
        return cached

    _KW_MISS += 1

    try:
        asyncio.get_running_loop()
        in_loop = True
    except RuntimeError:
        in_loop = False

    if not in_loop:
        kws = asyncio.run(gen_keywords(question, model_id, timeout))
    else:
        # 已在 event loop 里 → 丢到线程池执行
        def _worker():
            return asyncio.run(gen_keywords(question, model_id, timeout))

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            kws = ex.submit(_worker).result(timeout=timeout + 3)

    # 只缓存非空结果（LLM 失败不写，下次重试）
    if kws:
        _kw_cache_put(question, kws)
    return kws


def rewrite_query(question: str) -> List[str]:
    """
    生成多个改写查询，用于多路召回。
    返回：原问题 + 若干改写变体（都去检索，取并集）
    """
    out = [question]
    extra_terms = set()

    for triggers, expands in REWRITE_RULES:
        if any(t in question for t in triggers):
            extra_terms.update(expands)

    # 器物锚点：问「通灵宝玉上的铭文」要按物检索，不能按人检索
    for key, anchors in OBJECT_HINTS.items():
        if key in question:
            extra_terms.update(anchors)

    # 场景锚点：把"进贾府"这类抽象说法落到原文里的具体表达
    for key, anchors in SCENE_HINTS.items():
        if key in question:
            extra_terms.update(anchors)

    if extra_terms:
        # 变体 1：原问题 + 扩展词（给关键词检索用）
        out.append(" ".join([question] + sorted(extra_terms)[:8]))
        # 变体 2：只留扩展词（给向量检索用，聚焦场景）
        out.append(" ".join(sorted(extra_terms)[:6]))

    # 去重保序
    seen = set()
    uniq = []
    for q in out:
        if q and q not in seen:
            seen.add(q)
            uniq.append(q)
    return uniq[:4]


# ------------------------------------------------------------
# 元数据型问题直答（评测暴露的缺陷修复）
# ------------------------------------------------------------
# 实测失败案例：
#   「第3回的回目是什么？」→ 检索到第14/6/69/39/74回 → 答"语料中未找到"
#   「全书正文一共多少字？」→ 同样答"未找到"
# 根因：答案不在**正文段落**里，在**章节元数据（huimu）与统计（hlm_stats）**里。
# 修法：先识别，直接构造 context 喂给模型（不让它去正文里找不存在的东西）。
import re as _re

_META_HUIMU = _re.compile(r"第\s*(\d{1,3})\s*回\s*(?:的)?\s*回目")
# 纯主观 / 跨作品 / 荒谬问题：应直接软拒答，而不是"先说没有、再做推断"
# 实测案例（x007）：问"如果穿越到《红楼梦》你会选谁"，
# 模型答"语料里没有…下面是我依据片段做的推断"然后长篇推断。
# 判定脚本判为"未拒答"——因为它确实长篇回答了。
# 更正确的行为：明确说这是主观问题、超出语料范围，简短建议怎么问。
_SUBJECTIVE = _re.compile(
    r"(你觉得|你认为|你会选|你会选谁|谁更(好|强|厉害|伟大)|"
    r"哪个更(好|伟大|优秀)|穿越|是不是最|算不算最|"
    r"如果.{0,6}(你|穿越)|你会怎么|你更喜欢)")
_CROSSWORK = _re.compile(
    r"(三国演义|西游记|水浒|金瓶梅|小时代|哈利波特|莎士比亚|"
    r"金庸|古龙|鲁迅|张爱玲)")

SUBJECTIVE_REPLY = (
    "这个问题超出了《红楼梦》语料可以回答的范围"
    "（可能是跨作品、纯主观、或与本书无关的话题）。\n\n"
    "本系统的定位是「基于语料的可追溯问答」——每个结论都能落到原文出处。"
    "超出本书范围的问题没有语料依据，硬答只会变成编造。\n\n"
    "**建议换个问法**，例如：\n"
    "- 「为什么说《红楼梦》在叙事上很独特？」（可引用原文论述）\n"
    "- 「小红（林红玉）在书中是个什么样的人？」（有原文依据）\n"
    "- 「脂批怎么评价小红这个人物？」（有脂批依据）"
)


_META_TOTAL = _re.compile(r"(全书|一共|总共|前\s*80\s*回).*(多少字|字数|多少万字)")
_META_COUNT_HUI = _re.compile(r"(一共|总共|共有).*(多少|几)\s*回")


def build_meta_context(question: str) -> Optional[List[Dict]]:
    """识别元数据型问题，返回构造好的 context（或 None）"""
    # --- 某回回目 ---
    m = _META_HUIMU.search(question)
    if m:
        hui = int(m.group(1))
        ch = _find_chapter_meta(hui)
        if ch:
            return [{
                "chunk_id": f"meta_huimu_{hui:03d}",
                "version": CFG["version"],
                "hui": hui,
                "huimu": ch["huimu"],
                "para_start": 0,
                "para_end": 0,
                "seg": 0,
                "char_count": len(ch["huimu"]),
                "partition": "meta",
                "strategy_rev": "meta",
                "text": (f"【章节元数据】第 {hui} 回的回目是：{ch['huimu']}\n"
                         f"（该回共 {ch.get('char_count', '?')} 字，"
                         f"{ch.get('_n_para', '?')} 段）"),
                "rrf": 1.0,
                "scores": {"meta": 1.0},
            }]
    # --- 全书字数 ---
    if _META_TOTAL.search(question):
        st = _load_stats_summary()
        if st:
            return [{
                "chunk_id": "meta_total_chars",
                "version": CFG["version"],
                "hui": 0,
                "huimu": "全书统计",
                "para_start": 0, "para_end": 0, "seg": 0,
                "char_count": 0,
                "partition": "meta", "strategy_rev": "meta",
                "text": (f"【全书统计（前80回，抚琴居校本）】\n"
                         f"- 段落字数合计：{st['total_chars']:,} 字\n"
                         f"- 仅汉字（不含标点）：{st.get('han_chars', 526673):,} 字\n"
                         f"- 含标点原文：{st.get('with_punct', 591499):,} 字\n"
                         f"- 回数：{st['n_hui']} 回（凡例另计）\n"
                         f"- 段落数：{st['n_para']:,} 段\n"
                         f"注意：三个字数口径不同，回答时须说明用的是哪个。"),
                "rrf": 1.0, "scores": {"meta": 1.0},
            }]
    # --- 哪回批语最多 / 批语统计 ---
    if _re.search(r"(哪(一)?回|哪个回).{0,8}(批语|批注|脂批).{0,6}最多", question) or \
       _re.search(r"(批语|脂批).{0,6}(最多|数量|几条|多少)", question):
        stats = _pizhu_per_hui()
        if stats:
            ordered = sorted(stats.items(), key=lambda x: -x[1])
            total = sum(stats.values())
            avg = total / len(stats)
            top = ordered[:5]
            worst = ordered[-1]
            lines = [
                f"【脂批逐回统计（真实数据：HBase hlm_chunks 中 source=pizhu 的 "
                f"{total:,} 条脂批，按回次聚合）】",
                f"- 全书脂批总数：{total:,} 条",
                f"- 覆盖回数：{len(stats)} 回",
                f"- 每回平均：{avg:.1f} 条",
                f"- **批语最多的回次：第 {top[0][0]} 回，{top[0][1]} 条**",
                f"  （是平均值的 {top[0][1] / avg:.1f} 倍）",
                "",
                "前 5 名：",
            ]
            for h, n in top:
                lines.append(f"  - 第 {h} 回：{n} 条")
            lines.append(f"\n最少：第 {worst[0]} 回，{worst[1]} 条")
            lines.append(f"\n偏斜比 max/mean = {top[0][1] / avg:.3f}")
            lines.append("\n口径说明：一条脂批可能有多个版本标注（甲戌本/庚辰本等），"
                         "此处按「条」计，不按版本重复计。")
            return [{
                "chunk_id": "meta_pizhu_per_hui", "version": CFG["version"],
                "hui": 0, "huimu": "脂批逐回统计",
                "para_start": 0, "para_end": 0, "seg": 0, "char_count": 0,
                "partition": "meta", "strategy_rev": "meta",
                "text": "\n".join(lines), "rrf": 1.0, "scores": {"meta": 1.0},
            }]
    # --- 人物频次 ---
    if _re.search(
            r"(谁|哪个人物|人物).{0,8}(提及|出现|被提).{0,6}(最多|次数)"
            r"|(提及|出现).{0,6}(最多|次数最多)", question):
        rows = _load_stat_rows("personfreq|")
        if rows:
            vals = sorted([{"name": k.decode().split("|")[1],
                            "cnt": _toint(v.get(b"s:cnt"))}
                           for k, v in rows], key=lambda x: -x["cnt"])[:10]
            lines = ["【人物提及频次（MapReduce 作业产出）】"]
            for x in vals:
                lines.append(f"  - {x['name']}：{x['cnt']:,} 次")
            return [{
                "chunk_id": "meta_personfreq", "version": CFG["version"],
                "hui": 0, "huimu": "人物统计",
                "para_start": 0, "para_end": 0, "seg": 0, "char_count": 0,
                "partition": "meta", "strategy_rev": "meta",
                "text": "\n".join(lines), "rrf": 1.0, "scores": {"meta": 1.0},
            }]

    # --- 共多少回 ---
    if _META_COUNT_HUI.search(question):
        st = _load_stats_summary()
        if st:
            return [{
                "chunk_id": "meta_n_hui",
                "version": CFG["version"], "hui": 0,
                "huimu": "全书统计", "para_start": 0, "para_end": 0, "seg": 0,
                "char_count": 0, "partition": "meta", "strategy_rev": "meta",
                "text": (f"【全书统计】抚琴居校本正文共 {st['n_hui']} 回"
                         f"（另有一篇凡例）。"),
                "rrf": 1.0, "scores": {"meta": 1.0},
            }]
    return None


_chapter_meta_cache = None


def _find_chapter_meta(hui: int) -> Optional[Dict]:
    """
    取某回的元数据。数据来源：/app/chapter_meta.json
    （构建镜像时由 pipeline 产出 COPY 进来；也可回退到 chunks 聚合）
    """
    global _chapter_meta_cache
    if _chapter_meta_cache is None:
        _chapter_meta_cache = {}
        for p in ("/app/chapter_meta.json", "/workspace/pipeline/out/chapter_meta.json"):
            if os.path.exists(p):
                try:
                    for c in json.load(open(p, encoding="utf-8")):
                        _chapter_meta_cache[c["hui"]] = c
                    log(f"载入章节元数据: {len(_chapter_meta_cache)} 回  ({p})")
                    break
                except Exception as e:
                    log(f"读 {p} 失败: {e}")
        # 回退：从 chunks 聚合（只有字数/段数，没有回目）
        if not _chapter_meta_cache:
            for cid, c in load_chunks().items():
                h = c["hui"]
                d = _chapter_meta_cache.setdefault(h, {
                    "hui": h, "huimu": c["huimu"], "char_count": 0, "_n_para": 0})
                d["char_count"] += c["char_count"]
    return _chapter_meta_cache.get(hui)


_stat_rows_cache = {}


def _toint(b, default: int = 0) -> int:
    """HBase 取出来的是 bytes，安全转 int（避免 bytes 与 int 混用报 TypeError）"""
    if b is None:
        return default
    try:
        return int(b.decode() if isinstance(b, (bytes, bytearray)) else b)
    except (ValueError, TypeError):
        return default


_pizhu_hui_cache = None


def _pizhu_per_hui():
    """
    从已载入内存的 pizhu chunk 现算「逐回脂批条数」。
    不额外跑 MapReduce：数据已经在 load_chunks() 里了，O(n) 聚合即可。
    """
    global _pizhu_hui_cache
    if _pizhu_hui_cache is not None:
        return _pizhu_hui_cache
    cc = load_chunks()
    out = {}
    for c in cc.values():
        if c.get("source") != "pizhu":
            continue
        h = c.get("hui") or 0
        if h <= 0:
            continue
        out[h] = out.get(h, 0) + 1
    _pizhu_hui_cache = out
    log(f"脂批逐回统计: {sum(out.values()):,} 条 / {len(out)} 回")
    return out


def _load_stat_rows(prefix: str):
    """扫 hlm_stats 某前缀的所有行（带缓存）"""
    if prefix in _stat_rows_cache:
        return _stat_rows_cache[prefix]

    # S38：本地模式没有 hlm_stats 表，用 stats_summary.json 尽力模拟
    if use_local_storage():
        _stat_rows_cache[prefix] = []
        log(f"本地模式：stats[{prefix}] 不可用（相关统计功能降级）")
        return []

    def _scan():
        tb = hbase().table("hlm_stats")
        return list(tb.scan(row_prefix=prefix.encode(), batch_size=500))

    try:
        rows = with_hbase_retry(_scan)
        _stat_rows_cache[prefix] = rows
        log(f"载入 stats[{prefix}] {len(rows)} 行")
        return rows
    except Exception as e:
        log(f"读 stats[{prefix}] 失败: {e}")
        return []


_stats_summary_cache = None


def _load_stats_summary() -> Optional[Dict]:
    """全书统计摘要（构建镜像时生成）"""
    global _stats_summary_cache
    if _stats_summary_cache is not None:
        return _stats_summary_cache
    for p in ("/app/stats_summary.json", "/workspace/pipeline/out/stats_summary.json"):
        if os.path.exists(p):
            try:
                _stats_summary_cache = json.load(open(p, encoding="utf-8"))
                log(f"载入全书统计: {_stats_summary_cache}")
                return _stats_summary_cache
            except Exception as e:
                log(f"读 {p} 失败: {e}")
    return None


def retrieve(question: str, topk_context: int = 5, use_graph: bool = True,
             version: Optional[str] = None, model_id: Optional[str] = None,
             use_llm_kw: bool = True):
    """
    三路检索 + RRF 融合 + 回表。

    S32：检索关键词由 **LLM 生成**（理解语义 + 红楼专名归一），
         再与规则改写取并集。LLM 失败/超时则只用规则改写。
    """
    t0 = time.time()

    # -2) 主观 / 跨作品问题：直接软拒答（评测 x007 暴露）
    if _SUBJECTIVE.search(question) or _CROSSWORK.search(question):
        return {
            "contexts": [],
            "soft_refuse": SUBJECTIVE_REPLY,
            "debug": {
                "subjective_route": True,
                "rewrites": [question], "n_queries": 0, "expanded_aliases": 0,
                "llm_keywords": [],
                "kw": {"hits": 0, "bigrams": 0, "hit_bigrams": 0, "ms": 0},
                "vec": {"candidates": 0, "ms": 0},
                "graph": {"hits": 0, "ms": 0},
                "total_ms": round((time.time() - t0) * 1000, 1),
            },
        }

    # -1) 元数据型问题：直接构造 context，不去正文里搜（评测暴露的缺陷修复）
    meta_ctx = build_meta_context(question)
    if meta_ctx:
        return {
            "contexts": meta_ctx,
            "debug": {
                "meta_route": True,
                "rewrites": [question],
                "n_queries": 0,
                "expanded_aliases": 0,
                "kw": {"hits": 0, "bigrams": 0, "hit_bigrams": 0, "ms": 0},
                "vec": {"candidates": 0, "ms": 0},
                "graph": {"hits": 0, "ms": 0},
                "total_ms": round((time.time() - t0) * 1000, 1),
            },
        }

    # 0) 查询改写（规则）+ LLM 关键词生成（S32 新增）
    rewrites = rewrite_query(question)

    # S32：先由模型理解原问题，生成检索关键词
    llm_kws: List[str] = []
    if use_llm_kw:
        _mid = model_id or CFG.get("default_model") or "deepseek-flash"
        llm_kws = gen_keywords_sync(question, _mid, timeout=8.0)
        if llm_kws:
            log(f"  LLM 关键词: {llm_kws}")
            # 组合检索：原问题+关键词、纯关键词，各作为一路改写
            rewrites.append(" ".join([question] + llm_kws))
            rewrites.append(" ".join(llm_kws))
        else:
            log("  LLM 关键词为空，仅用规则改写")

    queries = []
    for rq in rewrites:
        for eq in expand_aliases(rq):
            if eq not in queries:
                queries.append(eq)
    # 关键词本身也直接参与 L1 检索（不光是拼成句子）
    for k in llm_kws:
        if k not in queries:
            queries.append(k)
    queries = queries[:24]        # 上限，避免过多 HBase 往返

    # L1：对所有（改写 × 别名）写法都查，取并集
    kw_all: Dict[str, Dict] = {}
    total_grams = total_hit = 0
    for q in queries:
        res, ng, nh = search_keyword(q, CFG["topk_recall"], version)
        total_grams += ng
        total_hit += nh
        for r in res:
            cid = r["chunk_id"]
            if cid not in kw_all or r["score"] > kw_all[cid]["score"]:
                kw_all[cid] = r
    kw = sorted(kw_all.values(), key=lambda x: -x["score"])[:CFG["topk_recall"]]
    t1 = time.time()

    # L2：向量用「原问题 + 改写变体」一起查，取并集
    vec_all: Dict[str, Dict] = {}
    for vq in rewrites[:3]:
        for r in search_vector(vq, CFG["topk_recall"], version):
            cid = r["chunk_id"]
            if cid not in vec_all or r["score"] > vec_all[cid]["score"]:
                vec_all[cid] = r
    vec = sorted(vec_all.values(), key=lambda x: -x["score"])[:CFG["topk_recall"]]
    t2 = time.time()

    graph = search_graph(question) if use_graph else []
    t3 = time.time()

    # 方案 B：不限版本时做同回次去重；指定单版本时无需去重
    fused = rrf_fuse(kw, vec, graph, CFG["rrf_k"], topk_context,
                     dedup_by_hui=(version is None))

    chunks = load_chunks()
    ctx = []
    for r in fused:
        c = chunks.get(r["chunk_id"])
        if not c:
            continue
        ctx.append({**c, "rrf": r["rrf"], "scores": r,
                    "other_versions": r.get("other_versions", [])})

    return {
        "contexts": ctx,
        "debug": {
            "version": version or "all",
            "pizhu_sources": PIZHU_SOURCES_FOR.get(version, []),
            "llm_keywords": llm_kws,
            "rewrites": rewrites,
            "n_queries": len(queries),
            "expanded_aliases": len(queries),
            "kw": {"hits": len(kw), "bigrams": total_grams, "hit_bigrams": total_hit,
                   "ms": round((t1 - t0) * 1000, 1)},
            "vec": {"candidates": len(vec), "ms": round((t2 - t1) * 1000, 1)},
            "graph": {"hits": len(graph), "ms": round((t3 - t2) * 1000, 1)},
            "dedup_by_hui": version is None,
            "n_fused": len(fused),
            "n_other_versions": sum(len(f.get("other_versions", [])) for f in fused),
            "total_ms": round((t3 - t0) * 1000, 1),
        },
    }


# ============================================================
# Prompt 构造
# ============================================================
SYSTEM_PROMPT = """你是「红楼智问」，一个专精《红楼梦》的知识问答助手。

【回答规则】
1. **必须**基于提供的【语料片段】回答，并标注引用编号，绝不编造原文。

   ⚠️ 只有在检索结果**完全为空**时（用户消息里出现【检索说明】且明确写着
   "未找到直接相关语料"），才可以按「模型自身知识兜底模式」用通识回答，
   并必须**在开头**标注「以下基于通识知识，非原文」。

   **若片段非空（哪怕只有部分相关），绝不能说"以通识知识为主体""以下基于通识"** ——
   必须先从片段里找依据、给引用，再补充解释。
   片段覆盖不全时，说"片段未完整覆盖，以下是基于片段的推断"，而不是退回通识。
2. 每条结论后用 [1] [2] 这样的编号标注出自哪个片段。
3. 若片段间存在版本差异或矛盾，要指出。
4. 人物、回次、诗词引用必须准确。不确定就说"不确定"。
5. 回答用中文，简洁清晰。可以在结尾补充一句延伸提示（如相关回次）。

【思考语言】
如果你使用思考模式，请用**中文**思考（本系统的使用者是中文读者）。

【关于《红楼梦》版本】
本系统的语料是「抚琴居校本」（前80回），另有程高本（120回）和癸酉本（后28回）作对照。
脂批（脂砚斋等人的批语）是重要的解读材料，引用时说明来自哪个版本。

【关于"全集型"问题 —— 必须遵守】
如果问题问的是「有哪些 / 都有谁 / 一共几个 / 名单 / 全部」这类**全集**，
而提供的片段只覆盖其中一部分，你必须：

1. **明确说明这属于"分散性事实"**：
   《红楼梦》的写法是"草蛇灰线"，人物、物件、地点通常**不集中交代**，
   而是分散在全書各处。本系统的检索每次只取若干片段，天然无法覆盖全集。

2. **只说"本次检索到的"**，不要说"只有"：
   ❌ 错误：「宝玉的丫鬟只有袭人一人」
   ✅ 正确：「本次检索到的片段中，明确提到的有袭人（第3回）；
             其他常见丫鬟如晴雯、麝月、秋纹等分散在第5、19、20回等处，
             本次检索未覆盖」

3. **给出可行的补充路径**：
   · 建议查看「人物关系」页面（有 120 人物 / 253 条关系）
   · 建议分回次提问（如"第5回的丫鬟有谁"）

4. **绝不用"只有X一人/没有其他人"这种结论性表述**，
   除非你确信片段覆盖了全書（几乎不可能）。

【关于「人物身份聚合表」】
如果用户消息里出现【人物身份聚合表】，说明系统已离线统计出完整名单。
此时你必须：
  1. **以聚合表为主干回答**，把「高置信」和「中置信」的名字全部列出
  2. 「待确认」项单独说明「可能属他人，需核对」
  3. 原文片段只作为**补充证据**（引用回次），不要因为片段里没有就否认聚合表
  4. 聚合表与片段冲突时，以聚合表为准，并说明冲突

原因是：聚合表来自全书统计，而原文片段只是 Top-K 检索的局部。

【示例】
用户问：「贾宝玉的丫鬟有谁？」
若片段只提到袭人，应回答：
"《红楼梦》未集中交代宝玉的丫鬟名单，这些人物分散在全書。
 本次检索到的片段（第3回）明确提到：袭人（本名珍珠，贾母所赐）。
 其他常见丫鬟（晴雯、麝月、秋纹、碧痕等）见于第5、19、20回等处，
 本次未覆盖。可查看「人物关系」页面，或按回次分别提问。"
"""


def locate_para_in_chunk(c: Dict, question: str, keywords: List[str] = None):
    """
    在 chunk 内部定位「问题真正命中的那一段落」。

    **这是修引用跳转 bug 的核心函数。**

    原 bug：后端 citations 里的 para_id 用的是 chunk 的 `para_start`，
    即「这个 chunk 从第几段开始」。但一个 chunk 通常覆盖 5~10 段，
    用户点引用跳过去，落在 chunk 的**开头**，不是「莫失莫忘」实际所在的那一段。
    实测：「通灵宝玉上的铭文」引用 chunk para_start=8（第8回段8），
    而「莫失莫忘，仙寿恒昌」实际在第 8 回段 14-16 → 跳错 6 段。

    修法：把 chunk 文本按 "\n" 拆回段落（切分时是 "\n".join 的，可逆），
    逐段算与问题的字面相关性，取最高分那一段：
      para_index = para_start + 段内偏移
    """
    text = c.get("text", "")
    p_start = c.get("para_start", 0)
    if p_start < 0:
        return p_start          # 脂批没有段落号

    # 切分时用 "\n".join(paras)，所以能按 \n 还原
    segs = text.split("\n")
    if len(segs) <= 1:
        return p_start

    # 关键词：问题里的 bigram（中文双字）
    qgrams = list(bigrams(question).keys())
    if keywords:
        qgrams += list(keywords)

    best_i, best_score = 0, -1.0
    for i, seg in enumerate(segs):
        if not seg.strip():
            continue
        # 字面命中数（bigram 出现即记 1 分）
        sc = sum(1 for g in qgrams if g in seg)
        # 长度归一，避免长段天然占优
        sc = sc / max(len(seg) ** 0.35, 1.0)
        if sc > best_score:
            best_score, best_i = sc, i
    return p_start + best_i


def hit_para_text(c: Dict, question: str) -> str:
    """
    返回 chunk 内「命中的那一段」的**完整文本**。

    **为什么返回文本而不是只返回下标：**
    实测发现后端 chunk 的 `para_start` 与前端段落数组下标**不一致**：
      - 后端切分时把「回目行」也算作第 0 段（chapters jsonl 里 para[0] 是回目）
      - 前端 web/data 的 paragraphs 已剔除回目
      → 同一段落后端 para_start=15，前端下标是 7，偏移 8 段
    只靠下标跳转必然错位。返回文本让前端按内容匹配，天然免疫偏移。
    """
    text = c.get("text", "")
    if c.get("source") == "pizhu" or c.get("para_start", 0) < 0:
        return text[:120]
    segs = [x for x in text.split(chr(10)) if x.strip()]
    if len(segs) <= 1:
        return (segs[0] if segs else text)[:120]
    qgrams = list(bigrams(question).keys())
    best_i, best = 0, -1.0
    for i, seg in enumerate(segs):
        sc = sum(1 for g in qgrams if g in seg) / max(len(seg) ** 0.35, 1.0)
        if sc > best:
            best, best_i = sc, i
    return segs[best_i][:120]


def make_para_id(c: Dict, question: str) -> str:
    """
    生成 para_id。同时返回给前端的还有 `hit_text`（命中段落首 120 字），
    前端优先按 hit_text 匹配，匹配不到再用 para_id 兜底下标。
    """
    src = c.get("source", "text")
    if src == "pizhu" or c.get("para_start", 0) < 0:
        return f"pz_{c['chunk_id']}"
    idx = locate_para_in_chunk(c, question)
    return f"p{c['hui']}_{idx}"


def make_para_range(c: Dict, question: str) -> Dict:
    """返回命中段落的范围（用于前端高亮 chunk 覆盖范围）"""
    src = c.get("source", "text")
    if src == "pizhu" or c.get("para_start", 0) < 0:
        return {"hit_start": -1, "hit_end": -1,
                "chunk_start": -1, "chunk_end": -1}
    idx = locate_para_in_chunk(c, question)
    return {"hit_start": idx, "hit_end": idx,
            "chunk_start": c.get("para_start", 0),
            "chunk_end": c.get("para_end", 0)}


# ============================================================
# 三档知识来源策略（S25）
# ============================================================
# 背景（实测问题）：
#   问「程高本和脂批本前八十回最大的区别有哪些」→ 检索召回 8 条零散脂批，
#   模型回答「语料中未找到」。
#   但这是**版本学的通识问题**，语料里本来就没有专门论述段落，
#   正确做法是用模型自身知识回答，并说明依据来源。
#
# 三档策略：
#   档1 语料充分   → 基于片段回答 + 引用（原行为）
#   档2 语料不足但问题合理 → 用模型知识回答 + **明确标注未经语料佐证**
#   档3 超出《红楼梦》范围 / 主观 / 荒谬 → 软拒答（原行为）

# 需要「模型自身知识」兜底的问题特征
#
# ⚠️ 设计教训（S30）：
#   原版把「为什么|原因|为何|何以」也放进这个正则，导致**任何问原因的具体情节问题**
#   都被误判为「元知识型问题」，强制走通识兜底模式。
#   实测：「宝玉为什么挨打？」检索明明召回了第33回原文，却仍被标注
#        「以下基于通识知识，非原文」——这是错误标注。
#
#   规律：**「为什么」是问题形式，不是问题领域。**
#        问「宝玉为什么挨打」是情节问题（语料里有答案），
#        问「为什么后40回争议大」才是元知识问题（语料里没有）。
#   所以把「为什么/原因/为何」从本正则删除，改由「n_contexts 是否充足」来判断。
META_KNOWLEDGE_PAT = _re.compile(
    r"(版本比较|版本区别|程高本|程甲本|程乙本|脂本|脂评本|庚辰本|甲戌本|己卯本|蒙府本|戚序本|"
    r"杨藏本|列藏本|舒序本|梦稿本|郑藏本|"
    r"区别|差异|异同|版本对比|"
    r"文学史|学术史|"
    r"意义|价值|地位|影响|评价|看法|赏析|艺术|手法|风格|"
    r"主题|主旨|思想|内涵|寓意|象征|隐喻|伏笔|草蛇灰线|"
    r"续书|后四十回|高鹗|程伟元|"
    r"红学|考证|探佚|脂砚斋是谁|曹雪芹|作者|成书|流传|"
    r"总结|概括|概述|梳理|归纳)"
)

# 明确超出范围 → 直接拒答
OUT_OF_SCOPE_PAT = _re.compile(
    r"(三国|水浒|西游|金瓶梅|聊斋|论语|诗经|唐诗|宋词|"
    r"相对论|量子|编程|python|java|股票|基金|天气|"
    r"我爱你|你是谁|你叫什么|讲个笑话|写首诗)"
)


# 「需要精确计数」的问题：这类**绝不能**用通识知识糊弄，必须走统计/自定义查询
COUNT_Q_PAT = _re.compile(
    r"(出现了?(几次|多少次|多少个)|一共(有)?(几次|多少)|"
    r"频次|频率|次数|多少字|多少个字|字符数|字数是多少|"
    r"排名|前几|最多|最少|统计)"
)


def is_count_question(question: str) -> bool:
    """是否为需要精确计数的统计型问题"""
    return bool(COUNT_Q_PAT.search(question or ""))


def classify_question(question: str, n_contexts: int, n_high: int = 0) -> str:
    """
    判定问题应当走哪一档：
      'corpus'     语料充分，基于片段回答
      'knowledge'  语料不足，用模型知识兜底（明确标注）
      'refuse'     超出范围，软拒答
      'normal'     普通问题，走原流程
    """
    q = question or ""
    # 明显超范围 → 拒答
    if OUT_OF_SCOPE_PAT.search(q):
        return "refuse"
    # 精确计数型问题 → 不能靠模型知识猜，交给统计通道/自定义查询
    if is_count_question(q):
        return "count"
    # 有检索结果 → 优先语料；但若是元知识型问题，允许混合
    if n_contexts >= 3:
        if META_KNOWLEDGE_PAT.search(q):
            return "knowledge_plus_corpus"   # 两者结合
        return "corpus"
    # 检索不足
    if META_KNOWLEDGE_PAT.search(q):
        return "knowledge"
    return "refuse"


COUNT_SYS_EXTRA = """

【关于「精确统计」问题】
用户问的是需要**精确计数**的问题（如「某字出现几次」「哪回字数最多」）。

你**不能**用通识知识猜测数字，也**不能**说"无法给出"就结束。

你的回答必须：
1. **说明为什么不能靠检索回答**：Top-K 检索只覆盖若干片段，无法做全书精确计数
2. **告诉用户正确做法**：点输入框左侧的 🔬 按钮（自定义查询），
   系统会写代码在全書语料/集群上精确计算
3. 如果本次片段里有可参考的**局部数字**，可以给出并标注"仅本次片段"
4. 绝对不要编造全书精确数字
"""


KNOWLEDGE_SYS_EXTRA = """

【关于「模型自身知识」兜底模式】
当系统告诉你「本次检索未找到直接相关的语料」时，说明这是**该用通识知识回答**的问题，
典型如：红学版本差异、曹雪芹家世、脂砚斋身份、全书主旨与艺术手法、成书与流传史。

此时你必须：
1. **直接回答**，不要再说"语料中未找到"就结束
2. **开头用一句话标注来源**：
   「以下内容基于通识知识，非本次检索到的原文，供参考：」
3. 回答**具体、准确、有信息量**，不要空泛
4. 涉及具体原文引用（某回某句）时，若不确定就说明「具体回次需核对原文」
5. **结尾提示**：可查阅《红楼梦》研究资料或具体回次原文验证

绝对禁止：
· 把通识知识伪装成"原文如此"
· 用"语料中未找到"作为最终答案
"""


# ============================================================
# 全集型问题（名单 / 有哪些 / 都有谁）处理
# ============================================================
# 背景（S19 诊断）：
#   《红楼梦》不集中交代名单，17 个丫鬟分散在 56 回，
#   Top-K 检索只取 5 段，天然装不下 → 模型只能答出「袭人一人」。
#
# 解法（方案 A）：
#   识别全集型问题 → 查预建的「人物-身份」聚合表 → 把完整名单注入上下文，
#   让模型有材料可答，同时明确标注哪些是「聚合表给出的」而非「原文直证」。

import re as _re

LIST_Q_PAT = _re.compile(
    r'(有(?:哪些|谁|几个|多少)|都有(?:谁|哪些)|'
    r'一共(?:有)?(?:几个|多少)|'
    r'(?:名单|列表|列举)|'
    r'(?:是|有)(?:谁|哪些)人?)'
)

ROLE_Q_PAT = _re.compile(
    r'(?:的)?(丫鬟|丫环|丫头|侍女|婢女|小厮|奴仆|仆人|乳母|奶母|'
    r'妻|妾|儿子|女儿|兄弟|姐妹|朋友|主子|家人)'
)

# 人物别名（与 s20_1 保持一致）
PERSON_ALIASES = {
    "贾宝玉": ["贾宝玉", "宝玉", "宝二爷", "怡红公子"],
    "林黛玉": ["林黛玉", "黛玉", "林妹妹", "颦儿", "潇湘妃子"],
    "薛宝钗": ["薛宝钗", "宝钗", "宝姐姐", "蘅芜君"],
    "王熙凤": ["王熙凤", "凤姐", "凤姐儿", "琏二奶奶"],
    "贾母":   ["贾母", "老太太", "史太君", "老祖宗"],
    "王夫人": ["王夫人"],
    "贾迎春": ["贾迎春", "迎春"], "贾探春": ["贾探春", "探春"],
    "贾惜春": ["贾惜春", "惜春"],
    "史湘云": ["史湘云", "湘云", "云儿"],
    "贾环":   ["贾环", "环儿"], "贾琏": ["贾琏", "琏二爷"],
    "贾赦":   ["贾赦"], "贾政": ["贾政", "政老爷"],
    "李纨":   ["李纨"], "妙玉": ["妙玉"],
    "薛姨妈": ["薛姨妈"], "赵姨娘": ["赵姨娘"],
}

_person_roles_cache = None

# 全集型问题：聚合表里提示的「相关回次」（供检索排序参考）
ROLE_HINT_HUIS: List[int] = []


def load_person_roles() -> Dict:
    """载入预建的「人物-身份」聚合表"""
    global _person_roles_cache
    if _person_roles_cache is not None:
        return _person_roles_cache
    for p in ("/app/person_roles.json", "/workspace/web/data/person_roles.json"):
        try:
            with open(p, encoding="utf-8") as f:
                _person_roles_cache = json.load(f)
                log(f"载入人物身份表: {len(_person_roles_cache)} 人")
                return _person_roles_cache
        except Exception:
            continue
    _person_roles_cache = {}
    log("⚠ 未找到 person_roles.json，全集型问题将退化为普通检索")
    return _person_roles_cache


def detect_list_question(q: str):
    """
    识别全集型问题，返回 (人物, 身份类别) 或 None
      例：「贾宝玉的丫鬟有谁」→ ("贾宝玉", "丫鬟")
    """
    if not LIST_Q_PAT.search(q):
        return None
    m = ROLE_Q_PAT.search(q)
    if not m:
        return None
    role_raw = m.group(1)
    # 归一化身份类别
    if any(k in role_raw for k in ("丫鬟", "丫环", "丫头", "侍女", "婢女")):
        role = "丫鬟"
    elif any(k in role_raw for k in ("乳母", "奶母")):
        role = "乳母"
    elif any(k in role_raw for k in ("小厮", "奴仆", "仆人")):
        role = "奴仆"
    else:
        return None          # 只处理身份类（亲属关系走图谱通道）

    # 找人名
    for canon, aliases in PERSON_ALIASES.items():
        for al in aliases:
            if al in q:
                return (canon, role)
    return None


def build_role_context(person: str, role: str) -> Optional[str]:
    """
    把聚合表里的名单组装成一段「参考材料」，注入到 prompt。
    返回 None 表示没有数据。
    """
    roles = load_person_roles()
    d = roles.get(person)
    if not d:
        return None
    lst = d.get(role) or []
    if not lst:
        return None

    # S31：把聚合表里提到的「首见回次」记下来，供检索侧排序参考。
    # 原因：全集型问题的答案主干来自聚合表（已正确），
    #      但 Top-K 引用可能落在无关回次（实测「宝玉的丫鬟有谁」Top1 曾为第87回），
    #      观感上像是"答非所问"。
    global ROLE_HINT_HUIS
    ROLE_HINT_HUIS = sorted({int(x.get("first_hui") or 0)
                             for x in lst
                             if isinstance(x, dict) and x.get("first_hui")})

    high = [x for x in lst if x.get("conf") == "high"]
    mid = [x for x in lst if x.get("conf") == "medium"]
    low = [x for x in lst if x.get("conf") == "low"]

    def fmt(items):
        out = []
        for x in items:
            extra = ""
            if x.get("first_hui"):
                extra += f"，首见第{x['first_hui']}回"
            if x.get("cooccur_sent"):
                extra += f"，与{person}同句 {x['cooccur_sent']} 次"
            out.append(f"{x['name']}{extra}")
        return "、".join(out)

    # 用明确的列表格式（LLM 对「清单」比「备注」更敏感）
    def fmt(items):
        out = []
        for x in items:
            extra = ""
            if x.get("first_hui"):
                extra += f"（首见第{x['first_hui']}回"
                if x.get("cooccur_sent"):
                    extra += f"，与{person}同句 {x['cooccur_sent']} 次"
                extra += "）"
            out.append(f"- {x['name']}{extra}")
        return "\n".join(out)

    lines = [
        "【人物身份聚合表 · 本次问题的主要依据】",
        "",
        f"系统已离线统计出「{person}的{role}」完整名单（来源：人物关系图谱 + 全书 1-80 回句内共现）。",
        "**请直接使用下面的名单作答，不要因为原文片段没提到就省略。**",
        "",
    ]
    if high:
        lines.append(f"### {person}的{role}（已确认，{len(high)} 人）")
        lines.append(fmt(high))
        lines.append("")
    if mid:
        lines.append(f"### 较可能（{len(mid)} 人）")
        lines.append(fmt(mid))
        lines.append("")
    if low:
        lines.append(f"### 待核对（{len(low)} 人，共现统计推得，可能属他人）")
        lines.append(fmt(low))
        lines.append("")
    lines.append("回答要求：把上面所有名字列出，按「已确认 / 较可能 / 待核对」分组；")
    lines.append("原文片段仅用于补充引用回次，不作为否认名单的依据。")

    return "\n".join(lines)


def build_messages(question: str, contexts: List[Dict], history: List[Dict],
                   selected_text: Optional[str] = None,
                   role_context: Optional[str] = None,
                   knowledge_mode: Optional[str] = None) -> List[Dict]:
    parts = []
    for i, c in enumerate(contexts, 1):
        parts.append(
            f"[{i}] (第{c['hui']}回 段{c['para_start']+1}-{c['para_end']+1} "
            f"· {c['version']} · chunk={c['chunk_id']})\n{c['text']}"
        )
    ctx_block = "\n\n".join(parts) if parts else "（未检索到相关语料）"

    user_parts = []
    if selected_text:
        user_parts.append(f"【用户选中的原文】\n{selected_text}")
    # 知识兜底模式：显式告知模型片段不足，请用自身知识回答
    if knowledge_mode == 'knowledge':
        user_parts.append(
            "【检索说明】\n本次检索**未找到**与问题直接相关的语料片段"
            "（或相关度很低）。这是一个该用通识知识回答的问题，"
            "请按「模型自身知识兜底模式」的规则作答。")
    elif knowledge_mode == 'knowledge_plus_corpus':
        user_parts.append(
            "【检索说明】\n本次检索到了一些片段，但它们**不是**对该问题的系统论述。"
            "请以通识知识为主体回答，并用片段做补充佐证（能对上的就引用，对不上的不要牵强附会）。")
    # 全集型问题：先给聚合表（完整名单），再给原文片段（逐条证据）
    if role_context:
        user_parts.append(role_context)
    user_parts.append(f"【语料片段】\n{ctx_block}")
    user_parts.append(f"【问题】\n{question}")
    user_content = "\n\n".join(user_parts)

    _sys = SYSTEM_PROMPT
    if knowledge_mode in ('knowledge', 'knowledge_plus_corpus'):
        _sys = SYSTEM_PROMPT + KNOWLEDGE_SYS_EXTRA
    elif knowledge_mode == 'count':
        _sys = SYSTEM_PROMPT + COUNT_SYS_EXTRA
    msgs = [{"role": "system", "content": _sys}]
    # 多轮：保留最近 6 轮，避免上下文爆炸
    for h in history[-12:]:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            msgs.append({"role": h["role"], "content": h["content"][:1500]})
    msgs.append({"role": "user", "content": user_content})
    return msgs


# ============================================================
# LLM 调用（OpenAI 兼容，默认 DeepSeek 官方）
# ============================================================
def _msgs_to_antigravity(messages: List[Dict]) -> Dict:
    """
    把 OpenAI 风格 messages 转成 Antigravity(Google) 的 contents 结构。

    差异：
      OpenAI:  [{"role":"system"|"user"|"assistant","content":"..."}]
      Google:  {"systemInstruction":{"parts":[{"text":...}]},
                "contents":[{"role":"user"|"model","parts":[{"text":...}]}]}
      注意 assistant → model
    """
    sys_parts = []
    contents = []
    for m in messages:
        role = m.get("role")
        text = m.get("content") or ""
        if role == "system":
            sys_parts.append({"text": text})
        else:
            contents.append({
                "role": "model" if role == "assistant" else "user",
                "parts": [{"text": text}],
            })
    out = {"contents": contents or [{"role": "user", "parts": [{"text": ""}]}]}
    if sys_parts:
        out["systemInstruction"] = {"parts": sys_parts}
    return out


def _antigravity_extract(data: Dict) -> Dict:
    """从 Antigravity/Google 响应里抽正文"""
    cands = data.get("candidates") or []
    if not cands:
        return {"content": "", "reasoning": "", "model": "", "usage": {}}
    c0 = cands[0]
    parts = (c0.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts)
    usage = data.get("usageMetadata") or {}
    return {
        "content": text,
        "reasoning": "",
        "model": data.get("modelVersion", ""),
        "usage": {
            "prompt_tokens": usage.get("promptTokenCount", 0),
            "completion_tokens": usage.get("candidatesTokenCount", 0),
            "total_tokens": usage.get("totalTokenCount", 0),
        },
    }


def _antigravity_url(base: str, model: str, stream: bool) -> str:
    """构造 Antigravity 端点 URL"""
    base = base.rstrip("/")
    # 兼容用户填 https://host 或 https://host/v1beta 两种写法
    if base.endswith("/v1beta") or base.endswith("/v1"):
        root = base
    else:
        root = base + "/v1beta"
    suffix = "?alt=sse" if stream else ""
    return f"{root}/models/{model}:{'streamGenerateContent' if stream else 'generateContent'}{suffix}"


async def call_llm(model_id: str, messages: List[Dict], stream: bool = False,
                   thinking: bool = False, temperature: float = 0.3):
    provs = all_providers()
    p = provs.get(model_id)
    if not p:
        raise HTTPException(400, f"未知模型: {model_id}（可选: {list(provs)}）")
    kind = p.get("kind", "openai")
    key = resolve_key(p)
    if not key and kind != "antigravity_local":
        raise HTTPException(500, f"模型 {model_id} 缺少 API Key（请在设置里填写或配置环境变量 {p.get('key_env')}）")

    if kind == "antigravity":
        return await _call_antigravity(model_id, p, key, messages, stream, temperature)
    # 默认 OpenAI 兼容
    return await _call_openai(model_id, p, key, messages, stream,
                              thinking, temperature)


async def _call_antigravity(model_id: str, p: Dict, key: str,
                           messages: List[Dict], stream: bool,
                           temperature: float):
    """
    Antigravity / Google Generative Language 协议。

    与 OpenAI 的三个关键差异（这是用户要求「注意区分」的重点）：
      1. **端点**：/v1beta/models/{model}:generateContent（不是 /chat/completions）
      2. **请求体**：contents[{role, parts[{text}]}]，assistant→model，system 单独放
      3. **响应**：candidates[0].content.parts[].text（不是 choices[0].message.content）
      4. **密钥**：走 ?key= 查询参数或 x-goog-api-key 头，**不是** Authorization: Bearer
      5. **流式**：?alt=sse，每行 `data: {...}`，正文在 candidates[0].content.parts[0].text
    """
    url = _antigravity_url(p["base_url"], p["model"], stream)
    body = _msgs_to_antigravity(messages)
    gen_cfg = {}
    if temperature is not None:
        gen_cfg["temperature"] = temperature
    if gen_cfg:
        body["generationConfig"] = gen_cfg

    headers = {"Content-Type": "application/json",
               "x-goog-api-key": key}

    if not stream:
        async with httpx.AsyncClient(timeout=180) as cli:
            r = await cli.post(url, json=body, headers=headers)
            if r.status_code != 200:
                raise HTTPException(r.status_code, f"Antigravity 错误: {r.text[:400]}")
            return _antigravity_extract(r.json())

    async def gen():
        async with httpx.AsyncClient(timeout=300) as cli:
            async with cli.stream("POST", url, json=body, headers=headers) as r:
                if r.status_code != 200:
                    txt = await r.aread()
                    yield f"data: {json.dumps({'error': txt.decode()[:400]}, ensure_ascii=False)}\n\n"
                    return
                async for line in r.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    payload = line[6:].strip()
                    if payload in ("[DONE]", ""):
                        continue
                    try:
                        obj = json.loads(payload)
                    except Exception:
                        continue
                    ex = _antigravity_extract(obj)
                    if ex["content"]:
                        yield f"data: {json.dumps({'delta': ex['content']}, ensure_ascii=False)}\n\n"

    return gen()


async def _call_openai(model_id: str, p: Dict, key: str, messages: List[Dict],
                       stream: bool, thinking: bool, temperature: float):

    url = p["base_url"].rstrip("/") + "/chat/completions"
    body = {
        "model": p["model"],
        "messages": messages,
        "stream": stream,
        "temperature": temperature,
    }
    # DeepSeek 官方 thinking 参数（见官方文档 /guides/thinking_mode）
    # 注意：不开 thinking 时不要把字段塞进去，否则部分模型会报错
    if model_id.startswith("deepseek"):
        if thinking:
            body["thinking"] = {"type": "enabled"}
            body["reasoning_effort"] = os.getenv("HLM_REASONING_EFFORT", "medium")
        else:
            body["thinking"] = {"type": "disabled"}

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    if not stream:
        async with httpx.AsyncClient(timeout=180) as cli:
            r = await cli.post(url, json=body, headers=headers)
            if r.status_code != 200:
                raise HTTPException(r.status_code, f"LLM 错误: {r.text[:400]}")
            data = r.json()
        msg = data["choices"][0]["message"]
        return {
            "content": msg.get("content") or "",
            "reasoning": msg.get("reasoning_content") or "",
            "model": data.get("model", model_id),
            "usage": data.get("usage", {}),
        }

    async def gen():
        async with httpx.AsyncClient(timeout=300) as cli:
            async with cli.stream("POST", url, json=body, headers=headers) as r:
                if r.status_code != 200:
                    txt = await r.aread()
                    yield f"data: {json.dumps({'error': txt.decode()[:400]}, ensure_ascii=False)}\n\n"
                    return
                async for line in r.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    payload = line[6:].strip()
                    if payload == "[DONE]":
                        yield "data: [DONE]\n\n"
                        return
                    try:
                        obj = json.loads(payload)
                    except Exception:
                        continue
                    delta = obj.get("choices", [{}])[0].get("delta", {})
                    out = {}
                    if delta.get("content"):
                        out["delta"] = delta["content"]
                    if delta.get("reasoning_content"):
                        out["reasoning"] = delta["reasoning_content"]
                    if out:
                        yield f"data: {json.dumps(out, ensure_ascii=False)}\n\n"

    return gen()


# ============================================================
# API 模型
# ============================================================
class ChatReq(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    model: str = DEFAULT_MODEL
    session_id: Optional[str] = None
    history: List[Dict[str, Any]] = []
    context_text: Optional[str] = None      # 划选的原文
    context_hui: Optional[int] = None
    use_graph: bool = True
    thinking: bool = False
    stream: bool = False
    topk: int = 5
    version: Optional[str] = None      # None = 全部版本融合检索


# ============================================================
# 路由
# ============================================================
# ============================================================
# S34：关键词缓存预热
# ============================================================
# 目的：后端启动后，主动为「前端示例问题 + 常见问法」生成关键词，
#      用户第一次点击示例时就能命缓存（省 1.2s）。
#
# 设计：
#   · **后台异步执行**，不阻塞启动（否则启动慢 10s+）
#   · 只在缓存为空/条目少时预热（避免每次重启都重跑）
#   · 失败不影响主流程

# S34：预热开关（开发调试时可关，避免反复消耗 token）
#   HLM_SKIP_WARMUP=1  → 完全跳过预热
#   HLM_WARMUP_N=6     → 只预热前 N 个问题（默认全部）
SKIP_WARMUP = os.getenv("HLM_SKIP_WARMUP", "0") in ("1", "true", "yes")
WARMUP_N = int(os.getenv("HLM_WARMUP_N", "0"))     # 0 = 全部

# S35：预热清单改为从共享 JSON 读取（与前端示例同一数据源）
# 目的：避免前后端各自硬编码 —— 曾经改前端示例忘记改后端，预热白跑。
# 文件：web/data/starters.json（容器内挂载为 /app/starters.json 或从 web 目录读）
_STARTERS_PATHS = [
    "/app/starters.json",
    "/workspace/web/data/starters.json",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web", "data", "starters.json"),
]

# 兜底：文件读不到时用内置清单，保证预热不失效
_FALLBACK_WARMUP = [
    "贾宝玉的丫鬟有谁？", "林黛玉为什么进贾府？", "第19回为什么批语最多？",
    "宝玉和黛玉是什么关系？", "「千红一窟」有什么隐喻？", "前80回一共多少字？",
    "宝玉为什么挨打？", "晴雯为什么被撵？", "金钏为什么投井？",
    "黛玉葬花在第几回？", "通灵宝玉上的铭文是什么？", "宝钗扑蝶是怎么回事？",
    "刘姥姥进大观园在哪一回？", "贾宝玉的丫鬟有哪些", "宝玉和黛玉什么关系",
]


def _load_warmup_questions():
    """从共享 JSON 读取预热清单；失败则用内置兜底"""
    for p in _STARTERS_PATHS:
        try:
            if not os.path.exists(p):
                continue
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            qs = [s.get("q") for s in (d.get("starters") or []) if s.get("q")]
            qs += [q for q in (d.get("warmup_extra") or []) if q]
            # 去重保序
            seen, out = set(), []
            for q in qs:
                if q not in seen:
                    seen.add(q)
                    out.append(q)
            if out:
                log(f"  预热清单: 从 {p} 读取 {len(out)} 条")
                return out
        except Exception as e:
            log(f"  读 {p} 失败: {type(e).__name__}")
    log(f"  预热清单: 用内置兜底 {len(_FALLBACK_WARMUP)} 条")
    return list(_FALLBACK_WARMUP)


WARMUP_QUESTIONS = _load_warmup_questions()

_warmup_state = {"running": False, "done": 0, "total": 0, "skipped": False}


async def warmup_keywords():
    """后台预热关键词缓存"""
    global _warmup_state

    # 开关：HLM_SKIP_WARMUP=1 完全跳过
    if SKIP_WARMUP:
        _warmup_state["skipped"] = True
        _warmup_state["reason"] = "HLM_SKIP_WARMUP=1"
        log("  关键词预热已禁用（HLM_SKIP_WARMUP=1）")
        return

    _kw_cache_load()

    # 截断：HLM_WARMUP_N=6 只预热前 6 个
    qs = WARMUP_QUESTIONS[:WARMUP_N] if WARMUP_N > 0 else WARMUP_QUESTIONS

    if len(_kw_cache) >= len(qs):
        _warmup_state["skipped"] = True
        _warmup_state["reason"] = f"缓存已有 {len(_kw_cache)} 条"
        log(f"  关键词缓存已有 {len(_kw_cache)} 条，跳过预热")
        return

    _warmup_state.update(running=True, total=len(qs), done=0, skipped=False)
    log(f"  开始预热关键词缓存（{len(qs)} 个问题）…")

    import asyncio
    model = DEFAULT_MODEL
    for i, q in enumerate(qs, 1):
        if _kw_cache_get(q) is not None:
            _warmup_state["done"] = i
            continue
        try:
            kws = await gen_keywords(q, model, timeout=10.0)
            if kws:
                _kw_cache_put(q, kws)
        except Exception as e:
            log(f"    预热失败 [{q}]: {type(e).__name__}")
        _warmup_state["done"] = i
        await asyncio.sleep(0.1)      # 让出事件循环，避免阻塞请求

    _kw_cache_save(force=True)
    _warmup_state["running"] = False
    log(f"  预热完成: {len(_kw_cache)} 条缓存已落盘")


@app.on_event("startup")
async def startup():
    log("=" * 60)
    log("红楼智问 RAG 后端启动")
    log(f"  HBase     : {CFG['hbase_host']}:{CFG['hbase_port']}")
    log(f"  模型目录  : {CFG['model_dir']}")
    log(f"  默认模型  : {DEFAULT_MODEL}")
    load_custom_providers()
    log(f"  可用模型  : {list(all_providers())}")
    # 预热（chunk/向量）
    try:
        load_chunks()
        load_vectors()
        log("  预热完成")
    except Exception as e:
        log(f"  预热失败（首次请求时会重试）: {e}")

    # S34：后台异步预热关键词缓存（不阻塞启动）
    if SKIP_WARMUP:
        _warmup_state["skipped"] = True
        _warmup_state["reason"] = "HLM_SKIP_WARMUP=1"
        log("  关键词预热: 已禁用（HLM_SKIP_WARMUP=1）")
    else:
        import asyncio as _aio
        try:
            _aio.create_task(warmup_keywords())
            _n = WARMUP_N if WARMUP_N > 0 else len(WARMUP_QUESTIONS)
            log(f"  关键词预热任务已提交（后台执行，{_n} 个问题）")
        except Exception as e:
            log(f"  关键词预热任务提交失败: {e}")
    log("=" * 60)


@app.get("/health")
async def health():
    try:
        n_chunks = len(load_chunks())
        vec = load_vectors()
        hb = "ok"
    except Exception as e:
        n_chunks, vec, hb = -1, {"mat": None}, f"error: {e}"
    try:
        cc = load_chunks()
        n_text = sum(1 for c in cc.values() if c.get("source") == "text")
        n_pizhu = sum(1 for c in cc.values() if c.get("source") == "pizhu")
    except Exception:
        n_text = n_pizhu = 0
    provs = all_providers()
    keys = {k: bool(resolve_key(v)) for k, v in provs.items()}
    return {
        "ok": True,
        "stage": "S4-RAG后端",
        "hbase": hb,
        # S38：明确当前存储模式，便于同学排查部署问题
        "storage": "local" if use_local_storage() else "hbase",
        "storage_mode_cfg": CFG.get("storage"),
        # S39：文件数据源详情（hdfs / local / none）
        "data_source": _data_source_info(),
        "n_chunks": n_chunks,
        "n_chunks_text": n_text,
        "n_chunks_pizhu": n_pizhu,
        "n_vectors": 0 if vec["mat"] is None else int(vec["mat"].shape[0]),
        "emb_dim": CFG["emb_dim"],
        "default_model": DEFAULT_MODEL,
        "models": list(provs),
        "keys_available": keys,
    }


class ProviderReq(BaseModel):
    id: str = Field(..., min_length=1, max_length=64)
    label: str = ""
    kind: str = Field("openai", description="openai | antigravity")
    base_url: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    api_key: str = ""


@app.get("/providers")
async def get_providers():
    """
    返回全部可用模型配置（内置 + 用户自定义）。
    **不回传 api_key 明文**，只回传是否已配置。
    """
    out = []
    for pid, p in all_providers().items():
        out.append({
            "id": pid,
            "label": p.get("label") or pid,
            "kind": p.get("kind", "openai"),
            "base_url": p.get("base_url", ""),
            "model": p.get("model", ""),
            "builtin": pid in PROVIDERS,
            "has_key": bool(resolve_key(p)),
        })
    return {"default": DEFAULT_MODEL, "providers": out}


@app.post("/providers")
async def upsert_provider(req: ProviderReq):
    """新增/修改一个模型配置（持久化到 /app/providers.json）"""
    if req.kind not in ("openai", "antigravity"):
        raise HTTPException(400, "kind 只能是 openai 或 antigravity")
    cfg = {
        "kind": req.kind,
        "base_url": req.base_url.strip(),
        "model": req.model.strip(),
        "label": (req.label or req.id).strip(),
    }
    # 只有用户真填了密钥才覆盖；传空表示保留原值
    if req.api_key.strip():
        cfg["api_key"] = req.api_key.strip()
    elif req.id in CUSTOM_PROVIDERS and CUSTOM_PROVIDERS[req.id].get("api_key"):
        cfg["api_key"] = CUSTOM_PROVIDERS[req.id]["api_key"]
    CUSTOM_PROVIDERS[req.id] = cfg
    ok = save_custom_providers()
    return {"ok": ok, "id": req.id,
            "note": "已保存" if ok else "配置已生效但写文件失败（重启后会丢失）"}


@app.delete("/providers/{pid}")
async def delete_provider(pid: str):
    """删除一个用户自定义模型配置（内置的不能删）"""
    if pid not in CUSTOM_PROVIDERS:
        raise HTTPException(404, f"没有自定义配置 {pid}")
    del CUSTOM_PROVIDERS[pid]
    save_custom_providers()
    return {"ok": True, "deleted": pid}


@app.post("/providers/test")
async def test_provider(req: ProviderReq):
    """
    测试一个模型配置是否可用（发一条最短请求）。
    这是「让用户自己填 API」的必备功能 —— 填完立刻知道对不对。
    """
    tmp_id = "__test__"
    cfg = {
        "kind": req.kind, "base_url": req.base_url.strip(),
        "model": req.model.strip(), "label": "test",
        "api_key": req.api_key.strip(),
    }
    saved = dict(CUSTOM_PROVIDERS)
    CUSTOM_PROVIDERS[tmp_id] = cfg
    try:
        t0 = time.time()
        out = await call_llm(tmp_id, [{"role": "user", "content": "回答一个字：好"}],
                             stream=False, thinking=False, temperature=0)
        return {"ok": True, "elapsed_ms": round((time.time() - t0) * 1000, 1),
                "reply": (out.get("content") or "")[:100],
                "usage": out.get("usage", {})}
    except Exception as e:
        return {"ok": False, "error": str(e)[:400]}
    finally:
        CUSTOM_PROVIDERS.clear()
        CUSTOM_PROVIDERS.update(saved)


@app.get("/corpus/stats")
async def corpus_stats():
    """
    语料与索引盘点（供「语料版本」界面与文档引用）。
    数据全部来自内存缓存，不额外查 HBase。
    """
    cc = load_chunks()
    by = {}
    for c in cc.values():
        key = f"{c.get('version')}/{c.get('source', 'text')}"
        by[key] = by.get(key, 0) + 1
    # 脂批来源细分
    pz_src = {}
    for c in cc.values():
        if c.get("source") == "pizhu":
            t = c.get("src_tag") or c.get("version")
            pz_src[t] = pz_src.get(t, 0) + 1
    return {
        "total_chunks": len(cc),
        "by_version_source": by,
        "pizhu_by_source_tag": pz_src,
        "text_versions": ROOT_VERSIONS,
        "pizhu_versions": PIZHU_VERSIONS,
        "pizhu_sources_for": {str(k): v for k, v in PIZHU_SOURCES_FOR.items()},
        "version_labels": VERSION_LABELS,
    }


@app.get("/models")
async def models():
    return {"default": DEFAULT_MODEL, "models": [
        {"id": k, "label": v.get("label", k), "kind": v.get("kind", "openai"),
         "available": bool(resolve_key(v))}
        for k, v in all_providers().items()
    ]}


@app.get("/retrieve")
async def retrieve_only(q: str, topk: int = 5, version: Optional[str] = None,
                        model: Optional[str] = None):
    """只检索不生成（供调试/演示）；version 可限定语料版本"""
    r = retrieve(q, topk, True, version, model_id=model)
    return {
        "question": q,
        "contexts": [{k: v for k, v in c.items() if k != "text"} | {"snippet": c["text"][:200]}
                     for c in r["contexts"]],
        "debug": r["debug"],
    }


def save_qa_log(rec: Dict):
    """问答记录落盘（S4 落本地，S5 上传 HDFS）"""
    try:
        os.makedirs(CFG["qa_log_dir"], exist_ok=True)
        day = datetime.now().strftime("%Y%m%d")
        p = os.path.join(CFG["qa_log_dir"], f"qa_{day}.jsonl")
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        log(f"写问答日志失败: {e}")


@app.post("/chat")
async def chat(req: ChatReq):
    """核心问答接口"""
    t0 = time.time()
    session_id = req.session_id or f"s_{uuid.uuid4().hex[:12]}"

    # 1) 检索
    r = retrieve(req.question, req.topk, req.use_graph, req.version,
                 model_id=req.model)
    contexts = r["contexts"]

    # 2a) 主观问题软拒答（不调 LLM，省 token 也更可控）
    if r.get("soft_refuse"):
        answer = r["soft_refuse"]
        rec = {
            "ts": datetime.now(timezone.utc).astimezone().isoformat(),
            "session_id": session_id, "question": req.question,
            "model": req.model, "answer": answer, "citations": [],
            "refused": True, "refuse_reason": "subjective",
            "retrieval": r["debug"],
            "elapsed_ms": round((time.time() - t0) * 1000, 1),
        }
        save_qa_log(rec)
        return {"answer": answer, "citations": [], "refused": True,
                "refuse_reason": "subjective", "session_id": session_id,
                "retrieval": r["debug"], "model": req.model,
                "elapsed_ms": rec["elapsed_ms"]}

    # 2) 无检索 → 三档分流（S25）
    #    不再一律拒答：元知识型问题用模型自身知识回答
    know_mode = None
    if not contexts:
        _tier = classify_question(req.question, 0)
        if _tier == "knowledge":
            know_mode = "knowledge"
            log("无检索但属元知识型问题 → 模型知识兜底")
        elif _tier == "count":
            know_mode = "count"
            log("无检索但属精确统计型 → 引导用自定义查询")
        else:
            answer = ("在语料中没有检索到与这个问题直接相关的内容，"
                      "且该问题看起来超出了《红楼梦》文本范围。\n\n"
                      "本系统专注于《红楼梦》文本问答。建议：\n"
                      "- 换个更具体的说法（带上人物名或回次）\n"
                      "- 或直接在「原文阅读」里划选一段文字来提问")
            rec = {
                "ts": datetime.now(timezone.utc).astimezone().isoformat(),
                "session_id": session_id,
                "question": req.question,
                "model": req.model,
                "answer": answer,
                "citations": [],
                "refused": True,
                "retrieval": r["debug"],
                "elapsed_ms": round((time.time() - t0) * 1000, 1),
            }
            save_qa_log(rec)
            return {"answer": answer, "citations": [], "refused": True,
                    "refuse_reason": "out_of_scope",
                    "session_id": session_id, "retrieval": r["debug"],
                "model": req.model, "elapsed_ms": rec["elapsed_ms"]}

    # 3) 构造 prompt
    # ---- 全集型问题：注入人物身份聚合表（方案 A）----
    role_ctx = None
    _lq = detect_list_question(req.question)
    if _lq:
        _person, _role = _lq
        role_ctx = build_role_context(_person, _role)
        if role_ctx:
            log(f"全集型问题命中: {_person} 的{_role}（注入聚合表）")
            # S31：按聚合表提示的「首见回次」重排引用
            # 答案主干已由聚合表提供（正确），但 Top-K 引用可能落在无关回次，
            # 观感上像"答非所问"。把命中提示回次的 chunk 提前。
            if ROLE_HINT_HUIS:
                _hint = set(ROLE_HINT_HUIS)
                _ctxs = r.get("contexts") or []
                _hit = [c for c in _ctxs if int(c.get("hui") or 0) in _hint]
                _rest = [c for c in _ctxs if int(c.get("hui") or 0) not in _hint]
                if _hit:
                    _new_ctxs = _hit + _rest
                    r["contexts"] = _new_ctxs
                    contexts = _new_ctxs      # 关键：citations 后续用的是 contexts 变量
                    log(f"  引用重排: {len(_hit)} 条命中提示回次 {sorted(_hint)}")

    # 有检索结果时，若为元知识型问题 → 组合模式
    if know_mode is None and contexts:
        _tier = classify_question(req.question, len(contexts))
        if _tier == "knowledge_plus_corpus":
            know_mode = "knowledge_plus_corpus"
            log("元知识型问题 + 有片段 → 组合模式")
        elif _tier == "count":
            know_mode = "count"
            log("精确统计型问题 → 引导用自定义查询")

    messages = build_messages(req.question, contexts, req.history,
                              req.context_text, role_context=role_ctx,
                              knowledge_mode=know_mode)

    # 4) 生成（含降级，R-607）
    fallback_used = None
    try:
        out = await call_llm(req.model, messages, stream=False,
                             thinking=req.thinking)
    except Exception as e:
        log(f"主模型失败: {e}")
        alt = [m for m in PROVIDERS if m != req.model and os.getenv(PROVIDERS[m]["key_env"])]
        if not alt:
            raise HTTPException(502, f"所有模型均不可用: {e}")
        fallback_used = alt[0]
        log(f"降级到 {fallback_used}")
        out = await call_llm(fallback_used, messages, stream=False,
                             thinking=req.thinking)

    # 5) 引用（R-103）
    citations = []
    for i, c in enumerate(contexts, 1):
        citations.append({
            "idx": i,
            "chunk_id": c["chunk_id"],
            "version": c["version"],
            "hui": c["hui"],
            "huimu": c["huimu"],
            "para_start": c["para_start"],
            "para_end": c["para_end"],
            "para_id": make_para_id(c, req.question),
            **make_para_range(c, req.question),
            "hit_text": hit_para_text(c, req.question),
            "snippet": c["text"][:240],
            "rrf": c["rrf"],
            "scores": c["scores"],
            "source": c.get("source", "text"),
            "version_label": VERSION_LABELS.get(c.get("version"), c.get("version")),
            "pizhu_versions": c.get("pizhu_versions", []),
            "pizhu_src_tag": c.get("src_tag", ""),
            "pizhu_ptype": c.get("pizhu_ptype", ""),
            "other_versions": c.get("other_versions", []),
        })

    elapsed = round((time.time() - t0) * 1000, 1)
    rec = {
        "ts": datetime.now(timezone.utc).astimezone().isoformat(),
        "session_id": session_id,
        "question": req.question,
        "model": out.get("model", req.model),
        "fallback_used": fallback_used,
        "answer": out["content"],
        "reasoning": out.get("reasoning", "")[:4000],
        "citations": [{k: v for k, v in c.items() if k != "snippet"} for c in citations],
        "usage": out.get("usage", {}),
        "retrieval": r["debug"],
        "elapsed_ms": elapsed,
    }
    save_qa_log(rec)

    return {
        "answer": out["content"],
        "reasoning": out.get("reasoning", ""),
        "citations": citations,
        "session_id": session_id,
        "model": out.get("model", req.model),
        "fallback_used": fallback_used,
        "usage": out.get("usage", {}),
        "retrieval": r["debug"],
        "elapsed_ms": elapsed,
    }


@app.post("/chat/stream")
async def chat_stream(req: ChatReq):
    """流式问答（SSE，R-106）"""
    session_id = req.session_id or f"s_{uuid.uuid4().hex[:12]}"
    r = retrieve(req.question, req.topk, req.use_graph, req.version,
                 model_id=req.model)
    contexts = r["contexts"]

    # 无检索 → 三档分流（S25）：元知识型问题用模型自身知识回答
    know_mode = None
    if not contexts:
        _tier = classify_question(req.question, 0)
        if _tier == "knowledge":
            know_mode = "knowledge"
            log("无检索但属元知识型问题 → 模型知识兜底（stream）")
        elif _tier == "count":
            know_mode = "count"
            log("无检索但属精确统计型 → 引导用自定义查询（stream）")
        else:
            async def gen_empty():
                answer = ("在语料中没有检索到与这个问题直接相关的内容，"
                          "且该问题看起来超出了《红楼梦》文本范围。\n\n"
                          "本系统专注于《红楼梦》文本问答。建议换个更具体的说法"
                          "（带上人物名或回次），或直接在「原文阅读」里划选一段文字来提问。")
                yield f"data: {json.dumps({'delta': answer}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'meta': {'refused': True, 'refuse_reason': 'out_of_scope', 'citations': [], 'session_id': session_id}}, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
            return StreamingResponse(gen_empty(), media_type="text/event-stream")

    # ---- 全集型问题：注入人物身份聚合表（方案 A）----
    role_ctx = None
    _lq = detect_list_question(req.question)
    if _lq:
        _person, _role = _lq
        role_ctx = build_role_context(_person, _role)
        if role_ctx:
            log(f"全集型问题命中: {_person} 的{_role}（注入聚合表）")
            # S31：按聚合表提示的「首见回次」重排引用（与 /chat 端点保持一致）
            if ROLE_HINT_HUIS:
                _hint = set(ROLE_HINT_HUIS)
                _hit = [c for c in contexts if int(c.get("hui") or 0) in _hint]
                _rest = [c for c in contexts if int(c.get("hui") or 0) not in _hint]
                if _hit:
                    _new_ctxs = _hit + _rest
                    r["contexts"] = _new_ctxs
                    contexts = _new_ctxs
                    log(f"  引用重排(stream): {len(_hit)} 条命中提示回次 {sorted(_hint)}")

    # 有检索结果时，元知识型问题 → 组合模式
    if know_mode is None and contexts:
        _tier = classify_question(req.question, len(contexts))
        if _tier == "knowledge_plus_corpus":
            know_mode = "knowledge_plus_corpus"
            log("元知识型问题 + 有片段 → 组合模式（stream）")
        elif _tier == "count":
            know_mode = "count"
            log("精确统计型问题 → 引导用自定义查询（stream）")

    messages = build_messages(req.question, contexts, req.history, req.context_text,
                               role_context=role_ctx, knowledge_mode=know_mode)

    citations = []
    for i, c in enumerate(contexts, 1):
        citations.append({
            "idx": i, "chunk_id": c["chunk_id"], "version": c["version"],
            "hui": c["hui"], "huimu": c["huimu"],
            "para_start": c["para_start"], "para_end": c["para_end"],
            "para_id": make_para_id(c, req.question),
            **make_para_range(c, req.question),
            "hit_text": hit_para_text(c, req.question),
            "snippet": c["text"][:240], "rrf": c["rrf"], "scores": c["scores"],
            "source": c.get("source", "text"),
            "version_label": VERSION_LABELS.get(c.get("version"), c.get("version")),
            "pizhu_versions": c.get("pizhu_versions", []),
            "pizhu_src_tag": c.get("src_tag", ""),
            "pizhu_ptype": c.get("pizhu_ptype", ""),
            "other_versions": c.get("other_versions", []),
        })

    async def gen():
        acc = []
        try:
            stream = await call_llm(req.model, messages, stream=True,
                                    thinking=req.thinking)
            async for chunk in stream:
                if chunk.startswith("data: ") and chunk.strip() != "data: [DONE]":
                    try:
                        obj = json.loads(chunk[6:].strip())
                        if obj.get("delta"):
                            acc.append(obj["delta"])
                    except Exception:
                        pass
                yield chunk
        except Exception as e:
            log(f"流式生成失败: {e}")
            yield f"data: {json.dumps({'error': str(e)[:300]}, ensure_ascii=False)}\n\n"
        finally:
            meta = {
                "citations": citations, "session_id": session_id,
                "model": req.model, "retrieval": r["debug"],
                "answer_full": "".join(acc),
            }
            yield f"data: {json.dumps({'meta': meta}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
            save_qa_log({
                "ts": datetime.now(timezone.utc).astimezone().isoformat(),
                "session_id": session_id, "question": req.question,
                "model": req.model, "answer": "".join(acc),
                "citations": [{k: v for k, v in c.items() if k != "snippet"} for c in citations],
                "retrieval": r["debug"],
            })

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# ---------- 统计问答（R-208：就某数据提问）----------
@app.get("/stats/ask")
async def stats_ask(topic: str, question: Optional[str] = None):
    """
    统计界面的「就此提问」后端支持：把统计事实直接拼进 prompt。
    比走全文检索更准 —— 因为「第19回为什么批语最多」这类问题在正文里搜不到。
    """
    # S38：本地模式无 hlm_stats 表 → 该接口降级
    if use_local_storage():
        return {"ok": False,
                "reason": "local_storage",
                "message": "本地模式（免 Hadoop）不提供该统计接口；"
                           "请使用完整 HBase 模式，或在统计界面直接查看图表。"}

    tb = hbase().table("hlm_stats")
    facts = {}
    if "批语" in topic or "回" in topic:
        # 逐回统计
        rows = {}
        for k, d in tb.scan(row_prefix=b"chunkstat|", batch_size=500):
            hui = int(k.decode().split("|")[1])
            rows[hui] = {
                "n_chunk": int(d.get(b"s:n_chunk", b"0").decode() or 0),
                "n_bytes": int(d.get(b"s:n_bytes", b"0").decode() or 0),
                "n_chars": int(d.get(b"s:n_chars", b"0").decode() or 0),
            }
        if rows:
            top = sorted(rows.items(), key=lambda x: -x[1]["n_chunk"])[:5]
            facts["chunk_per_hui_top5"] = [{"hui": h, **v} for h, v in top]
            facts["total_hui"] = len(rows)
    if "人物" in topic or "谁" in topic:
        rows = []
        for k, d in tb.scan(row_prefix=b"personfreq|", batch_size=500):
            rows.append((k.decode().split("|")[1],
                         int(d.get(b"s:cnt", b"0").decode() or 0)))
        facts["person_top10"] = [{"name": n, "count": c}
                                 for n, c in sorted(rows, key=lambda x: -x[1])[:10]]
    return {"topic": topic, "facts": facts,
            "note": "统计事实来自 MapReduce 作业产出，存于 HBase hlm_stats"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("HLM_PORT", "8000")))


# ============================================================
# S21 · 自定义查询：AI 写代码并执行
# ============================================================
# 安全设计见 sandbox.py：
#   AST 白名单 + 受限 builtins + 语料 API 白名单 + prlimit/timeout

import re
from sandbox import check_code, run_sandboxed, SecurityError

QUERY_SYS_PROMPT = """你是一个《红楼梦》语料分析编程助手。

用户想查询一个统计/计算类问题，你要**写 Python 代码**实现。

【可用 API（只能通过它们读数据）】
- load_all_text(ver="fuqinju") -> str
    读某版本全书正文（拼接字符串）。ver ∈ VERSIONS
- load_chapter(ver, hui) -> str
    读某一回全文，hui 从 1 开始
- load_chapters(ver) -> list[dict]
    逐回结构化：[{"hui":1,"huimu":"...","paragraphs":[...]}, ...]
- load_pizhu(ver="fuqinju") -> list[str]
    读该版本批语文本列表
- load_person_roles() -> dict
    人物身份表 {人物: {身份类别: [{"name":...}]}}
- load_stats() -> dict
    统计汇总
- VERSIONS = ["fuqinju","chenggao","guiyouben"]
- VERSION_LABELS = {"fuqinju":"抚琴居校本",...}

【Hadoop API（真实访问集群，只读）】
- hdfs_ls(path) -> list[dict]
    列出 HDFS 目录。path 必须在 /hlm/ 下
    返回 [{"path":..., "is_dir":..., "size":...}, ...]
- hdfs_cat(path, max_bytes=8388608) -> {"text":..., "bytes":..., "file_size":..., "truncated":...}
    读 HDFS 文本文件（有字节上限，超限截断）
- hdfs_count_lines(path) -> int
    数行数（流式，适合大文件）
- hdfs_du(path) -> {"size":..., "path":...}
    查文件/目录大小
- hbase_count(table) -> int
    表行数。table ∈ {"hlm_chunks","hlm_vectors","hlm_terms","hlm_wordfreq","hlm_stats"}
- hbase_get(table, rowkey) -> dict
    按 rowkey 取一行
- hbase_scan(table, prefix="", limit=20) -> list[dict]
    前缀扫描（limit 上限 2000）
- hadoop_status() -> dict
    集群状态：HDFS 容量、DataNode 数、各表行数
- HDFS_ALLOWED_PREFIXES / HBASE_ALLOWED_TABLES
    可访问的路径前缀与表白名单

【何时用 Hadoop API】
- 问「HDFS 里有什么文件」「某个文件多大/多少行」→ 用 hdfs_*
- 问「HBase 里有多少 chunk / 向量」→ 用 hbase_*
- 问「集群状态如何」→ 用 hadoop_status()
- 问「全文中出现了几次某字」→ 用 load_all_text（本地副本，更快）
- 需要原始 chunk 结构 / 元数据 → 用 hbase_scan

【硬性约束（违反会被安全检查拒绝）】
1. **禁止 import 任何模块**（包括标准库）
2. **禁止 open()**，读数据只能用上面 API
3. 禁止 exec / eval / compile / __import__
4. 禁止 while True（死循环）
5. 结果必须赋给变量 `result`（int/float/str/list/dict 均可）
6. 代码不超过 30 行，不要定义 class
7. 可以用 print() 输出中间过程（会被收集展示）
8. 不要用 f-string 里的复杂表达式，保持简单

【输出格式】
只输出一个 ```python 代码块，代码块外不要有任何文字。

【示例2：用 Hadoop 查集群】
用户：HDFS 里存了哪些数据？
```python
rows = hdfs_ls("/hlm/warehouse/")
result = [{"path": r["path"], "size": r["size"]} for r in rows if not r["is_dir"]]
```

【示例3：用 HBase 查行数】
用户：HBase 里有多少个 chunk？
```python
result = {"hlm_chunks": hbase_count("hlm_chunks"),
          "hlm_vectors": hbase_count("hlm_vectors"),
          "hlm_terms": hbase_count("hlm_terms")}
```

【示例4：集群状态】
用户：集群现在什么状态？
```python
result = hadoop_status()
```

【示例1：本地统计（最快）】
用户：全文中出现了几次「之」字？
```python
text = load_all_text("fuqinju")
n = text.count("之")
result = {"count": n, "total": len(text), "ratio": round(n/len(text)*100, 2)}
print(f"全文 {len(text)} 字，「之」出现 {n} 次")
```
"""


class QueryReq(BaseModel):
    question: str
    model: str = "deepseek-flash"
    version: str = "fuqinju"
    max_retry: int = 1


@app.post("/query/custom")
async def query_custom(req: QueryReq):
    """
    自定义查询：让 AI 写代码并运行。

    流程：
      ① LLM 生成代码
      ② AST 安全检查
      ③ 沙箱执行（prlimit + timeout）
      ④ 失败则把错误回给 LLM 重写一次
      ⑤ LLM 解释执行结果
    """
    t0 = time.time()
    question = req.question.strip()
    if not question:
        raise HTTPException(400, "问题不能为空")

    attempts = []
    code = None
    exec_result = None

    for attempt in range(req.max_retry + 1):
        # ---- ① 生成代码 ----
        hint = ""
        if exec_result and not exec_result.get("ok"):
            hint = (f"\n\n【上一次尝试失败】\n错误：{exec_result.get('error')}\n"
                    f"上次代码：\n```python\n{code}\n```\n"
                    f"请修正后重新生成。注意只能用 API 读数据，不能 import。")

        user_msg = (f"用户想查询：{question}\n"
                    f"（默认语料版本：{req.version} = {VERSION_LABELS.get(req.version, req.version)}）"
                    f"{hint}")

        try:
            out = await call_llm(
                req.model,
                [{"role": "system", "content": QUERY_SYS_PROMPT},
                 {"role": "user", "content": user_msg}],
                stream=False, thinking=False,
            )
            raw = out.get("content") or ""
        except Exception as e:
            raise HTTPException(502, f"代码生成失败：{e}")

        # 提取 ```python 代码块
        m = re.search(r"```(?:python)?\s*\n?(.*?)```", raw, re.S)
        code = (m.group(1) if m else raw).strip()

        # ---- ②③ 安全检查 + 沙箱执行 ----
        exec_result = run_sandboxed(code)
        attempts.append({
            "attempt": attempt + 1,
            "code": code,
            "ok": exec_result.get("ok"),
            "error": exec_result.get("error"),
            "blocked": exec_result.get("blocked"),
            "elapsed": exec_result.get("elapsed"),
        })
        if exec_result.get("ok"):
            break
        log(f"自定义查询第 {attempt+1} 次失败: {exec_result.get('error')}")

    # ---- ⑤ 解释结果 ----
    explanation = ""
    if exec_result and exec_result.get("ok"):
        try:
            res_json = json.dumps(exec_result.get("result"), ensure_ascii=False, default=str)
            stdout = exec_result.get("stdout") or ""
            ex_out = await call_llm(
                req.model,
                [{"role": "system",
                  "content": ("你是《红楼梦》知识助手。用户问了一个统计问题，"
                              "系统已用代码算出结果。请用自然语言简洁解释这个结果，"
                              "不要重复代码，不要编造数据。")},
                 {"role": "user",
                  "content": (f"用户问题：{question}\n\n"
                              f"代码运行结果（JSON）：{res_json[:3000]}\n\n"
                              f"代码输出：{stdout[:1500]}")}],
                stream=False, thinking=False,
            )
            explanation = (ex_out.get("content") or "").strip()
        except Exception as e:
            explanation = f"（结果解释生成失败：{e}）"

    return {
        "question": question,
        "version": req.version,
        "ok": bool(exec_result and exec_result.get("ok")),
        "code": code,
        "result": (exec_result or {}).get("result"),
        "stdout": (exec_result or {}).get("stdout"),
        "error": (exec_result or {}).get("error"),
        "blocked": (exec_result or {}).get("blocked", False),
        "explanation": explanation,
        "attempts": attempts,
        "elapsed_ms": round((time.time() - t0) * 1000, 1),
    }


@app.on_event("shutdown")
async def _on_shutdown():
    """进程关闭前强制刷盘关键词缓存（避免丢未落盘的条目）"""
    try:
        _kw_cache_save(force=True)
        log(f"关键词缓存已刷盘: {len(_kw_cache)} 条")
    except Exception as e:
        log(f"关闭时刷盘失败: {e}")


@app.get("/keyword-cache/stats")
async def keyword_cache_stats():
    """关键词缓存统计（供运维界面观察）"""
    _kw_cache_load()
    tot = len(_kw_cache)
    hits = _KW_HIT
    miss = _KW_MISS
    rate = round(hits / (hits + miss) * 100, 1) if (hits + miss) else 0.0
    return {
        "enabled": True,
        "entries": tot,
        "max_entries": KEYWORD_CACHE_MAX,
        "ttl_seconds": KEYWORD_CACHE_TTL,
        "hits": hits,
        "misses": miss,
        "hit_rate_pct": rate,
        "path": _KEYWORD_CACHE_PATH,
        "available": os.path.exists(_KEYWORD_CACHE_PATH),
        "warmup": {
            **dict(_warmup_state),
            "enabled": not SKIP_WARMUP,
            "limit": WARMUP_N if WARMUP_N > 0 else len(WARMUP_QUESTIONS),
            "n_questions": len(WARMUP_QUESTIONS),
        },
    }


@app.post("/keyword-cache/warmup")
async def keyword_cache_warmup():
    """手动触发关键词预热（后台执行）"""
    import asyncio
    if _warmup_state.get("running"):
        return {"started": False, "reason": "已在预热中", "state": dict(_warmup_state)}
    asyncio.create_task(warmup_keywords())
    return {"started": True, "n_questions": len(WARMUP_QUESTIONS)}


@app.delete("/keyword-cache")
async def keyword_cache_clear():
    """清空关键词缓存（内存 + 磁盘）"""
    global _kw_cache, _KW_HIT, _KW_MISS
    _kw_cache_load()
    n = len(_kw_cache)
    _kw_cache = {}
    _KW_HIT = _KW_MISS = 0
    try:
        if os.path.exists(_KEYWORD_CACHE_PATH):
            os.remove(_KEYWORD_CACHE_PATH)
    except Exception as e:
        log(f"删除缓存文件失败: {e}")
    log(f"关键词缓存已清空: {n} 条")
    return {"cleared": n}


@app.get("/query/examples")
async def query_examples():
    """给前端的示例查询"""
    return {
        "examples": [
            {"q": "全文出现了几次「之」字？", "tag": "字频"},
            {"q": "前80回里「笑道」出现了多少次？", "tag": "短语"},
            {"q": "统计每一回的字数，找出最长和最短的回", "tag": "分布"},
            {"q": "全书人物提及次数前 10 名是谁？", "tag": "人物"},
            {"q": "按回次统计批语数量，哪一回最多？", "tag": "批语"},
            {"q": "统计「之乎者也」这几个字各自的出现次数", "tag": "字频"},
            {"q": "哪一回出现的「宝玉」最多？", "tag": "人物"},
            {"q": "程高本和抚琴居本的总字数差多少？", "tag": "版本对比"},
        ]
    }
