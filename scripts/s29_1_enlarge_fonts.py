# -*- coding: utf-8 -*-
"""
S29-1 · 全站字号放大（课堂展示用）
==================================

需求
----
「课堂展示时需要更大的文本字体，把 UI 界面除去正文阅读外的所有字的字号放大」

实现
----
把 CSS / JS 里的 `font-size: XXpx` 按分段曲线放大：

    字号 ≤ 13.5px  → ×1.24 + 0.6   （小字大幅提升，课堂投影可读）
    14 ~ 20px      → ×1.15
    > 20px         → ×1.08         （标题类，避免过大）

换算表：
     9.0 → 11.5    10.0 → 13.0    10.5 → 13.5    11.0 → 14.0
    11.5 → 15.0    12.0 → 15.5    12.5 → 16.0    13.0 → 16.5
    13.5 → 17.5    14.0 → 16.0    15.0 → 17.0    16.0 → 18.5
    17.0 → 19.5    18.0 → 20.5    20.0 → 23.0    22.0 → 24.0
    40.0 → 43.0

排除项（正文阅读，保持原样）
--------------------------
    .rd-paper p        正文段落  16.5px
    .rd-paper .huimu   回目标题  22px

其余全部放大，包括阅读视图的侧栏目录 / 工具条 / 段号标注。

幂等
----
脚本内置标记注释，重复运行不会二次放大。

运行：
  python s29_1_enlarge_fonts.py --dry     # 只统计不写
  python s29_1_enlarge_fonts.py           # 执行
  python s29_1_enlarge_fonts.py --revert  # 从 .bak-fs 还原
"""
import os
import re
import sys
import shutil

sys.stdout.reconfigure(encoding="utf-8")

ROOT = r"D:\学校事务\大数据原理与应用\红楼智问"
CSS_DIR = os.path.join(ROOT, "web", "public", "css")
JS_DIR = os.path.join(ROOT, "web", "public", "js")

# 需要排除的选择器（正文阅读保持原字号）
EXCLUDE_SELECTORS = [
    ".rd-paper p",        # 正文段落
    ".rd-paper .huimu",   # 回目大标题
]

MARKER = "/* S29-FONT-SCALED */"


def log(m=""):
    print(m, flush=True)


def scale(v: float) -> float:
    """字号放大曲线"""
    if v <= 13.5:
        n = v * 1.24 + 0.6
    elif v <= 20:
        n = v * 1.15
    else:
        n = v * 1.08
    return round(n * 2) / 2      # 取到 0.5


def fmt(v: float) -> str:
    """格式化：整数不带小数点"""
    return str(int(v)) if abs(v - int(v)) < 1e-9 else f"{v:g}"


# ============================================================
# CSS 处理
# ============================================================
def process_css(path: str, dry: bool = False):
    src = open(path, encoding="utf-8").read()
    if MARKER in src:
        return 0, "已处理过（跳过）"

    lines = src.split("\n")
    out = []
    changed = 0
    # 当前规则的选择器（简单状态机：遇到含 { 的行记选择器，遇到 } 清空）
    cur_sel = ""
    depth = 0

    for line in lines:
        stripped = line.strip()

        # 记录选择器：以 { 结尾或包含 { 的行
        if "{" in line and not stripped.startswith("/*"):
            head = line.split("{")[0].strip()
            if head:
                cur_sel = head
            depth += line.count("{")
        if "}" in line:
            depth -= line.count("}")

        # 判断当前规则是否属于排除项
        excluded = False
        for ex in EXCLUDE_SELECTORS:
            # 选择器精确匹配（含组合如 `.rd-paper p`）
            if cur_sel == ex or cur_sel.replace(" ", "") == ex.replace(" ", ""):
                excluded = True
                break

        # 替换 font-size
        if "font-size" in line and not excluded:
            def _rep(m):
                nonlocal changed
                num = float(m.group(2))
                new = scale(num)
                if abs(new - num) < 1e-9:
                    return m.group(0)
                changed += 1
                return f"{m.group(1)}{fmt(new)}px"

            line2 = re.sub(r'(font-size:\s*)([\d.]+)px', _rep, line)
            out.append(line2)
        else:
            out.append(line)

    result = "\n".join(out)
    if changed and not dry:
        if not os.path.exists(path + ".bak-fs"):
            shutil.copyfile(path, path + ".bak-fs")
        result = result + "\n" + MARKER + "\n"
        open(path, "w", encoding="utf-8").write(result)
    return changed, ("dry" if dry else "written")


# ============================================================
# JS 内联样式处理
# ============================================================
def process_js(path: str, dry: bool = False):
    src = open(path, encoding="utf-8").read()
    if MARKER.replace("/*", "//").replace("*/", "") in src:
        return 0, "已处理过（跳过）"

    changed = 0

    def _rep(m):
        nonlocal changed
        num = float(m.group(2))
        new = scale(num)
        if abs(new - num) < 1e-9:
            return m.group(0)
        changed += 1
        return f"{m.group(1)}{fmt(new)}px"

    out = re.sub(r'(font-size:\s*)([\d.]+)px', _rep, src)

    if changed and not dry:
        if not os.path.exists(path + ".bak-fs"):
            shutil.copyfile(path, path + ".bak-fs")
        # 顶部加标记
        out = "/* S29-FONT-SCALED */\n" + out
        open(path, "w", encoding="utf-8").write(out)
    return changed, ("dry" if dry else "written")


# ============================================================
# 还原
# ============================================================
def revert():
    n = 0
    for d in (CSS_DIR, JS_DIR):
        for fn in os.listdir(d):
            if fn.endswith(".bak-fs"):
                orig = fn[:-len(".bak-fs")]
                shutil.copyfile(os.path.join(d, fn), os.path.join(d, orig))
                log(f"  还原 {orig}")
                n += 1
    log(f"\n还原 {n} 个文件")


def main():
    dry = "--dry" in sys.argv
    if "--revert" in sys.argv:
        log("=" * 66)
        log("S29-1 字号还原")
        log("=" * 66)
        revert()
        return

    log("=" * 66)
    log("S29-1 字号放大" + ("（DRY RUN）" if dry else ""))
    log("=" * 66)
    log(f"放大曲线: ≤13.5px ×1.24+0.6 / 14-20px ×1.15 / >20px ×1.08")
    log(f"排除: {', '.join(EXCLUDE_SELECTORS)}")

    log("\n[CSS]")
    total_css = 0
    for fn in sorted(os.listdir(CSS_DIR)):
        if not fn.endswith(".css"):
            continue
        p = os.path.join(CSS_DIR, fn)
        n, st = process_css(p, dry)
        total_css += n
        log(f"  {fn:16s} {n:>4} 处  {st}")

    log("\n[JS 内联样式]")
    total_js = 0
    for fn in sorted(os.listdir(JS_DIR)):
        if not fn.endswith(".js"):
            continue
        p = os.path.join(JS_DIR, fn)
        n, st = process_js(p, dry)
        if n:
            total_js += n
            log(f"  {fn:16s} {n:>4} 处  {st}")

    log(f"\n合计：CSS {total_css} 处 + JS {total_js} 处 = {total_css + total_js} 处")

    if dry:
        log("\n（未写入）")


if __name__ == "__main__":
    main()
