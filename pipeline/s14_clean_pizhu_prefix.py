# -*- coding: utf-8 -*-
"""
S14 · 脂批大全前缀噪音清理
==========================

问题盘点（实测 9,156 条中的 392 条）
----------------------------------
| 类型 | 条数 | 样例 |
|:---|---:|:---|
| 符号噪音 `X~Y)` | 351 | `死~故)一段石火光阴…` |
| 非汉字开头 | 33  | `”度脱“，请问是幻不是幻？` |
| 数字编号 | 8   | `其~起)6.风流真假一般看…` |

根因
----
看 PDF 原文（第 15 页）：

```
〖甲眉、觉双〗
一段妻妾迎新送死，倏恩倏爱、倏痛倏悲，缠绵不了。〖甲眉〗
（〖觉双〗死~故）
一段石火光阴，悲喜不了；风露草霜，富贵嗜欲，贪婪不了。〖甲眉〗
（〖觉双〗风露~一段风露）
一段儿女死后无凭，生前空为筹划计算，痴心不了。〖甲眉、觉双〗
```

`（〖觉双〗死~故）` 是**行间校勘标注**（「觉双」本此字作「故」），
它是独立的一行，不属于批语正文。

旧提取脚本按「批语一行」切分时，把这类校勘标注和下一行批语粘在了一起 →
变成 `死~故)一段石火光阴…`。

修复规则
--------
① `X~Y)` 前缀 → 删除（X~Y 是「原字~改字」，`)` 是括号残留）
   注意：只删**开头**的这类片段，正文中间的 ~ 不动。
② 数字编号 `N.` → 删除（那是诗题序号，如「其六」，批语正文不含序号）
③ 非汉字开头的引号 → 保留（`”度脱“` 是批语正文的引号，不该删）
   但对 `□者当记一大百乎`、`［更借得妙！］` 这类做保守处理

输出：
  zhiyanzhai_pizhu/pizhu.jsonl  原地修复（备份 .bak4）
  zhiyanzhai_pizhu/prefix_report.json  明细

运行：
  python s14_clean_pizhu_prefix.py
"""
import os
import re
import sys
import json
import shutil

sys.stdout.reconfigure(encoding="utf-8")

ROOT = r"D:\Programming\Red\hlm_corpus"
DQUAN = os.path.join(ROOT, "zhiyanzhai_pizhu", "pizhu.jsonl")


def log(m):
    print(m, flush=True)


# 校勘标注：X~Y)  或  X~Y）  （X、Y 各 1~6 字，允许内部有空格）
# 校勘标注前缀：X~Y)  或 X~Y）  或 X~Y（缺右括号）
# 实测样式：`死~故)`、`借用 ~ 借)(`、`未 写 荣 府 正 人 ~ …)`、`识~知)3`
RE_JIAOKAN = re.compile(
    r'^[\u4e00-\u9fff\s]{1,14}[~～][\u4e00-\u9fff\s~～]{1,16}[)）]?\s*')
# 数字编号：行首 N. 或 N、
RE_NUM = re.compile(r'^(\d{1,2})\s*[.。、,，]\s*')
# 前置的方括号包裹（构林/双行夹批标记）
RE_BRACKET = re.compile(r'^\[［][^\]］]{0,20}[\]］]\s*')


def clean_one(t):
    """
    分离「校勘标注」与「批语正文」。

    实测 PDF 结构（第 15 页原文）：

        〖甲眉、觉双〗
        一段妻妾迎新送死，倏恩倏爱、倏痛倏悲，缠绵不了。〖甲眉〗
        （〖觉双〗死~故）                        ← 校勘标注，独立一行
        一段石火光阴，悲喜不了；风露草霜，富贵嗜欲，贪婪不了。〖甲眉〗
        （〖觉双〗风露~一段风露）                ← 校勘标注

    旧提取把「标注」和「下一行正文」粘在一起 → `死~故)一段石火光阴…`

    返回 (正文, 应用的规则)
      · 若检测到 `X~Y)` 前缀 → 删掉前缀，返回后面的正文
      · 若整条就是标注（无正文）→ 返回空串
    """
    orig = t
    rules = []

    # ---- ① 校勘标注前缀：X~Y) / X~Y） / X~Y（可能多个并列） ----
    for _ in range(5):
        m = RE_JIAOKAN.match(t)
        if not m:
            break
        rest = t[m.end():].strip()
        # 若剩余部分还是「X~Y)」形式，继续
        t = rest
        rules.append('jiaokan')

    # ---- 若清理后为空 → 整条是标注，无正文 ----
    if not t:
        return '', rules

    # ---- ② 数字编号前缀 ----
    m = RE_NUM.match(t)
    if m and len(t) > m.end() + 2:
        t = t[m.end():]
        rules.append('num')

    # ---- ③ 尾部噪音清理 ----
    # a) 尾部孤立的 ( （ ） 等括号
    t2 = re.sub(r'[\s]*[(（\[【]+\s*$', '', t)
    if t2 != t:
        t = t2
        rules.append('tail_bracket')

    # b) 尾部以 ~ 结尾的截断标注：`…也 ~ 手笔哉)(` → 删到最后一个完整句末
    if re.search(r'[~～]', t) and not re.search(r'[。！？][^。！？]*$', t):
        # 找最后一个句末标点，后面若含 ~ 则截断
        m2 = list(re.finditer(r'[。！？]', t))
        if m2:
            last = m2[-1]
            tail = t[last.end():]
            if re.search(r'[~～]', tail):
                t = t[:last.end()]
                rules.append('tail_jiaokan')

    # c) 尾部残留的「,X~Y)」形式
    t2 = re.sub(r'[,，][^,，。！？]{0,12}[~～][^,，。！？]{0,12}[)）]?\s*$', '', t)
    if t2 != t:
        t = t2
        rules.append('tail_inline')

    # d) 开头孤立的标点（如 `,谓冷中…`）
    t2 = re.sub(r'^[,，、;；:：\s]+', '', t)
    if t2 != t:
        t = t2
        rules.append('lead_punct')

    return t.strip(), rules


def main():
    log("=" * 74)
    log("S14 脂批大全前缀噪音清理")
    log("=" * 74)

    rows = [json.loads(l) for l in open(DQUAN, encoding='utf-8')]
    log(f"总条数: {len(rows):,}")

    stat = {'jiaokan': 0, 'num': 0, 'bracket': 0}
    changed = []
    dropped = []

    def is_residue(t):
        """clean_one 返回空 / 只剩符号 → 整条删除"""
        t2 = re.sub(r'[\s()（）\[\]【】\d~～.。,，、;；:：]+', '', t or '')
        return not t2

    for r in list(rows):
        t = r.get('text') or ''
        nt, rules = clean_one(t)
        if nt == t:
            continue
        # 有正文 → 只替换文本；无正文 → 整条删除
        if nt and not is_residue(nt):
            r['text'] = nt
            r['prefix_cleaned'] = True
            changed.append({'hui': r['hui'], 'old': t, 'new': nt,
                            'rules': rules})
            for k in rules:
                stat[k] = stat.get(k, 0) + 1
        else:
            dropped.append({'hui': r['hui'], 'text': t})
            rows.remove(r)

    log(f"\n清理 {len(changed):,} 条")
    log(f"  其中「清理后是残渣」整条删除 {len(dropped)} 条")
    for k, v in stat.items():
        log(f"  {k}: {v} 处")

    log("\n=== 样例（前 12 条）===")
    for x in changed[:12]:
        log(f"  第{x['hui']}回 [{'/'.join(x['rules'])}]")
        log(f"    旧: {x['old'][:70]}")
        log(f"    新: {x['new'][:70]}")

    if not changed:
        log("无变化")
        return

    bak = DQUAN + '.bak4'
    if not os.path.exists(bak):
        shutil.copyfile(DQUAN, bak)
        log(f"\n备份: {bak}")

    with open(DQUAN, 'w', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    log(f"已写回 {DQUAN}")

    # 复查
    left = 0
    for r in rows:
        t = r.get('text') or ''
        if RE_JIAOKAN.match(t) or (RE_NUM.match(t) and len(t) > 6):
            left += 1
    log(f"\n复查：仍带前缀噪音 {left} 条")
    tot = sum(len(r['text']) for r in rows)
    bad = sum(1 for r in rows if '\ufffd' in (r.get('text') or ''))
    log(f"总字数: {tot:,}   含 U+FFFD: {bad} 条")

    with open(os.path.join(ROOT, "zhiyanzhai_pizhu", "prefix_report.json"),
              'w', encoding='utf-8') as f:
        json.dump({'n_changed': len(changed), 'by_rule': stat,
                   'samples': changed[:100]}, f, ensure_ascii=False, indent=1)
    log(f"报告: prefix_report.json")


if __name__ == '__main__':
    main()
