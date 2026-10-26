# -*- coding: utf-8 -*-
"""
S33-1 · 生成「人物 × 回目」提及矩阵（3D 曲面图的数据源）
========================================================

为什么需要这个
--------------
统计页原有的 6 张图都是**一维统计**（逐回字数、人物总频次、词频…）。
把它们做成 3D 只是换视角，不增加信息。

真正需要第三个维度的是**「人物 × 回目 × 提及次数」**：
  · x 轴 = 回目（1–80）
  · y 轴 = 人物（Top-30）
  · z 轴（高度）= 该人物在该回被提及的次数

这样能一眼看出：
  · 主要人物的「出场山谷」——某个角色在哪些回缺席
  · 双峰结构——宝黛感情线在两处高潮
  · 某回突然全员登场（如第 18 回元妃省亲）

输出：web/data/person_hui_matrix.json

运行：python s33_1_build_person_hui_matrix.py
"""
import os
import re
import sys
import json
import collections

sys.stdout.reconfigure(encoding="utf-8")

ROOT = r"D:\学校事务\大数据原理与应用\红楼智问"
WEB_DATA = os.path.join(ROOT, "web", "data")
VDIR = os.path.join(WEB_DATA, "versions", "fuqinju")
sys.path.insert(0, os.path.join(ROOT, "scripts"))

# 别名表（与 stats.json 的人物归并口径一致）
WORK = r"D:\学校事务\大数据原理与应用"
sys.path.insert(0, os.path.join(WORK, "查询示例"))
from 别名表 import ALIAS  # noqa: E402

A2C = {}
for canon, names in ALIAS.items():
    A2C[canon] = canon
    for n in names:
        A2C[n] = canon

ALIAS_PAT = re.compile(
    "|".join(sorted((re.escape(k) for k in A2C), key=len, reverse=True))
)

OUT = os.path.join(WEB_DATA, "person_hui_matrix.json")
TOP_N = 30          # 取提及最多的 30 个人物
HUI_MIN, HUI_MAX = 1, 80


def main():
    print("=" * 70)
    print("S33-1 生成人物 × 回目提及矩阵")
    print("=" * 70)

    # ---- 1) 读 80 回正文 ----
    chap_text = {}
    for hui in range(HUI_MIN, HUI_MAX + 1):
        p = os.path.join(VDIR, f"chapter_{hui:03d}.json")
        if not os.path.exists(p):
            continue
        d = json.load(open(p, encoding="utf-8"))
        paras = d.get("paragraphs") or []
        # 段落是 {i, id, t} 结构（不是纯字符串）
        parts = []
        for x in paras:
            if isinstance(x, dict):
                parts.append(str(x.get("t") or ""))
            else:
                parts.append(str(x))
        chap_text[hui] = "\n".join(parts)
    print(f"读入章节: {len(chap_text)} 回")

    # ---- 2) 逐回统计人物提及 ----
    # matrix[hui][person] = 次数
    matrix = {}
    total_by_person = collections.Counter()

    for hui, text in chap_text.items():
        row = collections.Counter()
        for m in ALIAS_PAT.finditer(text):
            c = A2C.get(m.group(0))
            if c:
                row[c] += 1
        matrix[hui] = row
        for k, v in row.items():
            total_by_person[k] += v

    print(f"识别到人物: {len(total_by_person)} 个")

    # ---- 3) 取 Top-N ----
    top = [name for name, _ in total_by_person.most_common(TOP_N)]
    print(f"\nTop-{TOP_N}:")
    for i, n in enumerate(top[:12], 1):
        print(f"  {i:>2}. {n:<8} {total_by_person[n]:>5}")

    # ---- 4) 组装输出 ----
    huis = sorted(matrix.keys())
    # z 值矩阵：[人物][回目]
    persons = []
    for name in top:
        series = [matrix[h].get(name, 0) for h in huis]
        persons.append({
            "name": name,
            "total": total_by_person[name],
            "series": series,
            "peak_hui": huis[series.index(max(series))] if series and max(series) > 0 else None,
            "peak_val": max(series) if series else 0,
            "zero_huis": [h for h, v in zip(huis, series) if v == 0],
        })

    # 每回的总提及量（用于观察「全员登场」的回目）
    per_hui_total = [sum(matrix[h].values()) for h in huis]
    top_hui = sorted(zip(huis, per_hui_total), key=lambda x: -x[1])[:10]

    data = {
        "huis": huis,
        "persons": persons,
        "per_hui_total": per_hui_total,
        "n_hui": len(huis),
        "n_person": len(persons),
        "max_val": max((max(p["series"]) for p in persons), default=1),
        "note": ("x=回目(1–80) · y=人物(Top-30,按全书提及排序) · z=该回提及次数。"
                 "别名已归并（如「宝二爷」「宝玉」计入「贾宝玉」）。"),
        "generated_by": "s33_1_build_person_hui_matrix.py",
    }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)

    print(f"\n已写 {OUT} ({os.path.getsize(OUT):,} B)")
    print(f"  矩阵规模: {len(persons)} 人物 × {len(huis)} 回")
    print(f"  最大值: {data['max_val']}")
    print(f"\n提及总量最高的 10 回:")
    for h, v in top_hui:
        print(f"  第 {h:>2} 回: {v:>4} 次")

    # ---- 5) 抽样校验：几位主角的峰值回 ----
    print("\n主要人物峰值回目:")
    for p in persons[:6]:
        print(f"  {p['name']:<8} 峰值第 {p['peak_hui']} 回（{p['peak_val']} 次）"
              f"，缺席 {len(p['zero_huis'])} 回")


if __name__ == "__main__":
    main()
