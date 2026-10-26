# -*- coding: utf-8 -*-
"""
S31 · 欢迎页「程序架构」图文字放大
====================================

问题
----
架构图是 SVG，viewBox 900×720，文字用 viewBox 单位：
  txt()  标题默认 11px
  mono() 说明默认 9.5px（部分 8 / 8.5）
渲染时 `max-width:900px`，基本 1:1 —— 所以文字就是 11px / 9.5px，投影偏小。

约束（关键）
------------
**不能无限放大字号**，因为：
  · 多行文字的行距是**硬编码的 14 个 viewBox 单位**（如 y=172/186/200）
  · 字号超过 ~11.5 就会出现行间重叠
  · 部分窄框只有 160 单位宽，最长一行 30 字符

所以采用「**温和放大 + 提高渲染宽度**」组合：

  ① 字号 ×1.15（9.5→11，11→12.5，8.5→10，8→9）
     → 11 号字 vs 14 行距 = 1.27 倍，仍有余量

  ② SVG 最大宽度 900 → 1024
     → 容器约 995px 可用，整体再放大约 1.10 倍

  合计视觉放大 ≈ 1.15 × 1.10 ≈ **1.27 倍**
  （标题 11 → 约 14px，说明 9.5 → 约 12px）

安全性验证
----------
  · 行距 14 / 最大字号 11 = 1.27，不重叠
  · 最宽 mono 行「personfreq 41 · cooccur 10,742」30 字符
    放大后在 200 宽框内约 181 单位，不溢出

运行：
  python s31_enlarge_arch_diagram.py --dry
  python s31_enlarge_arch_diagram.py
"""
import os
import re
import sys
import shutil

sys.stdout.reconfigure(encoding="utf-8")

ROOT = r"D:\学校事务\大数据原理与应用\红楼智问"
JS = os.path.join(ROOT, "web", "public", "js", "arch_diagram.js")

MARKER = "// S31-ARCH-FONT-SCALED"
FACTOR = 1.15


def log(m=""):
    print(m, flush=True)


def scale(v: float) -> float:
    return round(v * FACTOR * 2) / 2      # 取到 0.5


def fmt(v: float) -> str:
    return str(int(v)) if abs(v - int(v)) < 1e-9 else f"{v:g}"


def main():
    dry = "--dry" in sys.argv

    log("=" * 66)
    log("S31 架构图文字放大" + ("（DRY RUN）" if dry else ""))
    log("=" * 66)
    log(f"系数 ×{FACTOR}，取整到 0.5")

    src = open(JS, encoding="utf-8").read()
    if MARKER in src:
        log("\n已处理过（检测到标记），跳过")
        return

    changes = []

    # ---------- ① 两个辅助函数的默认字号 ----------
    def fix_default(m):
        name, old = m.group(1), float(m.group(2))
        new = scale(old)
        changes.append((f"{name} 默认", old, new))
        return f"{m.group(0).split('=')[0]}= {fmt(new)}"

    src2 = re.sub(r"(\bsize)\s*=\s*([\d.]+)", fix_default, src)

    # ---------- ② 显式传入的字号（第 4 个参数）----------
    # 匹配：'字符串', 数字  后接 , 或 )
    def fix_arg(m):
        lead, old_s = m.group(1), m.group(2)
        old = float(old_s)
        new = scale(old)
        if abs(new - old) > 1e-9:
            changes.append(("调用", old, new))
        return f"{lead}{fmt(new)}"

    # lead = 引号结束 + 逗号 + 空白
    src3 = re.sub(r"""(['"](?:\\.|[^'"\\\n])*['"]\s*,\s*)(\d+(?:\.\d+)?)(?=\s*[,)])""",
                  fix_arg, src2)

    # ---------- ③ SVG 渲染宽度 ----------
    old_w = "max-width:900px"
    new_w = "max-width:1024px"
    has_w = old_w in src3
    if has_w:
        src3 = src3.replace(old_w, new_w)

    if dry:
        from collections import Counter
        c = Counter((o, n) for _k, o, n in changes)
        log(f"\n字号改动 {len(changes)} 处：")
        for (o, n), cnt in sorted(c.items()):
            log(f"  {o}px → {n}px  ×{cnt}")
        log(f"\n渲染宽度: {'900px → 1024px' if has_w else '未找到 max-width:900px'}")
        log("\n（未写入）")
        return

    if not changes and not has_w:
        log("\n没有需要改的内容")
        return

    src3 = src3.rstrip() + "\n" + MARKER + "\n"
    if not os.path.exists(JS + ".bak-arch"):
        shutil.copyfile(JS, JS + ".bak-arch")
    open(JS, "w", encoding="utf-8").write(src3)

    from collections import Counter
    c = Counter((o, n) for _k, o, n in changes)
    log(f"\n已写入 {JS}")
    log(f"字号改动 {len(changes)} 处：")
    for (o, n), cnt in sorted(c.items()):
        log(f"  {o}px → {n}px  ×{cnt}")
    log(f"渲染宽度: {'900px → 1024px' if has_w else '未改'}")
    log(f"备份: {os.path.basename(JS)}.bak-arch")


if __name__ == "__main__":
    main()
