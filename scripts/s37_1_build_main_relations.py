# -*- coding: utf-8 -*-
"""
S37-1 · 生成「主要人物关系图」数据
==================================

背景
----
人物关系页已有 2D / 3D / 和弦图三种视图，但都基于全量 350 节点。
节点太多时看不清具体关系，且没有人物形象。

本脚本为「简易人物关系图」准备数据：
  · 只保留主要人物（默认 14 人，均有改琦画像）
  · 关系**双向去重 + 名称归一化**
  · 输出节点（含头像路径）与边（含关系文字）

为什么必须去重
--------------
全量图谱是四个开源来源合并的，同一对关系被重复记录，例如林黛玉与薛宝钗：

    黛玉 --[友]-->   宝钗
    黛玉 --[朋友]--> 宝钗
    宝钗 --[朋友]--> 黛玉     （反向重复）

不去重的话，14 个节点之间会画出几倍粗的重叠线，完全看不清。

归一化规则
----------
把同义关系词映射到统一的「显示名」，例如：
    友 / 朋友 / 好友            → 朋友
    妾 / 姨娘                   → 妾
    表兄妹 / 表兄 / 表妹 / 姑表 → 表亲
其余保留原词。

输出：web/data/main_relations.json

运行：python s37_1_build_main_relations.py
"""
import os
import sys
import json
import urllib.request
import collections

sys.stdout.reconfigure(encoding="utf-8")

ROOT = r"D:\学校事务\大数据原理与应用\红楼智问"
WEB = os.path.join(ROOT, "web")
OUT = os.path.join(WEB, "data", "main_relations.json")
AVATAR_DIR = os.path.join(WEB, "public", "img", "avatars")

# ---- 主要人物（14 人，全部有改琦《红楼梦图咏》画像）----
# 选取依据：
#   ① 主角必选：宝玉、黛玉、宝钗
#   ② 从核心做「与已选连接数最多」的贪心扩展（只在有改琦画像的人里选）
#   ③ 补入桥梁人物——否则王熙凤/秦可卿的边全指向非主角，会成孤岛
#   实测：16 人 / 内部边连通 / 无孤立点
MAIN = [
    "贾宝玉", "林黛玉", "薛宝钗", "王熙凤",
    "史湘云", "贾探春", "贾迎春", "贾惜春",
    "李纨",   "妙玉",   "袭人",   "晴雯",
    "秦可卿", "贾琏",   "王夫人", "邢岫烟",
]

# ---- 关系词归一化：同义 → 统一显示名 ----
NORM = {
    # 朋友类
    "友": "朋友", "好友": "朋友", "朋友": "朋友", "挚友": "朋友",
    # 亲属（把方向性描述合并为一个中性词，箭头上不区分）
    "表兄妹": "表亲", "表兄": "表亲", "表妹": "表亲", "表姐": "表亲",
    "姑表": "表亲", "姨表": "表亲",
    "丈夫": "夫妻", "妻": "夫妻", "妻子": "夫妻", "正妻": "夫妻",
    "妾": "妾室", "姨娘": "妾室",
    "父亲": "父子", "子": "父子", "儿子": "父子", "父": "父子",
    "母亲": "母子", "母": "母子", "女儿": "母女", "女": "母女",
    "儿媳": "婆媳", "婆婆": "婆媳",
    "内侄女": "姑侄", "伯父": "叔侄",
    "姐姐": "姐妹", "妹妹": "姐妹", "姐妹": "姐妹",
    "哥哥": "兄弟", "弟弟": "兄弟", "兄弟": "兄弟",
    # 主仆
    "丫鬟": "主仆", "丫环": "主仆", "奴仆": "主仆", "侍女": "主仆",
    "主人": "主仆",
}

# 归一化后仍视为「同一对关系」的键（用于去重）
def norm_rel(r):
    r = (r or "").strip()
    return NORM.get(r, r)


def main():
    print("=" * 66)
    print("S37-1 生成主要人物关系图数据")
    print("=" * 66)

    g = json.loads(urllib.request.urlopen("http://127.0.0.1:5173/api/graph", timeout=20).read())
    nodes_all = {n["id"]: n for n in g["nodes"]}
    print(f"全量图谱: {len(g['nodes'])} 节点 / {len(g['edges'])} 边")

    # ---- 1) 筛选主要人物，且必须有画像 ----
    have_avatar = set(
        f[:-4] for f in os.listdir(AVATAR_DIR)
        if f.endswith(".jpg") and not f.startswith("_")
    )
    nodes = []
    for name in MAIN:
        if name not in nodes_all:
            print(f"  [跳过] {name} 不在图谱中")
            continue
        # 所有人都有 _200.png：有改琦画像的用真图，
        # 贾琏 / 王夫人无专属画像 → 用「姓氏圆形徽记」（同规格，不冒称人物像）
        has_av = True
        n = nodes_all[name]
        nodes.append({
            "id": name,
            "name": name,
            "family": n.get("family") or "其他",
            "degree": n.get("degree", 0),
            "mention": n.get("mention", 0),
            "avatar": (f"img/avatars/{name}_200.png" if has_av else ""),
            "role": (n.get("role") or "")[:40],
        })
    ids = {n["id"] for n in nodes}
    print(f"\n主要人物: {len(nodes)} 个")

    # ---- 2) 关系去重 + 归一化 ----
    # key = 无序对 + 归一化关系名
    seen = {}
    for e in g["edges"]:
        s, t = e["source"], e["target"]
        if s not in ids or t not in ids or s == t:
            continue
        rel = norm_rel(e["relation"])
        if not rel:
            continue
        key = (frozenset((s, t)), rel)
        # 保留出现次数，用于决定显示哪个方向
        if key not in seen:
            seen[key] = {"a": s, "b": t, "rel": rel, "n": 0, "raw": e["relation"]}
        seen[key]["n"] += 1

    print(f"原始边(主要人物间): {sum(1 for e in g['edges'] if e['source'] in ids and e['target'] in ids)}")
    print(f"去重后: {len(seen)}")

    # ---- 3) 合并「同一对节点」的多种关系 ----
    # 一对人物可能有多种关系（如既是表亲又是朋友），合并成一个边，关系用 / 连接
    pairs = collections.defaultdict(list)
    for (pair, rel), info in seen.items():
        a, b = sorted(pair)
        pairs[(a, b)].append((rel, info["n"]))

    edges = []
    for (a, b), rels in pairs.items():
        rels.sort(key=lambda x: -x[1])
        # 子女关系只显示一条（「父子 / 母子」这种并列在图上会读成两种关系）
        CHILD = {"父子", "母子", "母女", "父子"}
        top = [r for r, _ in rels]
        if len(set(top) & CHILD) > 1:
            keep = next(r for r in top if r in CHILD)
            top = [keep] + [r for r in top if r not in CHILD]
        label = " / ".join(top[:2])
        edges.append({
            "source": a,
            "target": b,
            "relation": label,
            "all_relations": [r for r, _ in rels],
            "weight": sum(n for _, n in rels),
        })
    print(f"合并后关系边: {len(edges)}")

    # ---- 4) 确保图连通：丢掉孤立点（无任何边的人物）----
    linked = set()
    for e in edges:
        linked.add(e["source"]); linked.add(e["target"])
    dropped = [n["id"] for n in nodes if n["id"] not in linked]
    if dropped:
        print(f"  孤立点（无关系，将剔除）: {dropped}")
    nodes = [n for n in nodes if n["id"] in linked]

    # ---- 5) 输出 ----
    data = {
        "nodes": nodes,
        "edges": edges,
        "n_node": len(nodes),
        "n_edge": len(edges),
        "note": ("主要人物关系图。关系已双向去重并归一化同义词"
                 "（友/朋友→朋友，丈夫/妻→夫妻等）；一对人物有多个关系时最多显示两种。"),
        "source": "web/data/graph.json（四源合并）",
        "generated_by": "s37_1_build_main_relations.py",
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"\n已写 {OUT} ({os.path.getsize(OUT):,} B)")

    # ---- 6) 预览 ----
    print(f"\n=== 节点 {len(nodes)} ===")
    for n in nodes:
        cnt = sum(1 for e in edges if n["id"] in (e["source"], e["target"]))
        print(f"  {n['name']:<6} {n['family']}家  关系 {cnt} 条  {n['avatar']}")
    print(f"\n=== 关系 {len(edges)} 条 ===")
    for e in sorted(edges, key=lambda x: -x["weight"])[:20]:
        print(f"  {e['source']:<6} —{e['relation']:<10}— {e['target']:<6} (w={e['weight']})")


if __name__ == "__main__":
    main()
