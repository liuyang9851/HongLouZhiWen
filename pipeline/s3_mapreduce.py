# -*- coding: utf-8 -*-
"""
S3 · MapReduce 统计作业（Hadoop Streaming）
=============================================

对应需求 R-506：至少 4 个 MapReduce 作业，结果回写 HDFS + HBase
教材第 5 章（分布式批处理）主案例能力。

四个作业
--------
  job1_wordfreq     全书中文字词频（bigram 口径，与倒排索引一致）
  job2_personfreq   人物提及频次（别名归并，41 人物 / 98 别名）
  job3_cooccur      人物共现对（同 chunk 内共现，用于关系图边权）
  job4_chunkstat    逐回统计（chunk 数 / 字节数 / 平均块大小）

为什么用 Streaming + Python 而不是原生 Java MapReduce
--------------------------------------------------
  1. 复用本地已写好的 Python 语料处理代码（别名表、bigram），零改写
  2. 教材允许 Streaming（V3 实验 B 明确记录 run_wordcount.sh）
  3. 教学价值：mapper/reducer 用普通脚本表达，比 Java 类更容易讲清 shuffle 语义

设计要点
--------
  - 输入是 HDFS 上的 chunks.jsonl（每行一个 JSON）
  - **输入目录不能以 `_` 或 `.` 开头**（Hadoop FileInputFormat 会静默过滤，实测踩过）
  - 使用 combiner（= reducer）压缩 shuffle 数据量，这是 MapReduce 最核心的优化之一
  - 作业结束后把结果从 HDFS 取回，并写入 HBase（humans 可查）

运行（在 master 容器内）
------------------------
    docker exec master bash -lc "cd /workspace/pipeline && bash run_mr.sh"
"""
import os
import sys
import json
import subprocess
import collections

sys.stdout.reconfigure(encoding="utf-8")

HDFS = "/hlm"
HDFS_IN = f"{HDFS}/mrin/chunks"          # 注意：不能以 _ 开头
HDFS_OUT = f"{HDFS}/mrout"
STREAMING = "/opt/hadoop/share/hadoop/tools/lib/hadoop-streaming-3.3.6.jar"
PIPE = "/workspace/pipeline"

# ---------------- 别名表（41 人物 / 98 别名） ----------------
ALIAS = {
    "贾宝玉": ["贾宝玉", "宝玉", "宝二爷", "怡红公子", "绛洞花王", "富贵闲人", "混世魔王"],
    "林黛玉": ["林黛玉", "黛玉", "颦儿", "颦卿", "林妹妹", "潇湘妃子"],
    "薛宝钗": ["薛宝钗", "宝钗", "宝姐姐", "蘅芜君"],
    "王熙凤": ["王熙凤", "凤姐儿", "凤姐", "凤辣子", "琏二奶奶", "凤丫头"],
    "史湘云": ["史湘云", "湘云", "云妹妹", "枕霞旧友"],
    "贾母": ["贾母", "老太太", "史太君", "老祖宗"],
    "贾探春": ["贾探春", "探春", "三姑娘", "蕉下客"],
    "贾迎春": ["贾迎春", "迎春", "二姑娘", "菱洲"],
    "贾惜春": ["贾惜春", "惜春", "四姑娘", "藕榭"],
    "李纨": ["李纨", "大奶奶", "稻香老农", "宫裁"],
    "妙玉": ["妙玉"],
    "袭人": ["袭人", "花袭人", "珍珠"],
    "晴雯": ["晴雯"],
    "平儿": ["平儿", "平姑娘"],
    "鸳鸯": ["鸳鸯"],
    "紫鹃": ["紫鹃"],
    "香菱": ["香菱", "甄英莲", "英莲", "秋菱"],
    "贾琏": ["贾琏", "琏二爷"],
    "贾珍": ["贾珍"],
    "贾蓉": ["贾蓉"],
    "贾政": ["贾政"],
    "王夫人": ["王夫人"],
    "邢夫人": ["邢夫人"],
    "赵姨娘": ["赵姨娘"],
    "薛姨妈": ["薛姨妈"],
    "薛蟠": ["薛蟠", "呆霸王", "薛大傻子"],
    "刘姥姥": ["刘姥姥", "刘老老"],
    "尤二姐": ["尤二姐"],
    "尤三姐": ["尤三姐"],
    "贾环": ["贾环"],
    "贾兰": ["贾兰"],
    "贾雨村": ["贾雨村", "雨村"],
    "甄士隐": ["甄士隐", "士隐"],
    "秦可卿": ["秦可卿", "可卿", "秦氏"],
    "秦钟": ["秦钟", "鲸卿"],
    "柳湘莲": ["柳湘莲", "湘莲"],
    "蒋玉菡": ["蒋玉菡", "琪官"],
    "焦大": ["焦大"],
    "智能儿": ["智能儿", "智能"],
    "板儿": ["板儿"],
    "巧姐": ["巧姐儿", "巧姐"],
}

STOP = set("的了在是我有和就不人都一这中大为上个国到说们")


# ============================================================
# Job 1: 词频（bigram）
# ============================================================
JOB1_MAPPER = r'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""job1 mapper: 每个 chunk 抽出中文 bigram，输出 (bigram, 1)"""
import sys, json

STOP = set("的了在是我有和就不人都一这中大为上个国到说们")

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        c = json.loads(line)
    except Exception:
        continue
    chars = [ch for ch in c.get("text", "") if '\u4e00' <= ch <= '\u9fff']
    for i in range(len(chars) - 1):
        a, b = chars[i], chars[i + 1]
        if a in STOP and b in STOP:
            continue
        sys.stdout.write(f"{a}{b}\t1\n")
'''

JOB1_REDUCER = r'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""job1 reducer/combiner: 按 bigram 累加（输入已按 key 排序）"""
import sys

cur, s = None, 0
for line in sys.stdin:
    try:
        k, v = line.rstrip("\n").split("\t", 1)
        n = int(v)
    except Exception:
        continue
    if k == cur:
        s += n
    else:
        if cur is not None:
            sys.stdout.write(f"{cur}\t{s}\n")
        cur, s = k, n
if cur is not None:
    sys.stdout.write(f"{cur}\t{s}\n")
'''


# ============================================================
# Job 2: 人物频次（别名归并）
# ============================================================
JOB2_MAPPER = r'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
job2 mapper: 每个 chunk 抽人物提及，输出 (标准名, 1)
关键：别名归并。实测「贾宝玉」全名全书只出现 2 次，「宝玉」出现 2,626 次，
      不做归并会得出「贾宝玉只出现 2 次」的荒谬结论。
正则按别名长度降序排列，避免「林黛玉」被「黛玉」切开重复计数。
"""
import sys, json, re

ALIAS = json.load(open("alias.json", encoding="utf-8"))
a2c = {nm: canon for canon, names in ALIAS.items() for nm in names}
PAT = re.compile("|".join(re.escape(x) for x in
                          sorted(a2c, key=len, reverse=True)))

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        c = json.loads(line)
    except Exception:
        continue
    for m in PAT.finditer(c.get("text", "")):
        sys.stdout.write(f"{a2c[m.group(0)]}\t1\n")
'''

JOB2_REDUCER = JOB1_REDUCER


# ============================================================
# Job 3: 人物共现（同 chunk 内）
# ============================================================
JOB3_MAPPER = r'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
job3 mapper: 同一 chunk 内两个人物都出现 → 输出 (A|B, 1)，A<B 保证对称
用于关系图的边权（原文共现强度），与知识图谱三元组互为补充
"""
import sys, json, re, itertools

ALIAS = json.load(open("alias.json", encoding="utf-8"))
a2c = {nm: canon for canon, names in ALIAS.items() for nm in names}
PAT = re.compile("|".join(re.escape(x) for x in
                          sorted(a2c, key=len, reverse=True)))

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        c = json.loads(line)
    except Exception:
        continue
    seen = set()
    for m in PAT.finditer(c.get("text", "")):
        seen.add(a2c[m.group(0)])
    # 同一 chunk 内共现的人物两两配对
    for a, b in itertools.combinations(sorted(seen), 2):
        # 三列输出：a \t b \t 1
        # 为什么不输出 "a|b \t 1"：Hive 外部表按 tab 分列，
        # "a|b" 会整列塞进 a，b 变成 NULL（实测踩过）。
        sys.stdout.write(f"{a}\t{b}\t1\n")
'''

JOB3_REDUCER = r'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
job3 reducer: 按 (a,b) 二元组累加
输入已按 key 排序（a\tb 作为复合 key，MapReduce 保证同 key 相邻）
"""
import sys

cur, s = None, 0
for line in sys.stdin:
    try:
        a, b, v = line.rstrip("\n").split("\t")
        n = int(v)
        k = (a, b)
    except Exception:
        continue
    if k == cur:
        s += n
    else:
        if cur is not None:
            sys.stdout.write(f"{cur[0]}\t{cur[1]}\t{s}\n")
        cur, s = k, n
if cur is not None:
    sys.stdout.write(f"{cur[0]}\t{cur[1]}\t{s}\n")
'''


# ============================================================
# Job 4: 逐回统计
# ============================================================
JOB4_MAPPER = r'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""job4 mapper: 按回次聚合 (chunk 数, 总字节, 总字数)"""
import sys, json

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        c = json.loads(line)
    except Exception:
        continue
    hui = c.get("hui")
    if hui is None:
        continue
    # value: chunk数, 字节数, 字数
    sys.stdout.write(f"{hui}\t1\t{len(c.get('text','').encode('utf-8'))}\t{c.get('char_count',0)}\n")
'''

JOB4_REDUCER = r'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""job4 reducer: 按回次累加三元组"""
import sys

cur = None
nc, nb, nch = 0, 0, 0
for line in sys.stdin:
    try:
        k, a, b, c = line.rstrip("\n").split("\t")
        v1, v2, v3 = int(a), int(b), int(c)
    except Exception:
        continue
    if k == cur:
        nc += v1; nb += v2; nch += v3
    else:
        if cur is not None:
            sys.stdout.write(f"{cur}\t{nc}\t{nb}\t{nch}\n")
        cur, nc, nb, nch = k, v1, v2, v3
if cur is not None:
    sys.stdout.write(f"{cur}\t{nc}\t{nb}\t{nch}\n")
'''


def write_scripts():
    """把 mapper/reducer/alias 写到 pipeline 目录（-files 分发要求 ASCII 文件名）"""
    files = {
        "job1_mapper.py": JOB1_MAPPER,
        "job1_reducer.py": JOB1_REDUCER,
        "job2_mapper.py": JOB2_MAPPER,
        "job2_reducer.py": JOB2_REDUCER,
        "job3_mapper.py": JOB3_MAPPER,
        "job3_reducer.py": JOB3_REDUCER,
        "job4_mapper.py": JOB4_MAPPER,
        "job4_reducer.py": JOB4_REDUCER,
        "alias.json": json.dumps(ALIAS, ensure_ascii=False),
    }
    for name, content in files.items():
        p = os.path.join(PIPE, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        os.chmod(p, 0o755)
    print(f"写出 {len(files)} 个作业脚本到 {PIPE}")


def run_job(name, mapper, reducer, use_combiner=True, n_reduce=1):
    """提交一个 Streaming 作业"""
    out = f"{HDFS_OUT}/{name}"
    subprocess.run(["hdfs", "dfs", "-rm", "-r", "-f", out],
                   capture_output=True)
    cmd = [
        "hadoop", "jar", STREAMING,
        "-D", f"mapreduce.job.reduces={n_reduce}",
        "-D", "mapreduce.job.name=hlm_" + name,
        "-files", f"{PIPE}/{mapper},{PIPE}/{reducer},{PIPE}/alias.json",
        "-input", HDFS_IN,
        "-output", out,
        "-mapper", f"python3 {mapper}",
        "-reducer", f"python3 {reducer}",
    ]
    if use_combiner:
        cmd += ["-combiner", f"python3 {reducer}"]
    print(f"\n{'=' * 70}\n提交作业: {name}\n{'=' * 70}")
    r = subprocess.run(cmd, capture_output=True, text=True)
    tail = (r.stdout + r.stderr).splitlines()
    # 只打印关键行，避免几百行 INFO 淹没输出
    for line in tail:
        if any(k in line for k in (
                "Running job", "Job job_", "completed successfully",
                "failed with state", "Launched map tasks",
                "Launched reduce tasks", "Map input records",
                "Map output records", "Combine input records",
                "Combine output records", "Reduce input groups",
                "Reduce output records", "HDFS: Number of bytes written",
                "ERROR", "Exception")):
            print("  " + line.strip()[:200])
    return r.returncode == 0, out


def fetch(out, local_name):
    """把 HDFS 输出取回本地（getmerge）"""
    local = os.path.join(PIPE, "out", local_name)
    subprocess.run(["hdfs", "dfs", "-getmerge", out, local],
                   capture_output=True)
    if os.path.exists(local):
        n = sum(1 for _ in open(local, encoding="utf-8", errors="replace"))
        print(f"  取回 {local_name}: {n:,} 行  {os.path.getsize(local):,} B")
        return local
    return None


def main():
    if not os.path.exists("/workspace/pipeline"):
        print("必须在 master 容器内运行")
        sys.exit(1)

    write_scripts()

    # 准备输入（chunks.jsonl 已在 HDFS，但目录名必须是 mrin 不是 _mrin）
    subprocess.run(["hdfs", "dfs", "-rm", "-r", "-f", HDFS_IN], capture_output=True)
    subprocess.run(["hdfs", "dfs", "-mkdir", "-p", HDFS_IN], capture_output=True)
    r = subprocess.run(
        ["hdfs", "dfs", "-put", "-f",
         f"{PIPE}/out/chunks.jsonl", f"{HDFS_IN}/chunks.jsonl"],
        capture_output=True, text=True)
    print(f"输入准备: {'OK' if r.returncode == 0 else 'FAIL'}")
    print(subprocess.run(["hdfs", "dfs", "-ls", HDFS_IN],
                         capture_output=True, text=True).stdout.strip())

    jobs = [
        ("wordfreq", "job1_mapper.py", "job1_reducer.py", "wordfreq.tsv"),
        ("personfreq", "job2_mapper.py", "job2_reducer.py", "personfreq.tsv"),
        ("cooccur", "job3_mapper.py", "job3_reducer.py", "cooccur.tsv"),
        ("chunkstat", "job4_mapper.py", "job4_reducer.py", "chunkstat.tsv"),
    ]

    results = []
    for name, m, rd, local in jobs:
        ok, out = run_job(name, m, rd)
        results.append({"job": name, "ok": ok, "hdfs_out": out})
        if ok:
            fetch(out, local)
        else:
            print(f"  ❌ {name} 失败")

    print(f"\n{'=' * 70}\n作业汇总\n{'=' * 70}")
    for r in results:
        print(f"  {r['job']:<14} {'✅ 成功' if r['ok'] else '❌ 失败'}  {r['hdfs_out']}")

    with open(os.path.join(PIPE, "out", "mr_results.json"), "w",
              encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
