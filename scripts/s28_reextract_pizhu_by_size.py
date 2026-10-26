# -*- coding: utf-8 -*-
"""
S28 · 修复脂批截断：按「字号」重新提取
=====================================

问题诊断
--------
用户反馈：阅读界面的脂批在句子中间被切断。实测样例（第 1 回）：

  '何非梦幻，何不通灵？作者托言，不若此裙钗哉？原当有自。受气清浊，
   本无男女[之]别。实愧则有馀，悔又无益之大无可如何之日也！当此，则自欲将
   已往所赖天恩祖德，锦衣纨绔之时，饫甘餍肥之日，背父兄教育之恩，
   负师友规谈之德，以至今'          ← 断在「以至今」

  '明告看者。编述一集，以告天下人：我之罪固不免，然闺阁中本自历历有人，
   万不可因我之不肖，自护己短，一'    ← 断在「一」

根因（从 PDF 逐 span 实测）
---------------------------
抚琴居校本 PDF 的排版：

  · 正文     字号 22.9（SimSun；凡例部分也用 KaiTi）
  · 批语     字号 19.1（KaiTi_GB2312）
  · 批语起始 字号 19.2（EUDC：\ue04f / \ue052 / \ue054，书中显示为◆）
  · 页眉     字号 17.2  ·  页脚 字号 1.9  ·  校注号 字号 11.5

旧提取脚本用 **字体名是不是楷体** 来判断批语（`is_kai`）。
但凡例整段都是楷体 → 正文被当成批语 → 一条批语吞掉整页、
到页末 `flush()` 就断在那里 → 截断 + 跨页正文被混进来。

正确判据是 **字号**：批语恒为 19.1，正文恒为 22.9。

本脚本做法
----------
  1. 逐页取 span（含字体、字号、坐标）
  2. 只保留 18.5 ≤ 字号 ≤ 19.6 的 span（=批语与其起始标记）
  3. 按「行」重组：y 相近视为同一行，行内按 x 排序（关键，见下）
  4. 遇 EUDC 标记或 ◇ 即视为批语边界
  5. **跨页不 flush**：批语延续到下一页时继续接上
  6. 输出 pizhu_v3.jsonl

为什么必须先分组再排序
----------------------
同一视觉行里，批语与正文的 y 可能差 3（如 y=98 与 y=101）。
若直接按 (y,x) 排，正文「编述」会插到批语「明告看者。」前面。
所以先按 y 差 < 12 聚成行，行内按 x 排，才对得上阅读顺序。

运行：
  python s28_reextract_pizhu_by_size.py
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
OUT = os.path.join(OUT_DIR, "pizhu_v3.jsonl")
REPORT = os.path.join(OUT_DIR, "pizhu_v3_report.json")

# ---- 排版常量（实测） ----
SIZE_KAI_MIN, SIZE_KAI_MAX = 18.5, 19.6     # 批语字号区间
SIZE_BODY = 22.9
Y_LINE_TOL = 12                             # 同一行的 y 容差
EUDC_RANGE = (0xE000, 0xF8FF)               # 私用区：批语起始标记
SEP_CHARS = {"◇", "◆", "●", "○"}

# ---- 页码 → 回次（沿用既有映射；缺省用文件名/首行推断） ----
def load_page_hui(doc):
    """扫描每页首行，抓「第N回」，得到 page → hui"""
    import cn2num_local  # noqa  占位，若无则用内联函数
    return {}


CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
          "八": 8, "九": 9, "十": 10, "百": 100, "零": 0, "〇": 0}


def cn2num(s):
    """中文数字 → int（支持 1~120）"""
    s = s.strip()
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


def build_page_hui(doc):
    """page(0-based) → 回次；用每页文本里最后一个「第N回」锚定，向前回溯填充"""
    anchors = {}
    pat = re.compile(r"第\s*([一二三四五六七八九十百零〇\d]{1,4})\s*回")
    for i in range(doc.page_count):
        t = doc[i].get_text("text")
        ms = pat.findall(t)
        for m in ms:
            n = cn2num(m)
            if n and 1 <= n <= 120:
                anchors[i] = n            # 取该页出现的（通常页眉/正文标题）
                break
    # 回溯填充：没锚点的页沿用最近的前一页
    cur = 0
    page_hui = {}
    for i in range(doc.page_count):
        if i in anchors:
            cur = anchors[i]
        page_hui[i] = cur
    return page_hui, anchors


def page_spans(pg):
    """取一页的 span 列表：[(y, x, size, font, text)]"""
    out = []
    d = pg.get_text("dict")
    for b in d["blocks"]:
        for l in b.get("lines", []):
            for s in l.get("spans", []):
                t = s.get("text") or ""
                if not t.strip():
                    continue
                out.append((round(s["bbox"][1], 1), round(s["bbox"][0], 1),
                            round(s["size"], 1), s.get("font", ""), t))
    return out


def group_lines(spans):
    """按 y 聚成行；行内按 x 排序 → 阅读顺序的 span 序列"""
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
    return EUDC_RANGE[0] <= ord(ch) <= EUDC_RANGE[1]


def clean(t):
    """去掉标记字符与多余空白"""
    out = []
    for ch in t:
        if is_eudc(ch):
            continue
        if ch in SEP_CHARS:
            out.append("\u0001")        # 内部边界符，稍后再切
            continue
        if ch in "\r\n\t":
            out.append(" ")
            continue
        out.append(ch)
    s = "".join(out)
    s = re.sub(r"[ \u3000]+", "", s)
    return s


def main():
    print("=" * 74)
    print("S28 按字号重新提取脂批")
    print("=" * 74)

    doc = fitz.open(PDF)
    print(f"PDF: {PDF}")
    print(f"页数: {doc.page_count}")

    page_hui, anchors = build_page_hui(doc)
    print(f"回次锚点: {len(anchors)} 页有「第N回」")

    # ---- 逐页收集批语 span，按标记切分 ----
    records = []
    buf = []
    cur = None            # 当前批语状态 {hui, page, chars}
    n_pages_with_kai = 0
    pending_last = None   # 上一页结尾的批语（可能跨页续写）

    def push(chars, hui, page):
        txt = "".join(chars)
        txt = re.sub(r"[ \u3000]+", "", txt).strip()
        # 去掉纯标记残留
        txt = re.sub(r"^[◇◆●○\s]+", "", txt)
        txt = re.sub(r"[◇◆●○\s]+$", "", txt)
        if len(txt) < 2:
            return
        records.append({"hui": hui, "page": page, "text": txt})

    for pi in range(doc.page_count):
        sp = page_spans(doc[pi])
        if not sp:
            continue
        ordered = group_lines(sp)
        # 只看字号落在批语区间的 span
        kai = [z for z in ordered if SIZE_KAI_MIN <= z[2] <= SIZE_KAI_MAX]
        if not kai:
            continue
        n_pages_with_kai += 1
        hui = page_hui.get(pi) or 0

        for (y, x, size, font, raw) in kai:
            # 该 span 内可能含 EUDC 标记 → 按标记切段
            segs = []
            curseg = []
            for ch in raw:
                if is_eudc(ch) or ch in SEP_CHARS:
                    segs.append(("sep", ch))
                    curseg = []
                else:
                    curseg.append(ch)
            if curseg:
                segs.append(("txt", "".join(curseg)))

            for kind, val in segs:
                if kind == "sep":
                    # 边界：结束当前批语
                    if cur is not None and cur["chars"]:
                        push(cur["chars"], cur["hui"], cur["page"])
                    cur = None
                else:
                    txt = re.sub(r"\s+", "", val)
                    if not txt:
                        continue
                    if cur is None:
                        cur = {"hui": hui, "page": pi + 1, "chars": []}
                    cur["chars"].append(txt)

    # 收尾
    if cur is not None and cur["chars"]:
        push(cur["chars"], cur["hui"], cur["page"])

    print(f"含批语的页: {n_pages_with_kai}")
    print(f"提取批语: {len(records)} 条")

    # ---- 统计与自检 ----
    TERM = set("。！？…“”‘’）】》〉」』—～.?!］]")
    MID = set("，、：；,;:")
    n_term = sum(1 for r in records if r["text"] and r["text"][-1] in TERM)
    n_mid = sum(1 for r in records if r["text"] and r["text"][-1] in MID)
    n_other = len(records) - n_term - n_mid

    print("\n=== 结尾字符分布（截断指标）===")
    print(f"  句末标点（完整）: {n_term} ({n_term/max(1,len(records))*100:.1f}%)")
    print(f"  句中标点（可能未完）: {n_mid}")
    print(f"  其他字符: {n_other}")

    lens = [len(r["text"]) for r in records]
    print(f"\n长度: min={min(lens)} max={max(lens)} 中位={sorted(lens)[len(lens)//2]}")
    print(f"总字数: {sum(lens):,}")

    # 每回条数
    by_hui = collections.Counter(r["hui"] for r in records)
    print(f"\n覆盖回次: {len(by_hui)} 个（{min(by_hui) if by_hui else '-'} ~ {max(by_hui) if by_hui else '-'}）")

    # ---- 对比旧数据 ----
    old_path = os.path.join(OUT_DIR, "pizhu_redecoded.jsonl")
    old = [json.loads(l) for l in open(old_path, encoding="utf-8")] if os.path.exists(old_path) else []
    print(f"\n=== 对比旧数据（{len(old)} 条）===")
    if old:
        def is_trunc(t):
            t = (t or "").strip()
            return bool(t) and t[-1] not in TERM
        old_tr = sum(1 for r in old if is_trunc(r.get("text", "")))
        print(f"  旧：截断 {old_tr} 条 ({old_tr/len(old)*100:.1f}%)")
        print(f"  新：见上方「句末标点」比例")

    # ---- 输出 ----
    with open(OUT, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\n已写 {OUT}  ({os.path.getsize(OUT):,} B)")

    with open(REPORT, "w", encoding="utf-8") as f:
        json.dump({
            "pdf": PDF,
            "n_records": len(records),
            "n_chars": sum(lens),
            "n_terminal": n_term,
            "n_midpunct": n_mid,
            "n_other": n_other,
            "len_min": min(lens), "len_max": max(lens),
            "n_hui": len(by_hui),
            "by_hui": {str(k): v for k, v in sorted(by_hui.items())},
            "old_n": len(old),
            "size_rule": {"annotation_min": SIZE_KAI_MIN, "annotation_max": SIZE_KAI_MAX,
                          "body": SIZE_BODY},
        }, f, ensure_ascii=False, indent=1)
    print(f"报告 {REPORT}")

    # 抽样打印
    print("\n=== 第 1 回前 8 条 ===")
    n = 0
    for r in records:
        if r["hui"] != 1:
            continue
        n += 1
        if n > 8:
            break
        print(f"  [{n:02d}] page={r['page']} len={len(r['text'])}")
        print(f"       {r['text']!r}")


if __name__ == "__main__":
    main()
