# -*- coding: utf-8 -*-
"""
S2 数据管道 · 阶段 1：Chunk 切分
================================

对应需求：R-501（原文与 Chunk 存 HDFS）、R-508（分区设计）、R-512（版本不破坏分区）
对应难点①（项目A-难点分析.md 第三节）：Chunk 切分策略论证

切分策略（三级，见需求文档 §6.3）
---------------------------------
L1 按回        81 条      章节导航、逐回统计
L2 按场景段     见下        RAG 主检索单元（目标 300~800 字）
L3 滑动窗口     兜底       超长段再切，500 字 / 100 字重叠

chunk_id 生成规则（关键设计）
----------------------------
    chunk_id = sha1(version|hui|para_start|para_end|strategy_rev)[:16]

为什么要带 version 和 strategy_rev：
  1. version 进 ID → 不同版本的同位置 chunk 天然隔离，不会串版本（R-512）
  2. strategy_rev 进 ID → **改切分策略就得到全新的 ID 集**，旧索引可并行保留、
     可对比、可回滚。这是「索引可重建」的前提（R-510）

否则会出现：改了切分策略后，旧 chunk_id 和新 chunk_id 对不上，
索引重建时新旧混在一起，无法判断哪套是当前生效的。

产出
----
  out/chunks.jsonl           全部 chunk（含正文与元数据）
  out/chunks_manifest.json   分区 manifest（分区 → 文件 → 记录数 → sha256）
  out/chunk_stats.json       切分统计（用于验证与报告）
"""
import os
import sys
import json
import hashlib
import collections

sys.stdout.reconfigure(encoding="utf-8")

# ---------------- 路径 ----------------
WORK = r"D:\学校事务\大数据原理与应用"
CORPUS = r"D:\Programming\Red\hlm_corpus"
OUT_DIR = os.path.join(WORK, "红楼智问", "pipeline", "out")
os.makedirs(OUT_DIR, exist_ok=True)

# ---------------- 切分参数（进 strategy_rev 的语义） ----------------
STRATEGY_REV = "v1"        # 策略版本：改动任何参数都要升这个号
TARGET_MIN = 300           # L2 目标块最小字数
TARGET_MAX = 800           # L2 目标块最大字数
WIN_SIZE = 500             # L3 滑窗大小
WIN_OVERLAP = 100          # L3 重叠
N_PARTITIONS = 8           # 分区数（按回次范围，每 10 回一区）

# 场景边界标记：中文小说的叙事分段信号
# 依据：这套标记来自对第1回~第80回正文的实际观察，不是凭空写的
SCENE_MARKERS = [
    "话说", "却说", "且说", "原来", "如今", "这里", "只见", "忽见", "忽听",
    "一语未了", "正说着", "说话间", "不在话下", "暂且不表", "且听下回",
    "未知如何", "毕竟如何", "要知端的", "且看下回",
]


def sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def make_chunk_id(version: str, hui: int, p_start: int, p_end: int, seg: int) -> str:
    """chunk_id = sha1(version|hui|para_start|para_end|seg|strategy_rev)[:16]"""
    key = f"{version}|{hui:03d}|{p_start:04d}|{p_end:04d}|{seg:02d}|{STRATEGY_REV}"
    return sha1(key)[:16]


def split_long_text(text: str, p_start: int, p_end: int):
    """
    L3：对超长段落做滑动窗口切分。
    返回 [(sub_text, offset_in_paras)] 列表。
    """
    if len(text) <= TARGET_MAX:
        return [(text, 0)]
    out = []
    step = WIN_SIZE - WIN_OVERLAP
    i = 0
    while i < len(text):
        piece = text[i:i + WIN_SIZE]
        if len(piece) < TARGET_MIN // 2 and out:
            # 尾巴太短就并进上一块，避免产生垃圾小块
            out[-1] = (out[-1][0] + piece, out[-1][1])
            break
        out.append((piece, i))
        if i + WIN_SIZE >= len(text):
            break
        i += step
    return out


def merge_paragraphs(paras, start_idx):
    """
    L2：从 start_idx 开始向后合并段落，直到字数达到 TARGET_MIN；
    不超过 TARGET_MAX（除非单段本身就超长）。
    返回 (text, end_idx_inclusive, 是否需要 L3 再切)
    """
    buf = []
    total = 0
    i = start_idx
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
    # 关键修正：只要合并结果超过 TARGET_MAX，就一定要 L3 再切。
    # 之前只判断「单段超长」，会漏掉「短段 + 超长段」的组合（如 50 + 1376 字），
    # 结果产出 1,426 字的巨块，违背了切分目标。
    need_l3 = len(text) > TARGET_MAX
    return text, end, need_l3


def is_scene_break(para: str, prev_para: str) -> bool:
    """判断这一段是不是场景起点"""
    head = para.strip()[:6]
    for m in SCENE_MARKERS:
        if head.startswith(m):
            return True
    # 对话开头（引号起）一般不是场景起点，反而常是同一场景的延续
    if head.startswith(("“", "「", "『", "‘")):
        return False
    # 上一段以句末标点结束时，新段落更可能是新场景
    if prev_para and prev_para.rstrip().endswith(("。", "！", "？", "”")):
        return False
    return False


def main():
    print("=" * 72)
    print("S2-1 Chunk 切分  strategy_rev=%s  target=%d~%d  win=%d/%d"
          % (STRATEGY_REV, TARGET_MIN, TARGET_MAX, WIN_SIZE, WIN_OVERLAP))
    print("=" * 72)

    chapters_path = os.path.join(CORPUS, "clean", "chapters.jsonl")
    chapters = []
    with open(chapters_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                chapters.append(json.loads(line))

    version = "fuqinju"
    chunks = []
    per_hui = collections.Counter()
    per_partition = collections.Counter()
    too_long_single = 0

    for ch in chapters:
        hui = ch["hui"]
        if hui == 0:
            continue                      # 凡例不进 RAG 主链（保留在原文层）
        paras = ch["paragraphs"]
        huimu = ch["huimu"]

        # ---- L2 场景段合并 ----
        i = 0
        seg_no = 0
        while i < len(paras):
            text, end, is_long = merge_paragraphs(paras, i)
            if is_long:
                too_long_single += 1
                # L3 兜底：单段超长，滑窗切
                for sub, off in split_long_text(text, i, end):
                    cid = make_chunk_id(version, hui, i, end, seg_no)
                    if off:      # 同段多块，ID 需再区分
                        cid = sha1(f"{cid}|{off}")[:16]
                    chunks.append({
                        "chunk_id": cid,
                        "version": version,
                        "hui": hui,
                        "huimu": huimu,
                        "para_start": i,
                        "para_end": end,
                        "seg": seg_no,
                        "char_count": len(sub),
                        "strategy_rev": STRATEGY_REV,
                        "text": sub,
                    })
                    per_hui[hui] += 1
                    seg_no += 1
            else:
                cid = make_chunk_id(version, hui, i, end, seg_no)
                chunks.append({
                    "chunk_id": cid,
                    "version": version,
                    "hui": hui,
                    "huimu": huimu,
                    "para_start": i,
                    "para_end": end,
                    "seg": seg_no,
                    "char_count": len(text),
                    "strategy_rev": STRATEGY_REV,
                    "text": text,
                })
                per_hui[hui] += 1
                seg_no += 1
            i = end + 1

    # ---- 后处理：合并过短的尾块 ----
    # 实测问题：章末常剩 1 段十几字（如「不知后事如何，且听下回分解。」），
    # 单独成块既没有检索价值，又拉低块大小分布。
    # 解决：若块的正文 < TARGET_MIN/2，且与前一块同回，则并入前一块。
    merged = []
    for c in chunks:
        if (merged and c["char_count"] < TARGET_MIN // 2
                and merged[-1]["hui"] == c["hui"]
                and merged[-1]["char_count"] + c["char_count"] <= TARGET_MAX * 1.2):
            prev = merged[-1]
            prev["text"] = prev["text"] + "\n" + c["text"]
            prev["para_end"] = c["para_end"]
            prev["char_count"] = len(prev["text"])
            # ID 必须跟着变：para_end 改了，chunk_id 的语义就变了。
            # 这正是「strategy_rev 进 ID」的价值——ID 永远能反映真实内容边界。
            prev["chunk_id"] = make_chunk_id(
                prev["version"], prev["hui"], prev["para_start"], prev["para_end"], prev["seg"])
        else:
            merged.append(c)
    n_before = len(chunks)
    chunks = merged
    print(f"尾块合并        : {n_before} -> {len(chunks)}（并入 {n_before - len(chunks)} 个碎块）")

    # ---- 分区：按回次范围（方案 A，教材「按版本/卷/章范围」） ----
    per_hui = collections.Counter()
    for c in chunks:
        per_hui[c["hui"]] += 1
        part = min(N_PARTITIONS - 1, (c["hui"] - 1) // 10)
        c["partition"] = f"hui_{part * 10 + 1:03d}_{min(80, (part + 1) * 10):03d}"
        per_partition[c["partition"]] += 1

    # ---- 统计 ----
    sizes = sorted(c["char_count"] for c in chunks)
    n = len(sizes)
    stats = {
        "strategy_rev": STRATEGY_REV,
        "params": {
            "target_min": TARGET_MIN, "target_max": TARGET_MAX,
            "win_size": WIN_SIZE, "win_overlap": WIN_OVERLAP,
            "n_partitions": N_PARTITIONS,
        },
        "n_chunks": n,
        "n_hui": len(per_hui),
        "total_chars": sum(sizes),
        "size": {
            "min": sizes[0], "max": sizes[-1],
            "mean": round(sum(sizes) / n, 1),
            "p50": sizes[n // 2], "p95": sizes[int(n * 0.95)],
        },
        "single_para_too_long": too_long_single,
        "per_hui": dict(sorted(per_hui.items())),
        "partitions": {},
    }

    # 分区负载比（教材 V3 硬指标）
    for part, cnt in sorted(per_partition.items()):
        bytes_ = sum(c["char_count"] for c in chunks if c["partition"] == part) * 3  # UTF-8 粗估
        stats["partitions"][part] = {"chunks": cnt, "approx_bytes": bytes_}

    load = [v["chunks"] for v in stats["partitions"].values()]
    stats["partition_skew"] = {
        "max": max(load), "mean": round(sum(load) / len(load), 2),
        "ratio": round(max(load) / (sum(load) / len(load)), 3),
    }

    # ---- 写出 chunks.jsonl ----
    chunks_path = os.path.join(OUT_DIR, "chunks.jsonl")
    chunks.sort(key=lambda c: (c["hui"], c["seg"]))
    with open(chunks_path, "w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    # ---- manifest：分区 → 文件 → 记录数 → sha256 ----
    manifest = {
        "generated_at": "2026-10-24",
        "stage": "S2-1 chunk 切分",
        "strategy_rev": STRATEGY_REV,
        "source": "hlm_corpus/clean/chapters.jsonl",
        "version": version,
        "n_chunks": n,
        "id_rule": "sha1(version|hui|para_start|para_end|seg|strategy_rev)[:16]",
        "files": [{
            "path": "chunks.jsonl",
            "records": n,
            "bytes": os.path.getsize(chunks_path),
            "sha256": hashlib.sha256(open(chunks_path, "rb").read()).hexdigest(),
        }],
        "partitions": stats["partitions"],
    }
    with open(os.path.join(OUT_DIR, "chunks_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    with open(os.path.join(OUT_DIR, "chunk_stats.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    # ---- 打印 ----
    print(f"chunk 数        : {n:,}")
    print(f"覆盖回次        : {len(per_hui)} 回")
    print(f"总字数          : {stats['total_chars']:,}")
    print(f"块大小 min/mean/p50/p95/max : "
          f"{stats['size']['min']} / {stats['size']['mean']} / "
          f"{stats['size']['p50']} / {stats['size']['p95']} / {stats['size']['max']}")
    print(f"单段超长触发 L3 : {too_long_single} 次")
    print(f"分区数          : {len(stats['partitions'])}")
    print(f"分区负载比      : max/mean = {stats['partition_skew']['ratio']}")
    print(f"\n产出: {OUT_DIR}")
    print(f"  chunks.jsonl            {os.path.getsize(chunks_path):,} B")
    print(f"  chunks_manifest.json")
    print(f"  chunk_stats.json")
    print(f"\nsha256(chunks.jsonl) = {manifest['files'][0]['sha256']}")


if __name__ == "__main__":
    main()
