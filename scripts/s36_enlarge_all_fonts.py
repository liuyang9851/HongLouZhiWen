# -*- coding: utf-8 -*-
"""
S36 · 全站字号再放大（含左侧栏，但不加宽侧栏）
============================================

需求
----
「考虑再放大字号（全部放大，包括左边栏，但不增加左边栏栏宽）。这次放大时可能比较困难。」

难在哪
------
已经放过两轮（S29-1 / S29-2），字号中位数已到 17px。
再做一次全局乘法，会：

  ① 大字号（24~45px 的标题）继续膨胀 → 撑破卡片/图表容器
  ② 左侧栏固定 168px，文字放大会被 `text-overflow: ellipsis` 截成省略号
  ③ 表格 / 统计卡片是固定列宽，文字放大后换行、错位

本脚本的策略
------------
**平滑加性放大**（字号越大，加得越少）：

    nv = v + 2.3 × (12 / max(v,12))^0.35     上限 v + 2.3，超过 22px 不动

    9.5 → 11.9     13.5 → 15.8     17 → 19.1     20 → 21.8
    12  → 14.3     15   → 17.2     18 → 20.0     >22 不变

为什么不用乘法：试过「分层倍率」，15.0 和 15.5 都落到 17.1，字号层级被压平。
加性且增量单调递减，能保证 v1 < v2 → nv1 < nv2，层级完整。

**左侧栏专项**（不加宽容器，靠压缩图标列腾空间）：

    · .rail-item 字体   13.5 → 15.5px
    · .rail-item .ico   22px → 18px 宽（腾出 4px）
    · .rail-item gap    10px → 8px   （腾出 2px）
    · 内边距 padding    6px 10px → 6px 8px（左右各省 2px，共 4px）
    → 文字可用宽度从 116px 增到 126px，配合 15.5px 字号，5 字（≈78px）仍安全
    · .rail-item .badge 9.5 → 10.5px
    · .mini-stat b      15 → 16.5px
    · .mini-stat span   10 → 11.5px

**行高同步**：字号放大后若不调行高，文字会挤在一起。统一把紧行高放宽一档。

幂等
----
用标记 `S36-FONT-SCALED` 写在每个 CSS 文件头部，重复运行自动跳过。

运行：
  python s36_enlarge_all_fonts.py --dry
  python s36_enlarge_all_fonts.py
"""
import io
import os
import re
import shutil
import sys

sys.stdout.reconfigure(encoding="utf-8")

CSS_DIR = r"D:\学校事务\大数据原理与应用\红楼智问\web\public\css"
MARK = "/* S36-FONT-SCALED */"
FILES = ["app.css", "views.css", "pages.css", "principle.css"]

# 平滑加性放大：加多少随原字号递减，且严格单调（不会把不同字号压成同一个值）
#
#   nv = v + K * (12 / max(v, 12)) ** P
#
# 为什么不用乘法：上一版试用「分层倍率」，结果 15.0 与 15.5 都变成 17.1，
# 字号层级被压平，设计看起来会变糊。加性且偏移量单调递减，
# 能保证 v1 < v2 → nv1 < nv2，层级完整保留。
K = 2.3          # 最大增量（出现在最小字号处）
P = 0.35         # 衰减指数：越大衰减越快
CAP_MAX = 22.0   # 超过这个字号不再放大（大标题放大会溢出卡片/图表）


def scale_one(px, _unused=None):
    v = float(px)
    if v > CAP_MAX:
        return v
    base = 12.0 if v < 12.0 else v
    delta = K * (12.0 / base) ** P
    nv = round(v + delta, 1)
    # 安全钳制：最多 +2.3px
    nv = min(nv, round(v + K, 1))
    return nv


def main():
    dry = "--dry-run" in sys.argv or "--dry" in sys.argv

    total_files = 0
    total_edits = 0

    for fn in FILES:
        p = os.path.join(CSS_DIR, fn)
        if not os.path.exists(p):
            print(f"  [skip] {fn} 不存在")
            continue

        s = io.open(p, encoding="utf-8").read()

        if MARK in s:
            print(f"  [skip] {fn} 已放大过（含 {MARK.strip()}）")
            continue

        # ---- 1) 分层放大所有 font-size ----
        edits = []

        def repl(m):
            raw = m.group(1)
            nv = scale_one(raw, fn)
            if abs(nv - float(raw)) < 0.01:
                return m.group(0)
            edits.append((float(raw), nv))
            return f"font-size: {nv}px"

        s2 = re.sub(r"font-size:\s*([\d.]+)px", repl, s)

        # ---- 2) 行高：放大后适当放宽（只动过紧的 1.2~1.35）----
        def repl_lh(m):
            v = float(m.group(1))
            if 1.15 <= v <= 1.40:
                return f"line-height: {round(v + 0.08, 2)}"
            return m.group(0)

        s2 = re.sub(r"line-height:\s*(1\.\d+)", repl_lh, s2)

        # ---- 3) 左侧栏专项（不加宽容器）----
        rail_edits = 0
        if fn == "app.css":
            rail_rules = [
                # 项目文字
                (r"(\.rail-item\s*\{[^}]*?)font-size:\s*[\d.]+px",
                 r"\1font-size: 16px"),
                # 图标列收窄 22 → 18，gap 10 → 8
                (r"\.rail-item \.ico \{\s*width:\s*22px",
                 ".rail-item .ico { width: 18px"),
                (r"(\.rail-item\s*\{[^}]*?)gap:\s*10px", r"\1gap: 8px"),
                (r"(\.rail-item\s*\{[^}]*?)padding:\s*6px 10px", r"\1padding: 6px 8px"),
                # 徽标 / 底部统计
                (r"(\.rail-item \.badge\s*\{[^}]*?)font-size:\s*[\d.]+px",
                 r"\1font-size: 10.5px"),
                (r"(\.mini-stat b\s*\{[^}]*?)font-size:\s*[\d.]+px",
                 r"\1font-size: 16.5px"),
                (r"(\.mini-stat span\s*\{[^}]*?)font-size:\s*[\d.]+px",
                 r"\1font-size: 11.5px"),
            ]
            for pat, rep in rail_rules:
                s2, n = re.subn(pat, rep, s2, count=1)
                if n:
                    rail_edits += 1

        # ---- 4) 写标记 ----
        s2 = MARK + "\n" + s2

        changed = (s2 != s)
        print(f"\n{fn}")
        print(f"  字号改动: {len(edits)} 处" + (f"，左侧栏专项 {rail_edits} 处" if rail_edits else ""))
        if edits:
            # 抽样显示 6 个
            sample = sorted(set(edits))[:6]
            print("  样例: " + ", ".join(f"{a}→{b}" for a, b in sample))

        if dry:
            continue

        if changed:
            bak = p + ".bak-s36"
            if not os.path.exists(bak):
                shutil.copy(p, bak)
            io.open(p, "w", encoding="utf-8").write(s2)
            total_files += 1
            total_edits += len(edits) + rail_edits

    print("\n" + "=" * 60)
    if dry:
        print("--dry-run：未写入。确认上面数字后去掉 --dry 重跑。")
    else:
        print(f"完成：{total_files} 个文件，共 {total_edits} 处改动")
        print("备份：*.bak-s36")


if __name__ == "__main__":
    main()
