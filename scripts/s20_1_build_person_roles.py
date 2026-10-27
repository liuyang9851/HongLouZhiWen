# -*- coding: utf-8 -*-
"""
S20-1 · 生成「人物-身份」聚合表（供名单型问题使用）
==================================================

问题背景（S19 诊断）
--------------------
问「贾宝玉的丫鬟有谁」，系统只答出袭人一人。根因：
  · 《红楼梦》不集中交代名单，17 个丫鬟分散在 56 回
  · Top-K 检索只能取 5 段，装不下分散信息

解决思路（方案 A）
------------------
离线预建「人物-身份」聚合表，问答时直接查表 + 回原文取证。

数据来源（三路合并）
  · 路1 人物关系图谱：`web/data/graph.json` 的 edges
        如 `晴雯 --[丫鬟]--> 贾宝玉`
  · 路2 词频共现：`hlm_stats` 的 `cooccur|<A>|<B>`
        与目标人物高频共现的人名
  · 路3 原文规则匹配：在正文里搜「X的丫鬟」「X之婢」「X丫头」等模式

输出
----
  web/data/person_roles.json
  {
    "贾宝玉": {
      "丫鬟": [
        {"name": "袭人", "evidence": "第3回", "src": "text+graph", "conf": "high"},
        ...
      ],
      "乳母": [...],
      "奴仆": [...]
    },
    ...
  }

运行：
  python s20_1_build_person_roles.py
"""
import os
import re
import sys
import json
import collections

sys.stdout.reconfigure(encoding="utf-8")

ROOT = r"D:\学校事务\大数据原理与应用\红楼智问"
WEB_DATA = os.path.join(ROOT, "web", "data")
# [S41 已注释] CORPUS = r"D:\Programming\Red\hlm_corpus\fuqinju"
# ---- S41：路径可移植化 ----
# 原始路径是作者本机路径。为让其他人能重跑，改为环境变量优先。
#   HLM_WORK   项目上级目录（含「红楼智问」）
#   HLM_CORPUS 语料根目录
import os as _os

# 允许通过环境变量覆盖；未设置则用原作者默认值（保持我本机可直接跑）
WORK = _os.environ.get("HLM_WORK", r"D:\学校事务\大数据原理与应用")
CORPUS = _os.environ.get("HLM_CORPUS", r"D:\Programming\Red\hlm_corpus")
# ---- 路径可移植化结束 ----


# 身份类关系关键词（图谱里的 relation 值）
ROLE_KEYS = {
    "丫鬟": ["丫鬟", "丫环", "丫头", "大丫头", "小丫头", "婢", "侍儿"],
    "乳母": ["乳母", "奶母", "奶娘", "嬷嬷"],
    "奴仆": ["奴仆", "小厮", "仆人", "家人"],
}


def log(m):
    print(m, flush=True)


def classify_role(rel):
    """把图谱的 relation 归类到身份类别"""
    for cat, kws in ROLE_KEYS.items():
        for kw in kws:
            if kw in rel:
                return cat
    return None


def load_graph_roles():
    """路1：从图谱边提取「某人 --[丫鬟]--> 主子」"""
    p = os.path.join(WEB_DATA, "graph.json")
    if not os.path.exists(p):
        return {}
    d = json.load(open(p, encoding='utf-8'))
    out = collections.defaultdict(lambda: collections.defaultdict(list))
    for e in d.get("edges", []):
        cat = classify_role(e.get("relation") or "")
        if not cat:
            continue
        # 方向：source 是身份方，target 是主子
        master = e["target"]
        person = e["source"]
        out[master][cat].append({"name": person, "src": "graph",
                                 "relation": e["relation"]})
    return out


def load_corpus():
    """
    载入抚琴居本 1-80 回正文。

    注意：第 67 回的文件名是 `067_合并.txt`（原书分 067a/067b 两半合并），
    不是 `067.txt`。硬编码 `{h:03d}.txt` 会漏掉这一回（实测只载入 79 回）。
    """
    out = {}
    for h in range(1, 81):
        for fname in (f"{h:03d}.txt", f"{h:03d}_合并.txt"):
            p = os.path.join(CORPUS, fname)
            if os.path.exists(p):
                out[h] = open(p, encoding='utf-8').read()
                break
    return out


# 原文规则：找「X的丫鬟Y」「X之婢Y」「陪侍X的…丫鬟Y」
PATTERNS = [
    # 「宝玉之乳母李嬷嬷，并大丫鬟名唤袭人者」
    (r'([\u4e00-\u9fff]{2,4})之(?:乳母|奶母)([\u4e00-\u9fff]{2,4})', '乳母'),
    (r'([\u4e00-\u9fff]{2,4})之婢', '丫鬟'),
    # 「宝玉的丫鬟袭人」
    (r'([\u4e00-\u9fff]{2,4})的(?:丫鬟|丫环|丫头)(?:名唤)?([\u4e00-\u9fff]{2,4})', '丫鬟'),
    # 「袭人者，宝玉之婢也」类
    (r'([\u4e00-\u9fff]{2,4})(?:者)?[，,]([\u4e00-\u9fff]{2,4})之(?:婢|丫鬟)', '丫鬟_rev'),
]

# 常见丫鬟名单（用于第3路：验证某人是否为主子的丫鬟）
KNOWN_MAIDS = [
    # 宝玉房
    "袭人", "晴雯", "麝月", "秋纹", "碧痕", "茜雪", "四儿", "芳官",
    "檀云", "绮霰", "紫绡", "媚人", "可人", "良儿", "坠儿",
    "柳五儿", "篆儿", "靛儿", "春燕", "何春燕",
    # 黛玉房（紫鹃是最重要的，原名单漏了）
    "紫鹃", "雪雁", "藕官", "鹦哥",
    # 凤姐房
    "平儿", "丰儿", "小红", "林红玉",
    # 贾母房
    "鸳鸯",
    # 王夫人房
    "白金钏", "白玉钏", "彩云", "彩霞", "玉钏",
    # 薛家
    "莺儿", "文杏",
    # 其他
    "秋桐", "傻大姐", "翠墨", "待书", "司棋", "入画", "抱琴",
    "善姐", "宝蟾", "香菱",
]

# ============================================================
# 人工确认的「丫鬟 → 主子」归属（红学常识，可靠度最高）
# ============================================================
# 为什么需要这个表：
#   裸共现无法区分「服务对象」与「同场景」。
#   实测：紫鹃与宝玉共现 19 次 > 与黛玉 17 次（宝玉黛玉常同场景），
#        但紫鹃明明是黛玉的丫鬟。必须用先验知识锚定。
MANUAL_OWNERSHIP = {
    # 宝玉房
    "袭人": "贾宝玉", "晴雯": "贾宝玉", "麝月": "贾宝玉", "秋纹": "贾宝玉",
    "碧痕": "贾宝玉", "茜雪": "贾宝玉", "四儿": "贾宝玉", "芳官": "贾宝玉",
    "檀云": "贾宝玉", "绮霰": "贾宝玉", "紫绡": "贾宝玉", "媚人": "贾宝玉",
    "可人": "贾宝玉", "良儿": "贾宝玉", "坠儿": "贾宝玉",
    "柳五儿": "贾宝玉", "篆儿": "贾宝玉", "靛儿": "贾宝玉",
    "春燕": "贾宝玉", "何春燕": "贾宝玉",
    "叶茗烟": "贾宝玉",
    # 黛玉房
    "紫鹃": "林黛玉", "雪雁": "林黛玉", "藕官": "林黛玉", "鹦哥": "林黛玉",
    # 凤姐房
    "平儿": "王熙凤", "丰儿": "王熙凤", "小红": "王熙凤", "林红玉": "王熙凤",
    # 贾母房
    "鸳鸯": "贾母", "傻大姐": "贾母",
    # 王夫人房
    "白金钏": "王夫人", "白玉钏": "王夫人", "玉钏": "王夫人",
    "彩云": "王夫人", "彩霞": "王夫人",
    # 薛家
    "莺儿": "薛宝钗", "文杏": "薛宝钗",
    "香菱": "薛宝钗", "宝蟾": "薛宝钗",
    # 其他
    "秋桐": "贾琏", "善姐": "贾琏",
    "翠墨": "贾探春", "待书": "贾探春",
    "司棋": "贾迎春", "入画": "贾惜春", "抱琴": "贾元春",
}

# 主子名单 + 别名（正文多用简称，必须用别名才匹配得上）
# 实测全书出现次数：「贾宝玉」2 次 vs「宝玉」2,626 次
MASTER_ALIASES = {
    "贾宝玉": ["宝玉", "宝二爷", "怡红公子"],
    "林黛玉": ["黛玉", "林妹妹", "颦儿", "潇湘妃子"],
    "薛宝钗": ["宝钗", "宝姐姐", "蘅芜君"],
    "王熙凤": ["凤姐", "凤姐儿", "琏二奶奶"],
    "贾母":   ["贾母", "老太太", "史太君", "老祖宗"],
    "王夫人": ["王夫人", "太太"],
    "贾迎春": ["迎春", "二姑娘"],
    "贾探春": ["探春", "三姑娘"],
    "贾惜春": ["惜春", "四姑娘"],
    "史湘云": ["湘云", "云儿", "枕霞旧友"],
    "贾环":   ["贾环", "环儿"],
    "贾琏":   ["贾琏", "琏二爷"],
    "贾赦":   ["贾赦", "大老爷"],
    "贾政":   ["贾政", "政老爷", "政老"],
    "李纨":   ["李纨", "大奶奶"],
    "妙玉":   ["妙玉"],
    "薛姨妈": ["薛姨妈", "姨妈"],
    "赵姨娘": ["赵姨娘"],
}
MASTERS = list(MASTER_ALIASES.keys())


def main():
    log("=" * 74)
    log("S20-1 生成人物-身份聚合表")
    log("=" * 74)

    # ---- 路1：图谱 ----
    graph_roles = load_graph_roles()
    log(f"\n路1 图谱: {len(graph_roles)} 个主子有身份关系")
    for master, cats in list(graph_roles.items())[:5]:
        for cat, lst in cats.items():
            log(f"  {master} 的{cat}: {[x['name'] for x in lst]}")

    # ---- 路2 + 路3：原文 ----
    corpus = load_corpus()
    log(f"\n路3 原文: 载入 {len(corpus)} 回")

    text_roles = collections.defaultdict(lambda: collections.defaultdict(list))

    # 规则匹配
    for hui, txt in corpus.items():
        for pat, kind in PATTERNS:
            for m in re.finditer(pat, txt):
                g = m.groups()
                if kind == '丫鬟_rev':
                    person, master = g[0], g[1]
                else:
                    master, person = g[0], g[1] if len(g) > 1 else ('', '')
                if not person or not master:
                    continue
                if master not in MASTERS:
                    continue
                if person not in KNOWN_MAIDS:
                    continue
                real_kind = '乳母' if kind == '乳母' else '丫鬟'
                item = {"name": person, "src": "text", "hui": hui,
                        "snippet": txt[max(0, m.start() - 20):m.start() + 60].replace('\n', '')}
                if not any(x['name'] == person for x in text_roles[master][real_kind]):
                    text_roles[master][real_kind].append(item)

    log(f"\n  规则匹配合计:")
    for master, cats in text_roles.items():
        for cat, lst in cats.items():
            log(f"    {master} 的{cat}: {[x['name'] for x in lst]}")

    # ---- 路3b：句内共现（比同回共现信号强得多）----
    # 实测：宝玉 × 袭人 句内共现 148 次、晴雯 54、麝月 37 —— 与红楼事实吻合
    # 而「同回共现」会引入大量噪音（宝玉与黛玉同回 79 次，但丫鬟归属无关）
    cooccur = collections.defaultdict(collections.Counter)
    first_hui = collections.defaultdict(dict)      # master -> name -> 首次共现回
    for hui, txt in corpus.items():
        for sent in re.split(r'[。！？\n；]', txt):
            if len(sent) < 4:
                continue
            present = [m for m in KNOWN_MAIDS if m in sent]
            if not present:
                continue
            for master in MASTERS:
                # 用别名判定（正文用简称）
                if not any(al in sent for al in MASTER_ALIASES[master]):
                    continue
                for m in present:
                    cooccur[master][m] += 1
                    first_hui[master].setdefault(m, hui)

    log(f"\n  句内共现统计（与主子同句出现的丫鬟，top6）:")
    for master in MASTERS:
        if cooccur[master]:
            top = cooccur[master].most_common(6)
            log(f"    {master}: {top}")

    # ---- 合并三路 ----
    merged = collections.defaultdict(lambda: collections.defaultdict(dict))
    for master, cats in graph_roles.items():
        for cat, lst in cats.items():
            for it in lst:
                merged[master][cat].setdefault(it["name"], {"name": it["name"], "sources": []})
                merged[master][cat][it["name"]]["sources"].append("graph")

    for master, cats in text_roles.items():
        for cat, lst in cats.items():
            for it in lst:
                merged[master][cat].setdefault(it["name"], {"name": it["name"], "sources": []})
                e = merged[master][cat][it["name"]]
                e["sources"].append("text")
                e["evidence_hui"] = it.get("hui")
                e["snippet"] = it.get("snippet")

    # ---- 归属唯一性判定 ----
    # 若某丫鬟在另一个主子的共现次数明显更高（≥2 倍），则不归给当前主子
    # 实测反例：平儿（凤姐 26 vs 宝玉 15）、莺儿（宝钗 11 vs 宝玉 11）、
    #          雪雁（黛玉 6，图谱已明确）
    def best_owner(name):
        """返回该丫鬟最可能的主子及其共现次数"""
        best, best_n = None, 0
        for m in MASTERS:
            n = cooccur.get(m, {}).get(name, 0)
            if n > best_n:
                best, best_n = m, n
        return best, best_n

    # 图谱已明确归属的丫鬟（高置信度，不可被共现覆盖）
    graph_owner = {}
    for master, cats in graph_roles.items():
        for cat, lst in cats.items():
            for it in lst:
                graph_owner.setdefault(it["name"], master)

    # 共现强但未被前面识别的 → 补入（标为 cooccur 来源）
    # 阈值：句内共现 ≥3 次视为强信号
    for master, cnt in cooccur.items():
        for name, n in cnt.items():
            if n < 3:
                continue
            # 过滤 0（最强）：人工归属表优先
            man_own = MANUAL_OWNERSHIP.get(name)
            if man_own:
                if man_own != master:
                    continue        # 明确属于他人 → 跳过
                # 属于当前主子 → 直接采信
            # 过滤 1：图谱已明确归他人 → 跳过
            g_own = graph_owner.get(name)
            if g_own and g_own != master:
                continue
            # 过滤 2：句内共现明显低于其他主子（< 最高值的 50%）→ 跳过
            ow, ow_n = best_owner(name)
            if ow and ow != master and ow_n >= max(3, n * 2):
                continue
            cat = "丫鬟"
            if name in merged[master][cat]:
                e = merged[master][cat][name]
                if "cooccur" not in e["sources"]:
                    e["sources"].append("cooccur")
                    e["cooccur_sent"] = n
                    e["first_hui"] = first_hui[master].get(name)
                continue
            merged[master][cat][name] = {
                "name": name, "sources": ["cooccur"],
                "cooccur_sent": n,
                "first_hui": first_hui[master].get(name),
            }

    # ---- 输出 ----
    out = {}
    for master, cats in merged.items():
        out[master] = {}
        for cat, people in cats.items():
            lst = []
            for name, e in people.items():
                # 置信度：共现次数是关键信号
                #   图谱明确 → high
                #   共现 ≥10 次 → medium（高频共现基本可确认）
                #   共现 3~9 次 → low（可能属他人）
                cs = e.get("cooccur_sent") or 0
                if MANUAL_OWNERSHIP.get(name) == master:
                    conf = "high"          # 人工确认，最高置信
                elif "graph" in e["sources"]:
                    conf = "high"
                elif cs >= 10:
                    conf = "medium"
                elif cs >= 3:
                    conf = "low"
                else:
                    conf = "low"
                lst.append({
                    "name": name,
                    "conf": conf,      # 用重算的置信度（不要用 e 里的初始值）
                    "sources": e["sources"],
                    "evidence_hui": e.get("evidence_hui"),
                    "cooccur_sent": e.get("cooccur_sent"),
                    "first_hui": e.get("first_hui"),
                })
            lst.sort(key=lambda x: ({"high": 0, "medium": 1, "low": 2}[x["conf"]], x["name"]))
            out[master][cat] = lst

    path = os.path.join(WEB_DATA, "person_roles.json")
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    log(f"\n已写 {path}  ({os.path.getsize(path):,} B)")

    # ---- 重点验证：贾宝玉 ----
    log("\n" + "=" * 74)
    log("重点：贾宝玉 的身份关系")
    log("=" * 74)
    if "贾宝玉" in out:
        for cat, lst in out["贾宝玉"].items():
            log(f"\n  {cat} ({len(lst)} 人):")
            for x in lst:
                log(f"    {x['name']:8s} conf={x['conf']:6s} src={','.join(x['sources'])}")
    else:
        log("  ⚠ 未生成贾宝玉的记录")


if __name__ == '__main__':
    main()
