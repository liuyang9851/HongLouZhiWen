# -*- coding: utf-8 -*-
"""
S29-2 · 字号二次放大 + 左侧导航栏还原
====================================

需求
----
「再放大一点，但左边栏也无需放大」

做法
----
在 S29-1（第一遍放大）的基础上，做第二遍：

  ① 全局再放大（分段，字号越大增幅越小，避免溢出）
       当前 ≤16px  → ×1.10
        16~25px    → ×1.08
        >25px      → ×1.05

  ② 左侧导航栏**还原到原始字号**（从 .bak-fs 读回）
       .rail-item          17.5 → 13.5
       .rail-item .ico     17   → 15
       .rail-item .badge   12.5 → 9.5
       .mini-stat b        17   → 15
       .mini-stat span     13   → 10

  ③ 侧栏宽度回到 168px（字号回原后不需要加宽）

不动的部分
----------
  · 正文阅读 `.rd-paper p` / `.rd-paper .huimu`（第一遍就没动）
  · 顶栏高度 68px（顶栏内容是放大过的）
  · 表格 col-12 / nowrap、图谱提示限宽等布局修正

幂等
----
用独立标记 `S29B-FONT-SCALED`，重复运行跳过。

运行：
  python s29_2_enlarge_more.py --dry
  python s29_2_enlarge_more.py
"""
import os
import re
import sys
import shutil

sys.stdout.reconfigure(encoding="utf-8")

ROOT = r"D:\学校事务\大数据原理与应用\红楼智问"
CSS_DIR = os.path.join(ROOT, "web", "public", "css")
JS_DIR = os.path.join(ROOT, "web", "public", "js")

MARKER_B = "/* S29B-FONT-SCALED */"
MARKER_B_JS = "// S29B-FONT-SCALED"

# 左侧栏选择器：字号必须还原到原始值（不放大）
RAIL_SELECTORS = [
    r".rail-item",
    r".rail-item .ico",
    r".rail-item .badge",
    r".rail-item .lbl",
    r".mini-stat b",
    r".mini-stat span",
]

# 二次放大排除项（正文阅读）
EXCLUDE_ALWAYS = [
    r".rd-paper p",
    r".rd-paper .huimu",
]


def log(m=""):
    print(m, flush=True)


def scale2(v: float) -> float:
    """第二遍放大曲线"""
    if v <= 16:
        n = v * 1.10
    elif v <= 25:
        n = v * 1.08
    else:
        n = v * 1.05
    return round(n * 2) / 2


def fmt(v: float) -> str:
    return str(int(v)) if abs(v - int(v)) < 1e-9 else f"{v:g}"


# ============================================================
# 从 .bak-fs 里取某选择器的原始 font-size
# ============================================================
def orig_fs(sel: str):
    bak = os.path.join(CSS_DIR, "app.css.bak-fs")
    if not os.path.exists(bak):
        return None
    src = open(bak, encoding="utf-8").read()
    # 精确匹配「选择器 {」后第一个 font-size
    pat = re.escape(sel) + r"\s*\{([^}]*)\}"
    m = re.search(pat, src, re.S)
    if not m:
        return None
    fm = re.search(r"font-size:\s*([\d.]+)px", m.group(1))
    return fm.group(1) if fm else None


# ============================================================
# CSS 处理
# ============================================================
def process_css(path: str, dry: bool):
    src = open(path, encoding="utf-8").read()
    if MARKER_B in src:
        return 0, 0, "已处理（跳过）"

    lines = src.split("\n")
    out = []
    n_scale = 0
    n_restore = 0
    cur_sel = ""

    for line in lines:
        stripped = line.strip()

        if "{" in line and not stripped.startswith("/*"):
            head = line.split("{")[0].strip()
            if head:
                cur_sel = head

        # 判断当前规则属于哪一类
        is_rail = any(cur_sel.strip() == s or cur_sel.strip().startswith(s + " ")
                      or cur_sel.strip().startswith(s + ":")
                      or cur_sel.strip().startswith(s + ",")
                      for s in RAIL_SELECTORS)
        is_excluded = any(cur_sel.strip() == s for s in EXCLUDE_ALWAYS)

        if "font-size" not in line or is_excluded:
            out.append(line)
            continue

        if is_rail:
            # 还原到原始值
            def _restore(m):
                nonlocal n_restore
                o = orig_fs(cur_sel.strip())
                if o is None:
                    return m.group(0)
                if abs(float(o) - float(m.group(2))) < 1e-9:
                    return m.group(0)
                n_restore += 1
                return f"{m.group(1)}{fmt(float(o))}px"
            out.append(re.sub(r"(font-size:\s*)([\d.]+)px", _restore, line))
        else:
            # 二次放大
            def _scale(m):
                nonlocal n_scale
                num = float(m.group(2))
                new = scale2(num)
                if abs(new - num) < 1e-9:
                    return m.group(0)
                n_scale += 1
                return f"{m.group(1)}{fmt(new)}px"
            out.append(re.sub(r"(font-size:\s*)([\d.]+)px", _scale, line))

    result = "\n".join(out)
    if (n_scale or n_restore) and not dry:
        result = result + "\n" + MARKER_B + "\n"
        open(path, "w", encoding="utf-8").write(result)
    return n_scale, n_restore, ("dry" if dry else "written")


# ============================================================
# JS 内联样式
# ============================================================
def process_js(path: str, dry: bool):
    src = open(path, encoding="utf-8").read()
    if MARKER_B_JS in src:
        return 0, "已处理（跳过）"

    n = 0

    def _scale(m):
        nonlocal n
        num = float(m.group(2))
        new = scale2(num)
        if abs(new - num) < 1e-9:
            return m.group(0)
        n += 1
        return f"{m.group(1)}{fmt(new)}px"

    out = re.sub(r"(font-size:\s*)([\d.]+)px", _scale, src)
    if n and not dry:
        out = MARKER_B_JS + "\n" + out
        open(path, "w", encoding="utf-8").write(out)
    return n, ("dry" if dry else "written")


# ============================================================
# 主流程
# ============================================================
def main():
    dry = "--dry" in sys.argv

    log("=" * 68)
    log("S29-2 字号二次放大" + ("（DRY RUN）" if dry else ""))
    log("=" * 68)
    log("曲线: ≤16px ×1.10 / 16~25px ×1.08 / >25px ×1.05")
    log("左侧栏: 还原到原始字号（不放大）")
    log("正文: .rd-paper p / .huimu 保持不动")

    log("\n[左侧栏还原目标]")
    for s in RAIL_SELECTORS:
        o = orig_fs(s)
        if o:
            log(f"  {s:22s} → {o}px")

    log("\n[CSS]")
    tot_s = tot_r = 0
    for fn in sorted(os.listdir(CSS_DIR)):
        if not fn.endswith(".css"):
            continue
        ns, nr, st = process_css(os.path.join(CSS_DIR, fn), dry)
        tot_s += ns
        tot_r += nr
        if ns or nr:
            log(f"  {fn:16s} 放大 {ns:>3} 处 / 还原 {nr:>2} 处  {st}")

    log("\n[JS 内联]")
    tot_js = 0
    for fn in sorted(os.listdir(JS_DIR)):
        if not fn.endswith(".js"):
            continue
        n, st = process_js(os.path.join(JS_DIR, fn), dry)
        if n:
            tot_js += n
            log(f"  {fn:16s} 放大 {n:>3} 处  {st}")

    log(f"\n合计：放大 {tot_s + tot_js} 处 / 还原 {tot_r} 处")

    # 侧栏宽度回原（字号也回原了）
    if not dry:
        p = os.path.join(CSS_DIR, "app.css")
        s = open(p, encoding="utf-8").read()
        s2 = s.replace("--rail-w:   215px;", "--rail-w:   168px;")
        if s2 != s:
            open(p, "w", encoding="utf-8").write(s2)
            log("\n侧栏宽度 215px → 168px（字号已回原，不需要加宽）")
    if dry:
        log("\n（未写入）")


if __name__ == "__main__":
    main()
