# -*- coding: utf-8 -*-
"""
S22-4 · 追加研究：词性 n-gram + 跨时代对照
==========================================

回应 S22 主研究的两个局限：

  局限1：只用字/词频特征，未涉及语法
        → 本脚本加**词性 n-gram**（POS bigram / trigram）
          这是学界作者归属的主流方法（Burrows' Delta 的语法版）

  局限2：没有对照组，无法判断"癸酉本的异常"是不是通俗小说通病
        → 本脚本加 4 部明清小说作对照：
           金瓶梅        （明·万历，世情小说，题材最近）
           醒世姻缘传    （清初，世情小说，篇幅相当）
           儒林外史      （清中，讽刺小说）
           二十年目睹之怪现状（晚清，用来定位"晚清白话"特征）

语料来源：https://github.com/luoxuhai/chinese-novel （公版古籍）

关键假设与它的检验
------------------
若「癸酉本」是现代人续作，它的语言应**偏离所有明清小说**，
尤其偏离曹雪芹，且可能在「晚清/现代白话」特征上更接近晚清小说。

运行：
  python s22_4_pos_ngram.py
"""
import os
import re
import sys
import json
import math
import random
import collections

sys.stdout.reconfigure(encoding="utf-8")

import jieba
import jieba.posseg as pseg

HLM = r"D:\Programming\Red\hlm_corpus"
CMP = r"D:\Programming\Red\comparison_corpus"
OUT = r"D:\学校事务\大数据原理与应用\红楼智问\docs\S22-癸酉本词性分析.json"

jieba.setLogLevel(20)   # 减少初始化日志

# ---- 语料定义 ----
GROUPS = {
    "A_曹雪芹_前80回": {"dir": os.path.join(HLM, "fuqinju"), "range": (1, 80)},
    "B_高鹗_81-120":   {"dir": os.path.join(HLM, "chenggao"), "range": (81, 120)},
    "C_癸酉本_81-108": {"dir": os.path.join(HLM, "guiyouben"), "range": None, "renumber": True},
}

COMPARISON = {
    "金瓶梅":       os.path.join(CMP, "jinpingmei.txt"),
    "醒世姻缘传":   os.path.join(CMP, "xingshiyinyuan.txt"),
    "儒林外史":     os.path.join(CMP, "rulinwaishi.txt"),
    "二十年目睹":   os.path.join(CMP, "ershinian.txt"),
}

# 需要关注的词性（jieba 的 ICTCLAS 标记集）
# 只保留**功能类**词性：代词/副词/介词/连词/助词/语气词
# 这些是语法习惯，比实词更难伪装
FUNC_POS = {"r", "d", "p", "c", "u", "ul", "ug", "uj", "ud", "uz",
            "y", "e", "o", "zg"}

# 「被」字句/「把」字句等处置式，靠词性看不出来，单独数


def log(m):
    print(m, flush=True)


def load_hlm(cfg):
    """载入红楼语料，返回 [(hui, text)]"""
    d = cfg["dir"]
    lo, hi = cfg.get("range") or (None, None)
    out = []
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
        if cfg.get("renumber"):
            mm = re.search(r"第(\d+)回", t[:200])
            hui = int(mm.group(1)) if mm else 80 + idx
        if lo is not None and not (lo <= hui <= hi):
            continue
        out.append((hui, t))
    return out


def pos_features(text):
    """
    提取词性特征。返回 dict 或 None。
    只用功能词性 bigram，数量可控且信号强。
    """
    # 清洗：去掉标点、空白，只留中文
    t = re.sub(r"[^\u4e00-\u9fff]+", "。", text)
    if len(t) < 400:
        return None

    pairs = list(pseg.cut(t))
    pos_seq = [f for w, f in pairs if f and f != "x"]
    if len(pos_seq) < 200:
        return None

    # 1) 词性 unigram 分布（只留功能词性）
    uni = collections.Counter()
    for p in pos_seq:
        if p in FUNC_POS:
            uni[p] += 1

    # 2) 词性 bigram（只保留两端都是功能词性的）
    bi = collections.Counter()
    for i in range(len(pos_seq) - 1):
        a, b = pos_seq[i], pos_seq[i + 1]
        if a in FUNC_POS and b in FUNC_POS:
            bi[f"{a}>{b}"] += 1

    n = len(pos_seq)
    feat = {}
    for p, c in uni.items():
        feat[f"pos1_{p}"] = c / n * 100          # 百分比
    # bigram 只取高频的（减少维度），后面再统一筛选
    for k, c in bi.most_common(80):
        feat[f"pos2_{k}"] = c / n * 100

    # 3) 平均词长（分词的词长分布也是风格信号）
    lens = [len(w) for w, f in pairs if re.fullmatch(r"[\u4e00-\u9fff]+", w)]
    if lens:
        feat["avg_wordlen"] = sum(lens) / len(lens)
        feat["pct_1char"] = sum(1 for x in lens if x == 1) / len(lens) * 100
        feat["pct_2char"] = sum(1 for x in lens if x == 2) / len(lens) * 100
        feat["pct_3plus"] = sum(1 for x in lens if x >= 3) / len(lens) * 100

    return feat


def build_hlm_rows(name, cfg):
    rows = []
    for hui, t in load_hlm(cfg):
        f = pos_features(t)
        if f:
            rows.append({"group": name, "hui": hui, "feat": f, "chars": len(t)})
    return rows


def build_cmp_rows(name, path):
    """
    对照小说：按固定字数切块（每块约 1 万汉字），模拟「回」的粒度。
    """
    if not os.path.exists(path):
        return []
    txt = open(path, encoding="utf-8").read()
    # 去掉开头的序言（金瓶梅/儒林外史开头是现代序）
    # 用启发式：跳过前 3000 字中不含对话标记的部分
    body = re.sub(r"[^\u4e00-\u9fff。，！？；：、]+", "。", txt)

    CHUNK = 12000
    rows = []
    i = 0
    blk = 0
    while i + CHUNK <= len(body):
        seg = body[i:i + CHUNK]
        f = pos_features(seg)
        if f:
            rows.append({"group": name, "blk": blk, "feat": f, "chars": len(seg)})
        i += CHUNK
        blk += 1
        if blk >= 40:      # 每部最多取 40 块（约 48 万字），与红楼体量相当
            break
    return rows


def standardize(rowsets):
    allr = [r for rs in rowsets for r in rs]
    keys = set()
    for r in allr:
        keys.update(r["feat"].keys())
    keys = sorted(keys)
    # 缺失特征补 0
    for r in allr:
        for k in keys:
            r["feat"].setdefault(k, 0.0)
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


def main():
    log("=" * 78)
    log("S22-4 追加研究：词性 n-gram + 跨时代对照")
    log("=" * 78)

    # ---- 构建 ----
    log("\n[1/3] 提取特征（分词 + 词性标注，较慢）…")
    A = build_hlm_rows("A_曹雪芹", GROUPS["A_曹雪芹_前80回"])
    log(f"  A 曹雪芹前80回: {len(A)} 回")
    B = build_hlm_rows("B_高鹗", GROUPS["B_高鹗_81-120"])
    log(f"  B 高鹗后40回  : {len(B)} 回")
    C = build_hlm_rows("C_癸酉本", GROUPS["C_癸酉本_81-108"])
    log(f"  C 癸酉本后28回: {len(C)} 回")

    cmps = {}
    for name, path in COMPARISON.items():
        rows = build_cmp_rows(name, path)
        cmps[name] = rows
        log(f"  对照 {name:<8s}: {len(rows)} 块")

    # ---- 标准化（红楼三组 + 对照，统一空间）----
    log("\n[2/3] 标准化 + 计算距离…")
    allsets = [A, B, C] + list(cmps.values())
    allsets, keys = standardize(allsets)
    A, B, C = allsets[0], allsets[1], allsets[2]
    for i, name in enumerate(COMPARISON.keys()):
        cmps[name] = allsets[3 + i]

    log(f"  特征维度: {len(keys)}")

    ca, cb, cc = cen(A, keys), cen(B, keys), cen(C, keys)

    # ---- 核心：C 到 A / B / 各对照的距离 ----
    log("\n" + "=" * 78)
    log("【1】癸酉本到各组的距离（词性特征空间，{0} 维）".format(len(keys)))
    log("=" * 78)
    log(f"  {'目标组':<16}{'到 C 的距离':>14}{'相对 A':>12}")

    d_A = euclid(cc, ca, keys)
    d_B = euclid(cc, cb, keys)
    d_cmp = {n: euclid(cc, cen(r, keys), keys) for n, r in cmps.items()}

    log(f"  {'A 曹雪芹':<16}{d_A:>14.4f}{'基准':>12}")
    log(f"  {'B 高鹗':<16}{d_B:>14.4f}{d_B/d_A:>12.2f}x")
    for n, d in sorted(d_cmp.items(), key=lambda x: x[1]):
        log(f"  {n:<16}{d:>14.4f}{d/d_A:>12.2f}x")

    log(f"\n  → C 最近的组: {'A 曹雪芹' if d_A < min([d_B] + list(d_cmp.values())) else '其他'}")

    # ---- 对照基准：D（程高本前80回）在同空间的离距 ----
    log("\n" + "=" * 78)
    log("【2】基准尺度：同作者不同版本的距离")
    log("=" * 78)
    D = build_hlm_rows("D_程高本前80回",
                       {"dir": os.path.join(HLM, "chenggao"), "range": (1, 80)})
    # 需要用同一套标准化参数 → 简化：重新构造
    log(f"  D 组: {len(D)} 回（重新载入后单独标准化，见下）")

    # ---- 跨时代特征：哪些特征是「晚清/现代」标志 ----
    log("\n" + "=" * 78)
    log("【3】跨时代特征对比（关键）")
    log("=" * 78)
    log("  看癸酉本在哪些特征上更像晚清小说（二十年目睹之怪现状）")

    late = "二十年目睹"
    d_late = d_cmp.get(late, float("inf"))

    # 逐特征找「最像谁」
    log(f"\n  {'特征':<18}{'曹雪芹':>10}{'高鹗':>10}{'癸酉本':>10}{'晚清':>10}{'癸酉最近':>10}")
    cand = []
    for k in keys:
        if not k.startswith(("pos1_", "avg_wordlen", "pct_")):
            continue
        va, vb, vc = ca[k], cb[k], cc[k]
        vl = cen(cmps[late], [k])[k] if late in cmps and cmps[late] else 0
        da, db, dl = abs(vc - va), abs(vc - vb), abs(vc - vl)
        near = min([("曹", da), ("高", db), ("晚清", dl)], key=lambda x: x[1])[0]
        cand.append((abs(dl - da), k, va, vb, vc, vl, near))

    cand.sort(reverse=True)
    for _, k, va, vb, vc, vl, near in cand[:12]:
        log(f"  {k:<18}{va:>10.2f}{vb:>10.2f}{vc:>10.2f}{vl:>10.2f}{near:>10}")

    # ---- 聚类：C 的每一回最近的组 ----
    log("\n" + "=" * 78)
    log("【4】最近邻：C 每一回最像哪个组")
    log("=" * 78)
    pool = [(r, "A_曹雪芹") for r in A] + [(r, "B_高鹗") for r in B]
    for n, rs in cmps.items():
        pool += [(r, n) for r in rs]

    nn = collections.Counter()
    for rc in C:
        best = min(pool, key=lambda x: euclid(rc["feat"], x[0]["feat"], keys))
        nn[best[1]] += 1
    log(f"  C 的 {len(C)} 回分布:")
    for n, c in nn.most_common():
        log(f"    {n:<16}{c:>4} 回 ({c/len(C)*100:.1f}%)")

    # ---- bootstrap：C 到 A 与 C 到「各对照」的差值 ----
    log("\n" + "=" * 78)
    log("【5】bootstrap：C→A 距离 vs C→对照 距离")
    log("=" * 78)
    random.seed(42)
    N = 2000
    S = 20
    results_boot = {}
    for n, rs in cmps.items():
        if not rs:
            continue
        ctr = cen(rs, keys)
        diffs = []
        for _ in range(N):
            samp = random.sample(C, min(S, len(C)))
            sc = cen(samp, keys)
            diffs.append(euclid(sc, ctr, keys) - euclid(sc, ca, keys))
        diffs.sort()
        mean = sum(diffs) / len(diffs)
        lo = diffs[int(N * 0.025)]
        hi = diffs[int(N * 0.975)]
        results_boot[n] = {"mean": round(mean, 4), "ci95": [round(lo, 4), round(hi, 4)]}
        verdict = "C 比该组更近 A" if lo > 0 else ("C 比该组更远 A" if hi < 0 else "无显著差异")
        log(f"  {n:<14} 差值均值 {mean:+.4f}  95%CI [{lo:+.4f}, {hi:+.4f}]  → {verdict}")

    # ---- 保存 ----
    out = {
        "n_features": len(keys),
        "dist_to_C": {
            "A_曹雪芹": round(d_A, 4),
            "B_高鹗": round(d_B, 4),
            **{n: round(d, 4) for n, d in d_cmp.items()},
        },
        "nearest_neighbor": dict(nn),
        "bootstrap": results_boot,
        "feature_detail": [
            {"feature": k, "A": round(va, 3), "B": round(vb, 3),
             "C": round(vc, 3), "late_qing": round(vl, 3), "C_nearest": near}
            for _, k, va, vb, vc, vl, near in cand[:30]
        ],
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    log(f"\n结果已存: {OUT}")


if __name__ == "__main__":
    main()
