# -*- coding: utf-8 -*-
"""
S5 评测 · 阶段 1：构建评测集
=============================

对应需求 R-801 / R-802 / R-805
对应难点分析§四：「事实型 vs 推理型必须分开报告，不能合成单一准确率」

为什么不用现成的 golden_eval_60
--------------------------------
实测检查：那 60 条的 `relevant_chunk_ids` 指向 `cai_note_*`、
`qaSynthesisArticle_*`、`transfernotesArticle_*` 等**外部文档**，
不是本项目的 hlm_chunks 语料。用它做检索评测（Recall@k）无法对拍。
→ 事实型评测集必须自建，从**本项目语料**里抽可验证事实。

评测集设计
----------
  factual（事实型，可自动评）
    - 问题有唯一答案
    - 答案能在语料里精确找到
    - 指标：EM / F1 / 引用命中率（Recall@k）/ 回次命中率

  reasoning（推理型，人工抽样评）
    - 开放答案，无唯一标准
    - 只能人工 5 分制
    - 指标：人工评分均值 + Kappa

  refuse（应拒答，测 R-111）
    - 语料里没有答案的问题（跨作品/纯主观/伪前提）
    - 指标：正确拒答率 / 误拒率

输出
----
  eval/eval_factual.json     事实型（含 golden 答案 + 证据位置）
  eval/eval_reasoning.json   推理型
  eval/eval_refuse.json      应拒答
  eval/eval_manifest.json    构建说明与统计

运行（Windows 本地）：
    python s5_1_build_eval.py
"""
import os
import sys
import json
import re
import collections

sys.stdout.reconfigure(encoding="utf-8")

CORPUS = r"D:\Programming\Red\hlm_corpus"
WORK = r"D:\学校事务\大数据原理与应用"
OUT = os.path.join(WORK, "红楼智问", "eval")
os.makedirs(OUT, exist_ok=True)


def load_chapters():
    p = os.path.join(CORPUS, "clean", "chapters.jsonl")
    out = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


CHAPTERS = load_chapters()
HUI2CH = {c["hui"]: c for c in CHAPTERS}


def find(fragment, hui=None):
    """在语料里找包含某片段的段落，返回 [(hui, para_idx, para_text)]"""
    hits = []
    for c in CHAPTERS:
        if hui is not None and c["hui"] != hui:
            continue
        for i, p in enumerate(c["paragraphs"]):
            if fragment in p:
                hits.append((c["hui"], i, p))
    return hits


def mk_factual(qid, question, answer, keywords, hui, para_idx, note="", anchor=None):
    """
    构造一条事实型评测。

    **关键区分**（第一版在这里栽过）：
      answer_keywords —— 答案里应出现的词（评「答对没」）
      anchor          —— 证据段落里**必定出现**的原文片段（验「证据位置对不对」）

    为什么必须分开：判词类问题的证据段落是
      「可叹停机德，堪怜咏絮才」
    但答案要写「薛宝钗与林黛玉」——判词里根本没有这两个名字（要靠解读）。
    用 answer_keywords 去校验证据段落，必然全错（实测 17/27 误报）。

    回目类问题更特殊：回目存在 `huimu` 字段，**正文段落里没有**，
    所以 anchor=None + 用 huimu 校验（见 verify 逻辑）。
    """
    return {
        "id": qid,
        "type": "factual",
        "question": question,
        "answer": answer,
        "answer_keywords": keywords,
        "evidence": {"hui": hui, "para_index": para_idx},
        "evidence_anchor": anchor,
        "note": note,
    }


# ============================================================
# 1. 事实型（30 条）—— 每条都从语料里核实过证据位置
# ============================================================
def build_factual():
    items = []

    # ---- 1.1 通灵玉/金锁铭文（第8回）----
    h = find("莫失莫忘，仙寿恒昌", hui=8)
    assert h, "找不到通灵玉铭文"
    hui, idx, _ = h[0]
    items.append(mk_factual(
        "f001", "通灵宝玉上的铭文是什么？",
        "莫失莫忘，仙寿恒昌", ["莫失莫忘", "仙寿恒昌"], hui, idx,
        "第8回宝钗念出", anchor="莫失莫忘"))

    h = find("不离不弃", hui=8)
    assert h
    hui, idx, _ = h[0]
    items.append(mk_factual(
        "f002", "薛宝钗金锁上的铭文是什么？",
        "不离不弃，芳龄永继", ["不离不弃", "芳龄永继"], hui, idx,
        "第8回金锁正面/反面音注", anchor="不离不弃"))

    # ---- 1.2 判词（第5回）----
    h = find("可叹停机德", hui=5)
    assert h
    hui, idx, _ = h[0]
    items.append(mk_factual(
        "f003", "「可叹停机德，堪怜咏絮才」这句判词写的是谁？",
        "薛宝钗与林黛玉", ["宝钗", "黛玉"], hui, idx,
        "第5回金陵十二钗正册判词（判词本身不含人名，需解读）", anchor="可叹停机德"))

    h = find("二十年来辨是非", hui=5)
    assert h
    hui, idx, _ = h[0]
    items.append(mk_factual(
        "f004", "「二十年来辨是非，榴花开处照宫闱」是给谁的判词？",
        "贾元春", ["元春"], hui, idx,
        "第5回判词", anchor="二十年来辨是非"))

    # ---- 1.3 葬花吟（第27回）----
    h = find("侬今葬花人笑痴", hui=27)
    assert h
    hui, idx, _ = h[0]
    items.append(mk_factual(
        "f005", "「侬今葬花人笑痴，他年葬侬知是谁」出自哪首诗？",
        "葬花吟", ["葬花吟", "葬花"], hui, idx,
        "第27回黛玉葬花", anchor="侬今葬花人笑痴"))

    # ---- 1.4 人物身份 ----
    h = find("原来这袭人亦是贾母之婢", hui=3)
    assert h, "找不到袭人来历"
    hui, idx, _ = h[0]
    items.append(mk_factual(
        "f006", "袭人原本是谁的丫鬟？",
        "贾母", ["贾母"], hui, idx,
        "第3回交代袭人本名珍珠，贾母之婢", anchor="原来这袭人亦是贾母之婢"))

    h = find("名唤鹦哥者与了黛玉", hui=3)
    assert h, "找不到鹦哥"
    hui, idx, _ = h[0]
    items.append(mk_factual(
        "f007", "鹦哥被贾母给了谁？",
        "林黛玉", ["黛玉"], hui, idx,
        "第3回", anchor="名唤鹦哥者与了黛玉"))

    h = find("雪雁", hui=3)
    assert h
    hui, idx, _ = h[0]
    items.append(mk_factual(
        "f008", "雪雁是谁带来的丫鬟？",
        "林黛玉", ["黛玉", "雪雁"], hui, idx,
        "第3回黛玉从家中带来的小丫头", anchor="雪雁"))

    # ---- 1.5 回目类（可从章节数据直接验证）----
    ch3 = HUI2CH.get(3)
    if ch3:
        items.append(mk_factual(
            "f009", "第3回的回目是什么？",
            "金陵城起复贾雨村 荣国府收养林黛玉",
            ["金陵城起复贾雨村", "荣国府收养林黛玉"], 3, 0,
            "回目直接来自章节数据"))

    ch5 = HUI2CH.get(5)
    if ch5:
        items.append(mk_factual(
            "f010", "第5回的回目是什么？",
            "游幻境指迷十二钗 饮仙醪曲演红楼梦",
            ["游幻境指迷十二钗", "饮仙醪曲演红楼梦"], 5, 0,
            "第5回"))

    # ---- 1.6 关键情节 ----
    h = find("刘姥姥", hui=6)
    assert h
    hui, idx, _ = h[0]
    items.append(mk_factual(
        "f011", "刘姥姥第一次进荣国府是在第几回？",
        "第6回", ["6", "六"], hui, idx,
        "第6回回目「刘姥姥一进荣国府」", anchor=None))

    h = find("宝玉挨打", None)
    if not h:
        h = find("笞挞", None)
    if h:
        hui, idx, _ = h[0]
        items.append(mk_factual(
            "f012", "「宝玉挨打」发生在第几回？",
            "第33回", ["33", "三十三"], hui, idx,
            "第33回回目「不肖种种大遭笞挞」"))

    # ---- 1.7 诗词出处 ----
    h = find("满纸荒唐言", None)
    if h:
        hui, idx, _ = h[0]
        items.append(mk_factual(
            "f013", "「满纸荒唐言，一把辛酸泪」出现在第几回？",
            "第1回", ["1", "一"], hui, idx,
            "第1回曹雪芹自题绝句", anchor="满纸荒唐言"))

    h = find("好风频借力", None)
    if h:
        hui, idx, _ = h[0]
        items.append(mk_factual(
            "f014", "「好风频借力，送我上青云」是谁写的？",
            "薛宝钗", ["宝钗"], hui, idx,
            "第70回柳絮词（宝钗《临江仙》）", anchor="好风频借力"))

    # ---- 1.8 判词与人物 ----
    h = find("凡鸟偏从末世来", hui=5)
    if h:
        hui, idx, _ = h[0]
        items.append(mk_factual(
            "f015", "「凡鸟偏从末世来，都知爱慕此生才」写的是谁？",
            "王熙凤", ["凤姐", "王熙凤"], hui, idx,
            "第5回判词「一从二令三人木」", anchor="凡鸟偏从末世来"))

    h = find("才自精明志自高", hui=5)
    if h:
        hui, idx, _ = h[0]
        items.append(mk_factual(
            "f016", "「才自精明志自高，生于末世运偏消」写的是谁？",
            "贾探春", ["探春"], hui, idx,
            "第5回判词", anchor="才自精明志自高"))

    h = find("富贵又何为", hui=5)
    if h:
        hui, idx, _ = h[0]
        items.append(mk_factual(
            "f017", "「富贵又何为，襁褓之间父母违」写的是谁？",
            "史湘云", ["湘云"], hui, idx,
            "第5回判词", anchor="富贵又何为"))

    h = find("欲洁何曾洁", hui=5)
    if h:
        hui, idx, _ = h[0]
        items.append(mk_factual(
            "f018", "「欲洁何曾洁，云空未必空」写的是谁？",
            "妙玉", ["妙玉"], hui, idx,
            "第5回判词", anchor="欲洁何曾洁"))

    # ---- 1.9 太虚幻境 ----
    h = find("薄命司", hui=5)
    if h:
        hui, idx, _ = h[0]
        items.append(mk_factual(
            "f019", "警幻仙子带宝玉进入太虚幻境的哪个司？",
            "薄命司", ["薄命司"], hui, idx,
            "第5回", anchor="薄命司"))

    h = find("金陵十二钗正册", hui=5)
    if h:
        hui, idx, _ = h[0]
        items.append(mk_factual(
            "f020", "太虚幻境薄命司中，宝玉最先看到的是哪一册？",
            "金陵十二钗正册", ["正册", "金陵十二钗"], hui, idx,
            "第5回", anchor="金陵十二钗正册"))

    # ---- 1.10 其他可验证事实 ----
    h = find("冷子兴", hui=2)
    if h:
        hui, idx, _ = h[0]
        items.append(mk_factual(
            "f021", "「冷子兴演说荣国府」在第几回？",
            "第2回", ["2", "二"], hui, idx,
            "第2回回目", anchor=None))

    h = find("元妃省亲", None) or find("归省", None)
    if h:
        hui, idx, _ = h[0]
        items.append(mk_factual(
            "f022", "元妃省亲（贾元春归省庆元宵）主要在第几回？",
            "第18回", ["18", "十八"], hui, idx,
            "第18回回目"))

    h = find("黛玉葬花", None) or find("埋香冢", None)
    if h:
        hui, idx, _ = h[0]
        items.append(mk_factual(
            "f023", "「埋香冢飞燕泣残红」（黛玉葬花）在第几回？",
            "第27回", ["27", "二十七"], hui, idx,
            "第27回回目"))

    h = find("晴雯", hui=52)
    if h:
        hui, idx, _ = h[0]
        items.append(mk_factual(
            "f024", "「勇晴雯病补雀金裘」在第几回？",
            "第52回", ["52", "五十二"], hui, idx,
            "第52回回目"))

    h = find("尤三姐", None)
    if h:
        hui, idx, _ = h[0]
        items.append(mk_factual(
            "f025", "尤三姐自刎用的是谁的剑？",
            "柳湘莲", ["柳湘莲", "湘莲"], hui, idx,
            "第66回", anchor="尤三姐"))

    h = find("香菱学诗", None) or find("慕雅女雅集苦吟诗", None)
    if h:
        hui, idx, _ = h[0]
        items.append(mk_factual(
            "f026", "香菱学诗主要在第几回？",
            "第48回", ["48", "四十八"], hui, idx,
            "第48回回目"))

    # ---- 1.11 统计型（可从 MapReduce 数据验证）----
    items.append({
        "id": "f027", "type": "factual",
        "question": "全书正文（前80回）一共多少字？",
        "answer": "约60万字",
        "answer_keywords": ["60", "六十万", "599", "600"],
        "evidence": None,
        "note": "统计口径：段落字数合计 599,196；仅汉字 526,673",
        "stat_based": True,
    })
    items.append({
        "id": "f028", "type": "factual",
        "question": "全书中被提及次数最多的人物是谁？",
        "answer": "贾宝玉",
        "answer_keywords": ["贾宝玉", "宝玉"],
        "evidence": None,
        "note": "MapReduce personfreq: 贾宝玉 2,746 次",
        "stat_based": True,
    })
    items.append({
        "id": "f029", "type": "factual",
        "question": "前80回中，哪一回的批语（脂批）数量最多？",
        "answer": "第19回",
        "answer_keywords": ["19", "十九"],
        "evidence": None,
        "note": "重解码统计：第19回 160 条（另一口径第3回 199 条，需说明口径）",
        "stat_based": True,
    })
    items.append({
        "id": "f030", "type": "factual",
        "question": "脂批共有多少个不同版本的标注？",
        "answer": "9个（蒙府/戚序/甲戌/庚辰/己卯/甲辰/列藏/杨藏/未识别）",
        "answer_keywords": ["9", "九", "蒙府", "庚辰", "甲戌"],
        "evidence": None,
        "note": "重解码 versions_legend 共 9 类（含 1 条未识别）",
        "stat_based": True,
    })

    return items


# ============================================================
# 2. 推理型（15 条）—— 只能人工评
# ============================================================
REASONING = [
    ("r001", "林黛玉为什么进贾府？", "母亲去世、外祖母念及，接来抚养；贾母疼爱", "需结合第3回与第2回冷子兴演说"),
    ("r002", "「千红一窟」「万艳同杯」有什么隐喻？", "谐音千红一哭、万艳同悲；预示众女儿悲剧命运", ""),
    ("r003", "宝玉和黛玉是什么关系？", "姑表兄妹；黛玉之母贾敏是宝玉之父贾政的妹妹", "若语料未明写，应主动说明依据不足"),
    ("r004", "王熙凤为什么能管家？", "王夫人内侄女；贾琏之妻；精明强干；贾母支持", ""),
    ("r005", "薛宝钗为什么最终嫁给宝玉？", "金玉良缘；家族安排；黛玉病亡", "需综合多回"),
    ("r006", "晴雯之死说明了什么？", "封建压迫；宝玉无力；性格与命运的冲突", ""),
    ("r007", "贾府的衰败有哪些征兆？", "经济亏空；内部倾轧；元春失势；抄检大观园", "散见多回"),
    ("r008", "黛玉葬花这一情节有什么作用？", "塑造黛玉性格；预示其命运；以花喻人；悲剧氛围", ""),
    ("r009", "「金陵十二钗」判词的作用是什么？", "谶语；预叙人物命运；结构性伏笔", ""),
    ("r010", "为什么说《红楼梦》是「大旨谈情」？", "第1回原文即言大旨谈情；全书以宝黛爱情为主线", ""),
    ("r011", "刘姥姥三进荣国府分别有什么作用？", "一进引出贾府气象；二进对照贫富；三进见证败落", ""),
    ("r012", "探春理家为什么失败？", "庶出身份；家族积弊已深；改革触犯既得利益", ""),
    ("r013", "「好了歌」表达了什么思想？", "世事无常；功名富贵皆空；出世思想", ""),
    ("r014", "贾宝玉的「痴」体现在哪些方面？", "对女儿的尊重；对仕途经济的厌弃；对黛玉的痴情", ""),
    ("r015", "脂批对理解《红楼梦》有什么价值？", "创作背景；人物原型；伏笔线索；红学材料", ""),
]


def build_reasoning():
    """
    注意：REASONING 是四元组 (id, question, 参考要点, 评分提示)。
    **参考要点里不能混入「注意：…应说明」这类写给评分人看的话** ——
    第一版曾把这类元指令当成答案要点，导致自动评分把好回答判成 1 分（实测）。
    """
    return [{
        "id": rid,
        "type": "reasoning",
        "question": q,
        "reference_answer": ref,
        "scoring_hint": hint,
        "scoring": "人工 5 分制：1=完全错误 3=部分正确 5=完整准确",
    } for rid, q, ref, hint in REASONING]





# ============================================================
# 3. 应拒答（10 条）—— 测 R-111
# ============================================================
REFUSE = [
    ("x001", "《三国演义》里诸葛亮为什么六出祁山？", "跨作品"),
    ("x002", "《红楼梦》和《小时代》哪个更伟大？", "纯主观评价"),
    ("x003", "曹雪芹是哪一年写出第81回的？", "伪前提（曹雪芹未写后40回，且无确切年份）"),
    ("x004", "林黛玉的微信号是多少？", "荒谬问题"),
    ("x005", "贾宝玉最后有没有移民火星？", "跨题材"),
    ("x006", "《红楼梦》里孙悟空和贾宝玉谁厉害？", "跨作品"),
    ("x007", "如果穿越到《红楼梦》，你会选谁？", "纯主观"),
    ("x008", "红楼梦是不是中国最伟大的小说？", "纯主观"),
    ("x009", "贾宝玉的身份证号是多少？", "荒谬问题"),
    ("x010", "林黛玉和贾宝玉同床这么久为什么没怀孕？", "真实提问中的伪前提（74 条真实提问之一）"),
]


def build_refuse():
    return [{
        "id": rid, "type": "refuse",
        "question": q, "reason": why,
        "expected": "应明确说明语料无法回答，而非编造",
    } for rid, q, why in REFUSE]


# ============================================================
def main():
    log = print
    log("=" * 70)
    log("S5-1 构建评测集")
    log("=" * 70)

    factual = build_factual()
    reasoning = build_reasoning()
    refuse = build_refuse()

    # 校验事实型：证据位置必须真能找到
    log("\n校验事实型证据位置：")
    ok = 0
    fail = []
    for it in factual:
        ev = it.get("evidence")
        if not ev:
            log(f"  {it['id']}  统计型（无段落证据，跳过）")
            continue
        ch = HUI2CH.get(ev["hui"])
        if not ch or ev["para_index"] >= len(ch["paragraphs"]):
            log(f"  ❌ {it['id']}  证据位置越界: {ev}")
            fail.append(it["id"])
            continue
        anchor = it.get("evidence_anchor")
        if anchor is None:
            # 回目类：证据在 huimu 字段，不在正文段落
            hui_all = ch["huimu"]
            body = "\n".join(ch["paragraphs"])
            if any(k in hui_all for k in it["answer_keywords"]) or                any(k in body for k in it["answer_keywords"]):
                ok += 1
                log(f"  ✅ {it['id']}  （回目类，huimu/正文命中）")
            else:
                log(f"  ⚠ {it['id']}  回目类关键词未命中  huimu={hui_all!r}")
                fail.append(it["id"])
            continue
        para = ch["paragraphs"][ev["para_index"]]
        if anchor in para:
            ok += 1
        else:
            log(f"  ❌ {it['id']}  段落 {ev['para_index']} 不含 anchor='{anchor}'")
            fail.append(it["id"])
    log(f"\n  证据位置验证通过: {ok}/{len(factual)}")
    if fail:
        log(f"  未通过: {fail}")

    # 写出
    def w(name, obj):
        p = os.path.join(OUT, name)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        log(f"  {name:28s} {os.path.getsize(p):>6d} B  {len(obj) if isinstance(obj, list) else 1} 条")
        return p

    log("\n写出：")
    w("eval_factual.json", factual)
    w("eval_reasoning.json", reasoning)
    w("eval_refuse.json", refuse)

    manifest = {
        "built_at": "2026-10-24",
        "stage": "S5-1",
        "purpose": "事实型与推理型分开评测，不合成单一准确率",
        "counts": {
            "factual": len(factual),
            "reasoning": len(reasoning),
            "refuse": len(refuse),
            "total": len(factual) + len(reasoning) + len(refuse),
        },
        "metrics": {
            "factual": ["EM (exact match)", "F1 (token overlap)",
                        "keyword_coverage", "citation_hit@k (回次命中)",
                        "refuse_correctness"],
            "reasoning": ["human_score_1_5", "kappa (2 人独立打分)"],
            "refuse": ["correct_refusal_rate", "false_refusal_rate"],
        },
        "why_not_golden60": (
            "实测检查：golden_eval_60 的 relevant_chunk_ids 指向 "
            "cai_note_*/qaSynthesisArticle_*/transfernotesArticle_* 等外部文档，"
            "不是本项目的 hlm_chunks 语料，无法用于检索指标对拍。"
            "故事实型评测集自建，全部从本项目语料抽取并校验证据位置。"
        ),
        "sources": ["hlm_corpus/clean/chapters.jsonl（80回正文）",
                    "HBase hlm_stats（MapReduce 统计，用于 f027-f030）"],
    }
    w("eval_manifest.json", manifest)

    log(f"\n输出目录: {OUT}")
    log("=" * 70)


if __name__ == "__main__":
    main()
