# -*- coding: utf-8 -*-
"""
S22-3 · 稳健性检验（这一步决定结论可不可信）
=============================================

上一轮结果出现**内部矛盾**：
  欧氏距离   C 更近 A（曹雪芹）  ✅
  曼哈顿距离 C 更近 A            ✅
  余弦距离   C 更近 B（高鹗）    ❌

矛盾说明：结论可能依赖距离度量的选择，不可轻信。

本脚本做 4 个检验，判断"更像 A"这个结论是否稳健：

  检验1  留一特征（Leave-one-feature-out）
         每次去掉一个特征重跑，看结论是否翻转
         → 若某 1-2 个特征就能决定结论，说明结论脆弱

  检验2  只用虚词（去掉句长/标点/四字格等"可模仿"特征）
         虚词是公认最难伪装的作者指纹
         → 若只用虚词仍更近 A，结论更有力

  检验3  随机置换检验（permutation test）
         把 A/B/C 的标签随机打乱，看真实距离差是否极端
         → 给出真正的 p 值（不依赖分布假设）

  检验4  混淆检验：程高本前80回（D）伪装成"待测组"
         如果 D 被判为"更像 A"，说明方法本身有效
         如果 C 的得分与 D 相当，说明 C 与曹雪芹的差异在"同作者版本差异"范围内

运行：
  python s22_3_robustness.py
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

OUT = r"D:\学校事务\大数据原理与应用\红楼智问\docs\S22-癸酉本稳健性检验.json"


def log(m):
    print(m, flush=True)


def load_plain(d, lo=None, hi=None, renumber=False):
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
    n = len(text)
    if n < 500:
        return None
    han = len(re.findall(r"[\u4e00-\u9fff]", text))
    if han < 300:
        return None
    feat = {}
    cnt = collections.Counter(text)
    for w in FUNCTION_WORDS:
        feat[f"fw_{w}"] = cnt.get(w, 0) / n * 10000

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

    punct = sum(cnt.get(p, 0) for p in "，。！？；：、")
    feat["punct_per100"] = punct / max(1, han) * 100
    feat["comma_r"] = cnt.get("，", 0) / max(1, punct) * 100
    feat["period_r"] = cnt.get("。", 0) / max(1, punct) * 100

    win = min(3000, han)
    ttrs = []
    hanstr = "".join(re.findall(r"[\u4e00-\u9fff]", text))
    for start in range(0, max(1, han - win), max(1, win // 2)):
        seg = hanstr[start:start + win]
        if len(seg) >= win // 2:
            ttrs.append(len(set(seg)) / len(seg) * 100)
    feat["ttr"] = sum(ttrs) / len(ttrs) if ttrs else 0

    segs = re.split(r"[，。！？；：、\n「」『』“”‘’（）]+", text)
    four = sum(1 for s in segs
               if 4 <= len(s.strip()) <= 8 and re.fullmatch(r"[\u4e00-\u9fff]{4}", s.strip()))
    feat["four_per10k"] = four / n * 10000
    return feat


def build(data, label):
    rows = []
    for h, t in sorted(data.items()):
        f = featurize(t)
        if f:
            rows.append({"hui": h, "feat": f, "group": label})
    return rows


def standardize(rowsets):
    """统一 z-score（所有组一起）"""
    allr = [r for rs in rowsets for r in rs]
    keys = list(allr[0]["feat"].keys())
    for k in keys:
        vals = [r["feat"][k] for r in allr]
        mu = sum(vals) / len(vals)
        sd = math.sqrt(sum((v - mu) ** 2 for v in vals) / len(vals)) or 1.0
        for r in allr:
            r["feat"][k] = (r["feat"][k] - mu) / sd
    return rowsets, keys


def cen(rows, keys):
    n = len(rows)
    return {k: sum(r["feat"][k] for r in rows) / n for k in keys}


def euclid(a, b, keys):
    return math.sqrt(sum((a[k] - b[k]) ** 2 for k in keys))


def dist_diff(cr, ca, cb, keys):
    """返回 dist(C,B) - dist(C,A)，正数 = 更像 A"""
    cc = cen(cr, keys)
    return euclid(cc, cb, keys) - euclid(cc, ca, keys)


def main():
    log("=" * 78)
    log("S22-3 稳健性检验")
    log("=" * 78)

    A = build(load_plain(os.path.join(BASE, "fuqinju"), 1, 80), "A")
    B = build(load_plain(os.path.join(BASE, "chenggao"), 81, 120), "B")
    C = build(load_plain(os.path.join(BASE, "guiyouben"), renumber=True), "C")
    D = build(load_plain(os.path.join(BASE, "chenggao"), 1, 80), "D")

    (A, B, C, D), keys = standardize([A, B, C, D])
    ca, cb, cc, cd = cen(A, keys), cen(B, keys), cen(C, keys), cen(D, keys)

    log(f"\n样本: A={len(A)} B={len(B)} C={len(C)} D={len(D)}")
    log(f"特征数: {len(keys)}")

    base_diff = dist_diff(C, ca, cb, keys)
    d_diff = dist_diff(D, ca, cb, keys)
    log(f"\n基准距离差 (C): {base_diff:+.4f}  （正数=更像 A 曹雪芹）")
    log(f"对照距离差 (D, 程高本前80回): {d_diff:+.4f}")

    # ============ 检验1：留一特征 ============
    log("\n" + "=" * 78)
    log("【检验1】留一特征（Leave-one-feature-out）")
    log("=" * 78)
    log("  去掉每个特征重跑，看结论是否翻转")

    flips = []
    effects = []
    for k in keys:
        sub = [x for x in keys if x != k]
        d = dist_diff(C, cen(A, sub), cen(B, sub), sub)
        effects.append((abs(d - base_diff), k, d))
        if d <= 0:
            flips.append(k)

    effects.sort(reverse=True)
    log(f"\n  结论翻转的特征数: {len(flips)} / {len(keys)}")
    if flips:
        log(f"  ⚠ 去掉这些特征后变成「更像 B」: {flips}")
    else:
        log(f"  ✅ 去掉任一特征都不翻转 → 结论稳健")

    log(f"\n  影响最大的 6 个特征（去掉后距离差变化）:")
    log(f"  {'特征':<14}{'去掉后差值':>14}{'变化':>12}")
    for delta, k, d in effects[:6]:
        log(f"  {k:<14}{d:>+14.4f}{d - base_diff:>+12.4f}")

    # ============ 检验2：只用虚词 ============
    log("\n" + "=" * 78)
    log("【检验2】只用虚词（作者指纹，最难伪装）")
    log("=" * 78)
    fw_keys = [k for k in keys if k.startswith("fw_")]
    d_fw = dist_diff(C, cen(A, fw_keys), cen(B, fw_keys), fw_keys)
    log(f"  虚词特征数: {len(fw_keys)}")
    log(f"  只用虚词的距离差: {d_fw:+.4f}  → {'更像 A' if d_fw > 0 else '更像 B'}")
    log(f"  （全特征: {base_diff:+.4f}）")

    # 对照 D
    d_fw_D = dist_diff(D, cen(A, fw_keys), cen(B, fw_keys), fw_keys)
    log(f"  对照 D（程高本前80回）只用虚词: {d_fw_D:+.4f}")

    # ============ 检验3：置换检验 ============
    log("\n" + "=" * 78)
    log("【检验3】置换检验（permutation test）")
    log("=" * 78)
    log("  把 A/B/C 标签随机打乱 1000 次，看真实差值有多极端")

    random.seed(42)
    pool = A + B + C
    observed = base_diff
    n_perm = 1000
    extreme = 0
    perm_vals = []

    for _ in range(n_perm):
        shuffled = pool[:]
        random.shuffle(shuffled)
        na, nb, nc = len(A), len(B), len(C)
        pa = shuffled[:na]
        pb = shuffled[na:na + nb]
        pc = shuffled[na + nb:]
        d = dist_diff(pc, cen(pa, keys), cen(pb, keys), keys)
        perm_vals.append(d)
        if d >= observed:
            extreme += 1

    p_val = extreme / n_perm
    perm_vals.sort()
    log(f"\n  观测差值: {observed:+.4f}")
    log(f"  置换分布 95% 分位: {perm_vals[int(n_perm * 0.95)]:+.4f}")
    log(f"  置换分布最大值: {perm_vals[-1]:+.4f}")
    log(f"  p 值 = P(随机打乱后差值 ≥ 观测值) = {p_val:.3f}")
    if p_val < 0.01:
        log(f"  ✅ p < 0.01，结论高度显著")
    elif p_val < 0.05:
        log(f"  ✅ p < 0.05，结论显著")
    else:
        log(f"  ⚠ p ≥ 0.05，结论**不显著** —— 可能是随机波动")

    # ============ 检验4：混淆检验 ============
    log("\n" + "=" * 78)
    log("【检验4】混淆检验：把 D（程高本前80回）当作待测组")
    log("=" * 78)
    log("  D 是曹雪芹原文（只是版本不同）。若方法有效，D 应被判定为「更像 A」。")
    log(f"  D 的距离差: {d_diff:+.4f} → {'更像 A ✅' if d_diff > 0 else '更像 B ❌ 方法无效'}")
    log(f"  C 的距离差: {base_diff:+.4f}")
    log(f"  C / D 比值: {(base_diff / d_diff if d_diff else 0):.3f}")
    log(f"\n  解读:")
    if d_diff > 0 and base_diff > 0:
        log(f"    · 两组方向一致（都更近 A）→ 方法有效")
        log(f"    · C 的差值只有 D 的 {base_diff/d_diff*100:.1f}%")
        log(f"      → 说明 C 与曹雪芹的相似度**明显低于**同作者异版本的相似度")
        log(f"      → 但 C 仍**更接近曹雪芹而非高鹗**")
    else:
        log(f"    · 方向不一致，方法可能有问题")

    # ============ 保存 ============
    out = {
        "base_diff_C": round(base_diff, 4),
        "base_diff_D": round(d_diff, 4),
        "test1_leave_one_out": {
            "n_flip": len(flips),
            "flipped_features": flips,
            "robust": len(flips) == 0,
            "top_effects": [{"feature": k, "diff_without": round(d, 4),
                             "change": round(d - base_diff, 4)}
                            for _, k, d in effects[:8]],
        },
        "test2_function_words_only": {
            "n_features": len(fw_keys),
            "diff_C": round(d_fw, 4),
            "diff_D": round(d_fw_D, 4),
        },
        "test3_permutation": {
            "observed": round(observed, 4),
            "p_value": round(p_val, 4),
            "perm_95pct": round(perm_vals[int(n_perm * 0.95)], 4),
            "n_perm": n_perm,
        },
        "test4_confusion": {
            "D_diff": round(d_diff, 4),
            "C_diff": round(base_diff, 4),
            "ratio_C_over_D": round(base_diff / d_diff, 3) if d_diff else None,
        },
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    log(f"\n结果已存: {OUT}")


if __name__ == "__main__":
    main()
