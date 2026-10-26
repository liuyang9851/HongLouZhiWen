# -*- coding: utf-8 -*-
"""
S7-1 · 三版本语料统一构建（修复三个问题）
==========================================

解决的问题
----------
  问题 2 & 7：只有抚琴居本可读、只有抚琴居本进 RAG
      → 本脚本把 程高本 / 癸酉本 也解析成同样的 chapters 结构
  问题 3：第 64 回 / 第 78 回 回目里有 U+FFFD 替换符（「姽婳」变乱码）
      → 回目统一从 `clean/huimu.json` 取（那里有正确的三版本回目对照）

产出
----
  out/chapters_{ver}.jsonl     三个版本的章节（含回目修正）
  out/version_manifest.json    三版本元信息

运行（Windows 本地）：
    python s7_1_build_versions.py
"""
import os
import re
import sys
import json
import glob

sys.stdout.reconfigure(encoding="utf-8")

CORPUS = r"D:\Programming\Red\hlm_corpus"
OUT = os.path.join(r"D:\学校事务\大数据原理与应用", "红楼智问", "pipeline", "out")
os.makedirs(OUT, exist_ok=True)

VERSIONS = {
    "fuqinju": {"dir": "fuqinju", "label": "抚琴居校本", "max_hui": 80},
    "chenggao": {"dir": "chenggao", "label": "程高本", "max_hui": 120},
    "guiyouben": {"dir": "guiyouben", "label": "癸酉本", "max_hui": 108},
}

# huimu.json 的键：cgb=程高本，zhiping=脂评汇校本(前80回，即抚琴居)，guiyou=癸酉本
HUIMU_KEY = {"chenggao": "cgb", "fuqinju": "zhiping", "guiyouben": "guiyou"}


def load_huimu():
    """
    加载回目。

    **v2 修复（用户反馈驱动）**：
    用户指出「抚琴居 PDF 原文肯定不是乱码」—— 实测确认 PDF 文本层完好，
    真正的问题是旧流程把回目写死成 huimu.json 的 zhiping 字段（该字段本身坏了）。
    现在优先用 `huimu_pdf.json`（从 PDF 文本层直接提取，80/80 回零乱码）。
    """
    p = os.path.join(CORPUS, "clean", "huimu.json")
    with open(p, encoding="utf-8") as f:
        data = json.load(f)

    pdf_path = os.path.join(CORPUS, "clean", "huimu_pdf.json")
    if os.path.exists(pdf_path):
        with open(pdf_path, encoding="utf-8") as f:
            pdf_titles = json.load(f)
        # 用 PDF 提取结果覆盖 zhiping（抚琴居本回目的权威来源）
        data["zhiping"] = dict(pdf_titles)
        print(f"  回目来源: huimu_pdf.json（PDF 提取，{len(pdf_titles)} 条）")
    return data


def fix_huimu(h: str, hui: int, huimu: dict) -> str:
    """
    回目里的 U+FFFD 修复。

    v2 修订：原来用 `cgb`（程高本）兜底，但会引入**版本用字差异**：
      第64回 抚琴居本原文是「九龙珮」（玉字旁），程高本是「九龙佩」
      第22回 抚琴居本「制灯谜」，程高本「制灯迷」
    现在 zhiping 已用 PDF 提取结果（零乱码），这个函数只在
    PDF 也缺失时才走到 cgb 兜底。
    """
    """
    修回目里的 U+FFFD 替换符。

    实测发现：`huimu.json` 的 `zhiping`（脂评汇校本/抚琴居）字段本身也是坏的：
        第64回 '…情遗九龙�'        （缺「佩」）
        第78回 '…闲征��词…'        （「姽婳」两字都坏了）
    而 `cgb`（程高本）字段是**完整的**：
        第64回 '…情遗九龙佩'
        第78回 '…闲征姽婳词…'

    第 1–80 回两本回目文字一致（都是通行回目），
    所以用 `cgb` 同回次回目兜底是安全的。

    另外还做了字符级兜底：如果某个位置只是单个字坏，用 cgb 同位置的字符补。
    """
    if "\ufffd" not in h:
        return h

    # 策略 1：整条用程高本同回次回目替换（1-80 回两本一致）
    cgb = huimu.get("cgb", {})
    ref = cgb.get(str(hui))
    if ref and "\ufffd" not in ref:
        return ref

    # 策略 2：字符级替换 —— 长度相同则逐位替换坏字符
    if ref and len(ref) == len(h):
        out = "".join(
            (ref[i] if ch == "\ufffd" else ch) for i, ch in enumerate(h))
        if "\ufffd" not in out:
            return out

    return h


def split_paragraphs(text: str):
    """
    把全文切成段落。

    观察原始 txt 的结构：每段以「缩进/换行」分隔，段间可能有空行。
    做法：按换行切，去掉首尾空白，过滤空行和纯装饰行。
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    paras = []
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        # 过滤纯分隔线 / 页码这类噪音
        if re.fullmatch(r"[-=_*·—\s]+", s):
            continue
        if re.fullmatch(r"\d{1,4}", s):
            continue
        paras.append(s)
    return paras


def build_version(ver: str, cfg: dict, huimu: dict):
    d = os.path.join(CORPUS, cfg["dir"])
    files = sorted(glob.glob(os.path.join(d, "*.txt")))
    key = HUIMU_KEY[ver]
    titles = huimu.get(key, {})

    chapters = []
    n_fixed = 0
    for f in files:
        name = os.path.basename(f)
        m = re.match(r"(\d{3})", name)
        if not m:
            continue                      # 000_凡例.txt 之类
        file_idx = int(m.group(1))
        # 癸酉本：文件名 001..028 对应正文第 81..108 回（它不是从第1回开始）
        hui = file_idx + 80 if ver == "guiyouben" else file_idx
        raw = open(f, encoding="utf-8", errors="replace").read()
        paras = split_paragraphs(raw)
        if not paras:
            continue

        # ---- 回目：优先用 huimu.json，并做 U+FFFD 修复 ----
        title = titles.get(str(hui))
        if title:
            title = fix_huimu(title, hui, huimu)
            if "\ufffd" in title:
                title = None              # 还是坏就退回首行
        if not title:
            if hui == 0:
                # 凡例（第 0 回）没有回目，用其首段做标题
                title = (paras[0][:24] if paras else "凡例") or "凡例"
            else:
                head = paras[0]
                if "第" in head and "回" in head and len(head) < 60:
                    title = head
                else:
                    # 首段不是回目 → 用 huimu.json 或退化为「第N回」
                    title = titles.get(str(hui)) or f"第{hui}回"
                title = fix_huimu(title, hui, huimu)
        if paras and (paras[0] == title or (len(paras[0]) < 60 and "回" in paras[0])):
            paras = paras[1:]             # 去掉正文里重复的回目行

        # 记录修正
        if "\ufffd" in raw[:200]:
            n_fixed += 1

        chapters.append({
            "version": ver,
            "hui": hui,
            "huimu": title,
            "paragraphs": paras,
            "char_count": sum(len(p) for p in paras),
        })

    chapters.sort(key=lambda c: c["hui"])
    return chapters, n_fixed


def main():
    print("=" * 74)
    print("S7-1 三版本语料统一构建")
    print("=" * 74)

    huimu = load_huimu()
    manifest = {"versions": {}, "generated_at": "2026-10-24"}

    for ver, cfg in VERSIONS.items():
        print(f"\n[{ver}] {cfg['label']}")
        chapters, n_fixed = build_version(ver, cfg, huimu)
        if not chapters:
            print("  ⚠ 无数据")
            continue

        total_chars = sum(c["char_count"] for c in chapters)
        total_paras = sum(len(c["paragraphs"]) for c in chapters)
        # 检查回目里还有没有替换符
        bad = [c["hui"] for c in chapters if "\ufffd" in c["huimu"]]

        print(f"  回数     : {len(chapters)}")
        print(f"  段落数   : {total_paras:,}")
        print(f"  字符数   : {total_chars:,}")
        print(f"  回目含替换符: {bad if bad else '无 ✅'}")
        if bad:
            for h in bad:
                c = [x for x in chapters if x["hui"] == h][0]
                print(f"    第{h}回: {c['huimu']!r}")

        p = os.path.join(OUT, f"chapters_{ver}.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            for c in chapters:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
        print(f"  产出     : {p} ({os.path.getsize(p):,} B)")

        manifest["versions"][ver] = {
            "label": cfg["label"],
            "n_hui": len(chapters),
            "n_para": total_paras,
            "n_chars": total_chars,
            "huimu_bad": bad,
            "file": f"chapters_{ver}.jsonl",
        }

    # 特别验证第 64 / 78 回
    print("\n" + "=" * 74)
    print("重点验证：第 64 / 78 回回目（原来的乱码处）")
    print("=" * 74)
    for ver in VERSIONS:
        p = os.path.join(OUT, f"chapters_{ver}.jsonl")
        if not os.path.exists(p):
            continue
        rows = [json.loads(l) for l in open(p, encoding="utf-8")]
        for h in (64, 78):
            c = [x for x in rows if x["hui"] == h]
            if c:
                t = c[0]["huimu"]
                ok = "✅" if "\ufffd" not in t else "❌"
                print(f"  {ver:10s} 第{h}回 {ok} {t}")

    with open(os.path.join(OUT, "version_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"\n产出 manifest: {os.path.join(OUT, 'version_manifest.json')}")


if __name__ == "__main__":
    main()
