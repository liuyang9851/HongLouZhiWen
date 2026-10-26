# -*- coding: utf-8 -*-
"""
S14-2 · 脂批大全前缀噪音清理（保守版）
=====================================

背景
----
脂批大全 9,156 条里有约 116 条带「校勘标注」噪音。
PDF 原文（第 15 页）结构：

    〖甲眉、觉双〗
    一段妻妾迎新送死，倏恩倏爱、倏痛倏悲，缠绵不了。〖甲眉〗
    （〖觉双〗死~故）                    ← 校勘标注，独立一行
    一段石火光阴，悲喜不了；风露草霜，富贵嗜欲，贪婪不了。〖甲眉〗
    （〖觉双〗风露~一段风露）            ← 校勘标注

`X~Y` 表示「X 改作 Y」（底本作 X，校本作 Y）。
旧提取把「标注」和「下一行正文」粘在一起 → `死~故)一段石火光阴…`

**前几版失败教训（重要）**
------------------------
| 版本 | 错误 | 后果 |
|:---|:---|:---|
| v1 | 用「长度短」判断残渣 | 误删 617 条正常短批（「伏笔。」「细。」） |
| v2 | 清理后整条删 | 把「标注+正文」的正文一起删了（316 条） |
| v3 | 加尾部清理，正则过宽 | 复查又冒出 122 条新噪音 |

**本版原则：宁可少修，不可误删。**

只做两件确定安全的事：
  ① 整条就是 `X~Y` / `X~Y)(` 形式（无正文）→ **整条删除**（35 条）
  ② 以 `X~Y)` 开头且后面有正文 → **只删前缀**（81 条）

其余形态一律不动（保留原样，在报告里列出）。

运行：
  python s14_2_clean_prefix_safe.py
"""
import os
import re
import sys
import json
import shutil

sys.stdout.reconfigure(encoding="utf-8")

ROOT = r"D:\Programming\Red\hlm_corpus"
DQUAN = os.path.join(ROOT, "zhiyanzhai_pizhu", "pizhu.jsonl")

# 校勘标注本体：X~Y  或  X~Y)  或 X~Y)(
# X、Y 各 1~16 字（可含空格），中间必须是 ~ 或 ～
JK_BODY = r'[\u4e00-\u9fff\s]{1,16}[~～][\u4e00-\u9fff\s~～]{1,18}'
# 纯标注（整条就是标注，允许尾部有孤立括号）
RE_PURE = re.compile(r'^\s*[(（]?\s*' + JK_BODY + r'\s*[)）]?\s*[(（]?\s*$')
# 标注前缀（后面还有正文）
RE_PREFIX = re.compile(r'^\s*[(（]?\s*' + JK_BODY + r'\s*[)）]\s*')


def log(m):
    print(m, flush=True)


def main():
    log("=" * 74)
    log("S14-2 脂批大全前缀噪音清理（保守版）")
    log("=" * 74)

    rows = [json.loads(l) for l in open(DQUAN, encoding='utf-8')]
    n0 = len(rows)
    log(f"总条数: {n0:,}")

    dropped, prefixed, untouched = [], [], []

    for r in list(rows):
        t = r.get('text') or ''

        # ---- ① 纯标注 → 整条删除 ----
        if RE_PURE.match(t):
            dropped.append({'hui': r['hui'], 'text': t})
            rows.remove(r)
            continue

        # ---- ② 标注前缀 → 只删前缀 ----
        m = RE_PREFIX.match(t)
        if m:
            rest = t[m.end():].strip()
            # 剩余必须够长，且不是纯标注（防误删）
            # 判定剩余是否为「有效正文」：
            #   · 长度 ≥4                      → 有效
            #   · 长度 2~3 但含句末标点         → 有效（如「是蕉。」「也好。」）
            #   · 只剩数字/符号                → 无效（删除）
            has_end_punct = bool(re.search(r'[。！？]', rest))
            has_word = bool(re.search(r'[\u4e00-\u9fff]', rest))
            ok_rest = (
                (len(rest) >= 4 or (len(rest) >= 2 and has_end_punct))
                and has_word
                and not RE_PURE.match(rest)
            )
            if ok_rest:
                r['text'] = rest
                r['prefix_cleaned'] = True
                prefixed.append({'hui': r['hui'], 'old': t, 'new': rest})
                continue

        # ---- ②b 前缀后面只剩数字/符号 → 整条是噪音，删除 ----
        if m:
            rest2 = t[m.end():].strip()
            if not re.search(r'[\u4e00-\u9fff]', rest2):
                dropped.append({'hui': r['hui'], 'text': t})
                rows.remove(r)
                continue

        # ---- ③ 其他形态（含 ~）----
        if re.search(r'[~～]', t):
            # 判断是否「只有标注、无正文」：
            #   去掉所有「X~Y」片段 + □…□ + 括号 + 数字 + 标点/空格后，
            #   若剩余汉字 ≤3 → 无实质正文 → 删除
            stripped = re.sub(JK_BODY + r'[)）]?', '', t)
            stripped = re.sub(r'[□\s()（）\[\]【】\d.。,，、;；:：~～?？!！]+', '', stripped)
            if len(stripped) <= 3:
                dropped.append({'hui': r['hui'], 'text': t})
                rows.remove(r)
                continue
            untouched.append({'hui': r['hui'], 'text': t})

    log(f"\n  ① 纯标注删除      : {len(dropped)} 条")
    log(f"  ② 前缀删除保留正文: {len(prefixed)} 条")
    log(f"  ③ 未处理（中间/结尾含~）: {len(untouched)} 条")

    log("\n=== ② 样例（前 8 条）===")
    for x in prefixed[:8]:
        log(f"  第{x['hui']}回")
        log(f"    旧: {x['old'][:66]}")
        log(f"    新: {x['new'][:66]}")

    log("\n=== ① 样例（前 8 条，应全是纯标注）===")
    for x in dropped[:8]:
        log(f"  第{x['hui']}回 {x['text']!r}")

    if not dropped and not prefixed:
        log("无变化")
        return

    bak = DQUAN + '.bak5'
    if not os.path.exists(bak):
        shutil.copyfile(DQUAN, bak)
        log(f"\n备份: {bak}")

    with open(DQUAN, 'w', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    log(f"已写回 {DQUAN}")

    # ---- 复查 ----
    log("\n" + "=" * 74)
    log("复查")
    log("=" * 74)
    log(f"  条数: {n0:,} → {len(rows):,}（删 {n0 - len(rows)}）")
    log(f"  总字数: {sum(len(r['text']) for r in rows):,}")
    log(f"  含 U+FFFD: {sum(1 for r in rows if chr(0xfffd) in (r.get('text') or ''))} 条")

    still_pure = sum(1 for r in rows if RE_PURE.match(r.get('text') or ''))
    still_pref = sum(1 for r in rows if RE_PREFIX.match(r.get('text') or ''))
    log(f"  仍以纯标注开头: {still_pure} 条")
    log(f"  仍以标注前缀开头: {still_pref} 条")

    with open(os.path.join(ROOT, "zhiyanzhai_pizhu", "prefix_report.json"),
              'w', encoding='utf-8') as f:
        json.dump({'n_before': n0, 'n_after': len(rows),
                   'dropped': dropped, 'prefixed': prefixed,
                   'untouched': untouched}, f, ensure_ascii=False, indent=1)
    log(f"  报告: prefix_report.json")


if __name__ == '__main__':
    main()
