# -*- coding: utf-8 -*-
"""
S8 · 从 PDF 重新提取抚琴居校本的回目（修复乱码的真正根因）
=============================================================

用户的判断是对的
----------------
「抚琴居 PDF 原文肯定不是乱码，所以应该是从 pdf 提取文本时就出问题了」

实测确认：**PDF 文本层是完好的**。
    p16   : '第七十八回 '
            ' 老学士闲征姽婳词 痴公子杜撰芙蓉诔 '
    p2087 : '第 七 十 八 回 '
            '老学士闲征姽婳词 '
    p2089 : '第七十八回 老学士闲征 姽婳词 痴公子杜撰芙蓉诔 '

真正的根因
----------
现有 `_extract_fuqinju.py` 第 35 行：
    TITLES = {int(k): v for k, v in json.load(
        open(os.path.join(ROOT, "huimu.json"), ...))['zhiping'].items()}
把回目**写死为 huimu.json 的 zhiping 字段** —— 而那个字段本身就是坏的
（第 64 回「九龙�」、第 78 回「闲征��词」）。
PDF 里明明有正确文字，却从没被用上。

也就是说：不是 PDF 提取坏了，是**根本没有从 PDF 提取回目**。

本脚本做法
----------
从 PDF 文本层直接提取全部 80 回回目：
  1. 定位「第X回」所在行（可能字间带空格，如「第 七 十 八 回」）
  2. 回目正文可能在同页的后续行，或目录页（p16 附近）
  3. 清洗：去多余空格、校验无 U+FFFD
  4. 与 huimu.json 的 cgb（程高本）交叉核对（第1-80回两本回目文字一致）

输出：
  hlm_corpus/clean/huimu_pdf.json       {hui: "第78回 老学士闲征姽婳词 痴公子杜撰芙蓉诔"}
  hlm_corpus/clean/huimu.json          原地修复 zhiping / cgb 字段的 U+FFFD

运行：
  python s8_1_rehuimu_from_pdf.py
"""
import os
import re
import sys
import json
import unicodedata

sys.stdout.reconfigure(encoding="utf-8")

SRC = r"D:\Programming\Red\抚琴居校本（簡體大字本）.pdf"
ROOT = r"D:\Programming\Red\hlm_corpus"
CLEAN = os.path.join(ROOT, "clean")
PDF_HUIMU = os.path.join(CLEAN, "huimu_pdf.json")

CN = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8,
      '九': 9, '零': 0, '〇': 0, '十': 10, '百': 100}

HUI_HEAD = re.compile(r'第\s*([一二三四五六七八九十百零〇][一二三四五六七八九十百零〇\s]{0,8}?)\s*回')


def cn2num(s: str) -> int:
    """中文数字 → int（支持「七十八」「八十」「一百」）"""
    s = re.sub(r'\s', '', s)
    total = cur = 0
    for ch in s:
        if ch == '百':
            total += (cur or 1) * 100
            cur = 0
        elif ch == '十':
            total += (cur or 1) * 10
            cur = 0
        elif ch in CN:
            cur = CN[ch]
    return total + cur


def clean_title(t: str) -> str:
    """清洗回目文本：去首尾空白、合并内部空格、去尾巴页码"""
    t = unicodedata.normalize('NFC', t)
    t = re.sub(r'\s+', ' ', t).strip()
    # 去掉末尾可能粘上的页码 / 站名
    t = re.sub(r'\s*(www\.hlmbbs\.com|redactor.*|\d{1,4})\s*$', '', t).strip()
    return t


def main():
    print("=" * 74)
    print("S8-1 从 PDF 重新提取抚琴居校本回目")
    print("=" * 74)

    from pypdf import PdfReader
    print(f"打开 PDF: {SRC}")
    reader = PdfReader(SRC)
    n_pages = len(reader.pages)
    print(f"总页数: {n_pages}")

    # ---------- 1) 目录页（通常在前 30 页）：一次性拿到 80 条回目 ----------
    toc_hits = {}          # hui -> title
    print("\n[1] 扫描目录页（前 40 页）")
    for i in range(min(40, n_pages)):
        try:
            t = reader.pages[i].extract_text() or ""
        except Exception:
            continue
        lines = [x.strip() for x in t.split("\n")]
        for j, ln in enumerate(lines):
            m = HUI_HEAD.search(ln)
            if not m:
                continue
            try:
                hui = cn2num(m.group(1))
            except Exception:
                continue
            if not (1 <= hui <= 80):
                continue
            # 回目正文：优先取「第X回」之后的同段剩余文字
            rest = ln[m.end():].strip()
            if len(re.sub(r'\s', '', rest)) >= 4:
                title = clean_title(rest)
            else:
                # 否则取下一行（目录页一般一行一回）
                nxt = lines[j + 1] if j + 1 < len(lines) else ""
                title = clean_title(nxt)
            if title and hui not in toc_hits:
                toc_hits[hui] = title
    print(f"  目录页命中: {len(toc_hits)} 条")

    # ---------- 2) 正文回首页：逐回补齐 ----------
    # 正文里「第X回」出现在回首页，回目在其后的行
    print("\n[2] 扫描正文回首页（全书）")
    body_hits = {}         # hui -> [(page, title)]
    # 正文从第 20 页左右开始；为提速只在 toc 缺失时精扫
    need = set(range(1, 81)) - set(toc_hits.keys())
    if need:
        print(f"  目录缺 {len(need)} 条，正文精扫中…")
        for i in range(min(40, n_pages), n_pages):
            try:
                t = reader.pages[i].extract_text() or ""
            except Exception:
                continue
            if "第" not in t or "回" not in t:
                continue
            lines = [x.strip() for x in t.split("\n")]
            for j, ln in enumerate(lines):
                m = HUI_HEAD.search(ln)
                if not m:
                    continue
                try:
                    hui = cn2num(m.group(1))
                except Exception:
                    continue
                if hui not in need:
                    continue
                # 回目：本行剩余 + 后续 1-2 行拼起来
                parts = []
                rest = ln[m.end():].strip()
                if rest:
                    parts.append(rest)
                for k in range(j + 1, min(j + 3, len(lines))):
                    if lines[k]:
                        parts.append(lines[k])
                    if len(re.sub(r'\s', '', "".join(parts))) >= 8:
                        break
                title = clean_title(" ".join(parts))
                if title:
                    body_hits.setdefault(hui, []).append((i, title))
    for hui, lst in body_hits.items():
        if hui not in toc_hits and lst:
            toc_hits[hui] = lst[0][1]
    print(f"  正文补齐后合计: {len(toc_hits)} 条")

    # ---------- 3) 组装并清洗 ----------
    print("\n[3] 组装回目")
    result = {}
    for hui in sorted(toc_hits):
        body = toc_hits[hui]
        # 去掉可能残留的前导「第X回」
        body = HUI_HEAD.sub("", body).strip()
        body = clean_title(body)
        if not body or len(body) < 4:
            continue
        result[hui] = f"第{hui}回 {body}"

    # ---------- 4) 与程高本交叉核对 ----------
    print("\n[4] 与 huimu.json 的 cgb（程高本）交叉核对")
    hj_path = os.path.join(CLEAN, "huimu.json")
    hj = json.load(open(hj_path, encoding="utf-8"))
    cgb = hj.get("cgb", {})
    mismatch = []
    for hui, pdf_title in result.items():
        ref = cgb.get(str(hui), "")
        if not ref:
            continue
        # 去掉「第X回」前缀后比正文
        a = re.sub(r'^第\d+回\s*', '', pdf_title)
        b = re.sub(r'^第\d+回\s*', '', ref)
        if re.sub(r'\s', '', a) != re.sub(r'\s', '', b):
            mismatch.append((hui, pdf_title, ref))
    if mismatch:
        print(f"  与程高本有 {len(mismatch)} 处差异（版本差异或提取误差）：")
        for h, a, b in mismatch[:10]:
            print(f"    第{h}回")
            print(f"      PDF : {a}")
            print(f"      程高: {b}")
    else:
        print("  ✅ 全部与程高本一致")

    # ---------- 5) 重点检查原来的乱码处 ----------
    print("\n[5] 重点检查原乱码处（第 64 / 78 回）")
    for h in (64, 78):
        old = hj.get("zhiping", {}).get(str(h), "")
        new = result.get(h, "")
        bad_old = "\ufffd" in old
        bad_new = "\ufffd" in new
        print(f"  第{h}回")
        print(f"    旧（huimu.json zhiping）: {old!r}  {'❌含乱码' if bad_old else '✅'}")
        print(f"    新（PDF 提取）          : {new!r}  {'❌含乱码' if bad_new else '✅'}")

    # ---------- 6) 写文件 ----------
    print("\n[6] 写出")
    with open(PDF_HUIMU, "w", encoding="utf-8") as f:
        json.dump({str(k): v for k, v in result.items()}, f,
                  ensure_ascii=False, indent=1)
    print(f"  {PDF_HUIMU}  ({len(result)} 条)")

    # 原地修复 huimu.json：zhiping 用 PDF 结果覆盖；cgb 清掉 U+FFFD
    fixed = 0
    for key in ("zhiping", "cgb"):
        d = hj.get(key, {})
        for hui_s in list(d.keys()):
            try:
                hui = int(hui_s)
            except ValueError:
                continue
            if "\ufffd" not in d[hui_s]:
                continue
            # 优先用 PDF 结果（抚琴居本 PDF 提取）
            if hui in result:
                d[hui_s] = result[hui]
                fixed += 1
    # zhiping 整体用 PDF 结果替换（前 80 回）
    hj["zhiping"] = {str(k): v for k, v in result.items()}
    hj["note"] = (hj.get("note", "") +
                  " ｜v2（2026-10-24）：zhiping 回目改为从抚琴居 PDF 文本层直接提取，"
                  "修复第64/78回 U+FFFD 乱码。")
    with open(hj_path, "w", encoding="utf-8") as f:
        json.dump(hj, f, ensure_ascii=False, indent=1)
    print(f"  {hj_path}  修复 {fixed} 处乱码")

    # ---------- 7) 最终校验 ----------
    print("\n" + "=" * 74)
    print("最终校验")
    print("=" * 74)
    total_bad = sum(1 for v in result.values() if "\ufffd" in v)
    print(f"  回目总数: {len(result)} / 80")
    print(f"  含 U+FFFD 的: {total_bad}")
    missing = [h for h in range(1, 81) if h not in result]
    if missing:
        print(f"  ⚠ 缺失回次: {missing}")
    # 抽样
    print("\n  抽样 10 条:")
    for h in (1, 3, 5, 8, 19, 27, 64, 70, 78, 80):
        v = result.get(h, "(缺)")
        print(f"    {v}")


if __name__ == "__main__":
    main()
