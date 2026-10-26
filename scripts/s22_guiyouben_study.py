# -*- coding: utf-8 -*-
"""
S22 · 癸酉本真伪的定量研究
============================

研究问题
--------
网络上对「癸酉本」（吴氏石头记增删试评本）是否为真本争论激烈。
知乎/红学界的核心争议点：

  · 支持方：后28回情节与前80回脂批暗示高度吻合（情榜、狱神庙、悬崖撒手…）
  · 反对方：语言风格与曹雪芹前80回差异巨大，"六大硬伤"、词汇时代错误、文笔粗劣

本脚本**不做价值判断**，只用可复现的统计量回答：
  「癸酉本后28回的语言，更像曹雪芹、还是更像高鹗续书？」

三组对照（这是实验设计的核心）
-------------------------------
  A 组  抚琴居本 1-80 回      曹雪芹（公认）
  B 组  程高本   81-120 回    高鹗续书（公认）
  C 组  癸酉本   81-108 回    待测

  A = 曹雪芹基准
  B = 续书基准（已知「非曹雪芹」的对照）
  C 若接近 A → 支持「可能为曹雪芹原稿」
  C 若接近 B → 支持「后世续作」

统计维度（8 类，均为主流计量风格学指标）
----------------------------------------
  1. 虚词频率        「的、了、着、把、被、将、之、其…」
                     —— 虚词是作者的"指纹"，最难伪装
  2. 句长分布        平均句长、标准差、长短句比例
  3. 字频秩相关      Zipf 曲线与 A 组的 Spearman 相关
  4. 词汇丰富度      TTR / Hapax 比例（不同长度对齐）
  5. 四字格密度      成语使用频率（曹雪芹 vs 续书差异显著）
  6. 高频词重叠      Jaccard 相似度
  7. 标点节奏        逗号句号比例、句内停顿密度
  8. 聚类分析        8 维特征向量 → 距离矩阵 → 看 C 落在哪

运行：
  python s22_guiyouben_study.py
"""
import os
import re
import sys
import json
import math
import itertools
import collections

sys.stdout.reconfigure(encoding="utf-8")

BASE = r"D:\Programming\Red\hlm_corpus"
OUT = r"D:\学校事务\大数据原理与应用\红楼智问\docs\S22-癸酉本定量研究数据.json"

# ---- 语料分组 ----
GROUPS = {
    "A_曹雪芹_前80回": {
        "dir": os.path.join(BASE, "fuqinju"),
        "range": (1, 80),
        "desc": "抚琴居校本前80回（曹雪芹，公认）",
    },
    "B_高鹗_81-120回": {
        "dir": os.path.join(BASE, "chenggao"),
        "range": (81, 120),
        "desc": "程高本后40回（高鹗续书，公认）",
    },
    "C_癸酉本_81-108回": {
        "dir": os.path.join(BASE, "guiyouben"),
        "range": (None, None),   # 文件名是 001..028，正文里标第81..108回
        "desc": "癸酉本后28回（争议待测）",
    },
}

# 额外对照：程高本前80回（不同版本的同一作者，用于校验"版本差异不掩盖作者差异"）
CTRL = {
    "D_程高本_前80回": {
        "dir": os.path.join(BASE, "chenggao"),
        "range": (1, 80),
        "desc": "程高本前80回（同为曹雪芹，版本不同）",
    },
}

# 虚词表（计量风格学最经典的特征集）
FUNCTION_WORDS = [
    "的", "了", "着", "把", "被", "将", "之", "其", "而", "则",
    "也", "都", "又", "便", "却", "只", "还", "就", "才", "倒",
    "连", "和", "与", "同", "在", "从", "向", "对", "为", "因",
    "是", "有", "不", "没", "很", "太", "更", "最", "正", "已",
    "着", "过", "得", "地", "所", "者", "乎", "哉", "矣", "焉",
]

# 标点
PUNCT = "，。！？；：、"


def log(m):
    print(m, flush=True)


def load_group(cfg):
    """载入一组语料，返回 {hui: text}"""
    d = cfg["dir"]
    lo, hi = cfg["range"]
    out = {}
    if not os.path.isdir(d):
        return out
    for f in sorted(os.listdir(d)):
        if not f.endswith(".txt"):
            continue
        m = re.match(r"^(\d+)", f)
        if not m:
            continue
        idx = int(m.group(1))
        p = os.path.join(d, f)
        try:
            t = open(p, encoding="utf-8").read()
        except Exception:
            continue

        # 确定真实回次
        #  · fuqinju / chenggao：文件名就是回次
        #  · guiyouben：文件名 001..028，正文首行是「第XX回」
        hui = idx
        if cfg is GROUPS["C_癸酉本_81-108回"]:
            mm = re.search(r"第(\d+)回", t[:200])
            if mm:
                hui = int(mm.group(1))
            else:
                hui = 80 + idx       # 兜底

        if lo is not None and not (lo <= hui <= hi):
            continue
        out[hui] = t
    return out


# ============================================================
# 统计函数
# ============================================================

def stat_function_words(texts):
    """1. 虚词频率（每万字）"""
    all_text = "".join(texts)
    n = len(all_text)
    if n == 0:
        return {}
    cnt = collections.Counter(all_text)
    return {w: round(cnt.get(w, 0) / n * 10000, 2) for w in dict.fromkeys(FUNCTION_WORDS)}


def split_sentences(texts):
    """按句末标点切句"""
    out = []
    for t in texts:
        for s in re.split(r"[。！？…]+", t):
            s = s.strip()
            if s:
                out.append(s)
    return out


def stat_sentence_length(texts):
    """2. 句长分布"""
    sents = split_sentences(texts)
    if not sents:
        return {}
    lens = [len(s) for s in sents]
    lens_sorted = sorted(lens)
    n = len(lens)
    mean = sum(lens) / n
    var = sum((x - mean) ** 2 for x in lens) / n
    return {
        "n_sentences": n,
        "mean": round(mean, 2),
        "std": round(math.sqrt(var), 2),
        "median": lens_sorted[n // 2],
        "p90": lens_sorted[int(n * 0.9)],
        "short_ratio": round(sum(1 for x in lens if x <= 8) / n * 100, 2),
        "long_ratio": round(sum(1 for x in lens if x >= 40) / n * 100, 2),
    }


def stat_char_rank(texts):
    """3. 字频排序（用于跨组相关）"""
    cnt = collections.Counter("".join(texts))
    return cnt


def spearman(a_rank, b_rank):
    """秩相关（只用两组共有的字）"""
    common = [c for c in a_rank if c in b_rank]
    if len(common) < 50:
        return 0.0
    n = len(common)
    d2 = sum((a_rank[c] - b_rank[c]) ** 2 for c in common)
    return round(1 - 6 * d2 / (n * (n * n - 1)), 4)


def stat_lexical_richness(texts, sample_chars=50000):
    """4. 词汇丰富度（用固定样本长度对齐，避免长度偏差）"""
    all_text = "".join(texts)
    if len(all_text) < sample_chars:
        sample = all_text
    else:
        # 取中段，避开开头结尾的体例文字
        mid = len(all_text) // 2
        sample = all_text[max(0, mid - sample_chars // 2): mid + sample_chars // 2]
    n = len(sample)
    if n == 0:
        return {}
    # 用 2-gram 近似"词"
    bigrams = [sample[i:i + 2] for i in range(n - 1)]
    cnt = collections.Counter(bigrams)
    hapax = sum(1 for v in cnt.values() if v == 1)
    return {
        "sample_chars": n,
        "char_ttr": round(len(set(sample)) / n * 100, 3),
        "bigram_types": len(cnt),
        "bigram_ttr": round(len(cnt) / len(bigrams) * 100, 3),
        "hapax_ratio": round(hapax / max(1, len(cnt)) * 100, 2),
    }


FOUR_CHAR = re.compile(r"[\u4e00-\u9fff]{4}")


def stat_four_char(texts):
    """5. 四字格密度（需排除连续长串）"""
    all_text = "".join(texts)
    n = len(all_text)
    if n == 0:
        return {}
    # 找出所有连续汉字串，再看其中恰好 4 字的段
    runs = re.findall(r"[\u4e00-\u9fff]+", all_text)
    four = 0
    for r in runs:
        # 滑窗找 4 字，但要求前后是标点边界（真正的四字格）
        pass
    # 简化：统计「恰好在标点间、长度为 4-6 的短语」中出现四字格的密度
    segs = re.split(r"[，。！？；：、\n「」『』“”‘’（）]+", all_text)
    for s in segs:
        s = s.strip()
        if 4 <= len(s) <= 8:
            if FOUR_CHAR.fullmatch(s):
                four += 1
    return {
        "four_char_phrases": four,
        "per_10k_chars": round(four / n * 10000, 2),
    }


def stat_high_freq_overlap(cnt_a, cnt_b, topn=500):
    """6. 高频词重叠（Jaccard）"""
    a = {w for w, _ in cnt_a.most_common(topn)}
    b = {w for w, _ in cnt_b.most_common(topn)}
    if not a or not b:
        return 0.0
    return round(len(a & b) / len(a | b), 4)


def stat_punct(texts):
    """7. 标点节奏"""
    all_text = "".join(texts)
    n = len(all_text)
    if n == 0:
        return {}
    cnt = collections.Counter(all_text)
    total_punct = sum(cnt.get(p, 0) for p in PUNCT)
    chars = len(re.findall(r"[\u4e00-\u9fff]", all_text))
    return {
        "punct_per_100_chars": round(total_punct / max(1, chars) * 100, 2),
        "comma_ratio": round(cnt.get("，", 0) / max(1, total_punct) * 100, 2),
        "period_ratio": round(cnt.get("。", 0) / max(1, total_punct) * 100, 2),
        "q_ratio": round((cnt.get("？", 0) + cnt.get("！", 0)) / max(1, total_punct) * 100, 2),
    }


def main():
    log("=" * 78)
    log("S22 · 癸酉本真伪的定量研究")
    log("=" * 78)

    # ---- 载入 ----
    groups = {}
    for name, cfg in list(GROUPS.items()) + list(CTRL.items()):
        data = load_group(cfg)
        texts = [data[h] for h in sorted(data)]
        total = sum(len(t) for t in texts)
        groups[name] = {"texts": texts, "n_hui": len(data), "chars": total,
                        "desc": cfg["desc"]}
        log(f"\n{name}")
        log(f"  {cfg['desc']}")
        log(f"  {len(data):>3} 回 / {total:>8,} 字")
        if data:
            log(f"  回次: {min(data)} ~ {max(data)}")

    # ---- 逐项统计 ----
    results = {}
    log("\n" + "=" * 78)
    log("【1】虚词频率（每万字）")
    log("=" * 78)
    fw = {}
    for name, g in groups.items():
        fw[name] = stat_function_words(g["texts"])
    keys = ["的", "了", "着", "把", "被", "将", "之", "其", "也", "便", "却", "只"]
    log(f"  {'虚词':<4}" + "".join(f"{n.split('_')[0]:>10s}" for n in groups))
    for w in keys:
        row = "".join(f"{fw[n].get(w, 0):>10.2f}" for n in groups)
        log(f"  {w:<4}" + row)

    log("\n" + "=" * 78)
    log("【2】句长分布")
    log("=" * 78)
    sl = {}
    for name, g in groups.items():
        sl[name] = stat_sentence_length(g["texts"])
    log(f"  {'指标':<14}" + "".join(f"{n.split('_')[0]:>10s}" for n in groups))
    for k in ("n_sentences", "mean", "std", "median", "p90", "short_ratio", "long_ratio"):
        row = "".join(f"{sl[n].get(k, 0):>10}" for n in groups)
        log(f"  {k:<14}" + row)

    log("\n" + "=" * 78)
    log("【3】字频秩相关（与 A 组比较，越接近 1 越相似）")
    log("=" * 78)
    ranks = {}
    for name, g in groups.items():
        cnt = stat_char_rank(g["texts"])
        ranks[name] = {c: i + 1 for i, (c, _) in enumerate(cnt.most_common())}
    base = "A_曹雪芹_前80回"
    spearman_res = {}
    for name in groups:
        if name == base:
            continue
        r = spearman(ranks[base], ranks[name])
        spearman_res[name] = r

    log(f"  {'对照组':<26}{'spearman':>12}")
    for name, r in spearman_res.items():
        log(f"  {name:<26}{r:>12.4f}")

    log("\n" + "=" * 78)
    log("【4】词汇丰富度（5 万字样本对齐）")
    log("=" * 78)
    lr = {}
    for name, g in groups.items():
        lr[name] = stat_lexical_richness(g["texts"])
    log(f"  {'指标':<16}" + "".join(f"{n.split('_')[0]:>10s}" for n in groups))
    for k in ("sample_chars", "char_ttr", "bigram_types", "bigram_ttr", "hapax_ratio"):
        row = "".join(f"{lr[n].get(k, 0):>10}" for n in groups)
        log(f"  {k:<16}" + row)

    log("\n" + "=" * 78)
    log("【5】四字格密度")
    log("=" * 78)
    fc = {}
    for name, g in groups.items():
        fc[name] = stat_four_char(g["texts"])
    log(f"  {'指标':<20}" + "".join(f"{n.split('_')[0]:>10s}" for n in groups))
    for k in ("four_char_phrases", "per_10k_chars"):
        row = "".join(f"{fc[n].get(k, 0):>10}" for n in groups)
        log(f"  {k:<20}" + row)

    log("\n" + "=" * 78)
    log("【6】高频 500 词重叠（Jaccard，与 A 组比较）")
    log("=" * 78)
    cnts = {}
    for name, g in groups.items():
        cnts[name] = collections.Counter("".join(g["texts"]))
    jac = {}
    for name in groups:
        if name == base:
            continue
        jac[name] = stat_high_freq_overlap(cnts[base], cnts[name])
    log(f"  {'对照组':<26}{'Jaccard':>12}")
    for name, v in jac.items():
        log(f"  {name:<26}{v:>12.4f}")

    log("\n" + "=" * 78)
    log("【7】标点节奏")
    log("=" * 78)
    pt = {}
    for name, g in groups.items():
        pt[name] = stat_punct(g["texts"])
    log(f"  {'指标':<22}" + "".join(f"{n.split('_')[0]:>10s}" for n in groups))
    for k in ("punct_per_100_chars", "comma_ratio", "period_ratio", "q_ratio"):
        row = "".join(f"{pt[n].get(k, 0):>10}" for n in groups)
        log(f"  {k:<22}" + row)

    # ---- 汇总 ----
    results = {
        "groups": {n: {"desc": g["desc"], "n_hui": g["n_hui"], "chars": g["chars"]}
                   for n, g in groups.items()},
        "function_words": fw,
        "sentence_length": sl,
        "spearman_vs_A": spearman_res,
        "lexical_richness": lr,
        "four_char": fc,
        "jaccard_vs_A": jac,
        "punct": pt,
    }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    log(f"\n数据已存: {OUT}")

    # ---- 结论摘要 ----
    log("\n" + "=" * 78)
    log("结论摘要（癸酉本 C 更接近谁？）")
    log("=" * 78)
    c = "C_癸酉本_81-108回"
    b = "B_高鹗_81-120回"
    log(f"  字频秩相关:  C vs A = {spearman_res.get(c, 0):.4f}   "
        f"B vs A = {spearman_res.get(b, 0):.4f}")
    log(f"  高频词 Jaccard: C vs A = {jac.get(c, 0):.4f}   "
        f"B vs A = {jac.get(b, 0):.4f}")
    log(f"  平均句长:  A={sl[base]['mean']}  B={sl[b]['mean']}  C={sl[c]['mean']}")
    log(f"  四字格/万字: A={fc[base]['per_10k_chars']}  "
        f"B={fc[b]['per_10k_chars']}  C={fc[c]['per_10k_chars']}")


if __name__ == "__main__":
    main()
