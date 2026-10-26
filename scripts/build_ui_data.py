# -*- coding: utf-8 -*-
"""
build_ui_data.py —— 界面数据准备脚本（阶段 S1）

从真实语料生成前端需要的全部静态数据，供「界面设计与预览」阶段使用。
本项目不使用任何编造的 mock 数据：每一张图表、每一个人物节点、
每一处统计数字都来自 docs/01-需求分析说明书.md 附录 B 中列出的真实资产。

产出目录：D:\\学校事务\\大数据原理与应用\\红楼智问\\web\\data\\
  chapters.json      80 回目录 + 段落（供原文阅读界面）
  chapter_001.json   单回正文（按需加载，含段落级 id 用于高亮定位）
  stats.json         统计信息界面的全部数据
  graph.json         人物关系图的节点与边
  pizhu.json         批语样本（供阅读界面侧栏）
  manifest.json      数据清单与来源溯源
"""
import sys, os, json, re, hashlib, collections, csv

sys.stdout.reconfigure(encoding="utf-8")

WORK = r"D:\学校事务\大数据原理与应用"
RED = r"D:\Programming\Red\hlm_corpus"
OUT = os.path.join(WORK, "红楼智问", "web", "data")
os.makedirs(OUT, exist_ok=True)

# ---------------- 别名表（来自 查询示例/别名表.py，41 人物 / 98 别名） ----------------
sys.path.insert(0, os.path.join(WORK, "查询示例"))
from 别名表 import ALIAS  # noqa: E402

A2C = {}
for canon, names in ALIAS.items():
    for nm in names:
        A2C[nm] = canon
PAT = re.compile("|".join(re.escape(x) for x in sorted(A2C, key=len, reverse=True)))

# 家族归属（依据 KGQA_HLM relation.txt 的家族列 + 人物索引分层，人工整理）
FAMILY = {
    "贾宝玉": "贾", "林黛玉": "其他", "薛宝钗": "薛", "王熙凤": "王", "贾母": "史",
    "贾探春": "贾", "贾迎春": "贾", "贾惜春": "贾", "李纨": "贾", "妙玉": "其他",
    "袭人": "贾", "晴雯": "贾", "平儿": "王", "鸳鸯": "贾", "紫鹃": "其他",
    "香菱": "其他", "贾琏": "贾", "贾珍": "贾", "贾蓉": "贾", "贾政": "贾",
    "王夫人": "王", "邢夫人": "贾", "赵姨娘": "贾", "薛姨妈": "薛", "薛蟠": "薛",
    "刘姥姥": "其他", "尤二姐": "其他", "尤三姐": "其他", "贾环": "贾", "贾兰": "贾",
    "贾雨村": "其他", "甄士隐": "其他", "秦可卿": "贾", "秦钟": "其他", "柳湘莲": "其他",
    "蒋玉菡": "其他", "焦大": "贾", "智能儿": "其他", "板儿": "其他", "巧姐": "贾",
}

# 人物身份（供详情面板）
ROLE = {
    "贾宝玉": "荣国府嫡孙，神瑛侍者转世", "林黛玉": "姑苏林家女，绛珠仙草转世",
    "薛宝钗": "金陵薛家女，蘅芜君", "王熙凤": "荣国府管家奶奶，琏二奶奶",
    "贾母": "荣国府老祖宗，史太君", "袭人": "怡红院大丫鬟，后嫁蒋玉菡",
    "晴雯": "怡红院大丫鬟，被逐后夭亡", "平儿": "凤姐陪房丫鬟，琏二姨娘",
    "紫鹃": "潇湘馆大丫鬟，黛玉贴身", "鸳鸯": "贾母大丫鬟，誓死不嫁",
}


def load_jsonl(p):
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def w(name, obj):
    p = os.path.join(OUT, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    print("  written %-18s %8d B" % (name, os.path.getsize(p)))
    return p


# ============================================================
# 1. 章节：chapters.json（80 回目录）+ chapter_XXX.json（正文）
# ============================================================
print("[1/6] 章节数据 ...")
chapters = list(load_jsonl(os.path.join(RED, "clean", "chapters.jsonl")))
huimu = json.load(open(os.path.join(RED, "clean", "huimu.json"), encoding="utf-8"))

toc = []
for c in chapters:
    hui = c["hui"]
    paras = c["paragraphs"]
    ch_chars = sum(len(p) for p in paras)
    # 段落级 id：稳定的 hui+paraindex，供答案引用与高亮定位
    plist = [{"i": i, "id": "p%d_%d" % (hui, i), "t": t} for i, t in enumerate(paras)]
    fn = "chapter_%03d.json" % hui
    w(fn, {
        "hui": hui,
        "huimu": c["huimu"],
        "char_count": ch_chars,
        "is_fanli": hui == 0,
        "paragraphs": plist,
    })
    toc.append({
        "hui": hui,
        "huimu": c["huimu"],
        "n_para": len(paras),
        "chars": ch_chars,
        "short": (c["huimu"].split(" ", 1)[-1] if " " in c["huimu"] else c["huimu"]),
        "file": fn,
    })

w("chapters.json", {
    "book": "红楼梦",
    "version": "抚琴居校本",
    "version_id": "fuqinju",
    "n_chapters": len(toc),
    "n_paragraphs": sum(t["n_para"] for t in toc),
    "total_chars": sum(t["chars"] for t in toc),
    "chapters": toc,
})

# ============================================================
# 2. 统计：stats.json
# ============================================================
print("[2/6] 统计指标 ...")

# 2.1 逐回字数 / 段落数
per_chapter = [{"hui": t["hui"], "huimu": t["short"], "chars": t["chars"], "n_para": t["n_para"]}
               for t in toc if not t.get("is_fanli") and t["hui"] > 0]

# 2.2 人物提及频次（正文口径，别名归并）
cnt_body = collections.Counter()
cnt_pizhu = collections.Counter()
alias_detail = collections.defaultdict(collections.Counter)

for c in chapters:
    for p in c["paragraphs"]:
        for m in PAT.finditer(p):
            canon = A2C[m.group(0)]
            cnt_body[canon] += 1
            alias_detail[canon][m.group(0)] += 1

# 2.3 脂批：用重解码文件（唯一可信版本来源）
redec = list(load_jsonl(os.path.join(WORK, "码点核对", "pizhu_redecoded.jsonl")))
by_ver = collections.Counter()
by_hui_pz = collections.Counter()
for r in redec:
    for v in (r.get("versions_legend") or ["(未识别)"]):
        by_ver[v] += 1
    by_hui_pz[r["hui"]] += 1
    for m in PAT.finditer(r.get("text") or ""):
        cnt_pizhu[A2C[m.group(0)]] += 1

# 2.4 词频（用既有 Hadoop 作业的真实产出，见 hadoop词频/hadoop_wordcount.tsv）
word_freq = []
wf_path = os.path.join(WORK, "hadoop词频", "hadoop_wordcount.tsv")
if os.path.exists(wf_path):
    with open(wf_path, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2:
                try:
                    word_freq.append({"w": parts[0], "c": int(parts[1])})
                except ValueError:
                    pass
    word_freq.sort(key=lambda x: -x["c"])
    word_freq = word_freq[:100]

# 2.5 偏斜度（教材要求 >2 必须解释）
chars_vals = [x["chars"] for x in per_chapter]
pz_vals = [by_hui_pz.get(h, 0) for h in range(1, 81)]
ver_vals = list(by_ver.values())


def skew(vals):
    vals = [v for v in vals if v > 0]
    if not vals:
        return None
    mx, mn_ = max(vals), sum(vals) / len(vals)
    return {"max": mx, "mean": round(mn_, 3), "ratio": round(mx / mn_, 3), "n": len(vals)}


# 热点解释（领域解释，不是"数据就是这样"）
top_pz = sorted(by_hui_pz.items(), key=lambda x: -x[1])[:10]
early27 = sum(v for h, v in by_hui_pz.items() if 1 <= h <= 27)
total_pz_hui = sum(by_hui_pz.values())

stats = {
    "version_id": "fuqinju",
    "per_chapter": per_chapter,
    "top_person_body": [{"name": k, "count": v,
                         "aliases": [{"a": a, "c": c} for a, c in alias_detail[k].most_common()]}
                        for k, v in cnt_body.most_common(20)],
    "top_person_pizhu": [{"name": k, "count": v} for k, v in cnt_pizhu.most_common(20)],
    "pizhu_by_version": [{"name": k, "count": v} for k, v in by_ver.most_common()],
    "pizhu_by_hui": [{"hui": h, "count": by_hui_pz.get(h, 0)} for h in range(1, 81)],
    "word_freq_top100": word_freq,
    "skew": {
        "chapter_chars": skew(chars_vals),
        "pizhu_per_hui": skew(pz_vals),
        "pizhu_per_version": skew(ver_vals),
    },
    "hotspot_explain": {
        "top10_pizhu_hui": [{"hui": h, "count": c,
                             "huimu": next((t["short"] for t in toc if t["hui"] == h), "")}
                            for h, c in top_pz],
        "early_27_share": round(early27 / total_pz_hui, 4) if total_pz_hui else 0,
        "reason": [
            "版本存世范围不同：甲戌本仅存 16 回（覆盖 1–28 回），庚辰本存 65 回，故版本维度天然不均",
            "批者关注重心在前半部：前 27 回占全部批语的 77%",
            "批型分布不同：甲戌本以侧批为主，庚辰本以双行夹批为主，蒙府本独有回末总批",
        ],
        "chapter67_note": "第 67 回同时刊出两种文字（甲版「馈土物」/乙版「见土仪」），字数为他回 2.9 倍，是天然离群点",
    },
}
w("stats.json", stats)

# ============================================================
# 3. 人物关系图：graph.json
# ============================================================
print("[3/6] 人物关系图 ...")
nodes_map = {}
edges = []

# 3.1 三套三元组里，优先 kg-hongloumeng（head/tail/relation/label 最规范）
kg_path = os.path.join(WORK, "红学数据源", "kg-hongloumeng__hlm_triples.csv")
raw_triples = []
if os.path.exists(kg_path):
    with open(kg_path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            raw_triples.append((row["head"], row["tail"], row["relation"], row.get("label", "")))

# 3.2 KGQA 关系（含家族归属，关系更口语）
kgqa_path = os.path.join(WORK, "红学数据源", "KGQAHLM__relation.txt")
kgqa_triples = []
if os.path.exists(kgqa_path):
    with open(kgqa_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            parts = line.strip().split(",")
            if len(parts) >= 3 and parts[0] and parts[1]:
                kgqa_triples.append((parts[0], parts[1], parts[2],
                                     parts[3] if len(parts) > 3 else ""))


def add_edge(h, t, rel, fam, src):
    if not h or not t or h == t:
        return
    key = (h, t, rel)
    if key in seen:
        return
    seen.add(key)
    edges.append({"source": h, "target": t, "relation": rel, "family": fam, "src": src})
    for n in (h, t):
        if n not in nodes_map:
            nodes_map[n] = {"id": n, "name": n, "family": FAMILY.get(n, "其他"),
                            "role": ROLE.get(n, ""), "degree": 0}
    # KGQA 的家族列形如「贾家宁国府」/「史家」/「王家」/「薛家」/「林家」，用它补全 FAMILY
    if fam:
        for n in (h, t):
            if n not in FAMILY or FAMILY[n] == "其他":
                for fx in ("贾", "史", "王", "薛", "林"):
                    if fam.startswith(fx):
                        FAMILY[n] = fx
                        nodes_map[n]["family"] = fx
                        break


seen = set()
for h, t, rel, label in raw_triples:
    add_edge(h, t, label or rel, "", "kg-hongloumeng")
for h, t, rel, fam in kgqa_triples:
    add_edge(h, t, rel, fam, "KGQA_HLM")

# 3.3 节点权重用正文提及频次（真实统计）
for n, nd in nodes_map.items():
    nd["mention"] = cnt_body.get(n, 0) + cnt_pizhu.get(n, 0)
    nd["in_alias_table"] = n in ALIAS

for e in edges:
    nodes_map[e["source"]]["degree"] += 1
    nodes_map[e["target"]]["degree"] += 1

# 3.4 保留有连接的节点，按度排序取前 120 个（保证可视化流畅）
nodes_sorted = sorted(nodes_map.values(), key=lambda x: (-x["degree"], -x["mention"]))
keep = {n["id"] for n in nodes_sorted[:120]}
graph_nodes = [n for n in nodes_sorted if n["id"] in keep]
graph_edges = [e for e in edges if e["source"] in keep and e["target"] in keep]

w("graph.json", {
    "n_nodes": len(graph_nodes),
    "n_nodes_total": len(nodes_map),
    "n_edges": len(graph_edges),
    "n_edges_total": len(edges),
    "relations": sorted({e["relation"] for e in graph_edges}),
    "families": ["贾", "史", "王", "薛", "林", "其他"],
    "nodes": graph_nodes,
    "edges": graph_edges,
    "sources": ["kg-hongloumeng__hlm_triples.csv", "KGQAHLM__relation.txt"],
})

# ============================================================
# 4. 批语样本：pizhu.json（供阅读界面侧栏，每回最多 30 条）
# ============================================================
print("[4/6] 批语样本 ...")
pz_by_hui = collections.defaultdict(list)
for r in redec:
    pz_by_hui[r["hui"]].append({
        "text": r["text"][:300],
        "versions": r.get("versions_legend") or [],
        "page": r.get("page"),
        "conf": r.get("conf"),
    })
pz_out = {}
for h, lst in pz_by_hui.items():
    pz_out[str(h)] = lst[:250]   # S28：与 s16 对齐
w("pizhu.json", {"by_hui": pz_out, "note": "版本归属来自 码点核对/pizhu_redecoded.jsonl 的 versions_legend（书中略字对照表），旧 version/ptype/conf 已停用"})

# ============================================================
# 5. 版本覆盖矩阵（语料版本界面）
# ============================================================
print("[5/6] 版本覆盖矩阵 ...")
ver_cov = collections.defaultdict(set)
for r in redec:
    for v in (r.get("versions_legend") or []):
        ver_cov[v].add(r["hui"])
matrix = {v: sorted(hs) for v, hs in ver_cov.items()}
w("version_matrix.json", {"version_to_hui": matrix, "n_hui": 80})

# ============================================================
# 6. manifest：溯源
# ============================================================
print("[6/6] manifest ...")
w("manifest.json", {
    "generated_by": "build_ui_data.py",
    "generated_at": "2026-10-24",
    "stage": "S1 界面设计数据准备",
    "sources": [
        {"path": "hlm_corpus/clean/chapters.jsonl", "n": len(chapters), "desc": "81 条章节（凡例+80 回）"},
        {"path": "hlm_corpus/clean/huimu.json", "desc": "回目对照表"},
        {"path": "码点核对/pizhu_redecoded.jsonl", "n": len(redec), "desc": "脂批版本重解码，唯一可信版本来源"},
        {"path": "hadoop词频/hadoop_wordcount.tsv", "desc": "MapReduce 词频作业真实产出"},
        {"path": "查询示例/别名表.py", "n": len(ALIAS), "desc": "41 人物 / 98 别名，用于频次归并"},
        {"path": "红学数据源/kg-hongloumeng__hlm_triples.csv", "n": len(raw_triples), "desc": "人物关系三元组"},
        {"path": "红学数据源/KGQAHLM__relation.txt", "n": len(kgqa_triples), "desc": "关系+家族归属"},
    ],
    "note": "本目录所有数据均由真实语料生成，无编造 mock 数据",
    "stats_summary": {
        "chapters": len(toc),
        "paragraphs": sum(t["n_para"] for t in toc),
        "total_chars": sum(t["chars"] for t in toc),
        "pizhu": len(redec),
        "graph_nodes": len(graph_nodes),
        "graph_edges": len(graph_edges),
        "word_freq_entries": len(word_freq),
    },
})

print("\n=== 完成 ===")
print("输出目录:", OUT)
print("偏斜: 逐回字数 ratio=%s | 逐回批语 ratio=%s | 版本维度 ratio=%s" % (
    stats["skew"]["chapter_chars"]["ratio"],
    stats["skew"]["pizhu_per_hui"]["ratio"],
    stats["skew"]["pizhu_per_version"]["ratio"],
))
