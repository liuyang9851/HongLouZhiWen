# -*- coding: utf-8 -*-
"""
校验 HBase 5 张表的数据完整性
=============================

期望值（与项目文档、后端 /health 一致）：
    hlm_chunks    11,932   （正文 3,270 + 脂批 8,662）
    hlm_vectors   11,932
    hlm_terms    369,939
    hlm_stats        161
    hlm_wordfreq 133,673

用法（master 容器内）：
    docker cp tools/check_hbase_tables.py master:/tmp/
    docker exec master python3 /tmp/check_hbase_tables.py

退出码：0 = 全部通过，1 = 有不一致
"""
import sys

sys.stdout.reconfigure(encoding="utf-8")

import happybase

EXPECT = {
    "hlm_chunks":   11_932,   # 正文 3,270 + 脂批 8,662
    "hlm_vectors":  11_932,
    "hlm_terms":   369_939,
    "hlm_stats":       161,
    "hlm_wordfreq": 133_673,
}

# 正文/脂批分布（只对 hlm_chunks 校验）
EXPECT_DIST = {
    "chenggao/text":   1689,
    "fuqinju/text":    1051,
    "guiyouben/text":   530,
    "fuqinju/pizhu":   3221,   # S40b 修复 chunk_id 冲突后恢复原始条数
    "zhipi/pizhu":     5219,
    "guiyouben/pizhu":  222,
}


def main():
    try:
        c = happybase.Connection(
            "localhost", 9090, table_prefix=b"",
            table_prefix_separator=b"", timeout=60000,
        )
    except Exception as e:
        print(f"✗ 连不上 HBase Thrift(9090): {type(e).__name__}: {e}")
        print("  先启动 Thrift：")
        print("    docker exec -d master bash -lc \"hbase thrift start > /logs/hbase-thrift.log 2>&1\"")
        return 1

    existing = {t.decode() for t in c.tables()}
    print("现有表:", sorted(existing) or "无")
    print()

    ok = True
    print(f"{'表名':<14} {'实际':>10} {'期望':>10} {'状态':>6}")
    print("-" * 46)
    for name, exp in EXPECT.items():
        if name not in existing:
            print(f"{name:<14} {'—':>10} {exp:>10,} {'缺失':>6}")
            ok = False
            continue
        try:
            n = sum(1 for _ in c.table(name).scan(batch_size=2000))
        except Exception as e:
            print(f"{name:<14} {'ERR':>10} {exp:>10,} {type(e).__name__:>6}")
            ok = False
            continue
        status = "✓" if n == exp else "✗"
        if n != exp:
            ok = False
        print(f"{name:<14} {n:>10,} {exp:>10,} {status:>6}")

    # 分布校验
    if "hlm_chunks" in existing:
        print()
        print("hlm_chunks 分布:")
        dist = {}
        try:
            for _, data in c.table("hlm_chunks").scan(batch_size=2000):
                # 注意：source 两处列族不一致（实测）
                #   正文（s7_2 写入）→ d:source
                #   脂批（s40_import_pizhu 写入）→ m:source
                ver = (data.get(b"m:version") or b"").decode()
                src = (data.get(b"d:source") or data.get(b"m:source") or b"").decode()
                k = f"{ver}/{src}"
                dist[k] = dist.get(k, 0) + 1
            for k, exp in sorted(EXPECT_DIST.items()):
                got = dist.get(k, 0)
                mark = "✓" if got == exp else "✗"
                if got != exp:
                    ok = False
                print(f"  {k:<18} {got:>6,} / {exp:>6,}  {mark}")
        except Exception as e:
            print(f"  分布读取失败: {type(e).__name__}")
            ok = False

    print()
    if ok:
        print("✅ 全部通过，数据完整")
        return 0
    print("✗ 有缺失或不一致，见上表")
    print()
    print("重新导入：")
    print("  docker exec master bash -lc \"cd /workspace/pipeline && python3 s7_2_index_versions.py\"")
    print("  docker exec master python3 /workspace/pipeline/s40_import_pizhu.py")
    print("  docker exec master bash -lc \"cd /workspace/pipeline && python3 s3_to_hbase_hive.py\"")
    return 1


if __name__ == "__main__":
    sys.exit(main())
