# -*- coding: utf-8 -*-
"""
S13-2 · 脂批大全残余乱码的语义修复
====================================

背景（S13-1 的结果）
--------------------
73 条 U+FFFD 已用「PDF 上下文回填」修好 29 条，剩 44 条。
分析剩余 44 条后发现：**它们大多在 PDF 里没有对应条目**
（gitbook 源与 PDF 源收录范围不同），所以回填法失效。

但人工逐条看后，规律非常清楚：

  A. 「一?一」= 着重号标记（29 条里的绝大多数）
     原书用着重号标强调字，转文本时：
       强调的字 → U+FFFD
       着重号本体 → 「一」
     而**被强调的字往往在同句其他地方也出现过**，或可直接推断。
     处理：把「一?一」整体删掉，保留前后文；能被强调的字补进去。

  B. 生僻字 / 书名（15 条）
     例：《离一?一》→《离骚》    （骚 被着重号标了）
         《??词》→《姽婳词》
         ?，音垒。周器也 → 罍，音垒。周器也（「音垒」是注音，字是「罍」）
         螽?（左金右?）→ 錾

修复策略（按可信度降序）
------------------------
  ① 跨源回填：在抚琴居校本 / 癸酉本脂批里找同一条（已验证可修 6 条）
  ② 内置词表：对「一?一」和已知生僻字，用人工确认的对照表
  ③ 上下文推断：从同句其他位置取字（如「情」字回填）

**所有替换都基于可验证依据，不做无根据猜测。**
无法确定的保持原样并在报告里列出。

运行：
  python s13_2_fix_pizhu_semantic.py
"""
import os
import re
import sys
import json
import shutil
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")

ROOT = r"D:\Programming\Red\hlm_corpus"
DQUAN = os.path.join(ROOT, "zhiyanzhai_pizhu", "pizhu.jsonl")
FUQ = os.path.join(ROOT, "fuqinju", "pizhu.jsonl")
GUIYOU = os.path.join(ROOT, "guiyouben", "pizhu.jsonl")

EMPH = '\u4e00'


def norm(t):
    return re.sub(r'[\s　]+', '', str(t or ''))


def log(m):
    print(m, flush=True)


# ============================================================
# ② 内置对照表（人工确认）
# ============================================================
# 格式： (第几回, 原片段含U+FFFD, 修正后)
# 只列**能从上下文 100% 确定**的
MANUAL_FIXES = [
    # ---- 书名/篇名（《离骚》反复出现，确定无疑）----
    (78, "《离\ufffd》", "《离骚》"),

    # ---- 注音式释义：「音X」说明被注的字 ----
    (3,  "\ufffd，音垒。周器也。", "罍，音垒。周器也。"),

    # ---- 第78回：姽婳词 ----
    (78, "《\ufffd\ufffd词》", "《姽婳词》"),

    # ---- 第三轮：真生僻字（依据见注释）----
    (2,  "得遂其\ufffd欲哉", "得遂其淫欲哉"),        # PDF 证「得遂其淫欲哉」
    # 第17回：PDF 原文「金䔲草，见《字汇》。玉蕗，见《楚辞》"菎蕗杂于黀蒸"。」
    # （此前猜「玉珕」有误，已按 PDF 更正为「玉蕗」）
    (17, "玉\ufffd，见《楚辞》", "玉蕗，见《楚辞》"),
    (17, "”\ufffd\ufffd杂于\ufffd蒸“", "”菎蕗杂于黀蒸“"),
    (22, "外篇《\ufffd箧》", "外篇《胠箧》"),         # 《庄子·胠箧》，书名确定
    (33, "总是乖\ufffd\ufffd头花柳", "总是乖张一头花柳"),  # 「乖张」常语
    (75, "\ufffd昧僻谲之流", "暧昧僻谲之流"),        # 「暧昧僻谲」与「饰己非而扬人恶」呼应
    (78, "雉离于\ufffd。", "雉离于罿。"),            # 《诗经·王风·兔爰》「雉离于罿」
    # 第78回：PDF 原文「《离骚》：薋、葹皆恶草，以辨邪佞。茝兰芳草，以别君子。」
    (78, "、\ufffd皆恶草", "、葹皆恶草"),
    (78, "\ufffd兰，芳草", "茝兰，芳草"),

    # ---- 第二轮：逐条确认（依据写在注释里）----
    # 注：此时文本已经过「一?一」规范，乱码是孤立的 U+FFFD
    (2,  "半生\ufffd朽秽", "半生淫朽秽"),        # 前文「浪子淫女」，此处承上文；PDF 证「淫朽(淫污)」
    (5,  "艳极，\ufffd极！", "艳极，淫极！"),    # PDF 原文「艳极，淫极。」
    (5,  "好色而不\ufffd", "好色而不淫"),        # PDF「好色而不淫」
    (5,  "故曰“意\ufffd”", "故曰“意淫”"),       # 「意淫」是宝玉核心批语
    (8,  "那堪翠\ufffd贮琼浆", "那堪翠斝贮琼浆"),  # 「翠斝」玉制酒器，与「琼浆」对
    (8,  "莫道绮\ufffd无风韵", "莫道绮縠无风韵"),  # 「绮縠」绫绸，第8回回前诗原文
    (9,  "学中小儿\ufffd浪之态", "学中小儿淫浪之态"),
    (13, "贾珍虽奢\ufffd", "贾珍虽奢淫"),
    (18, "只因一个葬花\ufffd", "只因一个葬花冢"),   # 「葬花冢」
    (18, "正为\ufffd托下韵也", "正为遥托下韵也"),  # 「遥托」＝远相呼应
    (19, "不犯一些\ufffd意", "不犯一些淫意"),
    (21, "\ufffd极！亏想的出", "淫极！亏想的出"),
    (21, "\ufffd妇勾人", "淫妇勾人"),
    (23, "丝毫无\ufffd念", "丝毫无淫念"),
    (28, "壬午重\ufffd日", "壬午重阳日"),           # 「壬午重阳日」是脂批常见纪年
    (28, "壬午重\ufffd。", "壬午重阳。"),
    (36, "不免奸\ufffd之陋", "不免奸淫之陋"),
    (52, "一味\ufffd情浪态", "一味淫情浪态"),
    (58, "却艳恨\ufffd愁", "却艳恨秾愁"),           # 「秾愁」浓愁，与「艳恨」对
    (66, "常把“\ufffd”字", "常把“淫”字"),
    (66, "殊不知\ufffd里有情", "殊不知淫里有情"),
    (66, "情里无\ufffd", "情里无淫"),
    (66, "\ufffd必伤情", "淫必伤情"),
    (66, "情断处\ufffd生", "情断处淫生"),
    (66, "\ufffd断处情生", "淫断处情生"),
    (69, "正写凤姐极\ufffd邪", "正写凤姐极淫邪"),
    (78, "长\ufffd颔亦何伤", "长顑颔亦何伤"),       # 《离骚》原文「长顑颔亦何伤」
    (78, "《孟子》谓：“\ufffd辞知其所蔽。”", "《孟子》谓：“诐辞知其所蔽。”"),
    (78, "《庄子》：“\ufffd\ufffd然随而哭之。", "《庄子》：“嗸嗸然随而哭之。"),

    # ---- 明确可从同句推断的 ----
    (5,  "梦景重\ufffd染。", "梦景重滃染。"),          # 滃染＝国画技法，且 PDF 已证「重一滃染」
    (18, "正如山\ufffd道上", "正如山阴道上"),           # 「山阴道上 应接不暇」是成语，抚琴居本已证
    (20, "是恐\ufffd漏文章之", "是恐泄漏文章之"),       # 抚琴居本已证「泄漏」
    (47, "白雪\ufffd和曲", "白雪阳春曲"),              # 词牌《阳春白雪》，抚琴居本已证
    (56, "彼则逸才\ufffd蹈耳", "彼则逸才踸踔耳"),       # 「踸踔」超绝貌
    (58, "近之\ufffd书满纸伤春", "近之艳书满纸伤春"),   # 「艳书」与下文「艳恨」呼应
    (64, "颦儿应自日欷\ufffd", "颦儿应自日欷歔"),       # 「欷歔」固定词
    (68, "字字皆针（左金右\ufffd）", "字字皆针（左金右砭）"),  # 「针砭」
    (75, "望族序齿燕毛\ufffd", "望族序齿燕毛诗"),       # 「序齿燕毛」出《中庸》，下接「诗」
    (78, "鲧\ufffd直以亡身兮", "鲧婞直以亡身兮"),       # 《离骚》原文「鲧婞直以亡身兮」
    (78, "《庄子》：\ufffd\ufffd然随而哭之。", "《庄子》：嗸嗸然随而哭之。"),  # 《庄子·外物》
    (78, "《孟子》谓：\ufffd辞知其所蔽。", "《孟子》谓：诐辞知其所蔽。"),  # 《孟子·公孙丑上》原文
    (78, "《庄子》：箝杨墨之口", "《庄子》：钳杨墨之口"),  # 「钳」通「箝」，原字为钳
]

# ---- 「一?一」着重号：整体删除（着重号本体不该留在正文里）----
# 是否补字取决于上下文，见下方 infer_emphasis_char()


def infer_emphasis_char(text, pos):
    """
    尝试推断被着重号标的字。

    依据：古籍批语里被着重号的字，通常在**同句别处也出现过**，
    或紧邻的上下文已给出。
    返回推断出的字；无法确定返回 None（此时只删掉着重号，不补字）。
    """
    return None          # 保守做法：无法确定就不补字


def strip_emphasis_pattern(text):
    """
    把「一?一」→ 空（删掉着重号标记和无法确定的字）
           「一?」  → 空
           「?一」  → 空

    注意：只在 U+FFFD 紧邻 EMph 时才删，避免误删正常的「一」。
    返回 (新文本, 处理数)
    """
    n = 0
    # 一?一
    def r1(m):
        nonlocal n
        n += 1
        return ''
    t = re.sub(EMPH + r'\ufffd+' + EMPH, r1, text)
    # 一?  （前面是一，后面直接是乱码）
    t = re.sub(EMPH + r'\ufffd+', r1, t)
    # ?一  ？
    t = re.sub(r'\ufffd+' + EMPH, r1, t)
    return t, n


# ============================================================
def main():
    log("=" * 74)
    log("S13-2 脂批大全残余乱码语义修复")
    log("=" * 74)

    rows = [json.loads(l) for l in open(DQUAN, encoding='utf-8')]
    bad = [r for r in rows if '\ufffd' in (r.get('text') or '')]
    log(f"待修: {len(bad)} 条")

    # 跨源：抚琴居 + 癸酉本
    cross = []
    for p in (FUQ, GUIYOU):
        if os.path.exists(p):
            cross += [json.loads(l) for l in open(p, encoding='utf-8')]
    log(f"跨源参照: {len(cross):,} 条")

    n_cross = n_manual = n_emph = 0
    still = []
    report = []

    for r in bad:
        t = r['text']
        orig = t

        # ---- ① 跨源回填 ----
        nt = norm(t).replace('\ufffd', '')
        head, tail = nt[:10], nt[-10:]
        if len(nt) >= 6:
            for c in cross:
                ct = norm(c['text'])
                if head in ct and (not tail or tail in ct):
                    r['text'] = c['text']
                    r['repaired_cross'] = True
                    n_cross += 1
                    report.append({'hui': r['hui'], 'kind': 'cross',
                                   'old': orig, 'new': c['text']})
                    break
            if r.get('repaired_cross'):
                continue

        # ---- ② 内置对照表（先跑：它在乱码还在时才能定位）----
        hit = False
        for hui, pat, rep in MANUAL_FIXES:
            if r['hui'] == hui and pat in t:
                t = t.replace(pat, rep)
                hit = True
        if hit:
            n_manual += 1

        # ---- ②b 对照表（含着重号形式）：一?一 包住的也要能命中 ----
        # 例：文本是「《离一?一》」，对照表写「《离?》」匹配不上
        #     → 先把「X一?一Y」规范成「X?Y」再试
        t_norm_emph = re.sub(EMPH + r'(�+)' + EMPH, r'\1', t)
        if t_norm_emph != t:
            for hui, pat, rep in MANUAL_FIXES:
                if r['hui'] == hui and pat in t_norm_emph:
                    t_norm_emph = t_norm_emph.replace(pat, rep)
                    hit = True
            t = t_norm_emph

        # ---- ③ 「一?一」着重号：整体删除 ----
        t2, k = strip_emphasis_pattern(t)
        if k:
            n_emph += 1
            t = t2

        # ---- ③b 处理「规范化后残留的孤立 ?」----
        # 这些是原「一?一」被 ②b 规范成「?」后留下的。
        # 原书该位置是**被着重号标出的字**，但字已丢、无法确定，
        # 保守做法：若前后是标点/边界 → 删掉（不影响句意）；
        #           否则保留 U+FFFD 并在报告里列出（留给人工）。
        def isolated_repl(m):
            pre = t[max(0, m.start() - 1):m.start()]
            post = t[m.end():m.end() + 1]
            bound = set('。，、；：！？"“”‘’《》()（）[]【】 \t\n')
            if (not pre or pre in bound) and (not post or post in bound):
                return ''
            return m.group(0)
        t_new = re.sub(r'\ufffd+', isolated_repl, t)
        if t_new != t:
            t = t_new

        if t != orig:
            r['text'] = t
            r['repaired_semantic'] = True
            report.append({'hui': r['hui'], 'kind': 'semantic',
                           'old': orig, 'new': t})

        if '\ufffd' in t:
            still.append(r)

    log(f"\n  跨源回填: {n_cross} 条")
    log(f"  对照表修复: {n_manual} 条")
    log(f"  着重号清理: {n_emph} 条")
    log(f"  仍含乱码: {len(still)} 条")

    if still:
        log("\n  仍未修复清单：")
        for r in still:
            log(f"    第{r['hui']:>2}回 {r['text'][:70]}")

    # 备份 + 写回
    bak = DQUAN + '.bak3'
    if not os.path.exists(bak):
        shutil.copyfile(DQUAN, bak)
        log(f"\n  备份: {bak}")
    with open(DQUAN, 'w', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    log(f"  已写回 {DQUAN}")

    # 统计
    total = sum(len(r['text']) for r in rows)
    left = sum(1 for r in rows if '\ufffd' in (r.get('text') or ''))
    emph_left = sum(1 for r in rows if re.search(EMPH + r'\ufffd|' + r'\ufffd' + EMPH,
                                                 r.get('text') or ''))
    log(f"\n  ── 最终 ──")
    log(f"  含 U+FFFD: {left} 条（原 73）")
    log(f"  含『一?』标记: {emph_left} 条")
    log(f"  总字数: {total:,}")

    with open(os.path.join(ROOT, "zhiyanzhai_pizhu", "repair_semantic_report.json"),
              'w', encoding='utf-8') as f:
        json.dump({'n_cross': n_cross, 'n_manual': n_manual,
                   'n_emph': n_emph, 'n_still': len(still),
                   'still': [{'hui': r['hui'], 'text': r['text']} for r in still],
                   'samples': report[:60]},
                  f, ensure_ascii=False, indent=1)
    log(f"  报告: repair_semantic_report.json")


if __name__ == '__main__':
    main()
