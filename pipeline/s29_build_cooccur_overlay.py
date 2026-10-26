# -*- coding: utf-8 -*-
"""
S29 · 图谱共现叠加层（把 MapReduce 产出接入人物关系图）
=====================================================

背景
----
用户提问：「项目里人物关系图的实现和 hadoop 有没有关系？」

排查结论：**没有直接关系**。
  · 边（"丫鬟"/"父子"等语义关系）来自本地 CSV
    （kg-hongloumeng / KGQAHLM / DotRC 三个开源知识图谱）
  · 生成脚本 build_ui_data.py 纯 Python 读本地文件，不碰 HDFS/HBase
  · HDFS 里没有图谱文件，HBase 里没有 hlm_kg 表

但矛盾在于：项目里同时存在 **MapReduce 的 cooccur 产出**（10,742 行），
只用在了 RAG 的 L3 检索通道，**没用在关系图可视化上**。

本脚本做的事
------------
从 HBase `hlm_stats` 读出 MapReduce 聚合的共现对（588 对），
导出成前端可用的叠加图层，让关系图同时展示：
  · 语义边（CSV）：丫鬟 / 父子 / 夫妻 …  ← 有类型，但非本项目计算
  · 共现边（MapReduce）：同回共现 N 次      ← 无类型，但是真实 Hadoop 产出

这样图谱就真正带上了 Hadoop 血缘。

数据来源
--------
  HBase hlm_stats  rowkey = cooccur|<A>|<B>  cf=s:cnt
  上游：MapReduce cooccur 作业（/hlm/mrout/cooccur，10,742 行逐 chunk → 聚合为 588 对）

运行（在 master 容器内）：
  python3 s29_build_cooccur_overlay.py
输出：
  /tmp/cooccur_overlay.json  → 拷回 web/data/
"""
import os
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")

OUT = "/tmp/cooccur_overlay.json"

# 共现次数阈值：太低会变成蜘蛛网，太高只剩几人
MIN_CNT = 10
# 最多导出多少条边
MAX_EDGES = 300


def log(m):
    print(m, flush=True)


def main():
    import happybase

    log("=" * 70)
    log("S29 图谱共现叠加层（MapReduce → 关系图）")
    log("=" * 70)

    conn = happybase.Connection("localhost", 9090,
                               table_prefix=b"", table_prefix_separator=b"")
    t = conn.table("hlm_stats")

    pairs = []
    for k, v in t.scan(row_prefix=b"cooccur|"):
        parts = k.decode().split("|")
        if len(parts) != 3:
            continue
        _, a, b = parts
        cnt = int(v.get(b"s:cnt", b"0").decode() or 0)
        if cnt >= MIN_CNT:
            pairs.append({"source": a, "target": b, "count": cnt})

    pairs.sort(key=lambda x: -x["count"])
    pairs = pairs[:MAX_EDGES]

    log(f"\n读取 cooccur 完成：≥{MIN_CNT} 次的有 {len(pairs)} 对（上限 {MAX_EDGES}）")
    log(f"\nTop 10：")
    for p in pairs[:10]:
        log(f"  {p['source']:8s} × {p['target']:8s}  {p['count']:>4} 次")

    out = {
        "generated_from": "HBase hlm_stats (cooccur|A|B, cf=s:cnt)",
        "upstream": "MapReduce cooccur 作业 /hlm/mrout/cooccur",
        "min_count": MIN_CNT,
        "n_pairs": len(pairs),
        "note": ("这些边来自 MapReduce 的逐 chunk 共现统计，"
                 "反映「同回同时被提及」的强度，不含关系类型。"
                 "与 CSV 语义边（丫鬟/父子等）互补。"),
        "edges": pairs,
    }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    log(f"\n已写 {OUT}  ({os.path.getsize(OUT):,} B)")

    # 统计
    nodes = set()
    for p in pairs:
        nodes.add(p["source"])
        nodes.add(p["target"])
    log(f"涉及节点: {len(nodes)} 个")
    log(f"共现次数范围: {pairs[-1]['count']} ~ {pairs[0]['count']}")


if __name__ == "__main__":
    main()
