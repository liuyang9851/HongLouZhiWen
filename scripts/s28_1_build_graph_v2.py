# -*- coding: utf-8 -*-
"""
S28-1 · 人物关系图扩充（4 源合并 + 简称别名修复）
==================================================

背景
----
用户反馈：「人物关系图中不太完善，部分人物没有出现」。

实测诊断（按正文出现次数）：
  ❌ 平儿 511 次 → 不在图谱
  ❌ 湘云 368 次 → 不在图谱（图谱里叫「史湘云」）
  ❌ 探春 326 次 → 不在图谱（图谱里叫「贾探春」）
  ❌ 麝月 154 次 → 不在图谱
  ❌ 紫鹃 138 次 → 不在图谱
  ❌ 迎春 129 次、茗烟 114、莺儿 109、惜春 79、兴儿 77 …

两个根因
--------
  ① 数据源只用了 2 个（kg-hongloumeng 380 行 + KGQA 188 行），
     真正丰富的第 3、4 个源没用上：
       · The-Dream-of-the-Red-Chamber__data_stone_story_nodes.csv  389 个人物
       · DotRC__stone_story_edges.csv                              495 条边
  ② **图谱用全名，正文用简称**
     「贾探春」在图谱里，但正文写「探春」→ 提及统计为 0 → 节点权重低 → 被挤掉

修复
----
  1. 合并 4 个数据源，去重
  2. 建立「全名 → 简称」别名映射，统计提及次数时两者都算
  3. 保留节点上限从 120 提到 **350**（Canvas 渲染 389 个节点仍流畅）
  4. 每个节点记录 aliases，前端可显示

运行：
  python s28_1_build_graph_v2.py
"""
import os
import re
import sys
import csv
import json
import collections
from datetime import datetime, timezone, timedelta

sys.stdout.reconfigure(encoding="utf-8")

ROOT = r"D:\学校事务\大数据原理与应用\红楼智问"
WORK = r"D:\学校事务\大数据原理与应用"
SRC = os.path.join(WORK, "红学数据源")
WEB_DATA = os.path.join(ROOT, "web", "data")
CORPUS = r"D:\Programming\Red\hlm_corpus\fuqinju"

MAX_NODES = 350          # 保留节点上限（原 120）


def log(m=""):
    print(m, flush=True)


# ============================================================
# 1. 别名映射：全名 → 简称（关键修复）
# ============================================================
# 为什么需要：图谱数据用「贾探春」，正文全写「探春」。
# 不建映射的话，提及统计=0，节点会被挤掉。
ALIAS_GROUPS = {
    # 贾家四春
    "贾探春": ["探春", "三姑娘"],
    "贾迎春": ["迎春", "二姑娘"],
    "贾惜春": ["惜春", "四姑娘"],
    "贾元春": ["元春", "元妃", "贤德妃", "贾妃", "贵妃"],
    # 核心主角
    "贾宝玉": ["宝玉", "宝二爷", "怡红公子"],
    "林黛玉": ["黛玉", "林妹妹", "颦儿", "潇湘妃子"],
    "薛宝钗": ["宝钗", "宝姐姐", "蘅芜君"],
    "王熙凤": ["凤姐", "凤姐儿", "琏二奶奶"],
    "贾母": ["老太太", "史太君", "老祖宗", "贾母史太君"],
    # 重要配角
    "史湘云": ["湘云", "云儿", "枕霞旧友"],
    "李纨": ["大奶奶", "宫裁", "稻香老农"],
    "秦可卿": ["可卿", "蓉大奶奶"],
    "妙玉": ["槛外人"],
    "巧姐": ["巧哥儿", "大姐儿"],
    # 丫鬟
    "平儿": ["平儿姐姐"],
    "袭人": ["花袭人", "珍珠", "袭人姐姐"],
    "晴雯": [],
    "麝月": [],
    "紫鹃": ["鹦哥", "紫娟"],
    "莺儿": ["黄金莺"],
    "鸳鸯": ["鸳鸯姐姐"],
    "金钏": ["白金钏", "金钏儿"],
    "玉钏": ["白玉钏", "玉钏儿"],
    "秋纹": [],
    "司棋": [],
    "侍书": ["待书"],
    "入画": [],
    "香菱": ["甄英莲", "英莲", "秋菱"],
    "林红玉": ["小红", "红玉"],
    "龄官": [],
    "芳官": [],
    "藕官": [],
    "四儿": ["蕙香"],
    "坠儿": [],
    "茜雪": [],
    "春燕": ["何春燕"],
    # 男性
    "贾琏": ["琏二爷", "琏二哥哥"],
    "贾珍": [],
    "贾蓉": [],
    "贾政": ["政老爷", "政老"],
    "贾赦": ["大老爷"],
    "贾环": ["环儿", "环三爷"],
    "贾兰": [],
    "贾瑞": [],
    "贾芸": [],
    "贾蔷": [],
    "贾雨村": ["雨村", "贾化"],
    "甄士隐": ["士隐"],
    "茗烟": ["焙茗"],
    "焦大": [],
    "赖大": [],
    "柳湘莲": ["冷二郎"],
    "蒋玉菡": ["琪官"],
    "薛蟠": ["呆霸王", "薛文龙"],
    "薛蝌": [],
    "冯紫英": [],
    "秦钟": [],
    "刘姥姥": ["刘老老"],
}

# 反向索引：任意称呼 → 规范名（★ 用于合并重复节点）
# 实测问题：源数据里「花袭人」「珍珠」和「袭人」是三个独立节点，
# 但它们其实是同一人 → 必须在建边前归一化，否则图谱里一人占三格。
ALIAS2CANON = {}
for _full, _shorts in ALIAS_GROUPS.items():
    ALIAS2CANON[_full] = _full
    for _s2 in _shorts:
        ALIAS2CANON[_s2] = _full


def canonical(name: str) -> str:
    """把任意称呼映射到规范名（无法匹配的保持原样）"""
    if not name:
        return name
    if name in ALIAS2CANON:
        return ALIAS2CANON[name]
    # 去掉「家的」「之妻」等后缀再试
    for suf in ("家的", "之妻", "之母", "之父"):
        if name.endswith(suf) and name[:-len(suf)] in ALIAS2CANON:
            return name
    return name


# 反向索引：简称 → 全名
SHORT2FULL = {}
for full, shorts in ALIAS_GROUPS.items():
    for s in shorts:
        SHORT2FULL[s] = full


def variants(name):
    """返回某人物的所有称呼（含自己）"""
    out = {name}
    for s in ALIAS_GROUPS.get(name, []):
        out.add(s)
    # 若 name 本身是简称，补上全名
    if name in SHORT2FULL:
        full = SHORT2FULL[name]
        out.add(full)
        out.update(ALIAS_GROUPS.get(full, []))
    return out


# ============================================================
# 2. 家族归属 & 身份（人工整理，用于着色和详情）
# ============================================================
FAMILY = {
    # 贾家
    "贾宝玉": "贾", "贾琏": "贾", "贾珍": "贾", "贾蓉": "贾", "贾政": "贾",
    "贾赦": "贾", "贾环": "贾", "贾兰": "贾", "贾琮": "贾", "贾瑞": "贾",
    "贾探春": "贾", "贾迎春": "贾", "贾惜春": "贾", "贾元春": "贾",
    "李纨": "贾", "王夫人": "贾", "邢夫人": "贾", "赵姨娘": "贾",
    "尤氏": "贾", "秦可卿": "贾", "巧姐": "贾", "贾母": "史",
    "袭人": "贾", "晴雯": "贾", "麝月": "贾", "秋纹": "贾", "茜雪": "贾",
    "鸳鸯": "贾", "金钏": "贾", "玉钏": "贾", "司棋": "贾", "侍书": "贾",
    "入画": "贾", "芳官": "贾", "藕官": "贾", "四儿": "贾", "坠儿": "贾",
    "春燕": "贾", "林之孝": "贾", "赖大": "贾", "焦大": "贾", "茗烟": "贾",
    "贾芸": "贾", "贾蔷": "贾", "龄官": "贾", "彩云": "贾", "彩霞": "贾",
    # 史家
    "史湘云": "史", "史鼐": "史", "史鼎": "史",
    # 王家
    "王熙凤": "王", "王子腾": "王", "王仁": "王", "王夫人_": "王",
    "平儿": "王", "丰儿": "王", "旺儿": "王", "兴儿": "王",
    # 薛家
    "薛宝钗": "薛", "薛蟠": "薛", "薛姨妈": "薛", "薛蝌": "薛",
    "薛宝琴": "薛", "莺儿": "薛", "文杏": "薛", "香菱": "薛", "宝蟾": "薛",
    # 林家
    "林黛玉": "林", "林如海": "林", "紫鹃": "林", "雪雁": "林", "王嬷嬷": "林",
    # 其他
    "妙玉": "其他", "刘姥姥": "其他", "板儿": "其他", "贾雨村": "其他",
    "甄士隐": "其他", "柳湘莲": "其他", "蒋玉菡": "其他", "秦钟": "其他",
    "尤二姐": "其他", "尤三姐": "其他", "智能儿": "其他", "冯紫英": "其他",
    "花自芳": "其他",
}

ROLE = {
    "贾宝玉": "荣国府嫡孙，神瑛侍者转世",
    "林黛玉": "姑苏林家女，绛珠仙草转世",
    "薛宝钗": "金陵薛家女，蘅芜君",
    "王熙凤": "荣国府管家奶奶，琏二奶奶",
    "贾母": "荣国府老祖宗，史太君",
    "贾探春": "贾政与赵姨娘之女，三姑娘",
    "贾迎春": "贾赦之女，二姑娘",
    "贾惜春": "贾敬之女，四姑娘",
    "贾元春": "贾政长女，贤德妃",
    "史湘云": "保龄侯史家女，枕霞旧友",
    "李纨": "贾珠之妻，稻香老农",
    "秦可卿": "贾蓉之妻，蓉大奶奶",
    "妙玉": "大观园栊翠庵尼姑",
    "平儿": "王熙凤陪房丫头，贾琏之妾",
    "袭人": "宝玉大丫鬟，本名珍珠",
    "晴雯": "宝玉大丫鬟",
    "麝月": "宝玉大丫鬟",
    "紫鹃": "黛玉大丫鬟，原名鹦哥",
    "莺儿": "宝钗丫鬟，本名黄金莺",
    "鸳鸯": "贾母大丫鬟",
    "香菱": "甄士隐之女，薛蟠之妾",
    "刘姥姥": "乡下老妪，三进荣国府",
    "贾雨村": "应天府尹，黛玉蒙师",
    "甄士隐": "姑苏乡宦，香菱之父",
}


# ============================================================
# 2b. 家族归属规则补全（解决「贾家人被归到其他」的问题）
# ============================================================
# 实测问题：贾代善、贾珠、贾敏、贾演、贾敬、贾代化、贾源、贾化（贾雨村）
# 等贾家成员全被归到「其他」→ 家族筛选一勾「贾」就看不到他们。
# 修法：FAMILY 表里没有的，按姓氏/关系规则推断。
FAMILY_RULES = [
    # 贾家：姓贾，或明确是贾家媳妇/女儿
    (lambda n: n.startswith("贾") and len(n) >= 2, "贾"),
    (lambda n: n in {
        "王氏", "邢氏", "尤氏", "胡氏", "娄氏", "秦氏",
        "贾母", "史太君", "王夫人", "邢夫人", "赵姨娘", "周姨娘",
        "李纨", "秦可卿", "尤二姐", "尤三姐", "夏金桂",
        "平儿_x", "佩凤", "偕鸾", "文花", "娇红",
    }, "贾"),
    # 史家
    (lambda n: n.startswith("史") and len(n) >= 2, "史"),
    (lambda n: n in {"湘云", "翠墨_x"}, "史"),
    # 王家
    (lambda n: n.startswith("王") and n not in {"王嬷嬷", "王善保", "王善保家的"}, "王"),
    (lambda n: n in {"凤姐之父", "凤姐之祖"}, "王"),
    # 薛家
    (lambda n: n.startswith("薛") and len(n) >= 2, "薛"),
    (lambda n: n in {"香菱", "甄英莲", "宝蟾", "夏金桂"}, "薛"),
    # 林家
    (lambda n: n.startswith("林") and len(n) >= 2 and n not in {"林之孝", "林之孝家的"}, "林"),
]


def infer_family(name: str) -> str:
    """推断家族归属：先查人工表，再按规则"""
    if name in FAMILY:
        return FAMILY[name]
    for pred, fam in FAMILY_RULES:
        try:
            if pred(name):
                return fam
        except Exception:
            pass
    return "其他"


# ============================================================
# 3. 读取数据源
# ============================================================
# 关系英文 → 中文（模块级，供所有源共用）
REL_ZH_GLOBAL = {
    "servant_girl_of": "丫鬟", "son_of": "子", "husband_of": "丈夫",
    "friend_of": "朋友", "wife_of": "妻", "daughter_of": "女",
    "servant_of": "奴仆", "concubine_of": "妾", "mother_of": "母亲",
    "grandson_of": "孙子", "nurser_of": "乳母", "father_of": "父亲",
    "pupil_of": "学生", "master_of": "主子", "sister_of": "姐妹",
    "brother_of": "兄弟", "donor_of": "施主",
    "boy_friend_of": "好友", "girl_friend_of": "女友",
    "daughter_in_law_of": "儿媳",
    "daughter_in_law_of_grandson_of": "重孙媳妇",
    "granddaughter_of": "孙女", "father_in_law_of": "岳父",
    "mother_in_law_of": "岳母",
    "male_cousin_of": "表兄弟", "imperial_concubine_of": "妃嫔",
    "minister_of": "臣属", "sister_in_law_of": "姑嫂",
    "mothers_brother_of": "舅父", "nephew_of": "侄",
    "adopted_mother_of": "养母", "step_wife_of": "继室",
    "brother_in_law_of": "姻亲兄弟", "old_relatives_of": "远亲",
    "grandfather_of": "祖父", "son_in_law_of": "女婿",
    "adopted_son_of": "养子", "maternal_aunt_of": "姨母",
    "grandmother_of": "祖母", "kings_concubine_of": "王妃",
    "meme_ama_of": "嬷嬷", "neighbour_of": "邻居",
    "partner_of": "伴侣", "employer_of": "雇主",
    "great_great_grandson_of": "玄孙",
}


def zh_rel(rel: str) -> str:
    """英文关系 → 中文；已是中文或未收录的保持原样"""
    return REL_ZH_GLOBAL.get(rel, rel)


def load_sources():
    """返回 (triples, src_names)  triples = [(head, tail, relation_zh, src)]"""
    triples = []
    used = []

    # 源1：kg-hongloumeng（中文名，最规范）
    p = os.path.join(SRC, "kg-hongloumeng__hlm_triples.csv")
    if os.path.exists(p):
        n = 0
        for row in csv.DictReader(open(p, encoding="utf-8-sig")):
            h = (row.get("head") or row.get('\ufeff"head"') or "").strip()
            t = (row.get("tail") or "").strip()
            rel = (row.get("label") or row.get("relation") or "").strip()
            if h and t and h != t:
                triples.append((h, t, zh_rel(rel), "kg-hongloumeng"))
                n += 1
        used.append(("kg-hongloumeng__hlm_triples.csv", n))
        log(f"  源1 kg-hongloumeng: {n} 条")

    # 源2：KGQA（含家族列）
    p = os.path.join(SRC, "KGQAHLM__relation.txt")
    if os.path.exists(p):
        n = 0
        for line in open(p, encoding="utf-8", errors="replace"):
            parts = line.strip().split(",")
            if len(parts) >= 3 and parts[0] and parts[1]:
                triples.append((parts[0], parts[1], zh_rel(parts[2]), "KGQA_HLM"))
                n += 1
        used.append(("KGQAHLM__relation.txt", n))
        log(f"  源2 KGQA_HLM: {n} 条")

    # 源3：The-Dream-of-the-Red-Chamber data_triples（400 条中文名）
    p = os.path.join(SRC, "The-Dream-of-the-Red-Chamber__data_triples.csv")
    if os.path.exists(p):
        n = 0
        rows = list(csv.DictReader(open(p, encoding="utf-8-sig")))
        if rows:
            keys = list(rows[0].keys())
            hk = next((k for k in keys if "head" in k.lower()), keys[0])
            for row in rows:
                h = (row.get(hk) or "").strip()
                t = (row.get("tail") or "").strip()
                rel = (row.get("label") or row.get("relation") or "").strip()
                if h and t and h != t:
                    triples.append((h, t, zh_rel(rel), "DotRC-triples"))
                    n += 1
        used.append(("The-Dream-of-the-Red-Chamber__data_triples.csv", n))
        log(f"  源3 DotRC-triples: {n} 条")

    # 源4：DotRC edges（id 引用，需 nodes 表映射）★ 最大来源
    p_nodes = os.path.join(SRC, "The-Dream-of-the-Red-Chamber__data_stone_story_nodes.csv")
    p_edges = os.path.join(SRC, "DotRC__stone_story_edges.csv")
    if os.path.exists(p_nodes) and os.path.exists(p_edges):
        id2name = {}
        for r in csv.DictReader(open(p_nodes, encoding="utf-8-sig")):
            id2name[r["id"]] = r["name"]
        log(f"  源4a 节点表: {len(id2name)} 个人物")

        # 关系英文 → 中文（完整版，实测 21 种英文残留）
        REL_ZH = {
            "male_cousin_of": "表兄弟", "imperial_concubine_of": "妃嫔",
            "minister_of": "臣属", "sister_in_law_of": "姑嫂",
            "mothers_brother_of": "舅父", "nephew_of": "侄",
            "adopted_mother_of": "养母", "step_wife_of": "继室",
            "brother_in_law_of": "姻亲兄弟", "old_relatives_of": "远亲",
            "grandfather_of": "祖父", "son_in_law_of": "女婿",
            "adopted_son_of": "养子", "maternal_aunt_of": "姨母",
            "grandmother_of": "祖母", "kings_concubine_of": "王妃",
            "meme_ama_of": "嬷嬷", "neighbour_of": "邻居",
            "partner_of": "伴侣", "employer_of": "雇主",
    "great_great_grandson_of": "玄孙",
            "granddaughter_of": "孙女", "grandson_of": "孙子",
            "servant_girl_of": "丫鬟", "son_of": "子", "husband_of": "丈夫",
            "friend_of": "朋友", "wife_of": "妻", "daughter_of": "女",
            "servant_of": "奴仆", "concubine_of": "妾", "mother_of": "母亲",
            "grandson_of": "孙子", "nurser_of": "乳母", "father_of": "父亲",
            "pupil_of": "学生", "master_of": "主子", "sister_of": "姐妹",
            "brother_of": "兄弟", "donor_of": "施主",
            "boy_friend_of": "好友", "girl_friend_of": "女友",
            "daughter_in_law_of": "儿媳",
            "daughter_in_law_of_grandson_of": "重孙媳妇",
            "son_of_": "子", "granddaughter_of": "孙女",
            "father_in_law_of": "岳父", "mother_in_law_of": "岳母",
        }
        n = 0
        for e in csv.DictReader(open(p_edges, encoding="utf-8-sig")):
            h = id2name.get(e["head"])
            t = id2name.get(e["tail"])
            if not h or not t or h == t:
                continue
            rel = REL_ZH.get(e["label"], e["label"])
            triples.append((h, t, rel, "DotRC-edges"))
            n += 1
        used.append(("DotRC__stone_story_edges.csv", n))
        log(f"  源4b DotRC-edges: {n} 条")

    return triples, used


# ============================================================
# 4. 统计正文提及（含别名）
# ============================================================
def load_corpus():
    out = {}
    for h in range(1, 81):
        for fn in (f"{h:03d}.txt", f"{h:03d}_合并.txt"):
            p = os.path.join(CORPUS, fn)
            if os.path.exists(p):
                out[h] = open(p, encoding="utf-8").read()
                break
    return out


def main():
    log("=" * 72)
    log("S28-1 人物关系图扩充")
    log("=" * 72)

    log("\n[1/5] 读取数据源")
    triples, used = load_sources()
    log(f"  合计 {len(triples):,} 条三元组")

    log("\n[2/5] 载入正文（用于提及统计）")
    corpus = load_corpus()
    log(f"  {len(corpus)} 回")

    log("\n[3/5] 构建节点与边")
    seen = set()
    edges = []
    names = set()
    n_merged = 0
    for h, t, rel, src in triples:
        # ★ 归一化：别名 → 规范名（合并重复人物）
        h2, t2 = canonical(h), canonical(t)
        if h2 != h or t2 != t:
            n_merged += 1
        if not h2 or not t2 or h2 == t2:      # 归一化后可能自环
            continue
        key = (h2, t2, rel)
        if key in seen:
            continue
        seen.add(key)
        edges.append({"source": h2, "target": t2, "relation": rel,
                      "family": "", "src": src})
        names.add(h2)
        names.add(t2)
    log(f"  别名归一化：{n_merged:,} 条三元组被改写（合并重复人物）")

    log(f"  去重后 {len(edges):,} 条边 / {len(names):,} 个人物")

    log("\n[4/5] 计算提及频次（含别名）")
    mention = {}
    for n in names:
        vs = variants(n)
        cnt = 0
        for txt in corpus.values():
            for v in vs:
                cnt += txt.count(v)
        mention[n] = cnt

    # 抽查关键人物
    log("  关键人物提及频次：")
    for k in ("平儿", "贾探春", "史湘云", "麝月", "紫鹃", "贾迎春",
              "茗烟", "莺儿", "贾惜春", "贾宝玉", "林黛玉"):
        if k in mention:
            vs = variants(k)
            log(f"    {k:6s} {mention[k]:>6,} 次  含别名 {sorted(vs)}")

    log("\n[5/5] 生成节点")
    deg = collections.Counter()
    for e in edges:
        deg[e["source"]] += 1
        deg[e["target"]] += 1

    nodes = []
    for n in names:
        fam = infer_family(n)
        nodes.append({
            "id": n, "name": n, "family": fam,
            "role": ROLE.get(n, ""),
            "degree": deg[n],
            "mention": mention.get(n, 0),
            "in_alias_table": n in ALIAS_GROUPS or n in SHORT2FULL,
            "aliases": [x for x in sorted(variants(n)) if x != n][:4],
        })

    # 排序：度数优先，其次提及频次
    nodes.sort(key=lambda x: (-x["degree"], -x["mention"]))
    keep = {n["id"] for n in nodes[:MAX_NODES]}
    gnodes = [n for n in nodes if n["id"] in keep]
    gedges = [e for e in edges if e["source"] in keep and e["target"] in keep]

    # 家族统计
    fam_cnt = collections.Counter(n["family"] for n in gnodes)

    # ★ 唯一性断言：同名/同 id 节点会让前端画出「同一个人两个点」
    _ids = [n["id"] for n in gnodes]
    _names = [n["name"] for n in gnodes]
    assert len(_ids) == len(set(_ids)), f"存在重复 id: {[x for x in _ids if _ids.count(x) > 1][:5]}"
    assert len(_names) == len(set(_names)), f"存在重复 name: {[x for x in _names if _names.count(x) > 1][:5]}"
    log(f"  唯一性校验: {len(_ids)} 个 id / {len(_names)} 个 name 均唯一 ✅")

    log(f"  保留 {len(gnodes)} 个节点 / {len(gedges)} 条边（上限 {MAX_NODES}）")
    log(f"  家族分布: {dict(fam_cnt)}")

    # 检查关键人物是否都在
    log("\n  关键人物覆盖检查：")
    missing = []
    for k in ("贾宝玉", "林黛玉", "薛宝钗", "王熙凤", "贾母", "平儿", "贾探春",
              "史湘云", "麝月", "紫鹃", "贾迎春", "茗烟", "莺儿", "贾惜春",
              "鸳鸯", "袭人", "晴雯", "李纨", "妙玉", "刘姥姥", "贾雨村"):
        ok = k in keep
        if not ok:
            missing.append(k)
        log(f"    {'✅' if ok else '❌'} {k}")
    if missing:
        log(f"  ⚠️ 仍缺失: {missing}")

    # 输出
    cst = timezone(timedelta(hours=8))
    now = datetime.now(cst)
    out = {
        "n_nodes": len(gnodes),
        "n_nodes_total": len(nodes),
        "n_edges": len(gedges),
        "n_edges_total": len(edges),
        "relations": sorted({e["relation"] for e in gedges}),
        "families": ["贾", "史", "王", "薛", "林", "其他"],
        "family_counts": dict(fam_cnt),
        "nodes": gnodes,
        "edges": gedges,
        "sources": [u[0] for u in used],
        "source_counts": dict(used),
        "generated_at": now.strftime("%Y-%m-%d %H:%M"),
        "note": (f"四源合并，节点上限 {MAX_NODES}。"
                 f"提及频次已含别名（如「贾探春」计入「探春」）。"),
    }

    path = os.path.join(WEB_DATA, "graph.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    log(f"\n已写 {path}  ({os.path.getsize(path):,} B)")
    log(f"  {len(gnodes)} 节点 / {len(gedges)} 边"
        f"（原 120 节点 / 253 边）")


if __name__ == "__main__":
    main()
