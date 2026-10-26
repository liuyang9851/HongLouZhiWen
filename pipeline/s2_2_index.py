# -*- coding: utf-8 -*-
"""
S2 数据管道 · 阶段 2：向量化 + 双写 HDFS / HBase
=================================================

对应需求
--------
  R-501  原文与 Chunk 存 HDFS（按 版本/回次范围 分区）
  R-502  向量存 HBase（主）+ HDFS（快照）
  R-503  Chunk 正文 + 元数据存 HBase
  R-504  关键词倒排索引存 HBase
  R-512  版本参与 rowkey，分区不破坏版本
  R-510  可从 HDFS 事实源全量重建 HBase 索引

运行位置：**master 容器内**（需要 HDFS RPC + HBase Thrift）
    docker cp ... master:/workspace/pipeline/
    docker exec master bash -lc "cd /workspace/pipeline && python3 s2_2_index.py --stage all"

分阶段
------
  --stage upload    只上传 chunks.jsonl 到 HDFS（含 sha256 + fsck 校验）
  --stage embed     只做向量化，产出 vectors.jsonl（HDFS 快照）
  --stage hbase     只把 chunk/vector/term 写入 HBase
  --stage all       全流程（默认）

设计要点
--------
1. **HDFS 是事实源**：chunks.jsonl + vectors.jsonl 都在 HDFS，HBase 是派生索引。
   删掉 HBase 表后能从 HDFS 完整重建 —— 这是 R-510 的基础。
2. **rowkey 带版本前缀**：`<version>|<hui:03d>|<chunk_id>`。
   不同版本的同位置 chunk 天然隔离；按版本/回次扫描都走前缀。
3. **向量以 float32 字节存**：不是 base64 字符串（避免 4/3 膨胀）。
4. **倒排索引用 bigram**：中文不切词时最稳；比 unigram 精确、比 trigram 召回高。
"""
import os
import sys
import json
import time
import base64
import hashlib
import argparse
import subprocess
import collections

sys.stdout.reconfigure(encoding="utf-8")

# ---------------- 参数 ----------------
HDFS_BASE = "/hlm"
HDFS_WAREHOUSE = f"{HDFS_BASE}/warehouse/chunks"
HDFS_DERIVED = f"{HDFS_BASE}/derived/vectors"
HDFS_MANIFEST = f"{HDFS_BASE}/manifest"

MODEL_DIR = "/data/opt/models/bge-small-zh-v1.5"
MODEL_ID = "bge-small-zh-v1.5"
EMB_DIM = 512
MAX_LEN = 512
BATCH = 32

T_CHUNKS = "hlm_chunks"
T_VECTORS = "hlm_vectors"
T_TERMS = "hlm_terms"

VERSION = "fuqinju"

IN_CONTAINER = os.path.exists("/workspace/pipeline")
if IN_CONTAINER:
    LOCAL_OUT = "/workspace/pipeline/out"
else:
    LOCAL_OUT = os.path.join(r"D:\学校事务\大数据原理与应用",
                             "红楼智问", "pipeline", "out")
CHUNKS_FILE = os.path.join(LOCAL_OUT, "chunks.jsonl")
VECTORS_FILE = os.path.join(LOCAL_OUT, "vectors.jsonl")


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ============================================================
# 中文 bigram
# ============================================================
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


# ============================================================
# HDFS
# ============================================================
def hdfs(cmd):
    r = subprocess.run(["hdfs", "dfs"] + cmd.split(), capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"hdfs dfs {cmd} failed: {r.stderr[:400]}")
    return r.stdout


# ============================================================
# 阶段 1：上传 chunks 到 HDFS
# ============================================================
def stage_upload():
    log("== 阶段 upload：chunks.jsonl -> HDFS ==")
    if not os.path.exists(CHUNKS_FILE):
        raise SystemExit(f"缺 {CHUNKS_FILE}，请先跑 s2_1_chunk.py")

    hdfs(f"-mkdir -p {HDFS_WAREHOUSE}/{VERSION}")
    r = subprocess.run(
        ["hdfs", "dfs", "-put", "-f", CHUNKS_FILE,
         f"{HDFS_WAREHOUSE}/{VERSION}/chunks.jsonl"],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[:400])

    hdfs(f"-mkdir -p {HDFS_MANIFEST}")
    mani_local = os.path.join(LOCAL_OUT, "chunks_manifest.json")
    subprocess.run(["hdfs", "dfs", "-put", "-f", mani_local,
                    f"{HDFS_MANIFEST}/chunks_manifest.json"],
                   capture_output=True, text=True)

    size_local = os.path.getsize(CHUNKS_FILE)
    log("HDFS 文件: " + hdfs(f"-ls {HDFS_WAREHOUSE}/{VERSION}/chunks.jsonl").strip())
    log(f"本地字节: {size_local:,}")

    sha_local = hashlib.sha256(open(CHUNKS_FILE, "rb").read()).hexdigest()
    r2 = subprocess.run(
        ["hdfs", "dfs", "-cat", f"{HDFS_WAREHOUSE}/{VERSION}/chunks.jsonl"],
        capture_output=True)
    sha_hdfs = hashlib.sha256(r2.stdout).hexdigest()
    log(f"sha256(本地)  = {sha_local}")
    log(f"sha256(HDFS)  = {sha_hdfs}")
    log(f"校验结果: {'✅ 一致' if sha_hdfs == sha_local else '❌ 不一致'}")

    # 块信息（HDFS 实操证据，R-513）
    # 注意：fsck 是 `hdfs fsck` 独立子命令，不是 `hdfs dfs -fsck`
    log("-- HDFS 块信息（hdfs fsck -files -blocks -locations）--")
    r3 = subprocess.run(["hdfs", "fsck", f"{HDFS_WAREHOUSE}/{VERSION}/chunks.jsonl",
                         "-files", "-blocks", "-locations"],
                        capture_output=True, text=True)
    for line in r3.stdout.splitlines():
        t = line.strip()
        if not t:
            continue
        if ("blk_" in t) or ("Status:" in t) or ("Total blocks" in t) \
                or ("Minimally replicated" in t) or ("Missing blocks" in t) \
                or ("Number of data-nodes" in t) or ("Number of racks" in t) \
                or ("Average block size" in t):
            log("  " + t[:280])
    return sha_local


# ============================================================
# 阶段 2：向量化
# ============================================================
def load_tokenizer():
    """极简 BERT WordPiece 分词器（只依赖 vocab.txt；够用且可解释）"""
    import re
    vocab = {}
    for i, line in enumerate(open(os.path.join(MODEL_DIR, "vocab.txt"), encoding="utf-8")):
        vocab[line.rstrip("\n")] = i
    cls_id = vocab.get("[CLS]", 101)
    sep_id = vocab.get("[SEP]", 102)
    unk_id = vocab.get("[UNK]", 100)
    pad_id = vocab.get("[PAD]", 0)

    def tokenize(text):
        text = text.lower()
        toks = re.findall(r"[\u4e00-\u9fff]|[a-z]+|[0-9]+|[^\sa-z0-9\u4e00-\u9fff]", text)
        ids = []
        for t in toks:
            if t in vocab:
                ids.append(vocab[t])
                continue
            ok = False
            for end in range(len(t), 0, -1):
                sub = t[:end]
                if sub in vocab:
                    ids.append(vocab[sub])
                    rest = t[end:]
                    while rest:
                        found = False
                        for e2 in range(len(rest), 0, -1):
                            s2 = "##" + rest[:e2]
                            if s2 in vocab:
                                ids.append(vocab[s2])
                                rest = rest[e2:]
                                found = True
                                break
                        if not found:
                            ids.append(unk_id)
                            break
                    ok = True
                    break
            if not ok:
                ids.append(unk_id)
        return ids

    return tokenize, cls_id, sep_id, pad_id


def stage_embed():
    log("== 阶段 embed：向量化（bge-small-zh-v1.5 ONNX）==")
    import onnxruntime as ort
    import numpy as np

    tokenize, cls_id, sep_id, pad_id = load_tokenizer()
    sess = ort.InferenceSession(os.path.join(MODEL_DIR, "model.onnx"),
                                providers=["CPUExecutionProvider"])

    chunks = []
    with open(CHUNKS_FILE, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                chunks.append(json.loads(line))
    log(f"待向量化 chunk: {len(chunks):,}")

    t0 = time.time()
    n_done = 0
    with open(VECTORS_FILE, "w", encoding="utf-8") as fo:
        for start in range(0, len(chunks), BATCH):
            batch = chunks[start:start + BATCH]
            seqs = []
            for c in batch:
                ids = [cls_id] + tokenize(c["text"])[:MAX_LEN - 2] + [sep_id]
                seqs.append(ids)
            maxlen = max(len(s) for s in seqs)
            input_ids = np.full((len(seqs), maxlen), pad_id, dtype=np.int64)
            attn = np.zeros((len(seqs), maxlen), dtype=np.int64)
            tt = np.zeros((len(seqs), maxlen), dtype=np.int64)
            for i, s in enumerate(seqs):
                input_ids[i, :len(s)] = s
                attn[i, :len(s)] = 1

            hidden = sess.run(None, {
                "input_ids": input_ids,
                "attention_mask": attn,
                "token_type_ids": tt,
            })[0]

            # Mean pooling（attention_mask 加权）+ L2 归一化
            mask = attn[:, :, None].astype(np.float32)
            emb = (hidden * mask).sum(axis=1) / np.maximum(mask.sum(axis=1), 1e-9)
            emb = emb / np.maximum(np.linalg.norm(emb, axis=1, keepdims=True), 1e-9)

            for c, v in zip(batch, emb):
                blob = v.astype(np.float32).tobytes()
                fo.write(json.dumps({
                    "chunk_id": c["chunk_id"],
                    "version": c["version"],
                    "hui": c["hui"],
                    "model_id": MODEL_ID,
                    "dim": EMB_DIM,
                    "vec_b64": base64.b64encode(blob).decode("ascii"),
                }, ensure_ascii=False) + "\n")
                n_done += 1

            if (start // BATCH) % 8 == 0:
                el = time.time() - t0
                log(f"  {n_done}/{len(chunks)}  {n_done / max(el, 1e-9):.1f} chunk/s")

    el = time.time() - t0
    log(f"向量化完成: {n_done:,} 条  耗时 {el:.1f}s  ({n_done / el:.1f} chunk/s)")
    log(f"产出: {VECTORS_FILE}  {os.path.getsize(VECTORS_FILE) / 1048576:.2f} MB")

    hdfs(f"-mkdir -p {HDFS_DERIVED}/{VERSION}/model={MODEL_ID}")
    subprocess.run(["hdfs", "dfs", "-put", "-f", VECTORS_FILE,
                    f"{HDFS_DERIVED}/{VERSION}/model={MODEL_ID}/vectors.jsonl"],
                   capture_output=True)
    log(f"HDFS 快照: {HDFS_DERIVED}/{VERSION}/model={MODEL_ID}/vectors.jsonl")
    log(hdfs(f"-ls -h {HDFS_DERIVED}/{VERSION}/model={MODEL_ID}/").strip())
    return VECTORS_FILE


# ============================================================
# HBase 工具
# ============================================================
def hbase_conn():
    """
    连 Thrift。**必须传 table_prefix=None**（不是 b""！）。

    这是本项目最隐蔽的一个坑，实测过程：
      1. happybase 1.3.0 的 table_prefix 默认值是 None，
         但内部 `_table_name` 里 None 会被当成 b"_"（源码里 separator=b"_"）。
      2. 我一开始传 table_prefix=b""，结果拼接成 b"" + b"_" + name = b"_hlm_chunks"，
         Thrift 报 `TableNotFoundException: _hlm_terms`。
      3. 传 table_prefix=None 时同样会得到 b"_" 前缀 —— 因为 None 被替换成 b"_"。
      4. **真正有效的做法**：把 separator 也设成 b""，让前缀拼接退化为空串，
         即 table_prefix=b"" + table_prefix_separator=b"" → 表名原样。
    最终：建表用 shell（原样表名），读写用 happybase（前缀为空），两边对齐。
    """
    import happybase
    return happybase.Connection("master", port=9090, timeout=600000,
                                table_prefix=b"", table_prefix_separator=b"")


def shell(cmd):
    """
    执行一条 hbase shell 命令。

    坑（实测踩到）：把 DDL 用 `echo "{cmd}" | hbase shell` 传进去时，
    bash 双引号会展开 `{...}`，`{VERSIONS => 1}` 被 brace expansion 搞坏，
    最终 create 语句语法错误、表根本没建出来，而脚本又误判为"已存在"。

    修法：把命令写进临时文件，用 `hbase shell -n <file>` 执行（纯文件读取，
    不经过任何 shell 展开）。
    """
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".rb", delete=False,
                                     encoding="utf-8") as f:
        f.write(cmd + "\n")
        path = f.name
    try:
        r = subprocess.run(["hbase", "shell", "-n", path],
                           capture_output=True, text=True)
        return r.stdout + r.stderr
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def table_exists(name):
    """
    精确判断表是否存在。
    坑：之前用 `name in out` 做子串匹配，`hlm_chunks` 会被
    `hlm_chunks_old` 之类误判；而且 shell 输出里还有日志行。
    这里只认 `=> [...]` 那一行解析出的 JSON 列表。
    """
    import json as _json
    import re as _re
    out = shell("list")
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("=>") and "[" in line:
            try:
                arr = _json.loads(line.split("=>", 1)[1].strip())
                return name in arr
            except Exception:
                pass
    return False


def ensure_table(name, families, splits=None):
    """
    建表（幂等）。走 `hbase shell`：
      - 功能完整，支持 SPLITS 预分区（happybase 1.3.0 不支持）
      - 预分区是本项目教学点（写热点治理），不能省
    数据读写真走 happybase（批量 put 快得多）。
    """
    if table_exists(name):
        log(f"  表 {name} 已存在，跳过建表")
        return
    # 注意 ruby 语法：每个列族是一个独立的 hash：create 't', {NAME=>'d'}, {NAME=>'m'}, SPLITS=>[...]
    # 写成 { 'd'=>{...}, 'm'=>{...} } 是"一个 hash 里两个 key"，HBase 不认。
    fam_parts = ", ".join(f"{{NAME => '{f}', VERSIONS => 1}}" for f in families)
    if splits:
        sk = ", ".join(f"'{b.decode()}'" for b in splits)
        cmd = f"create '{name}', {fam_parts}, SPLITS => [{sk}]"
    else:
        cmd = f"create '{name}', {fam_parts}"
    out = shell(cmd)
    log(f"  建表 {name}  families={list(families)}  "
        f"预分区 splitKeys={[b.decode() for b in splits] if splits else '无'}")
    if "ERROR" in out.upper():
        log(f"    ⚠ {out.strip()[-250:]}")
    time.sleep(2)


def wait_table(name, conn, retries=30):
    """
    等表真正可用（能 scan 出结果，哪怕是 0 行）。

    坑1：最开始用 happybase .row() 探测，表刚建好 region 还没上线时
         Thrift 抛 TTransportException，把连接搞坏，后续批量 put 全失败。
    坑2：改成 `hbase shell describe` 探测，虽然不污染连接，
         但每次都要启动一个 JVM，单次 4 分钟，3 张表卡了 12 分钟。
    最终：复用同一个 happybase 连接做 scan(limit=1)，
         表不存在/region 未上线就重建连接重试，秒级完成。
    """
    import happybase
    for _ in range(retries):
        try:
            list(conn.table(name).scan(limit=1))
            return True
        except happybase.happybase.TableNotFound:
            time.sleep(1)
        except Exception:
            time.sleep(1)
    return False


# ============================================================
# 阶段 3：写 HBase
# ============================================================
def stage_hbase():
    log("== 阶段 hbase：chunk / vector / term 写入 HBase ==")
    conn = hbase_conn()

    # ---- 建表（预分区：rowkey 首字符是 hui 的十位 0~7） ----
    splits = [b"1", b"2", b"3", b"4", b"5", b"6", b"7"]
    ensure_table(T_CHUNKS, ["d", "m"], splits=splits)
    ensure_table(T_VECTORS, ["v", "m"], splits=splits)
    ensure_table(T_TERMS, ["p"])
    for t in (T_CHUNKS, T_VECTORS, T_TERMS):
        log(f"  等待 {t} 可用 ... {'OK' if wait_table(t, conn) else 'TIMEOUT'}")

    # ---- 1) chunks ----
    chunks = []
    with open(CHUNKS_FILE, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                chunks.append(json.loads(line))
    t0 = time.time()
    tb = conn.table(T_CHUNKS)
    with tb.batch(batch_size=200):
        for c in chunks:
            # rowkey = version|hui:03d|chunk_id  →  天然按版本+回次有序
            rk = f"{c['version']}|{c['hui']:03d}|{c['chunk_id']}".encode()
            tb.put(rk, {
                b"d:text": c["text"].encode("utf-8"),
                b"d:char_count": str(c["char_count"]).encode(),
                b"m:hui": str(c["hui"]).encode(),
                b"m:huimu": c["huimu"].encode("utf-8"),
                b"m:para_start": str(c["para_start"]).encode(),
                b"m:para_end": str(c["para_end"]).encode(),
                b"m:seg": str(c["seg"]).encode(),
                b"m:version": c["version"].encode(),
                b"m:strategy_rev": c["strategy_rev"].encode(),
                b"m:partition": c["partition"].encode(),
            })
    log(f"  {T_CHUNKS}: 写入 {len(chunks):,} 行  耗时 {time.time() - t0:.1f}s")

    # ---- 2) vectors ----
    if not os.path.exists(VECTORS_FILE):
        log(f"  跳过向量（缺 {VECTORS_FILE}）")
    else:
        t0 = time.time()
        meta = {c["chunk_id"]: c for c in chunks}
        tbv = conn.table(T_VECTORS)
        n = 0
        with tbv.batch(batch_size=200):
            with open(VECTORS_FILE, encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    v = json.loads(line)
                    c = meta.get(v["chunk_id"])
                    if not c:
                        continue
                    rk = f"{v['version']}|{c['hui']:03d}|{v['chunk_id']}".encode()
                    blob = base64.b64decode(v["vec_b64"])      # 真实 float32 字节
                    tbv.put(rk, {
                        b"v:emb": blob,
                        b"m:model_id": v["model_id"].encode(),
                        b"m:dim": str(v["dim"]).encode(),
                        b"m:hui": str(v["hui"]).encode(),
                    })
                    n += 1
        log(f"  {T_VECTORS}: 写入 {n:,} 行  耗时 {time.time() - t0:.1f}s  "
            f"({n * EMB_DIM * 4 / 1048576:.1f} MB 原始向量)")

    # ---- 3) 倒排索引（bigram） ----
    t0 = time.time()
    postings = collections.defaultdict(list)
    for c in chunks:
        for bg, tf in bigrams(c["text"]).items():
            postings[bg].append([c["chunk_id"], tf])
    log(f"  bigram 词项数: {len(postings):,}")

    tbt = conn.table(T_TERMS)
    n_term = 0
    with tbt.batch(batch_size=100):
        for term, plist in postings.items():
            tbt.put(f"{VERSION}|{term}".encode(), {
                b"p:postings": json.dumps(plist, ensure_ascii=False).encode("utf-8"),
                b"p:df": str(len(plist)).encode(),
            })
            n_term += 1
    log(f"  {T_TERMS}: 写入 {n_term:,} 个词项  耗时 {time.time() - t0:.1f}s")

    # ---- 校验 ----
    log("-- 校验（逐表计数 + 抽样回读）--")
    for tname in (T_CHUNKS, T_VECTORS, T_TERMS):
        t = conn.table(tname)
        cnt = sum(1 for _ in t.scan(batch_size=500))
        log(f"  {tname}: {cnt:,} 行")

    t = conn.table(T_CHUNKS)
    for k, d in t.scan(limit=1):
        log(f"  抽样 rowkey = {k.decode()}")
        log(f"    d:text[:50] = {d[b'd:text'].decode('utf-8')[:50]}")
        meta_d = {kk.decode(): vv.decode('utf-8', 'replace')
                  for kk, vv in d.items() if kk.startswith(b'm:')}
        log(f"    meta = {meta_d}")

    tv = conn.table(T_VECTORS)
    for k, d in tv.scan(limit=1):
        log(f"  向量 rowkey = {k.decode()}")
        log(f"    v:emb 字节数 = {len(d[b'v:emb'])} (= {EMB_DIM} × 4 = {EMB_DIM * 4})")
        log(f"    m:model_id = {d[b'm:model_id'].decode()}, m:dim = {d[b'm:dim'].decode()}")

    conn.close()


# ============================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="all",
                    choices=["all", "upload", "embed", "hbase"])
    args = ap.parse_args()

    if not IN_CONTAINER:
        print("⚠ 本脚本必须在 master 容器内运行")
        sys.exit(1)

    t_all = time.time()
    if args.stage in ("all", "upload"):
        stage_upload()
    if args.stage in ("all", "embed"):
        stage_embed()
    if args.stage in ("all", "hbase"):
        stage_hbase()
    log(f"全部完成，总耗时 {time.time() - t_all:.1f}s")


if __name__ == "__main__":
    main()
