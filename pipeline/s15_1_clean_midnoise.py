# -*- coding: utf-8 -*-
"""
S15-1 · 脂批大全「中间混入校勘标注」清理
========================================

问题
----
S14 已处理了两类：
  ① 纯标注（整条就是 X~Y）        → 删除 131 条
  ② 标注在开头（X~Y)正文）         → 删前缀 117 条

剩 118 条是**标注混在中间**，形态如下：

```
族大人〜□□□ ,明〜□ ,则〜□ ,文字 ~ 文事)(       ← 全是标注，无正文
荣府 ~ 荣府正人,然后一一〜一一,奴仆〜一仆,拮据之笔〜之笔,手中之物也 ~ 手笔哉)(
〔疒赘〕瘰 ~ 累赘)开笔即写贾夫人已死,是特使黛玉入荣之速也。(       ← 标注 + 正文
写~骂,马儿好~马好)注2:【列、杨】在回目之后则只有一首诗,而无批语。…  ← 标注 + 注文
身世~身分。)补尼道一段,又伏一案。                              ← 标注 + 正文
```

关键观察
--------
**标注段总是以 `)` 或 `）` 结束**，`)` 之后才是正文。
括号 `（` 在 PDF 里是标注的起始符，转文本时部分丢失，部分残留。

所以：**从开头往后找第一个 `)`，把它之前（含标注）全部丢弃，`）` 之后是正文。**

但要防误伤：正文里也可能有 `)`（如「（归案?)」），所以要求：
  · `)` 前面必须出现过 `~`
  · `)` 之后的剩余长度 ≥ 4

清理策略
--------
```
输入:  X~Y,Z~W)正文
       └── 前缀标注 ──┘
输出:  正文
```

若整条在 `)` 之前结束（无正文）→ 整条删除。
若找不到 `)` → 不动（保守）。

运行：
  python s15_1_clean_midnoise.py
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


def clean(t):
    """
    返回 (新文本, 动作)
      动作: 'drop' | 'trim' | 'keep'
    """
    if not re.search(r'[~～]', t):
        return t, 'keep'

    # 找第一个分隔符：) ） } ｝
    m = re.search(r'[)）}｝]', t)
    if not m:
        # 无分隔符：尝试「标注串 + 正文」的启发式切分
        # 特征：开头是 X~Y,X~Y,… 形式的连续标注，用逗号分隔，
        #       一旦某段里没有 ~ 且长度较长，那就是正文开始
        parts = re.split(r'(?<=[^~～]),', t)
        # 逐段检查：前几段都含 ~ ⇒ 是标注
        k = 0
        for i, seg in enumerate(parts):
            if re.search(r'[~～]', seg):
                k = i + 1
            else:
                break
        if 0 < k < len(parts):
            rest2 = ','.join(parts[k:]).strip()
            if len(rest2) >= 6 and re.search(r'[\u4e00-\u9fff]{4,}', rest2):
                return rest2, 'trim'
            # 剩余太短（如「”私”字。」）→ 整条视为标注，删
            return '', 'drop'
        # 整条都是标注 → 删
        if k == len(parts) and all(re.search(r'[~～]', x) for x in parts):
            return '', 'drop'
        # 每一段都含 ~ 且整条不长 → 纯标注串，删
        if all(re.search(r'[~～]', x) for x in parts) and len(t) <= 80:
            return '', 'drop'
        # 编者说明（含「说明N」「批语原文录自」「固定格式」）→ 保留
        if re.search(r'(说明\s*\d|批语原文录自|固定格式)', t):
            return t, 'keep'
        # 单段纯标注（如「瞒~〔貝艹两〕」「辉~〔火军〕。」）
        if re.fullmatch(r'[^,，]{1,20}[~～][^,，]{1,20}[。]?', t.strip()):
            return '', 'drop'
        return t, 'keep'

    head, rest = t[:m.start()], t[m.end():]

    # 要求 head 里确实有 ~（校勘标注特征）
    if not re.search(r'[~～]', head):
        return t, 'keep'

    rest = rest.lstrip(' ,，、;；:：')
    # 去掉可能残留的前导括号
    rest = re.sub(r'^[)）(（\s]+', '', rest)

    if not rest or not re.search(r'[\u4e00-\u9fff]', rest):
        return '', 'drop'          # 无正文
    if len(rest) < 4:
        # 很短但含汉字：如「应前葫芦案。」(6字) ok；「是蕉。」类不适合
        if not re.search(r'[。！？]', rest):
            return '', 'drop'
    return rest, 'trim'


def main():
    log("=" * 74)
    log("S15-1 脂批大全「中间混入校勘标注」清理")
    log("=" * 74)

    rows = [json.loads(l) for l in open(DQUAN, encoding='utf-8')]
    n0 = len(rows)
    log(f"总条数: {n0:,}")

    dropped, trimmed, kept = [], [], []
    for r in list(rows):
        t = r.get('text') or ''
        if not re.search(r'[~～]', t):
            continue
        nt, act = clean(t)
        if act == 'drop':
            dropped.append({'hui': r['hui'], 'text': t})
            rows.remove(r)
        elif act == 'trim':
            r['text'] = nt
            r['midnoise_cleaned'] = True
            trimmed.append({'hui': r['hui'], 'old': t, 'new': nt})
        else:
            kept.append({'hui': r['hui'], 'text': t})

    log(f"\n  删除（无正文）: {len(dropped)} 条")
    log(f"  裁掉标注保留正文: {len(trimmed)} 条")
    log(f"  保持原样: {len(kept)} 条")

    log("\n=== 裁剪样例 ===")
    for x in trimmed[:10]:
        log(f"  第{x['hui']}回")
        log(f"    旧: {x['old'][:72]}")
        log(f"    新: {x['new'][:72]}")

    log("\n=== 保持原样（需人工判断）===")
    for x in kept[:10]:
        log(f"  第{x['hui']}回 {x['text'][:70]}")

    if not dropped and not trimmed:
        log("无变化")
        return

    bak = DQUAN + '.bak6'
    if not os.path.exists(bak):
        shutil.copyfile(DQUAN, bak)
        log(f"\n备份: {bak}")

    with open(DQUAN, 'w', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    log(f"已写回 {DQUAN}")

    # 复查
    left = [r for r in rows if re.search(r'[~～]', r.get('text') or '')]
    log("\n" + "=" * 74)
    log("复查")
    log("=" * 74)
    log(f"  条数: {n0:,} → {len(rows):,}")
    log(f"  仍含 ~ : {len(left)} 条")
    log(f"  总字数: {sum(len(r['text']) for r in rows):,}")
    log(f"  含 U+FFFD: {sum(1 for r in rows if chr(0xfffd) in (r.get('text') or ''))} 条")

    with open(os.path.join(ROOT, "zhiyanzhai_pizhu", "midnoise_report.json"),
              'w', encoding='utf-8') as f:
        json.dump({'n_before': n0, 'n_after': len(rows),
                   'dropped': dropped, 'trimmed': trimmed, 'kept': kept},
                  f, ensure_ascii=False, indent=1)
    log(f"  报告: midnoise_report.json")


if __name__ == '__main__':
    main()
