# -*- coding: utf-8 -*-
"""
S29-2 · 脂批重提取 v5：按「行尾」判断批语是否续行
================================================

v4 的问题
--------
v4 的规则是「正文一出现就结束批语」。但实际排版是**行内夹批**：

    第1行：[正文A][◆][批语第1段]                    ← 行尾是批语，应该续行
    第2行：[批语第2段][正文B]                        ← 行尾是正文，批语在此结束

v4 在第1行遇到正文A 就 close 了，于是把一条批语劈成两条：
    '看他所写开卷之第'  +  '一个女子便用此二语以订终身…'
诊断显示 61/109 条截断是这一原因。

v5 规则（按行处理）
------------------
对每一行，只看 size>=10 的 span，取**按 x 排序后的最后一个**：

  · 若最后一个 span 是批语（19.x KaiTi）      → 批语续到下一行，不结束
  · 若最后一个 span 是正文（22.9）             → 批语在本行结束

这样「夹在正文中的批语」无论跨多少行都能连续收全，而批语一旦让位给正文就正确结束。

其它修正
--------
  1. 15.0~16.5 号 KaiTi  → 行内异文小注（如「(以)」），收进批语文本
  2. 圈号 ①②③          → 结束批语并跳过后续校注，直到下一个 ◆
  3. 22.9 EUDC 标记      → 回前/回末批（总评）开始（同 v4）
  4. 22.9 无标记楷体      → 凡例正文，按正文处理
  5. 页脚 1.9 号         → 忽略且**不结束**批语（跨页续写）
  6. 版本归属沿用 v4 的 EUDC 码映射

运行：python s29_2_reextract_pizhu_v5.py
"""
import os
import re
import sys
import json
import collections

sys.stdout.reconfigure(encoding="utf-8")
import fitz

PDF = r"D:\Programming\Red\抚琴居校本（簡體大字本）.pdf"
OUT_DIR = r"D:\Programming\Red\hlm_corpus"
OUT = os.path.join(OUT_DIR, "pizhu_v5.jsonl")
REPORT = os.path.join(OUT_DIR, "pizhu_v5_report.json")

# ---- 字号常量（实测） ----
KAI_LO, KAI_HI = 18.5, 19.6          # 行内批语
INLINE_LO, INLINE_HI = 14.6, 16.8    # 行内异文小注（15.2）
BODY_SIZE = 22.9
BIGMARK_LO, BIGMARK_HI = 21.5, 24.0  # 回前/回末批标记
HDR_SIZE = 17.2
FOOT_SIZE_MAX = 3.0
NOTE_MARK_MAX = 12.5
BIG_NOTE_MAXLEN = 400

Y_LINE_TOL = 12
EUDC_LO, EUDC_HI = 0xE000, 0xF8FF
CIRCLED = set(chr(c) for c in range(0x2460, 0x2474)) | set(chr(c) for c in range(0x2776, 0x2790))
SEP = {"◇", "◆", "●", "○", "◎"}

EUDC_VERSION = {
    0xE04F: "甲戌本", 0xE051: "己卯本", 0xE052: "庚辰本", 0xE053: "戚序本",
    0xE054: "蒙府本", 0xE055: "列藏本", 0xE056: "杨藏本", 0xE057: "甲辰本",
}

CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
          "八": 8, "九": 9, "十": 10, "百": 100, "零": 0, "〇": 0, "两": 2}
HUI_PAT = re.compile(r"第\s*([一二三四五六七八九十百零〇两\d]{1,4})\s*回")


def cn2num(s):
    s = (s or "").strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if "百" in s:
        a, _, b = s.partition("百")
        base = (CN_NUM.get(a, 1) if a else 1) * 100
        rest = 0
        if b:
            if "十" in b:
                x, _, y = b.partition("十")
                rest = (CN_NUM.get(x, 1) if x else 1) * 10 + (CN_NUM.get(y, 0) if y else 0)
            else:
                rest = sum(CN_NUM.get(c, 0) for c in b)
        return base + rest
    if "十" in s:
        a, _, b = s.partition("十")
        return (CN_NUM.get(a, 1) if a else 1) * 10 + (CN_NUM.get(b, 0) if b else 0)
    return CN_NUM.get(s)


def code_to_versions(code):
    vs = []
    for ch in (code or ""):
        n = EUDC_VERSION.get(ord(ch))
        if n and n not in vs:
            vs.append(n)
    return vs


def is_eudc(ch):
    return EUDC_LO <= ord(ch) <= EUDC_HI


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
    for ln in lines:
        ln.sort(key=lambda z: z[1])
    return lines


def build_page_hui(doc):
    anchors = {}
    for i in range(doc.page_count):
        found = None
        for (y, x, size, font, t) in page_spans(doc[i]):
            if y > 95 or abs(size - HDR_SIZE) > 0.8:
                continue
            m = HUI_PAT.search(t)
            if m:
                n = cn2num(m.group(1))
                if n and 1 <= n <= 120:
                    found = n
                    break
        if found is None:
            for (y, x, size, font, t) in page_spans(doc[i]):
                if size >= 30:
                    m = HUI_PAT.search(t.replace(" ", ""))
                    if m:
                        n = cn2num(m.group(1))
                        if n and 1 <= n <= 120:
                            found = n
                            break
        if found:
            anchors[i] = found
    page_hui = {}
    cur = 0
    for i in range(doc.page_count):
        if i in anchors:
            cur = anchors[i]
        page_hui[i] = cur
    return page_hui, anchors


def is_big_text(size, font):
    """22.9 号楷体 = 回前/回末批正文（凡例正文同为 22.9 楷体，靠「有无 bigmark」区分）"""
    return abs(size - BODY_SIZE) < 1.2 and ("KaiTi" in font)


def classify(size, font):
    """返回 span 的类型"""
    if FOOT_SIZE_MAX >= size:
        return "foot"
    if (KAI_LO <= size <= KAI_HI) and "EUDC" in font:
        return "mark"                     # ◆ 行内批语标记
    if (BIGMARK_LO <= size <= BIGMARK_HI) and "EUDC" in font:
        return "bigmark"                  # ◆ 回前/回末批标记
    if (KAI_LO <= size <= KAI_HI) and "KaiTi" in font:
        return "annot"
    if (INLINE_LO <= size <= INLINE_HI) and "KaiTi" in font:
        return "inline_note"              # 行内异文小注
    if abs(size - BODY_SIZE) < 1.2:
        return "body"                     # 正文（含凡例 22.9 楷体）
    if size <= NOTE_MARK_MAX:
        return "note_mark"                # 校注圈号
    return "other"


def main():
    print("=" * 78)
    print("S29-2 脂批重提取 v5（行尾判定续行）")
    print("=" * 78)
    doc = fitz.open(PDF)
    print(f"页数: {doc.page_count}")
    page_hui, anchors = build_page_hui(doc)
    print(f"页眉回次锚点: {len(anchors)} 页")

    records = []
    cur = None
    state = "body"
    n_close_lineend = 0
    n_close_mark = 0
    n_drop_big = 0
    n_drop_fanli = 0
    n_big = 0
    n_inline = 0

    def close(reason=None):
        nonlocal cur, n_close_lineend, n_close_mark, n_drop_big, n_drop_fanli
        if cur is not None:
            txt = "".join(cur["chars"])
            txt = re.sub(r"[ \u3000]+", "", txt).strip()
            txt = re.sub(r"^[◇◆●○◎\s]+", "", txt)
            txt = re.sub(r"[◇◆●○◎\s]+$", "", txt)
            txt = re.sub(r"[◇◆●○◎]{2,}", "◇", txt)
            if len(txt) >= 2:
                if cur.get("kind") == "big" and len(txt) > BIG_NOTE_MAXLEN:
                    n_drop_big += 1
                elif txt.startswith("此开卷第一回也"):
                    n_drop_fanli += 1
                else:
                    records.append({
                        "hui": cur["hui"], "page": cur["page"], "text": txt,
                        "kind": cur.get("kind", "inline"),
                        "code": cur.get("code", ""),
                        "versions_legend": code_to_versions(cur.get("code", "")),
                    })
            if reason == "lineend":
                n_close_lineend += 1
            elif reason == "mark":
                n_close_mark += 1
        cur = None

    for pi in range(doc.page_count):
        sp = page_spans(doc[pi])
        if not sp:
            continue
        hui = page_hui.get(pi) or 0
        for line in group_lines(sp):
            # 只看 size>=10 的 span 决定「行尾是什么」
            sig = [(y, x, size, font, t) for (y, x, size, font, t) in line if size >= 10]
            if not sig:
                continue
            last_kind = classify(sig[-1][2], sig[-1][3])
            last_is_bigtext = is_big_text(sig[-1][2], sig[-1][3])

            for (y, x, size, font, raw) in sig:
                kind = classify(size, font)

                if kind == "note_mark" and any(c in CIRCLED for c in raw):
                    close("mark")
                    state = "note"
                    continue

                if kind in ("mark", "bigmark"):
                    close("mark")
                    code = "".join(ch for ch in raw if is_eudc(ch))
                    cur = {"hui": hui, "page": pi + 1, "chars": [],
                           "kind": "big" if kind == "bigmark" else "inline",
                           "code": code}
                    if kind == "bigmark":
                        n_big += 1
                    state = "annot"
                    continue

                if kind in ("annot", "inline_note"):
                    if state == "note":
                        continue
                    t = re.sub(r"\s+", "", raw)
                    if not t:
                        continue
                    # span 内混入圈号 → 截断并转 note
                    cut = next((i for i, ch in enumerate(t) if ch in CIRCLED), None)
                    if cut is not None:
                        if cut > 0:
                            if cur is None:
                                cur = {"hui": hui, "page": pi + 1, "chars": [],
                                       "kind": "inline", "code": ""}
                            cur["chars"].append(t[:cut])
                        close("mark")
                        state = "note"
                        continue
                    if cur is None:
                        cur = {"hui": hui, "page": pi + 1, "chars": [],
                               "kind": "inline", "code": ""}
                    if kind == "inline_note":
                        n_inline += 1
                    # span 内混入 ◇ 分隔符 → 切成多条
                    if any(ch in SEP for ch in t):
                        parts = [p for p in re.split(r"[◇◆●○◎]", t)]
                        for k, p in enumerate(parts):
                            p = p.strip()
                            if not p:
                                continue
                            if k == 0:
                                cur["chars"].append(p)
                            else:
                                close("mark")
                                cur = {"hui": hui, "page": pi + 1, "chars": [p],
                                       "kind": cur.get("kind", "inline") if cur else "inline",
                                       "code": ""}
                                state = "annot"
                        continue
                    cur["chars"].append(t)
                    state = "annot"
                    continue

                if kind == "body":
                    # 回前/回末批的正文是 22.9 号楷体，正在收 big 批语时要继续收
                    if (cur is not None and cur.get("kind") == "big"
                            and is_big_text(size, font)):
                        t = re.sub(r"\s+", "", raw)
                        if t:
                            cur["chars"].append(t)
                        state = "annot"
                        continue
                    state = "body"
                    continue

                # foot / other → 忽略，不影响批语状态（允许跨页/跨页脚续写）
                continue

            # ---- 行尾判定 ----
            if state == "annot" and last_kind == "body":
                if cur is not None and cur.get("kind") == "big" and last_is_bigtext:
                    pass                      # 大字批语续行
                else:
                    close("lineend")
                    state = "body"
            elif state == "annot" and last_kind == "note_mark":
                close("mark")

    close("mark")

    # ---------------- 统计 ----------------
    TERM = set("。！？…“”‘’）】》〉」』—～.?!］]")
    MID = set("，、：；,;:")
    n = len(records)
    n_term = sum(1 for r in records if r["text"][-1] in TERM)
    n_mid = sum(1 for r in records if r["text"][-1] in MID)
    lens = [len(r["text"]) for r in records]

    print(f"\n提取批语: {n} 条")
    print(f"  行尾遇正文而结束: {n_close_lineend}")
    print(f"  遇 ◆ 标记而结束 : {n_close_mark}")
    print(f"  收录行内异文小注: {n_inline}")
    print(f"  回前/回末批     : {n_big}")
    print(f"  丢弃凡例条款    : {n_drop_fanli}")
    print(f"  丢弃超长 big    : {n_drop_big}")

    print(f"\n结尾字符:")
    print(f"  句末标点（完整）: {n_term} ({n_term/max(1,n)*100:.1f}%)")
    print(f"  句中标点        : {n_mid} ({n_mid/max(1,n)*100:.2f}%)")
    print(f"  其他            : {n-n_term-n_mid} ({(n-n_term-n_mid)/max(1,n)*100:.2f}%)")
    print(f"\n长度: min={min(lens)} max={max(lens)} 中位={sorted(lens)[len(lens)//2]}")
    print(f"总字数: {sum(lens):,}")

    by = collections.Counter(r["hui"] for r in records)
    missing = [h for h in range(1, 81) if by.get(h, 0) == 0]
    print(f"\n覆盖回次: {len(by)} 个；缺失: {missing}")

    # 对比 v4
    v4p = os.path.join(OUT_DIR, "pizhu_v4.jsonl")
    if os.path.exists(v4p):
        v4 = [json.loads(l) for l in open(v4p, encoding="utf-8")]
        v4t = sum(1 for r in v4 if r["text"].strip() and r["text"].strip()[-1] not in TERM)
        print(f"\n=== 对比 v4 ===")
        print(f"  v4: {len(v4)} 条，截断 {v4t} ({v4t/len(v4)*100:.1f}%)，总字 {sum(len(r['text']) for r in v4):,}")
        print(f"  v5: {n} 条，截断 {n-n_term} ({(n-n_term)/n*100:.1f}%)，总字 {sum(lens):,}")

    with open(OUT, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\n已写 {OUT} ({os.path.getsize(OUT):,} B)")

    with open(REPORT, "w", encoding="utf-8") as f:
        json.dump({
            "pdf": PDF, "n_records": n, "n_chars": sum(lens),
            "n_terminal": n_term, "n_midpunct": n_mid,
            "n_truncated": n - n_term,
            "len_min": min(lens), "len_max": max(lens),
            "n_hui": len(by), "missing_hui": missing,
            "closed_by_lineend": n_close_lineend, "closed_by_mark": n_close_mark,
            "n_big": n_big, "n_inline_note": n_inline,
            "by_hui": {str(k): v for k, v in sorted(by.items())},
            "rules": {"annot": [KAI_LO, KAI_HI], "inline_note": [INLINE_LO, INLINE_HI],
                      "body": BODY_SIZE, "bigmark": [BIGMARK_LO, BIGMARK_HI]},
        }, f, ensure_ascii=False, indent=1)

    print("\n=== 第 1 回前 8 条 ===")
    k = 0
    for r in records:
        if r["hui"] != 1:
            continue
        k += 1
        if k > 8:
            break
        print(f"  [{k:02d}] p{r['page']} len={len(r['text']):>3} {r['text'][:62]!r}")


if __name__ == "__main__":
    main()
