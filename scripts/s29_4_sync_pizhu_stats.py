# -*- coding: utf-8 -*-
"""
S29-4 · 同步统计口径（stats.json 的脂批相关字段）
================================================

背景
----
S29-3 重建脂批索引后，脂批数据从 3,234 条变成 4,563 条
（截断率 35.9% → 0.1%）。但 `web/data/stats.json` 里的脂批统计
仍是旧数据算出来的：

    pizhu_by_hui      回1 = 160（实际 224）
    pizhu_by_version  蒙府本 1612 …
    top_person_pizhu  贾宝玉 307 …

本脚本只重算这三项（不动 per_chapter / word_freq / skew 等正文统计），
算法与 build_ui_data.py 保持一致（同一张别名表 查询示例/别名表.py）。

运行：python s29_4_sync_pizhu_stats.py
"""
import os
import re
import sys
import json
import collections

sys.stdout.reconfigure(encoding="utf-8")

WORK = r"D:\学校事务\大数据原理与应用"
ROOT = os.path.join(WORK, "红楼智问")
sys.path.insert(0, os.path.join(WORK, "查询示例"))
from 别名表 import ALIAS  # noqa: E402

A2C = {}
for canon, names in ALIAS.items():
    A2C[canon] = canon
    for n in names:
        A2C[n] = canon
ALIAS_PAT = re.compile("|".join(sorted((re.escape(k) for k in A2C), key=len, reverse=True)))

SRC = r"D:\Programming\Red\hlm_corpus\pizhu_v5.jsonl"
GUIYOU = r"D:\Programming\Red\hlm_corpus\guiyouben\pizhu.jsonl"
STATS = os.path.join(ROOT, "web", "data", "stats.json")


def main():
    rows = [json.loads(l) for l in open(SRC, encoding="utf-8")]
    print(f"pizhu_v5: {len(rows):,} 条")

    by_hui = collections.Counter()
    by_ver = collections.Counter()
    cnt_person = collections.Counter()

    for r in rows:
        t = (r.get("text") or "").strip()
        if len(t) < 2:
            continue
        by_hui[r.get("hui") or 0] += 1
        for v in (r.get("versions_legend") or []):
            by_ver[v] += 1
        for m in ALIAS_PAT.finditer(t):
            c = A2C.get(m.group(0))
            if c:
                cnt_person[c] += 1

    total = sum(by_hui.values())
    print(f"有效 {total:,} 条")
    print(f"  回1 = {by_hui.get(1, 0)} 条（旧 160）")
    print(f"  版本: {by_ver.most_common(6)}")
    print(f"  人物 Top5: {cnt_person.most_common(5)}")

    d = json.load(open(STATS, encoding="utf-8"))
    old_hui = {x["hui"]: x["count"] for x in d.get("pizhu_by_hui", [])}
    old_ver = {x["name"]: x["count"] for x in d.get("pizhu_by_version", [])}
    old_per = {x["name"]: x["count"] for x in d.get("top_person_pizhu", [])}

    d["pizhu_by_hui"] = [{"hui": h, "count": by_hui.get(h, 0)} for h in range(1, 81)]
    d["pizhu_by_version"] = [{"name": k, "count": v} for k, v in by_ver.most_common()]
    d["top_person_pizhu"] = [{"name": k, "count": v} for k, v in cnt_person.most_common(20)]

    # 便于前端/文档引用
    d["pizhu_total"] = total
    d["pizhu_source"] = "pizhu_v5.jsonl（S29 按字号重提取，截断率 0.1%）"

    with open(STATS, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)

    print(f"\n已更新 {STATS}")
    print(f"  pizhu_by_hui      回1: {old_hui.get(1)} → {by_hui.get(1, 0)}")
    print(f"  pizhu_by_version  蒙府本: {old_ver.get('蒙府本')} → {by_ver.get('蒙府本')}")
    print(f"  top_person_pizhu  贾宝玉: {old_per.get('贾宝玉')} → {cnt_person.get('贾宝玉')}")

    # 顺带检查 skew 里的 pizhu 指标是否受影响（不重算，只提示）
    sk = d.get("skew", {})
    if "pizhu_per_hui" in sk:
        print(f"\n提示: skew.pizhu_per_hui 仍是旧值 {sk['pizhu_per_hui']}（需重算请跑 build_ui_data.py）")


if __name__ == "__main__":
    main()
