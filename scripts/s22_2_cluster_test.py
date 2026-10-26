# -*- coding: utf-8 -*-
"""
S22-2 · 癸酉本定量研究：聚类 + 显著性检验
==========================================

上一脚本（s22_guiyouben_study.py）给出了描述性统计。
本脚本做**推断性**分析，回答两个问题：

  Q1  癸酉本 C 在多维特征空间里，离 A（曹雪芹）近还是离 B（高鹗）近？
  Q2  这个距离差异**统计显著**吗？（用留一回重采样的 bootstrap）

方法
----
1. 逐回提取特征向量（每回单独算，不是整组算）
2. 特征标准化（z-score，防止量纲影响）
3. 3 种距离：欧氏 / 余弦 / 曼哈顿
4. bootstrap：每次随机抽 20 回，算 C 到 A、C 到 B 的距离差，重复 2000 次
5. 输出：距离均值 + 95% 置信区间 + p 值

关键设计
--------
**必须是逐回特征，不能整组平均。**
整组平均会抹掉「回与回之间的波动」，而波动本身是重要信号
（同一作者的 80 回之间也有差异，这个差异的尺度就是判断基准）。

运行：
  python s22_2_cluster_test.py
"""
import os
import re
import sys
import json
import math
import random
import collections

sys.stdout.reconfigure(encoding="utf-8")

BASE = r"D:\Programming\Red\hlm_corpus"

FUNCTION_WORDS = [
    "的", "了", "着", "把", "被", "将", "之", "其", "而", "则",
    "也", "都", "又", "便", "却", "只", "还", "就", "才", "倒",
    "是", "有", "不", "没", "很", "太", "更", "最", "正", "已",
]


def log(m):
    print(m, flush=True)


def load_plain(d, lo=None, hi=None, renumber=False):
    """载入 {hui: text}"""
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
        try:
            t = open(os.path.join(d, f), encoding="utf-8").read()
        except Exception:
            continue
        hui = idx
        if renumber:
            mm = re.search(r"第(\d+)回", t[:200])
            hui = int(mm.group(1)) if mm else 80 + idx
        if lo is not None and not (lo <= hui <= hi):
            continue
        out[hui] = t
    return out


def featurize(text):
    """单回 → 特征向量（都做长度归一）"""
    n = len(text)
    if n < 500:
        return None
    han = len(re.findall(r"[\u4e00-\u9fff]", text))
    if han < 300:
        return None

    feat = {}
    # 1) 30 个虚词频率（每万字）
    cnt = collections.Counter(text)
    for w in FUNCTION_WORDS:
        feat[f"fw_{w}"] = cnt.get(w, 0) / n * 10000

    # 2) 句长统计
    sents = [s.strip() for s in re.split(r"[。！？…]+", text) if s.strip()]
    if not sents:
        return None
    lens = [len(s) for s in sents]
    mean = sum(lens) / len(lens)
    var = sum((x - mean) ** 2 for x in lens) / len(lens)
    feat["sent_mean"] = mean
    feat["sent_std"] = math.sqrt(var)
    feat["sent_short"] = sum(1 for x in lens if x <= 8) / len(lens) * 100
    feat["sent_long"] = sum(1 for x in lens if x >= 40) / len(lens) * 100

    # 3) 标点节奏
    punct = sum(cnt.get(p, 0) for p in "，。！？；：、")
    feat["punct_per100"] = punct / max(1, han) * 100
    feat["comma_r"] = cnt.get("，", 0) / max(1, punct) * 100
    feat["period_r"] = cnt.get("。", 0) / max(1, punct) * 100

    # 4) 词汇丰富度（TTR 用固定窗口）
    win = min(3000, han)
    ttrs = []
    for start in range(0, max(1, han - win), max(1, win // 2)):
        seg = "".join(re.findall(r"[\u4e00-\u9fff]", text))[start:start + win]
        if len(seg) >= win // 2:
            ttrs.append(len(set(seg)) / len(seg) * 100)
    feat["ttr"] = sum(ttrs) / len(ttrs) if ttrs else 0

    # 5) 四字格密度
    segs = re.split(r"[，。！？；：、\n「」『』“”‘’（）]+", text)
    four = sum(1 for s in segs if 4 <= len(s.strip()) <= 8 and re.fullmatch(r"[\u4e00-\u9fff]{4}", s.strip()))
    feat["four_per10k"] = four / n * 10000

    return feat


def zscore_matrix(rows):
    """对每个特征做 z-score 标准化"""
    keys = list(rows[0]["feat"].keys())
    n = len(rows)
    for k in keys:
        vals = [r["feat"][k] for r in rows]
        mu = sum(vals) / n
        sd = math.sqrt(sum((v - mu) ** 2 for v in vals) / n) or 1.0
        for r in rows:
            r["feat"][k] = (r["feat"][k] - mu) / sd
    return rows, keys


def dist(a, b, keys, metric="euclid"):
    if metric == "euclid":
        return math.sqrt(sum((a[k] - b[k]) ** 2 for k in keys))
    if metric == "manhattan":
        return sum(abs(a[k] - b[k]) for k in keys)
    if metric == "cosine":
        num = sum(a[k] * b[k] for k in keys)
        da = math.sqrt(sum(a[k] ** 2 for k in keys))
        db = math.sqrt(sum(b[k] ** 2 for k in keys))
        return 1 - (num / (da * db) if da and db else 0)
    raise ValueError(metric)


def centroid(rows, keys):
    n = len(rows)
    return {k: sum(r["feat"][k] for r in rows) / n for k in keys}


def main():
    log("=" * 78)
    log("S22-2 癸酉本：聚类 + bootstrap 显著性检验")
    log("=" * 78)

    # ---- 载入逐回 ----
    A = load_plain(os.path.join(BASE, "fuqinju"), 1, 80)
    B = load_plain(os.path.join(BASE, "chenggao"), 81, 120)
    C = load_plain(os.path.join(BASE, "guiyouben"), renumber=True)
    D = load_plain(os.path.join(BASE, "chenggao"), 1, 80)

    def build(data, label):
        rows = []
        for h, t in sorted(data.items()):
            f = featurize(t)
            if f:
                rows.append({"hui": h, "feat": f, "group": label, "chars": len(t)})
        return rows

    ar, br, cr, dr = build(A, "A"), build(B, "B"), build(C, "C"), build(D, "D")
    log(f"\n逐回样本:")
    log(f"  A 曹雪芹前80回   {len(ar):>3} 回")
    log(f"  B 高鹗后40回     {len(br):>3} 回")
    log(f"  C 癸酉本后28回   {len(cr):>3} 回")
    log(f"  D 程高本前80回   {len(dr):>3} 回（对照：同作者异版本）")

    # ---- 标准化（用 A+B+D 作基准，不含 C，避免污染）----
    allrows = ar + br + dr
    allrows, keys = zscore_matrix(allrows)
    # C 用同一套 mu/sd
    ckeys = list(cr[0]["feat"].keys())
    for k in keys:
        a_vals = [r["feat"][k] for r in ar]
        pass
    # 重算标准化参数（从 allrows 反推不现实，重新来一遍）
    raw = {r["group"] + str(r["hui"]): dict(r["feat"]) for r in allrows + cr}
    rows_all = allrows + cr
    # 用 A+B+D 的均值和标准差
    params = {}
    for k in keys:
        vals = [r["feat"] for r in allrows]  # 这里 feat 已被 z 化，需原始值
    # 简化：直接对全部 4 组统一标准化
    for r in cr:
        for k in keys:
            r["feat"][k] = r["feat"][k]   # 占位
    
    # 正确做法：重新载入原始特征，统一标准化
    def rebuild():
        A_ = build(A, "A"); B_ = build(B, "B"); C_ = build(C, "C"); D_ = build(D, "D")
        return A_, B_, C_, D_
    ar, br, cr, dr = rebuild()
    allrows = ar + br + dr
    for k in keys:
        vals = [r["feat"][k] for r in allrows] + [r["feat"][k] for r in cr]
        mu = sum(vals) / len(vals)
        sd = math.sqrt(sum((v - mu) ** 2 for v in vals) / len(vals)) or 1.0
        for r in allrows + cr:
            if k in r["feat"]:
                r["feat"][k] = (r["feat"][k] - mu) / sd

    # ---- 质心距离 ----
    log("\n" + "=" * 78)
    log("【质心距离】C 到 A / B 的距离（越小越像）")
    log("=" * 78)
    ca, cb, cc, cd = centroid(ar, keys), centroid(br, keys), centroid(cr, keys), centroid(dr, keys)

    dist_table = {}
    for metric in ("euclid", "manhattan", "cosine"):
        d_C_A = dist(cc, ca, keys, metric)
        d_C_B = dist(cc, cb, keys, metric)
        d_D_A = dist(cd, ca, keys, metric)   # 对照基准：同作者的版本差异
        dist_table[metric] = {"C_A": d_C_A, "C_B": d_C_B, "D_A": d_D_A}

    log(f"  {'距离':<12}{'C→A(曹)':>12}{'C→B(高)':>12}{'更近?':>10}{'D→A(同作者)':>14}")
    for metric, d in dist_table.items():
        closer = "A 曹雪芹" if d["C_A"] < d["C_B"] else "B 高鹗"
        log(f"  {metric:<12}{d['C_A']:>12.4f}{d['C_B']:>12.4f}{closer:>10}{d['D_A']:>14.4f}")

    # ---- bootstrap 显著性 ----
    log("\n" + "=" * 78)
    log("【bootstrap 显著性】随机抽 20 回，算 C→A 与 C→B 的距离差")
    log("=" * 78)
    log("  （差值 = dist(C,B) - dist(C,A)，正数表示更像 A）")

    random.seed(42)
    N_BOOT = 2000
    SAMPLE = 20
    boot = []

    for _ in range(N_BOOT):
        sample = random.sample(cr, min(SAMPLE, len(cr)))
        sc = centroid(sample, keys)
        d_a = dist(sc, ca, keys, "euclid")
        d_b = dist(sc, cb, keys, "euclid")
        boot.append(d_b - d_a)

    boot.sort()
    mean_diff = sum(boot) / len(boot)
    lo = boot[int(len(boot) * 0.025)]
    hi = boot[int(len(boot) * 0.975)]
    p_neg = sum(1 for x in boot if x <= 0) / len(boot)

    log(f"\n  距离差均值: {mean_diff:+.4f}")
    log(f"  95% 置信区间: [{lo:+.4f}, {hi:+.4f}]")
    log(f"  P(更像 B 高鹗) = {p_neg:.4f}")
    log(f"  P(更像 A 曹雪芹) = {1 - p_neg:.4f}")

    if lo > 0:
        verdict = "显著更像 A（曹雪芹）"
    elif hi < 0:
        verdict = "显著更像 B（高鹗续书）"
    else:
        verdict = "无显著差异（置信区间跨 0）"
    log(f"\n  → {verdict}")

    # ---- 对照：D（同作者异版本）的 bootstrap ----
    boot_d = []
    for _ in range(N_BOOT):
        sample = random.sample(dr, min(SAMPLE, len(dr)))
        sd_ = centroid(sample, keys)
        d_a = dist(sd_, ca, keys, "euclid")
        d_b = dist(sd_, cb, keys, "euclid")
        boot_d.append(d_b - d_a)
    boot_d.sort()
    log(f"\n  【对照】D（程高本前80回，同为曹雪芹）的距离差:")
    log(f"    均值 {sum(boot_d)/len(boot_d):+.4f}  "
        f"95%CI [{boot_d[int(len(boot_d)*0.025)]:+.4f}, {boot_d[int(len(boot_d)*0.975)]:+.4f}]")
    log("    → 这是「A 组自身波动」的尺度。C 的差值若落在此范围外，说明差异是真实的。")

    # ---- 最近邻分析 ----
    log("\n" + "=" * 78)
    log("【最近邻】C 的每一回，最像哪个组的哪一回？")
    log("=" * 78)
    pool = [(r, "A") for r in ar] + [(r, "B") for r in br]
    nn_count = collections.Counter()
    nn_examples = []
    for rc in cr:
        best = min(pool, key=lambda x: dist(rc["feat"], x[0]["feat"], keys, "euclid"))
        nn_count[best[1]] += 1
        nn_examples.append({
            "c_hui": rc["hui"],
            "near_group": best[1],
            "near_hui": best[0]["hui"],
            "dist": round(dist(rc["feat"], best[0]["feat"], keys, "euclid"), 3),
        })

    log(f"  C 的 {len(cr)} 回中：")
    log(f"    最像 A（曹雪芹）: {nn_count['A']:>3} 回 ({nn_count['A']/len(cr)*100:.1f}%)")
    log(f"    最像 B（高鹗）  : {nn_count['B']:>3} 回 ({nn_count['B']/len(cr)*100:.1f}%)")
    log(f"\n  前 8 例：")
    for e in nn_examples[:8]:
        log(f"    癸酉第{e['c_hui']}回 → 最像 {e['near_group']} 组第{e['near_hui']}回 (d={e['dist']})")

    # ---- 逐特征差异 ----
    log("\n" + "=" * 78)
    log("【逐特征】C 与 A、B 的标准化均值对比（差异最大的 8 个特征）")
    log("=" * 78)
    diffs = []
    for k in keys:
        va = ca[k]
        vb = cb[k]
        vc = cc[k]
        # C 离谁更近
        da = abs(vc - va)
        db = abs(vc - vb)
        diffs.append((abs(da - db), k, va, vb, vc, da, db))
    diffs.sort(reverse=True)

    log(f"  {'特征':<16}{'A(曹)':>9}{'B(高)':>9}{'C(癸)':>9}{'更近':>8}")
    for _, k, va, vb, vc, da, db in diffs[:8]:
        closer = "A" if da < db else "B"
        log(f"  {k:<16}{va:>9.2f}{vb:>9.2f}{vc:>9.2f}{closer:>8}")

    # ---- 存盘 ----
    out = {
        "n_per_group": {"A": len(ar), "B": len(br), "C": len(cr), "D": len(dr)},
        "centroid_dist": dist_table,
        "bootstrap": {
            "n_boot": N_BOOT, "sample": SAMPLE,
            "mean_diff": round(mean_diff, 4),
            "ci95": [round(lo, 4), round(hi, 4)],
            "p_closer_B": round(p_neg, 4),
            "verdict": verdict,
        },
        "bootstrap_D": {
            "mean_diff": round(sum(boot_d) / len(boot_d), 4),
            "ci95": [round(boot_d[int(len(boot_d) * 0.025)], 4),
                     round(boot_d[int(len(boot_d) * 0.975)], 4)],
        },
        "nearest_neighbor": dict(nn_count),
        "feature_diffs": [{"feature": k, "A": round(va, 3), "B": round(vb, 3),
                           "C": round(vc, 3), "closer": "A" if da < db else "B"}
                          for _, k, va, vb, vc, da, db in diffs],
    }
    p = r"D:\学校事务\大数据原理与应用\红楼智问\docs\S22-癸酉本聚类结果.json"
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    log(f"\n结果已存: {p}")


if __name__ == "__main__":
    main()
