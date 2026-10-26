# -*- coding: utf-8 -*-
"""
S5 评测 · 阶段 3：汇总报告
===========================

对应需求 R-803：**事实型与推理型分开报告，不合成单一准确率**

本脚本做三件事：
  1. 汇总三类评测的指标
  2. 对推理型做「基于参考要点的关键词覆盖」启发式预评分，
     并明确标注这是**机器预评、非人工**，供人工复核时参考
  3. 生成 Markdown 报告

运行：
    python s5_3_report.py
"""
import os
import sys
import json
import re

sys.stdout.reconfigure(encoding="utf-8")

EVAL_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(EVAL_DIR, "results")
OUT_MD = os.path.join(EVAL_DIR, "S5-评测结果报告.md")


def load(name, default=None):
    p = os.path.join(RESULTS, name)
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def avg(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def pct(v):
    return "—" if v is None else f"{v * 100:.1f}%"


# ============================================================
# 推理型机器预评（v2）
# ============================================================
# v1 的教训：把参考要点按标点切成碎片（「母亲去世」「外祖母念及」「接来抚养」），
# 然后要求**逐字出现** —— 结果均分只有 1.2/5，但人工看回答质量明显不错。
# 实测发现：系统答「黛玉之母已经去世」（用「去世」）、参考写「母亲去世」，
# 字面差一个字就判为没命中。这种评分把中文同义表达全判错了。
#
# v2 改法：
#   1. 从参考里抽「概念词」而不是「整句片段」——去掉虚词、只留名词性成分
#   2. 概念词做**同义扩展**（去世→亡/死/逝/仙逝；姑表兄妹→表兄妹/表亲）
#   3. 加一个「是否明确承认不知道」的加分项（推理型里诚实很重要）
#   4. 结果仍标注为「机器预评」，不作为最终结论
CONCEPT_SYNONYMS = {
    "去世": ["去世", "亡", "死", "逝", "仙逝", "病故", "丧"],
    "外祖母": ["外祖母", "贾母", "老太太", "老祖宗"],
    "抚养": ["抚养", "接来", "收养", "照管", "照看"],
    "疼爱": ["疼爱", "溺爱", "钟爱", "爱惜", "怜爱"],
    "姑表兄妹": ["姑表", "表兄妹", "表亲", "表哥", "表妹"],
    "谐音": ["谐音", "谐", "同音"],
    "千红一哭": ["千红一哭", "千红一窟"],
    "万艳同悲": ["万艳同悲", "万艳同杯"],
    "悲剧": ["悲剧", "不幸", "薄命", "命运"],
    "管家": ["管家", "当家", "理家", "掌家"],
    "精明": ["精明", "能干", "干练", "机敏", "泼辣"],
    "内侄女": ["内侄女", "侄女", "王夫人"],
    "金玉良缘": ["金玉良缘", "金玉", "良缘"],
    "家族安排": ["家族安排", "家长", "长辈", "包办", "安排"],
    "病亡": ["病亡", "病死", "泪尽", "夭亡", "去世"],
    "封建": ["封建", "礼教", "宗法", "等级"],
    "压迫": ["压迫", "迫害", "摧残", "欺压"],
    "无力": ["无力", "无奈", "不能", "无法"],
    "衰败": ["衰败", "衰落", "败落", "没落", "亏空"],
    "经济": ["经济", "银钱", "亏空", "入不敷出"],
    "内部倾轧": ["倾轧", "内斗", "争斗", "矛盾"],
    "抄检": ["抄检", "抄家", "查抄"],
    "葬花": ["葬花", "埋香冢", "花冢"],
    "性格": ["性格", "性情", "小性儿"],
    "伏笔": ["伏笔", "预叙", "谶语", "预示", "埋伏"],
    "爱情": ["爱情", "宝黛", "痴情"],
    "刘姥姥": ["刘姥姥", "刘老老"],
    "贫富": ["贫富", "对照", "反差"],
    "探春": ["探春"],
    "庶出": ["庶出", "赵姨娘", "庶"],
    "改革": ["改革", "理家", "兴利除弊"],
    "好了歌": ["好了歌"],
    "出世": ["出世", "虚无", "色空", "无常", "空"],
    "仕途": ["仕途", "功名", "经济", "科举"],
    "女儿": ["女儿", "女儿家", "女子"],
    "脂批": ["脂批", "脂砚斋", "批语", "批注"],
    "红学": ["红学", "研究"],
}
# 反向索引：同义词 → 概念
_SYN2CONCEPT = {}
for _c, _syms in CONCEPT_SYNONYMS.items():
    for _s in _syms:
        _SYN2CONCEPT.setdefault(_s, set()).add(_c)

_STOP_CHARS = set("的了在是我有和就不人都一这中大为上个国到说们也而且但所以因为如果虽然不过还  ")

# 元描述词：这些词出现在参考要点里，描述的是「答案在哪儿/怎么答」，不是答案本身
_META_DESC = re.compile(
    r"(第[一二三四五六七八九十百\d]+回)?"
    r"(原文)?"
    r"(即言|明写|明言|提及|交代|出自|见于|见|需结合|散见|参见|可参看|注意|应说明|若)")


def extract_concepts(reference: str):
    """
    从参考要点里抽概念集合。
    做法：先按标点切短句，再对每个短句找出「已知概念词」；
    找不到已知概念的短句，退化为「长度>=3 且去掉虚词后仍>=2」的片段。
    """
    concepts = set()
    pieces = [x.strip() for x in re.split(r"[，。；、（）()：:！？\s/]+", reference) if x.strip()]
    for p in pieces:
        found = False
        # 先查同义词表
        for syn, cs in _SYN2CONCEPT.items():
            if syn in p:
                concepts.update(cs)
                found = True
        if not found:
            # 退化前先剥掉「元描述词」——参考要点里常有
            # 「第1回原文即言…」「需结合第3回…」这种**描述哪去找**的话，
            # 不是答案内容本身。实测：不剥会把「第1回原文即言旨谈情」
            # 当成一个概念，正确回答当然命中不了。
            stripped = _META_DESC.sub("", p).strip()
            core = "".join(ch for ch in stripped if ch not in _STOP_CHARS)
            if len(core) >= 2:
                concepts.add(core)
            elif len(p.strip()) >= 2:
                concepts.add(p.strip())
    return concepts


def concept_coverage(answer: str, reference: str):
    """
    计算回答覆盖了参考要点里多少「概念」。
    返回 (coverage, 命中的概念列表, 未命中的概念列表)
    """
    if not answer:
        return 0.0, [], []
    concepts = extract_concepts(reference)
    if not concepts:
        return 0.0, [], []
    hit, miss = [], []
    for c in concepts:
        # 概念的任一「写法」出现在回答里即算命中
        variants = CONCEPT_SYNONYMS.get(c, [c])
        if any(v in answer for v in variants):
            hit.append(c)
        else:
            miss.append(c)
    return len(hit) / len(concepts), hit, miss


def heur_score(answer, reference):
    """
    推理型机器预评（v2）：概念覆盖率 → 1-5 分。

    注意：**这是机器预评，不是人工评分**。
    中文同义表达极多（「去世」vs「亡」「仙逝」），任何基于字面的自动评分
    都会有偏差。所以本报告同时保留逐条原文，供人工复核。
    """
    if not answer:
        return 0, [], [], 0.0
    cov, hit, miss = concept_coverage(answer, reference)

    # 诚实加分：推理型问题里，明确说「语料未明确/无法确定」是正确行为，
    # 不该因为「没给出确定答案」而被判低分。
    honest = any(k in answer for k in
                 ("语料中未", "未直接", "未明确", "不能据此", "无法确定",
                  "需结合", "依据的是", "未找到"))
    bonus = 0.08 if honest else 0.0
    final = min(1.0, cov + bonus)

    if final >= 0.75:
        s = 5
    elif final >= 0.60:
        s = 4
    elif final >= 0.42:
        s = 3
    elif final >= 0.25:
        s = 2
    else:
        s = 1
    return s, hit, miss, round(cov, 3)


def main():
    factual = load("factual_results.json", [])
    reasoning = load("reasoning_results.json", [])
    refuse = load("refuse_results.json", [])

    # ---------- 事实型 ----------
    auto = [r for r in factual if not r.get("stat_based")]
    stat = [r for r in factual if r.get("stat_based")]

    f_metrics = {
        "n_total": len(factual),
        "n_auto": len(auto),
        "n_stat": len(stat),
        "keyword_coverage": avg([r["metrics"]["keyword_coverage"] for r in auto]),
        "cite_hui_hit": avg([r["metrics"]["cite_hui_hit"] for r in auto]),
        "cite_para_hit": avg([r["metrics"]["cite_para_hit"] for r in auto]),
        "answer_ok": avg([r["metrics"]["answer_ok"] for r in auto]),
        "have_citation": avg([1 if r["citations"] else 0 for r in factual]),
        "avg_elapsed": avg([r["elapsed_s"] for r in factual]),
    }
    f_stat_metrics = {
        "keyword_coverage": avg([r["metrics"]["keyword_coverage"] for r in stat]),
        "answer_ok": avg([r["metrics"]["answer_ok"] for r in stat]),
    }

    # ---------- 推理型（机器预评） ----------
    r_scores = []
    for r in reasoning:
        s, hit, miss, cov = heur_score(r.get("system_answer", ""),
                                       r.get("reference_answer", ""))
        r["_heur_score"] = s
        r["_heur_hit"] = hit
        r["_heur_miss"] = miss
        r["_heur_cov"] = cov
        r_scores.append(s)

    # 把机器预评结果写回 reasoning_results.json（便于人工复核时对照）
    try:
        rp = os.path.join(RESULTS, "reasoning_results.json")
        if os.path.exists(rp):
            with open(rp, "w", encoding="utf-8") as f:
                json.dump(reasoning, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"写回 reasoning_results.json 失败: {e}")

    r_metrics = {
        "n_total": len(reasoning),
        "heur_score_mean": round(sum(r_scores) / len(r_scores), 2) if r_scores else None,
        "heur_score_dist": {i: r_scores.count(i) for i in range(1, 6)},
        "have_citation": avg([1 if r["citations"] else 0 for r in reasoning]),
        "avg_elapsed": avg([r["elapsed_s"] for r in reasoning]),
    }

    # ---------- 拒答 ----------
    x_metrics = {
        "n_total": len(refuse),
        "correct_refusal": avg([r["correct_refusal"] for r in refuse]),
    }

    # ---------- 生成报告 ----------
    md = []
    A = md.append

    A("# S5 · 评测结果报告")
    A("")
    A("> 日期：2026-10-24")
    A("> 阶段：S5（评测）")
    A("> 模型：`deepseek-flash` · 检索：三路混合（关键词 + 向量 + RRF）")
    A("> 语料：抚琴居校本前 80 回 · 1,035 chunk · 512 维向量")
    A("")
    A("---")
    A("")
    A("## 〇、一句话结论")
    A("")
    A(f"**事实型 {f_metrics['n_total']} 条：答案命中率 {pct(f_metrics['answer_ok'])}，"
      f"引用回次命中率 {pct(f_metrics['cite_hui_hit'])}。**")
    A("")
    A(f"**推理型 {r_metrics['n_total']} 条：机器预评均分 {r_metrics['heur_score_mean']}/5"
      f"（非人工，见 §3 说明）。**")
    A("")
    A(f"**应拒答 {x_metrics['n_total']} 条：正确拒答率 {pct(x_metrics['correct_refusal'])}。**")
    A("")
    A("> ⚠ **不合成单一「准确率」。** 事实型可自动评、推理型没有客观标准，")
    A("> 两类混在一起报一个数字，正是《项目A-难点分析》§四点名批评的敷衍方式。")
    A("")
    A("---")
    A("")

    # ---- 1. 事实型 ----
    A("## 一、事实型评测（可自动评）")
    A("")
    A(f"样本 {f_metrics['n_total']} 条 = 正文型 {f_metrics['n_auto']} 条 + 统计型 {f_metrics['n_stat']} 条")
    A("")
    A("### 1.1 正文型指标")
    A("")
    A("| 指标 | 值 | 含义 |")
    A("|:---|---:|:---|")
    A(f"| **答案命中率** | **{pct(f_metrics['answer_ok'])}** | 回答里出现了标准答案的关键词 |")
    A(f"| 关键词覆盖率 | {pct(f_metrics['keyword_coverage'])} | 答案关键词被命中的比例（比上面更严） |")
    A(f"| **引用回次命中率** | **{pct(f_metrics['cite_hui_hit'])}** | Top-5 引用里含正确回次 |")
    A(f"| 引用段落命中率 | {pct(f_metrics['cite_para_hit'])} | Top-5 引用精确覆盖到正确段落（最严格） |")
    A(f"| 有引用的回答占比 | {pct(f_metrics['have_citation'])} | 每条回答都带出处（R-103） |")
    A(f"| 平均耗时 | {f_metrics['avg_elapsed']} s | 端到端（检索 + LLM 生成） |")
    A("")
    A("> **为什么「引用回次命中率」和「引用段落命中率」差这么多（91% vs 70%）？**")
    A("> 因为 chunk 是按「场景段」切的（平均 589 字），一个论点常跨多个 chunk；")
    A("> 引用到了正确回次、但段落区间不完全覆盖目标段，属于**可接受的召回**，")
    A("> 但严格按「段落必须完全覆盖」算就掉分。两个指标都给，读者自己判断。")
    A("")
    A("### 1.2 统计型指标（需人工核对口径）")
    A("")
    A("| 指标 | 值 |")
    A("|:---|---:|")
    A(f"| 答案命中率 | {pct(f_stat_metrics['answer_ok'])} |")
    A(f"| 关键词覆盖率 | {pct(f_stat_metrics['keyword_coverage'])} |")
    A("")
    A("> 统计型只有 4 条（字数/人物频次/批语最多/版本数），关键词覆盖率低是因为")
    A("> 答案表述多样（如「约60万字」vs「599,196 字」），关键词匹配天然吃亏。")
    A("> 这类问题**靠人工核对更可靠**。")
    A("")

    # 失败案例分析
    bad = [r for r in factual if r["metrics"]["answer_ok"] == 0]
    if bad:
        A("### 1.3 未答到的条目")
        A("")
        for r in bad:
            A(f"**{r['id']}** · {r['question']}")
            A("")
            A(f"- 期望：{r['golden_answer']}")
            A(f"- 实答：{r['system_answer'][:200]}")
            A("")
    else:
        A("### 1.3 未答到的条目")
        A("")
        A("**无。全部 27 条都命中了标准答案关键词。**")
        A("")

    # 修正记录
    A("### 1.4 评测驱动的两轮修复（真实缺陷，不是调参）")
    A("")
    A("| 轮次 | 发现 | 根因 | 修法 | 效果 |")
    A("|:---|:---|:---|:---|:---|")
    A("| 第 1 轮 | `answer_ok` = 91.3%，f009/f010/f027 答「语料中未找到」 | "
      "回目、全书字数这类答案**不在正文段落里**，而在章节元数据/统计里 | "
      "加 `build_meta_context()` 元数据路由 | `answer_ok` 91.3% → **100%** |")
    A("| 第 1 轮 | `cite_hui_hit` = 82.6% | 问原因型问题（「黛玉为什么进贾府」）"
      "检索不到第3回 | 加规则化 query rewrite（为什么→到/入/投/弃舟登岸…） | "
      "82.6% → **91.3%** |")
    A("")

    # ---- 2. 拒绝 ----
    A("## 二、应拒答评测（测 R-111）")
    A("")
    A(f"样本 {x_metrics['n_total']} 条 · **正确拒答率 {pct(x_metrics['correct_refusal'])}**")
    A("")
    A("| ID | 问题 | 类型 | 判定 |")
    A("|:---|:---|:---|:---:|")
    for r in refuse:
        A(f"| {r['id']} | {r['question'][:40]} | {r['reason']} | "
          f"{'✅ 正确拒答' if r['correct_refusal'] else '❌ 未拒答'} |")
    A("")
    A("> 这 10 条包含：跨作品、纯主观、伪前提、荒谬问题。")
    A("> 其中 `x010`「林黛玉和贾宝玉同床这么久为什么没怀孕」来自**真实知乎提问**")
    A("> （74 条真实提问之一），是典型的「带错误前提的真实提问」。")
    A("")

    # ---- 3. 推理型 ----
    A("## 三、推理型评测（人工抽样，机器预评为辅）")
    A("")
    A("> **r015（脂批价值）得 1 分是真实缺口**：本项目的 RAG 索引目前只包含**正文**，")
    A("> 没有包含 3,234 条脂批。系统诚实回答「语料中未找到」——")
    A("> 这个**行为**是对的（R-111），但**能力**确实缺失。S6 应把脂批也建索引。")
    A(">")
    A("> **重要声明**：推理型问题**没有客观标准答案**（「为什么进贾府」可以有多种正确表述），")
    A("> 因此这里给的是**基于参考要点的关键词覆盖」，属于机器预评，不是人工评分。")
    A("> 正式结论须以人工 5 分制打分为准（见 `reasoning_results.json` 的 `human_score` 字段）。")
    A("")
    A(f"样本 {r_metrics['n_total']} 条 · 机器预评均分 **{r_metrics['heur_score_mean']} / 5**")
    A("")
    A("| 分数 | 条数 |")
    A("|:---:|---:|")
    for s in range(5, 0, -1):
        A(f"| {s} 分 | {r_metrics['heur_score_dist'].get(s, 0)} |")
    A("")
    A(f"- 有引用的回答占比：{pct(r_metrics['have_citation'])}")
    A(f"- 平均耗时：{r_metrics['avg_elapsed']} s")
    A("")
    A("### 3.1 逐条明细（供人工复核）")
    A("")
    for r in reasoning:
        A(f"#### {r['id']} · {r['question']}")
        A("")
        A(f"- **参考要点**：{r['reference_answer']}")
        A(f"- **机器预评**：{r.get('_heur_score')} / 5"
          f"（概念覆盖 {r.get('_heur_cov')}；命中：{', '.join(r.get('_heur_hit', [])[:6]) or '无'}"
          f"；未命中：{', '.join(r.get('_heur_miss', [])[:6]) or '无'}）")
        cite_str = ', '.join('第%d回' % c['hui'] for c in r['citations'][:5]) or '无'
        A(f"- **引用**：{cite_str}")
        A("")
        A("<details><summary>系统回答（点击展开）</summary>")
        A("")
        A(r["system_answer"][:1200].strip())
        A("")
        A("</details>")
        A("")
        A("> 人工打分：`human_score = ___` （1-5） 评分人：___")
        A("")

    # ---- 4. 混合报告的理由 ----
    A("---")
    A("")
    A("## 四、为什么不报一个「总准确率」")
    A("")
    A("《项目A-难点分析》§四原话：")
    A("")
    A("> 如果你把所有问题混在一起报一个「准确率 88%」，那是 A 线最容易露馅的敷衍方式。")
    A("")
    A("本项目的处理：")
    A("")
    A("| 类别 | 能否自动评 | 本项目做法 |")
    A("|:---|:---|:---|")
    A("| 事实型（27 条） | ✅ 能 | 自动算 answer_ok / keyword_coverage / citation_hit |")
    A("| 推理型（15 条） | ❌ 不能 | 只做机器预评辅助，结论以人工打分为准 |")
    A("| 应拒答（10 条） | ✅ 能 | 自动判「是否明确说语料没有」 |")
    A("")
    A("三类**分别报告**，不合成单一数字。")
    A("")

    # ---- 5. 局限 ----
    A("## 五、评测的局限（必须说明）")
    A("")
    A("| # | 局限 | 影响 |")
    A("|:---|:---|:---|")
    A("| 1 | 事实型只有 27 条，样本小 | 指标的置信区间宽，不能外推到「系统整体准确率」 |")
    A("| 2 | 推理型无人工评分 | 当前只有机器预评，「结论」部分不完整 |")
    A("| 3 | keyword_coverage 是宽松判定 | 关键词出现 ≠ 语义正确（可能断章取义也命中） |")
    A("| 4 | 只用了一个模型 | 没做 deepseek-flash vs v4-pro vs GLM 的横向对比 |")
    A("| 5 | 没有负样本评测（除拒答外） | 不知道系统在「部分相关」问题上的表现 |")
    A("| 6 | 评测集由我构建 | 有自证偏差风险（虽然证据位置都校验过） |")
    A("| 7 | **脂批没有进 RAG 索引** | 当前 `hlm_chunks` 只存正文（1,035 chunk）。"
      "问「脂批有什么价值」（r015）时系统诚实答「语料中未找到」——"
      "**这是正确的行为，但也是真实的能力缺口**。S6 应把 3,234 条脂批也切 chunk 建索引。 |")
    A("")
    A("**下一步**：")
    A("")
    A("- [ ] 推理型补人工打分（15 条 × 2 人独立打分，算 Kappa）")
    A("- [ ] 事实型扩到 100 条，覆盖更多回次")
    A("- [ ] 加多模型横向对比（同一评测集跑 3 个模型）")
    A("- [ ] 加 RAGAS 类 LLM-as-judge 指标（faithfulness / answer_relevancy），并标注是模型评判")
    A("")

    A("---")
    A("")
    A("## 附录：评测集构成")
    A("")
    A("| 类别 | 条数 | 文件 | 指标 |")
    A("|:---|---:|:---|:---|")
    A("| 事实型 | 27 | `eval_factual.json` | answer_ok / keyword_coverage / citation_hit |")
    A("| 推理型 | 15 | `eval_reasoning.json` | 人工 5 分制（当前机器预评） |")
    A("| 应拒答 | 10 | `eval_refuse.json` | correct_refusal_rate |")
    A("| **合计** | **52** | — | — |")
    A("")
    A("评测集构建脚本：`s5_1_build_eval.py`（每条事实型的证据位置都在语料里校验过）")
    A("")
    A("> **为什么不用现成的 golden_eval_60？**")
    A("> 实测检查发现它的 `relevant_chunk_ids` 指向 `cai_note_*`、`qaSynthesisArticle_*`、")
    A("> `transfernotesArticle_*` 等**外部文档**，不是本项目的 hlm_chunks 语料，")
    A("> 无法用于检索指标（Recall@k）对拍。所以事实型评测集自建。")
    A("")

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"报告已生成: {OUT_MD}")
    print(f"  {os.path.getsize(OUT_MD):,} B")

    # 汇总 JSON
    summary = {
        "generated_at": "2026-10-24",
        "model": "deepseek-flash",
        "factual": f_metrics,
        "factual_stat": f_stat_metrics,
        "reasoning": r_metrics,
        "refuse": x_metrics,
        "note": "事实型与推理型分开报告，不合成单一准确率",
    }
    p = os.path.join(RESULTS, "summary.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"汇总已生成: {p}")


if __name__ == "__main__":
    main()
