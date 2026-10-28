# -*- coding: utf-8 -*-
"""
重建 HBase 5 张表（S40b）
=========================

背景
----
2026-10-28 集群因 Docker Desktop 重启 + 容器启动顺序问题导致
HBase 元数据损坏（hbase:meta 的 info:server 指向已死会话，
Master 卡在 holding-pattern）。清理 ZK + HDFS /hbase 后，
HBase 重建成功但表结构丢失，需重新建表。

表结构与原版一致（见 s2_2_index.py / s3_to_hbase_hive.py）：
    hlm_chunks     families: d, m
    hlm_vectors    families: v, m
    hlm_terms      families: p
    hlm_stats      families: s
    hlm_wordfreq   families: f

运行（master 容器内）：
    docker exec master bash -lc "python3 /workspace/pipeline/s40_rebuild_tables.py"
"""
import sys

sys.stdout.reconfigure(encoding="utf-8")

import happybase

HOST = "localhost"
PORT = 9090

TABLES = {
    "hlm_chunks":   ["d", "m"],
    "hlm_vectors":  ["v", "m"],
    "hlm_terms":    ["p"],
    "hlm_stats":    ["s"],
    "hlm_wordfreq": ["f"],
}


def main():
    c = happybase.Connection(
        HOST, PORT,
        table_prefix=b"",
        table_prefix_separator=b"",
        timeout=60000,
    )
    existing = {t.decode() for t in c.tables()}
    print(f"现有表: {sorted(existing) or '无'}")

    for name, fams in TABLES.items():
        if name in existing:
            print(f"  跳过 {name}（已存在）")
            continue
        c.create_table(name, {f: {} for f in fams})
        print(f"  ✅ 建表 {name}  列族={fams}")

    final = {t.decode() for t in c.tables()}
    print()
    print("最终表列表:")
    for name in sorted(final):
        print(f"  {name}")
    print()
    print(f"共 {len(final)} 张表")
    missing = set(TABLES) - final
    if missing:
        print(f"⚠ 缺少: {missing}")
        return 1
    print("✅ 5 张表全部就绪")
    return 0


if __name__ == "__main__":
    sys.exit(main())
