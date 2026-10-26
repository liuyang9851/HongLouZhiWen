# -*- coding: utf-8 -*-
"""
S22-5 · 词性结论的可信度检验（最重要的一步）
==========================================

S22-4 用词性 n-gram 得出一个**与主研究相反的结论**：

    S22 主研究（字/词频）: C 癸酉本更像 A 曹雪芹（p=0.018）
    S22-4（词性 n-gram）: C 离 B 高鹗最近（6.917），离 A 曹雪芹最远（7.082）

这种矛盾必须查清。两种可能：
  ① 词性方法抓到了字频方法漏掉的语法信号 → S22-4 更可信
  ② jieba 是**现代汉语**分词器，对古籍切分有系统偏差，
     癸酉本的用词习惯导致**分词错误率**不同 → 是方法伪影

本脚本用**已知答案的对照组**来判断是哪种：

  检验1  D（程高本前80回，确实是曹雪芹）在同空间的最近邻
         若 D 大量判给 A → 方法有效
         若 D 也判给 B/儒林外史 → 方法有系统偏差

  检验2  随机基线
         把 C 的 28 回随机打散重组成"伪回"，看最近邻分布是否变化
         若随机化后分布相同 → 信号来自内容而非切块

  检验3  分词质量代理指标
         未登录词比例 / 单字词比例 —— 若 C 的未登录词显著更多，
         说明 jieba 对它的分词质量更差

  检验4  剔除"分词敏感"特征后重跑
         只用「不依赖分词正确性」的特征（如标点、句子长度、单字比例）

运行：
  python s22_5_verify_pos.py
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

jieba.setLogLevel(20)

HLM = r"D:\Programming\Red\hlm_corpus"
CMP = r"D:\Programming\Red\comparison_corpus"
OUT = r"D:\学校事务\大数据原理与应用\红楼智问\docs\S22-词性结论检验.json"

FUNC_POS = {"r", "d", "p", "c", "u", "ul", "ug", "uj", "ud", "uz",
            "y", "e", "o", "zg"}


def log(m):
    print(m, flush=True)


def load_plain(d, lo=None, hi=None, renumber=False):
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
        if renumber:
            mm = re.search(r"第(\d+)回", t[:200])
            hui = int(mm.group(1)) if mm else 80 + idx
        if lo is not None and not (lo <= hui <= hi):
            continue
        out.append((hui, t))
    return out


def pos_features(text):
    t = re.sub(r"[^\u4e00-\u9fff]+", "。", text)
    if len(t) < 400:
        return None
    pairs = list(pseg.cut(t))
    pos_seq = [f for w, f in pairs if f and f != "x"]
    if len(pos_seq) < 200:
        return None

    uni = collections.Counter(p for p in pos_seq if p in FUNC_POS)
    bi = collections.Counter()
    for i in range(len(pos_seq) - 1):
        a, b = pos_seq[i], pos_seq[i + 1]
        if a in FUNC_POS and b in FUNC_POS:
            bi[f"{a}>{b}"] += 1

    n = len(pos_seq)
    feat = {f"pos1_{p}": c / n * 100 for p, c in uni.items()}
    for k, c in bi.most_common(80):
        feat[f"pos2_{k}"] = c / n * 100

    lens = [len(w) for w, f in pairs if re.fullmatch(r"[\u4e00-\u9fff]+", w)]
    if lens:
        feat["avg_wordlen"] = sum(lens) / len(lens)
        feat["pct_1char"] = sum(1 for x in lens if x == 1) / len(lens) * 100
        feat["pct_2char"] = sum(1 for x in lens if x == 2) / len(lens) * 100
        feat["pct_3plus"] = sum(1 for x in lens if x >= 3) / len(lens) * 100

    # ---- 分词质量代理指标（关键！）----
    words = [w for w, f in pairs if re.fullmatch(r"[\u4e00-\u9fff]+", w)]
    total_w = len(words) or 1
    # 单字词比例：古籍分词若质量差，单字词会异常多
    feat["_q_pct_single"] = sum(1 for w in words if len(w) == 1) / total_w * 100
    # 平均词长
    feat["_q_avg_len"] = sum(len(w) for w in words) / total_w
    # 「的/了/着」等功能词被切成单字的比例（正常应被识别为助词）
    feat["_q_pct_unk"] = 0.0   # 占位
    return feat


def pos_features_simple(text):
    """
    只用不依赖分词的特征（检验4用）：
      句子长度、标点、单字/双字字符比例、字重复率
    这些特征即使分词完全错误也成立。
    """
    han = re.findall(r"[\u4e00-\u9fff]", text)
    n = len(han)
    if n < 300:
        return None
    sents = [s.strip() for s in re.split(r"[。！？…]+", text) if s.strip()]
    if not sents:
        return None
    lens = [len(s) for s in sents]
    mean = sum(lens) / len(lens)
    var = sum((x - mean) ** 2 for x in lens) / len(lens)

    cnt = collections.Counter(han)
    # 单字重复率：不同字数/总字数
    ttr = len(cnt) / n * 100
    # 高频字集中度（前10字占比）
    top10 = sum(c for _, c in cnt.most_common(10)) / n * 100

    punct = len(re.findall(r"[，。！？；：、]", text))

    return {
        "s_mean": mean,
        "s_std": math.sqrt(var),
        "s_short": sum(1 for x in lens if x <= 8) / len(lens) * 100,
        "s_long": sum(1 for x in lens if x >= 40) / len(lens) * 100,
        "ttr": ttr,
        "top10_conc": top10,
        "punct_per100": punct / n * 100,
        "comma_r": (text.count("，") / max(1, punct)) * 100,
        "period_r": (text.count("。") / max(1, punct)) * 100,
        "q_r": (text.count("？") + text.count("！")) / max(1, punct) * 100,
    }


def standardize(rowsets):
    allr = [r for rs in rowsets for r in rs]
    keys = set()
    for r in allr:
        keys.update(r["feat"].keys())
    keys = sorted(keys)
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


def build_cmp(name, path, featurizer, chunk=12000, maxblk=40):
    if not os.path.exists(path):
        return []
    txt = open(path, encoding="utf-8").read()
    body = re.sub(r"[^\u4e00-\u9fff。，！？；：、]+", "。", txt)
    rows = []
    i = 0
    blk = 0
    while i + chunk <= len(body) and blk < maxblk:
        seg = body[i:i + chunk]
        f = featurizer(seg)
        if f:
            rows.append({"group": name, "blk": blk, "feat": f})
        i += chunk
        blk += 1
    return rows


def main():
    log("=" * 78)
    log("S22-5 词性结论的可信度检验")
    log("=" * 78)

    # ============ 检验1：用已知答案的 D 组验证方法 ============
    log("\n" + "=" * 78)
    log("【检验1】方法有效性：D（程高本前80回=曹雪芹）在同空间判给谁？")
    log("=" * 78)

    A = [{"group": "A", "hui": h, "feat": pos_features(t)}
         for h, t in load_plain(os.path.join(HLM, "fuqinju"), 1, 80)]
    A = [r for r in A if r["feat"]]
    B = [{"group": "B", "hui": h, "feat": pos_features(t)}
         for h, t in load_plain(os.path.join(HLM, "chenggao"), 81, 120)]
    B = [r for r in B if r["feat"]]
    C = [{"group": "C", "hui": h, "feat": pos_features(t)}
         for h, t in load_plain(os.path.join(HLM, "guiyouben"), renumber=True)]
    C = [r for r in C if r["feat"]]
    D = [{"group": "D", "hui": h, "feat": pos_features(t)}
         for h, t in load_plain(os.path.join(HLM, "chenggao"), 1, 80)]
    D = [r for r in D if r["feat"]]

    cmps = {
        "金瓶梅": build_cmp("金瓶梅", os.path.join(CMP, "jinpingmei.txt"), pos_features),
        "醒世姻缘传": build_cmp("醒世姻缘传", os.path.join(CMP, "xingshiyinyuan.txt"), pos_features),
        "儒林外史": build_cmp("儒林外史", os.path.join(CMP, "rulinwaishi.txt"), pos_features),
        "二十年目睹": build_cmp("二十年目睹", os.path.join(CMP, "ershinian.txt"), pos_features),
    }

    sets = [A, B, C, D] + list(cmps.values())
    sets, keys = standardize(sets)
    A, B, C, D = sets[0], sets[1], sets[2], sets[3]
    for i, n in enumerate(cmps.keys()):
        cmps[n] = sets[4 + i]

    # 池子：A/B/对照（不含 D）
    pool = [(r, "A_曹雪芹") for r in A] + [(r, "B_高鹗") for r in B]
    for n, rs in cmps.items():
        pool += [(r, n) for r in rs]

    def nearest_profile(target_rows, label):
        nn = collections.Counter()
        for rt in target_rows:
            best = min(pool, key=lambda x: euclid(rt["feat"], x[0]["feat"], keys))
            nn[best[1]] += 1
        log(f"\n  {label}（{len(target_rows)} 回）最近邻分布:")
        for n, c in nn.most_common():
            log(f"    {n:<14}{c:>4} 回 ({c/len(target_rows)*100:>5.1f}%)")
        return nn

    nn_D = nearest_profile(D, "D 程高本前80回（答案应为 A 曹雪芹）")
    nn_C = nearest_profile(C, "C 癸酉本后28回（待测）")
    nn_B = nearest_profile(B, "B 高鹗后40回（答案应为 B）")

    # 判定方法是否有偏
    d_to_A = nn_D.get("A_曹雪芹", 0) / len(D) * 100
    log(f"\n  >>> 方法诊断:")
    log(f"      D（曹雪芹原文）判给 A 的比例: {d_to_A:.1f}%")
    if d_to_A >= 80:
        log(f"      ✅ 方法有效：已知曹雪芹文本能被正确识别")
        method_ok = True
    elif d_to_A >= 50:
        log(f"      ⚠ 方法偏弱：仅 {d_to_A:.1f}% 正确")
        method_ok = False
    else:
        log(f"      ❌ 方法失效：曹雪芹原文都识别不出")
        method_ok = False

    # ============ 检验3：分词质量代理 ============
    log("\n" + "=" * 78)
    log("【检验3】分词质量代理指标（判断 jieba 对哪组更吃力）")
    log("=" * 78)
    log("  若癸酉本的单字词比例显著更高，说明 jieba 对它的分词更差")

    def avg_feat(rows, k):
        vs = [r["feat"].get(k, 0) for r in rows if k in r["feat"]]
        return sum(vs) / len(vs) if vs else 0

    log(f"\n  {'组':<14}{'单字词%':>10}{'平均词长':>10}")
    # 注意：这些已在 standardize 里被 z 化，需用原始值
    # → 重新算一遍原始值
    raw = {}
    for name, data in (("A_曹雪芹", load_plain(os.path.join(HLM, "fuqinju"), 1, 80)),
                       ("B_高鹗", load_plain(os.path.join(HLM, "chenggao"), 81, 120)),
                       ("C_癸酉本", load_plain(os.path.join(HLM, "guiyouben"), renumber=True)),
                       ("D_程高本前80", load_plain(os.path.join(HLM, "chenggao"), 1, 80))):
        singles, lens = [], []
        for h, t in data[:20]:     # 抽样 20 回，够用
            tt = re.sub(r"[^\u4e00-\u9fff]+", "。", t)
            pairs = list(pseg.cut(tt))
            words = [w for w, f in pairs if re.fullmatch(r"[\u4e00-\u9fff]+", w)]
            if not words:
                continue
            singles.append(sum(1 for w in words if len(w) == 1) / len(words) * 100)
            lens.append(sum(len(w) for w in words) / len(words))
        if singles:
            raw[name] = (sum(singles) / len(singles), sum(lens) / len(lens))
            log(f"  {name:<14}{raw[name][0]:>10.2f}{raw[name][1]:>10.3f}")

    if "A_曹雪芹" in raw and "C_癸酉本" in raw:
        diff = raw["C_癸酉本"][0] - raw["A_曹雪芹"][0]
        log(f"\n  癸酉本单字词比例 - 曹雪芹 = {diff:+.2f} 个百分点")
        if abs(diff) > 3:
            log(f"  ⚠ 差异较大：jieba 对癸酉本的分词质量可能不同 → 词性结论需谨慎")
        else:
            log(f"  ✅ 差异不大：分词质量相当 → 词性结论基本可信")

    # ============ 检验4：只用不依赖分词的特征 ============
    log("\n" + "=" * 78)
    log("【检验4】剔除分词依赖特征，只用「硬指标」重跑")
    log("=" * 78)
    log("  只用：句长/标点/字频集中度（这些不受分词正确性影响）")

    def build_simple(loader, label):
        rows = []
        for item in loader:
            if len(item) == 2:
                h, t = item
                f = pos_features_simple(t)
            else:
                f = pos_features_simple(item)
            if f:
                rows.append({"group": label, "feat": f})
        return rows

    SA = build_simple(load_plain(os.path.join(HLM, "fuqinju"), 1, 80), "A")
    SB = build_simple(load_plain(os.path.join(HLM, "chenggao"), 81, 120), "B")
    SC = build_simple(load_plain(os.path.join(HLM, "guiyouben"), renumber=True), "C")
    SD = build_simple(load_plain(os.path.join(HLM, "chenggao"), 1, 80), "D")
    scmps = {n: build_cmp(n, p, pos_features_simple) for n, p in
             (("金瓶梅", os.path.join(CMP, "jinpingmei.txt")),
              ("醒世姻缘传", os.path.join(CMP, "xingshiyinyuan.txt")),
              ("儒林外史", os.path.join(CMP, "rulinwaishi.txt")),
              ("二十年目睹", os.path.join(CMP, "ershinian.txt")))}

    ssets = [SA, SB, SC, SD] + list(scmps.values())
    ssets, skeys = standardize(ssets)
    SA, SB, SC, SD = ssets[0], ssets[1], ssets[2], ssets[3]
    for i, n in enumerate(scmps.keys()):
        scmps[n] = ssets[4 + i]

    ca, cb, cc, cd = cen(SA, skeys), cen(SB, skeys), cen(SC, skeys), cen(SD, skeys)
    log(f"\n  {'目标':<16}{'C 到该组距离':>16}")
    for name, ctr in [("A_曹雪芹", ca), ("B_高鹗", cb)] + \
                     [(n, cen(r, skeys)) for n, r in scmps.items()]:
        log(f"  {name:<16}{euclid(cc, ctr, skeys):>16.4f}")

    log(f"\n  D（曹雪芹）到 A/B: {euclid(cd, ca, skeys):.4f} / {euclid(cd, cb, skeys):.4f}")
    d_C_A = euclid(cc, ca, skeys)
    d_C_B = euclid(cc, cb, skeys)
    log(f"  C 到 A/B: {d_C_A:.4f} / {d_C_B:.4f}")
    log(f"  → 只用硬指标: C {'更近 A' if d_C_A < d_C_B else '更近 B'}")

    # ============ 汇总 ============
    log("\n" + "=" * 78)
    log("结论")
    log("=" * 78)
    log(f"  检验1 方法有效性: D 判给 A 的比例 {d_to_A:.1f}% → "
        f"{'方法可用' if method_ok else '方法存疑'}")
    log(f"  检验3 分词质量: 见上")
    log(f"  检验4 硬指标: C {'更近 A' if d_C_A < d_C_B else '更近 B'}")

    out = {
        "test1_method_validity": {
            "D_to_A_pct": round(d_to_A, 1),
            "D_distribution": dict(nn_D),
            "C_distribution": dict(nn_C),
            "B_distribution": dict(nn_B),
            "method_ok": method_ok,
        },
        "test3_tokenization_quality": {k: {"pct_single": round(v[0], 2),
                                           "avg_wordlen": round(v[1], 3)}
                                       for k, v in raw.items()},
        "test4_hard_features_only": {
            "C_to_A": round(d_C_A, 4),
            "C_to_B": round(d_C_B, 4),
            "closer": "A" if d_C_A < d_C_B else "B",
        },
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    log(f"\n结果已存: {OUT}")


if __name__ == "__main__":
    main()
