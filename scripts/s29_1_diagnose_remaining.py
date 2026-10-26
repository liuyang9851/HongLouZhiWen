# -*- coding: utf-8 -*-
"""
S29-1 · 诊断剩余截断：到底是什么打断了批语
==========================================

背景
----
S28 用「字号」重提取后，截断从 35.9% 降到 2.3%（109 条）。
本脚本针对这 109 条，逐条回到 PDF，看**批语最后那个 span 之后紧跟的是什么**，
把打断原因分类，为下一步修复提供依据。

输出：每个截断条目 → 页面、最后 span（size/font）、下一个 span（size/font/文本）

运行：python s29_1_diagnose_remaining.py
"""
import os
import re
import sys
import json
import collections

sys.stdout.reconfigure(encoding="utf-8")
import fitz

PDF = r"D:\Programming\Red\抚琴居校本（簡體大字本）.pdf"
SRC = r"D:\Programming\Red\hlm_corpus\pizhu_v4.jsonl"
OUT = r"D:\Programming\Red\hlm_corpus\s29_truncation_diagnosis.json"

TERM = set("。！？…“”‘’）】》〉」』—～.?!］]")
KAI_LO, KAI_HI = 18.5, 19.6
Y_LINE_TOL = 12
EUDC_LO, EUDC_HI = 0xE000, 0xF8FF


def page_spans(pg):
    out = []
    for b in pg.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            for s in l.get("spans", []):
                t = s.get("text") or ""
                if not t.strip():
                    continue
                out.append((round(s["bbox"][1], 1), round(s["bbox"][0], 1),
                            round(s["size"], 1), s.get("font", ""), t))
    return out


def group_lines(spans):
    ss = sorted(spans, key=lambda z: (z[0], z[1]))
    lines = []
    for sp in ss:
        if lines and abs(sp[0] - lines[-1][-1][0]) <= Y_LINE_TOL:
            lines[-1].append(sp)
        else:
            lines.append([sp])
    ordered = []
    for ln in lines:
        ln.sort(key=lambda z: z[1])
        ordered.extend(ln)
    return ordered


def main():
    rows = [json.loads(l) for l in open(SRC, encoding="utf-8")]
    trunc = [r for r in rows if r["text"].strip() and r["text"].strip()[-1] not in TERM]
    print(f"v4 共 {len(rows)} 条，截断 {len(trunc)} 条")
    doc = fitz.open(PDF)

    diag = []
    cause = collections.Counter()

    for r in trunc:
        pno = (r.get("page") or 1) - 1
        tail = r["text"].strip()[-12:]
        sp = page_spans(doc[pno])
        ordered = group_lines(sp)

        # 在页面里定位这个批语的最后一段文本（用尾部 8 字匹配）
        needle = r["text"].strip()[-8:]
        last_idx = None
        for i, (y, x, size, font, t) in enumerate(ordered):
            flat = re.sub(r"\s+", "", t)
            if KAI_LO <= size <= KAI_HI and "EUDC" not in font and needle and needle[-4:] in flat:
                last_idx = i
        nxt = None
        if last_idx is not None:
            for j in range(last_idx + 1, min(last_idx + 4, len(ordered))):
                y, x, size, font, t = ordered[j]
                if not t.strip():
                    continue
                nxt = {"size": size, "font": font, "text": t[:40],
                       "eudc": "EUDC" in font,
                       "body": abs(size - 22.9) < 1.2}
                break

        rec = {"page": r["page"], "hui": r["hui"], "len": len(r["text"]),
               "tail": tail, "next": nxt}
        diag.append(rec)

        if nxt is None:
            cause["页内找不到后续 span（跨页或末尾）"] += 1
        elif nxt["eudc"]:
            cause["下一个是 ◆ 标记（批语确实在此结束）"] += 1
        elif nxt["body"]:
            cause["下一个是正文 22.9（正常结束）"] += 1
        elif nxt["size"] < KAI_LO:
            cause[f"下一个是小字号 {nxt['size']}（可能是引文/校注）"] += 1
        elif nxt["size"] > KAI_HI:
            cause[f"下一个是大字号 {nxt['size']}"] += 1
        else:
            cause[f"其他 {nxt['size']}/{nxt['font'][:16]}"] += 1

    print("\n=== 打断原因分布 ===")
    for k, v in cause.most_common():
        print(f"  {v:>4}  {k}")

    print("\n=== 样本（按原因挑）===")
    shown = collections.Counter()
    for d in diag:
        key = "none" if d["next"] is None else (
            "eudc" if d["next"]["eudc"] else
            "body" if d["next"]["body"] else f"{d['next']['size']}")
        if shown[key] >= 4:
            continue
        shown[key] += 1
        nx = d["next"]
        print(f"\n  [p{d['page']} hui{d['hui']}] 尾={d['tail']!r}")
        if nx:
            print(f"     next: size={nx['size']} font={nx['font'][:22]!r} {nx['text']!r}")
        else:
            print("     next: (无)")

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"n_trunc": len(trunc), "causes": dict(cause), "items": diag},
                  f, ensure_ascii=False, indent=1)
    print(f"\n已写 {OUT}")

    # 顺便统计全文批语字号区间的字体分布，看有没有漏掉的字体
    print("\n=== 批语字号区间(18.5~19.6)的字体分布（全书）===")
    fonts = collections.Counter()
    for i in range(doc.page_count):
        for (y, x, size, font, t) in page_spans(doc[i]):
            if KAI_LO <= size <= KAI_HI:
                fonts[font] += 1
    for f_, n in fonts.most_common(12):
        print(f"  {f_:<28} {n:>6}")


if __name__ == "__main__":
    main()
