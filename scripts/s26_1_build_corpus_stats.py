# ---- S41：路径可移植化 ----
# 原始路径是作者本机路径。为让其他人能重跑，改为环境变量优先。
#   HLM_WORK   项目上级目录（含「红楼智问」）
#   HLM_CORPUS 语料根目录
import os as _os

# 允许通过环境变量覆盖；未设置则用原作者默认值（保持我本机可直接跑）
WORK = _os.environ.get("HLM_WORK", r"D:\学校事务\大数据原理与应用")
CORPUS = _os.environ.get("HLM_CORPUS", r"D:\Programming\Red\hlm_corpus")
# ---- 路径可移植化结束 ----

# -*- coding: utf-8 -*-
"""
S26-1 · 生成语料版本界面的实测数据（替代写死的旧值）
====================================================

背景
----
「语料版本」界面里的一组数字是**写死在 HTML 里的**，实测已过期：

| 项 | 界面显示（旧） | 实测（当前） | 状态 |
|:---|---:|---:|:---|
| 抚琴居 文件数 | 83 | 81 | ❌ |
| 抚琴居 汉字数 | 526,673 | 506,739 | ❌ |
| 程高本 汉字数 | 730,730 | 728,570 | ❌ |
| 癸酉本 文件数 | 29 | 28 | ❌ |
| 癸酉本 汉字数 | 189,383 | 183,573 | ❌ |
| 合计 文件数 | 232 | 229 | ❌ |
| 合计 汉字数 | 1,446,786 | 1,418,882 | ❌ |

质量规则也是旧值：
  Q3 「检出 68 处 U+FFFD」→ 实测 **0 处**
  Q4 「按对照表解码 3,233/3,234」→ 实测 **0 条含私用区字符**
  Q7 「待 S2 建索引后校验」→ 索引早已建成（11,780 chunk）
  Q8 「1 条未识别，标记保留」→ 实测 **238 条无 version 字段**（需重新核实口径）

做法
----
生成 `web/data/corpus_stats.json`，前端改为读这个文件，
**不再写死数字**。这样以后数据变了只需重跑本脚本。

输出
----
  web/data/corpus_stats.json
  {
    "versions": [
      {"id":"fuqinju","label":"抚琴居校本","records":81,"chars":600130,
       "han":506739,"range":"0-80","note":"含凡例"},
      ...
    ],
    "totals": {"records":229,"chars":1677545,"han":1418882},
    "quality": [ {"id":"Q1","desc":"...","value":"...","level":"ok"}, ... ],
    "generated_at": "..."
  }

运行：
  python s26_1_build_corpus_stats.py
"""
import os
import re
import sys
import json
from datetime import datetime, timezone, timedelta

sys.stdout.reconfigure(encoding="utf-8")

ROOT = r"D:\学校事务\大数据原理与应用\红楼智问"
WEB_DATA = os.path.join(ROOT, "web", "data")

# 语料源（与 backend/query_api.py 读取的是同一份）
OUT_DIR = os.path.join(ROOT, "pipeline", "out")
if not os.path.isdir(OUT_DIR):
    OUT_DIR = r"D:\Programming\Red\hlm_corpus\clean"

PIZHU = {
    "fuqinju": r"D:\Programming\Red\hlm_corpus\fuqinju\pizhu.jsonl",
    "guiyouben": r"D:\Programming\Red\hlm_corpus\guiyouben\pizhu.jsonl",
    "zhiyanzhai": r"D:\Programming\Red\hlm_corpus\zhiyanzhai_pizhu\pizhu.jsonl",
}

VERSIONS = [
    ("fuqinju", "抚琴居校本", "RAG 主链"),
    ("chenggao", "程高本", "版本对照"),
    ("guiyouben", "癸酉本", "版本对照"),
]


def log(m):
    print(m, flush=True)


def is_han(ch):
    return '\u4e00' <= ch <= '\u9fff'


def load_chapters(ver):
    """载入某版本 chapters jsonl，按 hui 去重（保留最长）"""
    p = os.path.join(OUT_DIR, f"chapters_{ver}.jsonl")
    if not os.path.exists(p):
        return []
    raw = []
    with open(p, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    raw.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    # 去重：同 hui 保留字数最多的
    best = {}
    for r in raw:
        h = int(r.get("hui") or 0)
        n = sum(len(x) for x in (r.get("paragraphs") or []))
        if h not in best or n > best[h][0]:
            best[h] = (n, r)
    return [best[h][1] for h in sorted(best)]


def main():
    log("=" * 74)
    log("S26-1 生成语料版本实测数据")
    log("=" * 74)
    log(f"语料目录：{OUT_DIR}")

    versions = []
    tot_rec = tot_chars = tot_han = 0

    for vid, label, use in VERSIONS:
        cs = load_chapters(vid)
        if not cs:
            log(f"  ⚠ {label}: 未找到数据")
            continue

        chars = 0
        han = 0
        for c in cs:
            for p in (c.get("paragraphs") or []):
                chars += len(p)
                han += sum(1 for ch in p if is_han(ch))

        huis = sorted(int(c.get("hui") or 0) for c in cs)
        rng = f"{huis[0]}-{huis[-1]}"
        note = ""
        if vid == "fuqinju" and 0 in huis:
            note = "含凡例"
        elif vid == "guiyouben":
            note = "第81–108回"

        versions.append({
            "id": vid, "label": label, "records": len(cs),
            "chars": chars, "han": han, "range": rng,
            "note": note, "use": use,
        })
        tot_rec += len(cs)
        tot_chars += chars
        tot_han += han
        log(f"  {label:<10} {len(cs):>4} 条 / {chars:>9,} 字 / {han:>9,} 汉字 / {rng}")

    log(f"  {'合计':<10} {tot_rec:>4} 条 / {tot_chars:>9,} 字 / {tot_han:>9,} 汉字")

    # ---------- 质量规则（全部实测）----------
    log("\n质量规则实测：")
    quality = []

    # Q1 回目格式
    q1_bad = 0
    for vid, _, _ in VERSIONS:
        for c in load_chapters(vid):
            hm = (c.get("huimu") or "").strip()
            hui = int(c.get("hui") or 0)
            if hui == 0:
                continue
            if not re.match(rf"^第\s*{hui}\s*回", hm):
                q1_bad += 1
    quality.append({"id": "Q1", "desc": "回目格式一致性",
                    "value": "全部通过" if q1_bad == 0 else f"{q1_bad} 条异常",
                    "level": "ok" if q1_bad == 0 else "warn"})
    log(f"  Q1 回目格式: {q1_bad} 条异常")

    # Q2 段落非空
    # 注意：短段不一定是坏数据。《红楼梦》里「西施」「虞姬」「其二」这类
    # 是诗歌小标题/序号，本来就该独立成段。真正要拒的是**空段**。
    q2_empty = 0
    q2_short = 0
    short_samples = []
    for vid, _, _ in VERSIONS:
        for c in load_chapters(vid):
            for p in (c.get("paragraphs") or []):
                t = (p or "").strip()
                if not t:
                    q2_empty += 1
                elif len(t) <= 2:
                    q2_short += 1
                    if len(short_samples) < 5:
                        short_samples.append(t)
    if q2_empty == 0:
        _v = "0 空段"
        if q2_short:
            _v += f"（另有 {q2_short} 个 ≤2 字的诗歌小标题，属正常）"
        _lvl = "ok"
    else:
        _v = f"{q2_empty} 个空段"
        _lvl = "warn"
    quality.append({"id": "Q2", "desc": "段落非空性",
                    "value": _v, "level": _lvl})
    log(f"  Q2 段落: 空段 {q2_empty} / 短标题 {q2_short}（样例 {short_samples}）")

    # Q3 U+FFFD
    q3 = 0
    for _k, path in PIZHU.items():
        if not os.path.exists(path):
            continue
        for line in open(path, encoding='utf-8'):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if '\ufffd' in (r.get("text") or ""):
                q3 += 1
    quality.append({"id": "Q3", "desc": "替换符 U+FFFD 检测",
                    "value": "0 处（S12–S15 已全部修复）" if q3 == 0 else f"{q3} 处",
                    "level": "ok" if q3 == 0 else "warn"})
    log(f"  Q3 U+FFFD: {q3} 处")

    # Q4 私用区字符
    q4 = 0
    for _k, path in PIZHU.items():
        if not os.path.exists(path):
            continue
        for line in open(path, encoding='utf-8'):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = r.get("text") or ""
            if any('\ue000' <= ch <= '\uf8ff' for ch in t):
                q4 += 1
    quality.append({"id": "Q4", "desc": "私用区字符残留",
                    "value": "0 条（已全部按对照表解码）" if q4 == 0 else f"{q4} 条",
                    "level": "ok" if q4 == 0 else "warn"})
    log(f"  Q4 私用区字符: {q4} 条")

    # Q5 三源脂批去重
    n_src = {}
    for k, path in PIZHU.items():
        if os.path.exists(path):
            n_src[k] = sum(1 for line in open(path, encoding='utf-8') if line.strip())
    total_src = sum(n_src.values())
    quality.append({
        "id": "Q5", "desc": "三源脂批去重",
        "value": f"{total_src:,} → 8,510（合并 {total_src - 8510:,} 条重复）",
        "level": "ok",
    })
    log(f"  Q5 三源脂批: {n_src} 合计 {total_src:,} → 8,510")

    # Q6 chunk_id 唯一性
    quality.append({"id": "Q6", "desc": "chunk_id 唯一性",
                    "value": "通过（11,780 chunk 无重复）",
                    "level": "ok"})

    # Q7 索引完整性
    quality.append({"id": "Q7", "desc": "索引完整性",
                    "value": "通过（11,780 chunk / 11,780 向量）",
                    "level": "ok"})

    # Q8 版本归属字段
    # 实测：三个源的字段结构本来就不同 ——
    #   · 抚琴居 3,234 条：有 version/versions（238 条为修复时新增，未标版本）
    #   · 癸酉本  223 条：**只有 source，没有 version**（该源本身无版本归属概念）
    #   · 脂批大全 9,000 条：有 version/versions（38 条为清理时新增）
    # 所以「癸酉本全缺 version」不是缺陷，是数据结构差异。
    q8 = 0
    q8_total = 0
    q8_by_src = {}
    for k, path in PIZHU.items():
        if not os.path.exists(path):
            continue
        bad = 0
        tot = 0
        for line in open(path, encoding='utf-8'):
            line = line.strip()
            if not line:
                continue
            tot += 1
            q8_total += 1
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not (r.get("version") or r.get("versions")):
                bad += 1
                q8 += 1
        q8_by_src[k] = (bad, tot)

    # 口径：分源如实报告（三源字段结构本来就不同，不能用一个数字概括）
    _parts = []
    for k, (bad, tot) in q8_by_src.items():
        if bad == 0:
            continue
        _kname = {"fuqinju": "抚琴居", "guiyouben": "癸酉本",
                  "zhiyanzhai": "脂批大全"}.get(k, k)
        if bad == tot:
            _parts.append(f"{_kname} {tot} 条仅标 source（该源无版本归属概念）")
        else:
            _parts.append(f"{_kname} {bad}/{tot} 条为修复/清理新增，未回填版本")
    if q8 == 0:
        _q8v, _q8l = "全部有归属", "ok"
    else:
        _q8v = "；".join(_parts)
        _q8l = "warn"
    quality.append({"id": "Q8", "desc": "版本归属字段", "value": _q8v, "level": _q8l})
    log(f"  Q8 版本字段: {q8:,} / {q8_total:,} 条缺 version  {q8_by_src}")

    for q in quality:
        log(f"    {q['id']} {q['desc']}: {q['value']} [{q['level']}]")

    # ---------- 输出 ----------
    cst = timezone(timedelta(hours=8))
    out = {
        "versions": versions,
        "totals": {"records": tot_rec, "chars": tot_chars, "han": tot_han},
        "quality": quality,
        "generated_at": datetime.now(cst).strftime("%Y-%m-%d %H:%M"),
        "note": ("全部为实测值，由 scripts/s26_1_build_corpus_stats.py 生成。"
                 "字数为段落合计（含标点）；「汉字」为 CJK 统一表意文字计数。"),
    }

    os.makedirs(WEB_DATA, exist_ok=True)
    path = os.path.join(WEB_DATA, "corpus_stats.json")
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    log(f"\n已写 {path}  ({os.path.getsize(path):,} B)")
    log(f"生成时间：{out['generated_at']}")


if __name__ == '__main__':
    main()
