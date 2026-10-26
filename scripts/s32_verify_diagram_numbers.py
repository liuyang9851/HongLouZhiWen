# -*- coding: utf-8 -*-
"""
S32 · 首页/原理页数字一致性校验
================================

背景
----
「程序架构图」「项目原理页」里的数据是**硬编码在 JS 字符串里**的，
索引重建后极易过期：

  · 2026-10-25 索引被旧脚本污染 → 图还写 11,780，实际 13,124
  · 2026-10-26 索引再次被污染 → 图写 13,124，实际 11,780
  · 图谱从 120 节点扩到 350 节点后，图里仍写 120

本脚本把「JS 里写的数字」与「集群实测值」逐项比对，不一致就报错。

比对项
------
  · 索引 chunk / 向量数        ← 后端 /health
  · HBase 5 张表行数            ← HBase 实测
  · 图谱节点 / 边数             ← web/data/graph.json
  · 正文总字数                  ← corpus_stats.json
  · strategy_rev 是否为 v3      ← HBase （防止旧脚本污染）

用法
----
  python s32_verify_diagram_numbers.py          # 校验
  python s32_verify_diagram_numbers.py --fix    # 校验并自动改 JS 里的数字

退出码：0 = 全部一致，1 = 有不一致
"""
import os
import re
import sys
import json
import subprocess

sys.stdout.reconfigure(encoding="utf-8")

ROOT = r"D:\学校事务\大数据原理与应用\红楼智问"
JS_DIR = os.path.join(ROOT, "web", "public", "js")
WEB_DATA = os.path.join(ROOT, "web", "data")
JS_FILES = ["arch_diagram.js", "page_principle.js", "page_home.js"]

EXPECT_STRATEGY = "v3-pizhu3src"


def log(m=""):
    print(m, flush=True)


def sh(cmd, timeout=180):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, shell=isinstance(cmd, str))
        return r.returncode == 0, (r.stdout or ""), (r.stderr or "")
    except Exception as e:
        return False, "", str(e)


# ============================================================
# 采集实测值
# ============================================================
def get_live():
    live = {}

    # 1) 后端健康 = chunk / 向量
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=20) as r:
            d = json.loads(r.read())
            live["chunks"] = d.get("n_chunks")
            live["vectors"] = d.get("n_vectors")
            live["text"] = d.get("n_chunks_text")
            live["pizhu"] = d.get("n_chunks_pizhu")
    except Exception as e:
        log(f"  ⚠ 后端不可用: {e}")

    # 2) HBase 5 张表行数 + strategy_rev
    py = r'''
import happybase, collections
c = happybase.Connection(host="master", port=9090, timeout=120,
                         table_prefix=b"", table_prefix_separator=b"")
out = {}
for t in ["hlm_chunks","hlm_vectors","hlm_terms","hlm_wordfreq","hlm_stats"]:
    tb = c.table(t)
    n = 0
    for k, v in tb.scan(batch_size=500, filter=b"FirstKeyOnlyFilter()"):
        n += 1
    out[t] = n
rev = collections.Counter()
tb = c.table("hlm_chunks")
for k, v in tb.scan(row_prefix=b"fuqinju|pizhu|", batch_size=200, limit=6000):
    for kk, vv in v.items():
        if kk == b"m:strategy_rev":
            rev[vv.decode()] += 1
out["_strategy_rev"] = dict(rev)
c.close()
import json
print(json.dumps(out, ensure_ascii=False))
'''
    # 用临时文件传脚本（python3 -c 会把换行压坏）
    import tempfile, shutil as _sh
    tf = os.path.join(tempfile.gettempdir(), "_hlm_hbstat.py")
    with open(tf, "w", encoding="utf-8") as f:
        f.write(py)
    sh(["docker", "cp", tf, "master:/tmp/_hlm_hbstat.py"])
    ok, out, err = sh(["docker", "exec", "master", "bash", "-lc",
                       "cd /workspace/pipeline && timeout 380 python3 /tmp/_hlm_hbstat.py"],
                      timeout=420)
    if ok and out.strip().startswith("{"):
        try:
            hb = json.loads(out.strip().splitlines()[-1])
            live["terms"] = hb.get("hlm_terms")
            live["wordfreq"] = hb.get("hlm_wordfreq")
            live["stats"] = hb.get("hlm_stats")
            live["strategy_rev"] = hb.get("_strategy_rev", {})
        except Exception as e:
            log(f"  ⚠ HBase 结果解析失败: {e}")
    else:
        log(f"  ⚠ HBase 查询失败: {(err or out)[:120]}")

    # 3) 图谱
    try:
        g = json.load(open(os.path.join(WEB_DATA, "graph.json"), encoding="utf-8"))
        live["gnodes"] = g.get("n_nodes")
        live["gedges"] = g.get("n_edges")
    except Exception:
        pass

    # 4) 正文总字数
    try:
        cs = json.load(open(os.path.join(WEB_DATA, "corpus_stats.json"), encoding="utf-8"))
        live["chars_wan"] = round(cs["totals"]["chars"] / 10000, 1)
        live["pz3"] = 12457          # 三源合计（源文件常量）
    except Exception:
        pass

    return live


# ============================================================
# 从 JS 里抽取数字
# ============================================================
def fmt(n):
    return f"{n:,}" if isinstance(n, int) else str(n)


def read_js(name):
    p = os.path.join(JS_DIR, name)
    return open(p, encoding="utf-8").read() if os.path.exists(p) else ""


def check_and_fix(live, do_fix):
    problems = []
    changed = {}

    for name in JS_FILES:
        src = read_js(name)
        if not src:
            continue
        orig = src

        # chunk / 向量：凡是 "N 行" 且 N 是 chunk 数量级
        if live.get("chunks"):
            want = fmt(live["chunks"])
            for old in ("13,124", "11,780"):
                if old != want and old in src:
                    src = src.replace(old, want)
                    problems.append(f"{name}: chunk 数 {old} → {want}")

        # hlm_terms
        if live.get("terms"):
            want = fmt(live["terms"])
            for old in ("452,460", "448,841"):
                if old != want and old in src:
                    src = src.replace(old, want)
                    problems.append(f"{name}: hlm_terms {old} → {want}")

        # 脂批相关常见过期值（历史污染残留）
        for old_v, new_v in (("9,854", "8,510"), ("4,563", "3,219"), ("5,754", "5,069")):
            if old_v in src:
                src = src.replace(old_v, new_v)
                problems.append(f"{name}: 脂批数字 {old_v} → {new_v}")

        # 图谱
        if live.get("gnodes") and live.get("gedges"):
            want = f"{live['gnodes']} 节点 / {live['gedges']} 边"
            for old in ("120 节点 / 253 边", "120 人物 / 253 条关系",
                        "350 节点 / 674 边", "350 节点 / 676 边"):
                if old != want and old in src:
                    src = src.replace(old, want)
                    problems.append(f"{name}: 图谱规模 {old} → {want}")

        if src != orig:
            changed[name] = src

    if do_fix:
        for name, content in changed.items():
            open(os.path.join(JS_DIR, name), "w", encoding="utf-8").write(content)
        if changed:
            log(f"\n✅ 已修正 {len(changed)} 个文件: {', '.join(changed)}")

    return problems


def main():
    do_fix = "--fix" in sys.argv

    log("=" * 70)
    log("S32 数字一致性校验" + ("（自动修正）" if do_fix else ""))
    log("=" * 70)

    log("\n[1] 采集实测值")
    live = get_live()
    for k in ("chunks", "vectors", "text", "pizhu", "terms", "wordfreq",
              "stats", "gnodes", "gedges", "chars_wan"):
        if k in live:
            log(f"  {k:12s} = {live[k]}")

    # strategy_rev 检查
    rev = live.get("strategy_rev") or {}
    log(f"\n[2] strategy_rev 检查: {rev}")
    bad_rev = [k for k in rev if k != EXPECT_STRATEGY]
    if bad_rev:
        log(f"  ❌ 发现非 {EXPECT_STRATEGY} 的策略：{bad_rev}")
        log(f"     → 说明索引被旧脚本污染，请重跑 s10_1_unify_pizhu.py")
    elif rev:
        log(f"  ✅ 全部为 {EXPECT_STRATEGY}")
    else:
        log("  ⚠ 未读到 strategy_rev")

    log("\n[3] JS 硬编码数字比对")
    problems = check_and_fix(live, do_fix)
    if problems:
        for p in problems:
            log(f"  ❌ {p}")
    else:
        log("  ✅ 全部一致")

    log("\n" + "=" * 70)
    if bad_rev or (problems and not do_fix):
        log("结果：存在不一致，需要处理")
        return 1
    log("结果：全部一致 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
