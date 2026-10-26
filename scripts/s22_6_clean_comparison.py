# -*- coding: utf-8 -*-
"""
S22-6 · 清洗对照语料的现代污染（这是上一轮结论不可信的原因）
==========================================================

问题
----
S22-4 用对照语料做词性对比，得出「癸酉本 82% 判给儒林外史/金瓶梅」。

但检查发现：**对照语料被现代文字污染了**。

实测 `rulinwaishi.txt` 开头：

    中国的白话长篇小说，自《水浒》、《三国演义》等不朽巨著问世以后，
    在知识分子及市民阶层中都产生了很大的影响…
    《儒林外史》的作者是康、乾年间名人吴敬梓…

这是**20 世纪的学术导读**，不是吴敬梓原文。
jinpingmei.txt 开头也是现代序（"《金瓶梅》，秽书也。袁石公亟称之…" 这段是明清序，但后面混入现代前言）。

现代汉语的虚词/词性分布与清代白话差异巨大，
把它混进"儒林外史"组，必然导致该组风格被拉向现代 → 癸酉本"更像"它。

这正好解释了为什么癸酉本被判给儒林外史：
**不是癸酉本像儒林外史，而是两者都掺了现代文字。**

清洗策略
--------
  1. 找出原文真正的起点（第一章回目标题）
  2. 只保留「第X回」之后的内容
  3. 剔除包含现代特征词（"白话长篇小说""作者是""我们""这个"高频）的段落

运行：
  python s22_6_clean_comparison.py
"""
import os
import re
import sys
import json
import shutil
import collections

sys.stdout.reconfigure(encoding="utf-8")

CMP = r"D:\Programming\Red\comparison_corpus"

# 现代汉语高标记词（在清代白话小说中极罕见）
MODERN_MARKERS = [
    "白话长篇小说", "作者是", "这部小说", "本书", "我们", "这个", "那个",
    "但是", "所以", "因为", "现在", "已经", "什么", "他的", "她的",
    "研究", "认为", "指出", "所谓", "发展", "历史", "社会", "意义",
    "艺术", "思想", "内容", "人物形象", "描写", "反映了", "表现了",
]


def log(m):
    print(m, flush=True)


def find_real_start(text):
    """
    找原文出发点：第一个「第X回」出现的位置。
    明清章回小说都有「第X回」标题，这是最可靠的锚点。
    """
    m = re.search(r"第[一二三四五六七八九十百零〇\d]+回", text)
    if m:
        return m.start()
    # 兜底：找「话说」「却说」「且说」等章回起首语
    for kw in ("话说", "却说", "且说", "看官"):
        i = text.find(kw)
        if i > 0:
            return i
    return 0


def is_modern_line(line):
    """判断一行是否像现代文字"""
    if not line:
        return True
    hits = sum(1 for w in MODERN_MARKERS if w in line)
    # 现代文字常有较长无标点段落、或出现排比式学术表达
    if hits >= 2:
        return True
    return False


def clean(path, name):
    txt = open(path, encoding="utf-8").read()
    orig_len = len(txt)

    # ---- 第1步：砍掉开头的现代序 ----
    start = find_real_start(txt)
    body = txt[start:] if start > 0 else txt
    log(f"  {name}: 原文起点在第 {start} 字符处（砍掉序言 {start} 字）")

    # ---- 第2步：行级过滤 ----
    lines = body.split("\n")
    kept = []
    dropped = 0
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        if is_modern_line(s):
            dropped += 1
            continue
        kept.append(s)
    body = "\n".join(kept)
    log(f"    行过滤: 丢弃 {dropped} 行现代文字")

    # ---- 第3步：现代标记词残留检查 ----
    remain = {}
    for w in MODERN_MARKERS:
        c = body.count(w)
        if c > 0:
            remain[w] = c
    if remain:
        log(f"    残留现代标记词: {dict(list(remain.items())[:6])}")

    return body, {"orig": orig_len, "clean": len(body),
                  "cut_head": start, "dropped_lines": dropped}


def main():
    log("=" * 74)
    log("S22-6 清洗对照语料中的现代污染")
    log("=" * 74)

    stats = {}
    for f in sorted(os.listdir(CMP)):
        if not f.endswith(".txt"):
            continue
        p = os.path.join(CMP, f)
        name = f.replace(".txt", "")
        log(f"\n--- {name} ---")

        cleaned, st = clean(p, name)
        if len(cleaned) < 10000:
            log(f"  ⚠ 清洗后过短（{len(cleaned)} 字），跳过")
            continue

        # 备份原文
        bak = p + ".orig"
        if not os.path.exists(bak):
            shutil.copyfile(p, bak)

        with open(p, "w", encoding="utf-8") as fh:
            fh.write(cleaned)

        st["name"] = name
        stats[name] = st
        log(f"  ✅ {st['orig']:,} → {st['clean']:,} 字 "
            f"（保留 {st['clean']/st['orig']*100:.1f}%）")

    # 复查
    log("\n" + "=" * 74)
    log("复查：清洗后各组现代标记词密度")
    log("=" * 74)
    log(f"  {'语料':<22}{'字数':>10}{'现代词密度(每万字)':>20}")
    for name, st in stats.items():
        txt = open(os.path.join(CMP, name + ".txt"), encoding="utf-8").read()
        hits = sum(txt.count(w) for w in MODERN_MARKERS)
        dens = hits / len(txt) * 10000 if txt else 0
        log(f"  {name:<22}{len(txt):>10,}{dens:>20.2f}")

    # 对照：红楼三组
    log(f"\n  {'《红楼梦》三组':<22}")
    for ver, d in (("抚琴居前80", r"D:\Programming\Red\hlm_corpus\fuqinju"),
                   ("程高后40", r"D:\Programming\Red\hlm_corpus\chenggao"),
                   ("癸酉本后28", r"D:\Programming\Red\hlm_corpus\guiyouben")):
        tot = ""
        hits = 0
        n = 0
        for fn in os.listdir(d):
            if not fn.endswith(".txt"):
                continue
            t = open(os.path.join(d, fn), encoding="utf-8").read()
            n += len(t)
            hits += sum(t.count(w) for w in MODERN_MARKERS)
        dens = hits / n * 10000 if n else 0
        log(f"  {ver:<22}{n:>10,}{dens:>20.2f}")

    with open(os.path.join(CMP, "_clean_stats.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=1)
    log(f"\n统计已存: {os.path.join(CMP, '_clean_stats.json')}")


if __name__ == "__main__":
    main()
