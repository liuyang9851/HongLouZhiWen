# -*- coding: utf-8 -*-
"""
S28-2 · 脂批重提取 v2（修正校注混入 / 跨页失控 / 回次缺失）
=========================================================

v1 的三个缺陷与修法
------------------
| 缺陷 | 现象 | 根因 | v2 修法 |
|:---|:---|:---|:---|
| ① 校注混入批语 | 出现 9478 字的条目，尾部落到「下回分解」 | 现代编者的校注（①②③…）与脂批同为 19.1 号字 | 遇到圈号即结束当前批语，并进入「跳过」状态，直到下一个 ◆ 标记 |
| ② 跨页失控 | 批语一直不结束，逐页累积 | 只在 ◆ 标记处结束；正文恢复时没有结束 | **正文（22.9 号）一出现就结束批语** |
| ③ 回次缺失 | 29/59/67/68/69 回无批语 | 用「本页第一个第N回」锚定，会被正文里的「第N回」误导 | 改按**页眉**（y<95、17.2 号、含「第N回」）锚定 |

排版事实（实测）
----------------
  正文        22.9 号 SimSun（凡例段落亦为 22.9 号 KaiTi）
  脂批        19.1 号 KaiTi_GB2312
  批语起始 ◆  19.2 号 EUDC（\\ue04f / \\ue051 / \\ue052 / \\ue053 / \\ue054 等）
  页眉        17.2 号（单页＝书名，双页＝「第N回 回目」）
  页脚        1.9 号  ·  校注圈号 11.5 号 SimSun

运行：
  python s28_2_reextract_pizhu_v2.py
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
OUT = os.path.join(OUT_DIR, "pizhu_v4.jsonl")
REPORT = os.path.join(OUT_DIR, "pizhu_v4_report.json")

# 字号
KAI_LO, KAI_HI = 18.5, 19.6     # 脂批（含 ◆ 标记 19.2）
BODY_SIZE = 22.9
HDR_SIZE = 17.2
NOTE_MARK_MAX = 12.5            # 校注圈号字号 11.5
BIGMARK_LO, BIGMARK_HI = 21.5, 24.0   # 回前/回末批标记字号（22.9）
BIG_NOTE_MAXLEN = 400           # 超过此长度视为凡例正文，丢弃

Y_LINE_TOL = 12
EUDC_LO, EUDC_HI = 0xE000, 0xF8FF
CIRCLED = set(chr(c) for c in range(0x2460, 0x2474))      # ①..⑳
CIRCLED |= set(chr(c) for c in range(0x2776, 0x2790))     # ❶..➓
SEP = {"◇", "◆", "●", "○", "◎"}

# 版式标记码 → 脂本名（从旧数据 3234 条单字符 code 统计得出，唯一对应）
EUDC_VERSION = {
    0xE04F: "甲戌本", 0xE051: "己卯本", 0xE052: "庚辰本", 0xE053: "戚序本",
    0xE054: "蒙府本", 0xE055: "列藏本", 0xE056: "杨藏本", 0xE057: "甲辰本",
}

def code_to_versions(code):
    vs = []
    for ch in (code or ""):
        n = EUDC_VERSION.get(ord(ch))
        if n and n not in vs:
            vs.append(n)
    return vs


CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
          "八": 8, "九": 9, "十": 10, "百": 100, "零": 0, "〇": 0, "两": 2}


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


HUI_PAT = re.compile(r"第\s*([一二三四五六七八九十百零〇两\d]{1,4})\s*回")


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
    """按 y 聚行，行内按 x 排序 → 阅读顺序"""
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


def is_eudc(ch):
    return EUDC_LO <= ord(ch) <= EUDC_HI


def build_page_hui(doc):
    """
    只用**页眉**锚定回次：y < 95、字号≈17.2、含「第N回」
    双页页眉格式：「第一回 甄士隐梦幻识通灵 贾雨村风尘怀闺秀」
    单页页眉为书名，无回次 → 沿用
    """
    anchors = {}
    for i in range(doc.page_count):
        found = None
        for (y, x, size, font, t) in page_spans(doc[i]):
            if y > 95:
                continue
            if abs(size - HDR_SIZE) > 0.8:
                continue
            m = HUI_PAT.search(t)
            if m:
                n = cn2num(m.group(1))
                if n and 1 <= n <= 120:
                    found = n
                    break
        # 章首大标题（34.4 号「第 一 回」）
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

    # 回溯填充 + 前向填充
    page_hui = {}
    cur = 0
    for i in range(doc.page_count):
        if i in anchors:
            cur = anchors[i]
        page_hui[i] = cur
    return page_hui, anchors


def main():
    print("=" * 76)
    print("S28-2 脂批重提取 v2（字号 + 圈号 + 正文边界 + 页眉锚定）")
    print("=" * 76)

    doc = fitz.open(PDF)
    print(f"页数: {doc.page_count}")
    page_hui, anchors = build_page_hui(doc)
    print(f"页眉回次锚点: {len(anchors)} 页")

    records = []
    cur = None            # {'hui','page','chars'}
    n_skip_note = 0
    n_closed_by_body = 0
    n_closed_by_mark = 0
    n_drop_big = 0
    n_drop_fanli = 0
    n_big = 0
    pending_code = [""]

    def close(reason=None):
        nonlocal cur, n_closed_by_body, n_closed_by_mark, n_drop_big, n_drop_fanli
        if cur is not None:
            chars = cur["chars"]
            txt = "".join(chars)
            txt = re.sub(r"[ \u3000]+", "", txt).strip()
            txt = re.sub(r"^[◇◆●○◎\s]+", "", txt)
            txt = re.sub(r"[◇◆●○◎\s]+$", "", txt)
            if len(txt) >= 2:
                if cur.get("kind") == "big" and len(txt) > BIG_NOTE_MAXLEN:
                    n_drop_big += 1
                elif txt.startswith("此开卷第一回也"):
                    n_drop_fanli += 1          # 凡例第五条，非脂批
                else:
                    code = cur.get("code", "")
                    records.append({"hui": cur["hui"], "page": cur["page"],
                                    "text": txt, "kind": cur.get("kind", "inline"),
                                    "code": code,
                                    "versions_legend": code_to_versions(code)})
            if reason == "body":
                n_closed_by_body += 1
            elif reason == "mark":
                n_closed_by_mark += 1
        cur = None

    state = "body"       # body | annot | note

    for pi in range(doc.page_count):
        sp = page_spans(doc[pi])
        if not sp:
            continue
        ordered = group_lines(sp)
        hui = page_hui.get(pi) or 0

        for (y, x, size, font, raw) in ordered:
            is_big_mark = (BIGMARK_LO <= size <= BIGMARK_HI) and ("EUDC" in font)
            is_note_mark = (size <= NOTE_MARK_MAX) and any(c in CIRCLED for c in raw)
            is_marker = (KAI_LO <= size <= KAI_HI) and "EUDC" in font
            is_kai = (KAI_LO <= size <= KAI_HI) and ("EUDC" not in font)
            is_body = (abs(size - BODY_SIZE) < 1.2)

            if is_big_mark:
                # 22.9 号标记 = 回前批 / 回末批（总评类）开始
                close("mark")
                code = "".join(ch for ch in raw if is_eudc(ch))
                cur = {"hui": hui, "page": pi + 1, "chars": [], "kind": "big",
                       "code": code}
                n_big += 1
                state = "bigannot"
                continue

            if is_note_mark:
                # 现代编者校注开始 → 结束当前批语，进入跳过态
                close("mark")
                state = "note"
                n_skip_note += 1
                continue

            if is_marker:
                # ◆ 新批语开始
                close("mark")
                code = "".join(ch for ch in raw if is_eudc(ch))
                cur = {"hui": hui, "page": pi + 1, "chars": [], "kind": "inline",
                       "code": code}
                state = "annot"
                continue

            if is_kai:
                if state == "note":
                    continue           # 校注正文，丢弃
                # 批语正文（可能上页延续）
                if cur is None:
                    cur = {"hui": hui, "page": pi + 1, "chars": [],
                           "kind": "inline", "code": ""}
                # span 内若混入圈号，截断并转入 note
                cut = None
                for i, ch in enumerate(raw):
                    if ch in CIRCLED:
                        cut = i
                        break
                if cut is not None:
                    if cut > 0:
                        cur["chars"].append(re.sub(r"\s+", "", raw[:cut]))
                    close("mark")
                    state = "note"
                    n_skip_note += 1
                    continue
                # span 内若混入 ◆ 类分隔符，同样切
                t = re.sub(r"\s+", "", raw)
                if any(ch in SEP for ch in t):
                    for part in re.split(r"[◇◆●○◎]", t):
                        part = part.strip()
                        if not part:
                            continue
                        if cur is None:
                            cur = {"hui": hui, "page": pi + 1, "chars": []}
                        cur["chars"].append(part)
                        close("mark")
                        state = "annot"
                    continue
                cur["chars"].append(t)
                state = "annot"
                continue

            if (BIGMARK_LO <= size <= BIGMARK_HI) and ("KaiTi" in font):
                if state == "bigannot":
                    t = re.sub(r"\s+", "", raw)
                    if cur is None:
                        cur = {"hui": hui, "page": pi + 1, "chars": [], "kind": "big"}
                    cur["chars"].append(t)
                    continue
                # 无标记的 22.9 楷体 = 凡例正文等，按正文处理
                if state == "annot":
                    close("body")
                state = "body"
                continue

            if is_body:
                # 正文一出现 → 批语结束（这样才不会跨页失控）
                if state == "annot":
                    close("body")
                state = "body"
                continue

            # 页眉/页脚/其他 → 忽略，但若正在收批语且遇到页脚，仅忽略不结束
            continue

    close("mark")

    # ---------------- 统计 ----------------
    TERM = set("。！？…“”‘’）】》〉」』—～.?!］]")
    MID = set("，、：；,;:")
    n = len(records)
    n_term = sum(1 for r in records if r["text"][-1] in TERM)
    n_mid = sum(1 for r in records if r["text"][-1] in MID)
    lens = [len(r["text"]) for r in records]

    print(f"\n提取批语: {n} 条")
    print(f"  由正文出现而结束: {n_closed_by_body}")
    print(f"  由 ◆ 标记而结束 : {n_closed_by_mark}")
    print(f"  跳过校注块       : {n_skip_note}")
    print(f"  丢弃凡例条款     : {n_drop_fanli}")
    print(f"  丢弃超长big块    : {n_drop_big}")
    print(f"\n结尾字符:")
    print(f"  句末标点（完整）: {n_term} ({n_term/max(1,n)*100:.1f}%)")
    print(f"  句中标点        : {n_mid} ({n_mid/max(1,n)*100:.1f}%)")
    print(f"  其他            : {n-n_term-n_mid}")
    print(f"\n长度: min={min(lens)} max={max(lens)} 中位={sorted(lens)[len(lens)//2]}")
    print(f"总字数: {sum(lens):,}")

    long = [r for r in records if len(r["text"]) > 400]
    print(f"\n超长（>400 字）: {len(long)} 条")
    for r in sorted(long, key=lambda x: -len(x["text"]))[:4]:
        print(f"  hui={r['hui']} page={r['page']} len={len(r['text'])}")
        print(f"    尾: {r['text'][-50:]!r}")

    by = collections.Counter(r["hui"] for r in records)
    missing = [h for h in range(1, 81) if by.get(h, 0) == 0]
    print(f"\n覆盖回次: {len(by)} 个；缺失: {missing}")

    # ---------------- 对比 ----------------
    old_path = os.path.join(OUT_DIR, "pizhu_redecoded.jsonl")
    old = [json.loads(l) for l in open(old_path, encoding="utf-8")] if os.path.exists(old_path) else []
    if old:
        def is_trunc(t):
            t = (t or "").strip()
            return bool(t) and t[-1] not in TERM
        ot = sum(1 for r in old if is_trunc(r.get("text", "")))
        print(f"\n=== 对比旧数据 ===")
        print(f"  旧: {len(old)} 条，截断 {ot} ({ot/len(old)*100:.1f}%)，总字 {sum(len(r.get('text','') ) for r in old):,}")
        print(f"  新: {n} 条，句末标点 {n_term} ({n_term/max(1,n)*100:.1f}%)，总字 {sum(lens):,}")

    # ---------------- 输出 ----------------
    with open(OUT, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\n已写 {OUT} ({os.path.getsize(OUT):,} B)")

    with open(REPORT, "w", encoding="utf-8") as f:
        json.dump({
            "pdf": PDF, "n_records": n, "n_chars": sum(lens),
            "n_terminal": n_term, "n_midpunct": n_mid,
            "len_min": min(lens), "len_max": max(lens),
            "n_hui": len(by), "missing_hui": missing,
            "closed_by_body": n_closed_by_body, "closed_by_mark": n_closed_by_mark,
            "skipped_note_blocks": n_skip_note,
            "old_n": len(old), "by_hui": {str(k): v for k, v in sorted(by.items())},
            "rules": {"annotation_size": [KAI_LO, KAI_HI], "body_size": BODY_SIZE,
                      "header_size": HDR_SIZE},
        }, f, ensure_ascii=False, indent=1)

    print("\n=== 第 1 回前 10 条 ===")
    k = 0
    for r in records:
        if r["hui"] != 1:
            continue
        k += 1
        if k > 10:
            break
        print(f"  [{k:02d}] p{r['page']} len={len(r['text'])} {r['text'][:60]!r}")


if __name__ == "__main__":
    main()
