# -*- coding: utf-8 -*-
"""
S15-3 · 抚琴居校本剩余 35 条短批语续接
======================================

这 35 条都以「，」或「之/是/此」等未完成词结尾，说明后面还有文字，
但都没超过 30 字，所以 S15-2 没处理。

做法
----
对每条：
  1. 用现有文本**末尾 6-10 字**在 PDF 里定位（跨页查找）
  2. 从该位置往后**继续收集楷体（KaiTi）原语**，直到遇到宋体正文
  3. 只接受「新增部分含句末标点」或「新增 ≥4 字」的结果
  4. 与原文本拼接后仍要能通过校验（原文本必须是新文本前缀）

安全阀
------
  · 若 PDF 里找不到定位点 → 跳过
  · 若新增内容为空 → 跳过
  · 若新增内容含 U+FFFD 或 □□ → 跳过
  · 每条修复都记录 old→new，可人工复核

运行：
  python s15_3_extend_short_pizhu.py
  python s15_3_extend_short_pizhu.py --dry   # 只看不写
"""
import os
import re
import sys
import json
import shutil

sys.stdout.reconfigure(encoding="utf-8")

ROOT = r"D:\Programming\Red\hlm_corpus"
FUQ = os.path.join(ROOT, "fuqinju", "pizhu.jsonl")
PDF = r"D:\Programming\Red\抚琴居校本（簡體大字本）.pdf"

SUS = "，、；：的之与和以在是为把被从对向于其此则也"
MAX_LOOKAHEAD_PAGES = 3


def log(m):
    print(m, flush=True)


def main():
    dry = '--dry' in sys.argv

    from pypdf import PdfReader
    log("=" * 74)
    log("S15-3 抚琴居剩余短批语续接" + ("（DRY RUN）" if dry else ""))
    log("=" * 74)

    rows = [json.loads(l) for l in open(FUQ, encoding='utf-8')]
    sus = [r for r in rows if r['text'] and r['text'][-1] in SUS]
    log(f"总条数 {len(rows):,}   待处理 {len(sus)} 条")

    reader = PdfReader(PDF)

    # 缓存每页的楷体原语序列
    cache = {}

    def ops_of(pi):
        if pi in cache:
            return cache[pi]
        out = []

        def vis(text, cm, tm, font, size, _o=out):
            t = (text or '')
            if not t.strip():
                return
            bf = re.sub(r'^/[A-Z]{6}\+', '', str(font.get('/BaseFont', '?'))).lstrip('/')
            _o.append((round(tm[5], 2), round(tm[4], 2), bf, t))

        try:
            reader.pages[pi].extract_text(visitor_text=vis)
        except Exception:
            pass
        out.sort(key=lambda z: (-z[0], z[1]))
        out = [z for z in out if 58 <= z[0] <= 755]
        cache[pi] = out
        return out

    def is_kai(bf):
        return ('KaiTi' in bf) or ('SimHei' in bf)

    def extend(tail_text, start_page):
        """
        用 tail_text 末尾若干字在 PDF 定位，往后收集连续楷体（批语）。

        改进点（相对旧版）：
          ① 定位后**跨页**继续（批语可能跨页）
          ② 同行右侧宋体 = 双行夹批里的正文 → 跳过，不中断
          ③ 不同行宋体 = 真正正文 → 停止
        """
        n = norm(tail_text)
        for nlen in (12, 10, 8, 6, 5):
            if len(n) < nlen:
                continue
            anchor = n[-nlen:]
            # 只在该条所在页附近查（±2 页），避免全文扫描太慢
            lo = max(0, start_page - 1)
            hi = min(len(reader.pages), start_page + 4)
            for pi in range(lo, hi):
                ops = ops_of(pi)
                for i, (y, x, bf, t) in enumerate(ops):
                    tt = re.sub(r'\s+', '', t)
                    if anchor not in tt:
                        continue
                    pos = tt.find(anchor) + len(anchor)

                    collected = []          # 收集到的每段文字
                    cur_seg = tt[pos:]
                    if cur_seg:
                        collected.append(cur_seg)

                    last_y, last_x = y, x
                    # 从当前原语之后开始，最多跨 2 页
                    for pj in range(pi, min(pi + 3, len(reader.pages))):
                        ops2 = ops_of(pj)
                        start_k = (i + 1) if pj == pi else 0
                        for k in range(start_k, len(ops2)):
                            ny, nx, nbf, nt = ops2[k]
                            ntt = re.sub(r'\s+', '', nt)
                            if not ntt:
                                continue
                            if 'EUDC' in nbf:
                                # 新批语代号：若已收集到内容则结束，否则忽略
                                if ''.join(collected).strip():
                                    return ''.join(collected)
                                continue
                            if is_kai(nbf):
                                collected.append(ntt)
                                last_y, last_x = ny, nx
                                continue
                            # 宋体：同行靠右 → 双行夹批的正文，跳过继续
                            if last_y is not None and abs(ny - last_y) < 1.5 \
                                    and nx > last_x + 15:
                                continue
                            # 同行但很靠左（x 接近页边 54）→ 可能是下一段正文，结束
                            if last_y is not None and abs(ny - last_y) < 1.5:
                                return ''.join(collected)
                            # 不同行的宋体：这是正文行。
                            # **不立即结束** —— 继续往后找，看是否还有楷体续文
                            # （双行夹批常夹在正文行之间）
                            continue
                        # 换页时重置行位置基准
                        last_y = None
                    return ''.join(collected)
        return ''

    def norm(t):
        return re.sub(r'[\s　]+', '', str(t or ''))

    fixed, skipped = [], []
    for r in sus:
        page = r.get('page')
        if page is None:
            skipped.append((r, 'no page'))
            continue
        extra = extend(r['text'], page)
        extra = extra.strip()

        # ---- 校验 ----
        if not extra:
            skipped.append((r, 'no continuation'))
            continue
        if '\ufffd' in extra or '□□' in extra or '〔' in extra:
            skipped.append((r, 'bad char'))
            continue
        if len(extra) < 3:
            skipped.append((r, 'too short'))
            continue
        # 续接内容不应以「，」开头
        if extra[0] in '，、；：':
            extra = extra[1:]
        if not extra:
            skipped.append((r, 'empty after strip'))
            continue

        # ---- 防止把「独立的下一行批语」误合并 ----
        # ① 原文本以冒号结尾（「谚云：」）→ 后面是引文，属另一条
        if r['text'].rstrip().endswith(('：', ':')):
            skipped.append((r, 'ends with colon'))
            continue
        # ② 续文以引号/书名号/括号开头 → 新的独立内容
        if extra[0] in '“”"「」『』《》【】〔〕()（）':
            skipped.append((r, 'starts with quote/bracket'))
            continue
        # ③ 续文以 ◇ 开头 → 明确的批语分隔符
        if extra.startswith('◇'):
            skipped.append((r, 'starts with diamond'))
            continue

        old = r['text']
        new = old + extra
        # 防止重复（PDF 有时会给出已包含的部分）
        if old.endswith(extra[:8]):
            skipped.append((r, 'overlap'))
            continue

        r['text'] = new
        r['repaired4'] = True
        fixed.append({'hui': r['hui'], 'page': page,
                      'old_len': len(old), 'new_len': len(new),
                      'old': old, 'new': new})

    log(f"\n  可修复 {len(fixed)} 条")
    log(f"  跳过   {len(skipped)} 条")

    log("\n=== 修复样例 ===")
    for x in fixed[:20]:
        log(f"  第{x['hui']:>2}回 {x['old_len']}→{x['new_len']}字")
        log(f"    旧: {x['old']}")
        log(f"    新: {x['new']}")

    log("\n=== 跳过原因统计 ===")
    import collections
    c = collections.Counter(reason for _, reason in skipped)
    for k, v in c.most_common():
        log(f"  {k}: {v}")

    if dry or not fixed:
        log("\n（未写入）")
        return

    bak = FUQ + '.bak4'
    if not os.path.exists(bak):
        shutil.copyfile(FUQ, bak)
        log(f"\n备份: {bak}")

    with open(FUQ, 'w', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    log(f"已写回 {FUQ}")

    left = [r for r in rows if r['text'] and r['text'][-1] in SUS]
    log("\n" + "=" * 74)
    log("复查")
    log("=" * 74)
    log(f"  疑似截断: {len(sus)} → {len(left)} 条")
    log(f"  总字数: {sum(len(r['text']) for r in rows):,}")
    log(f"  含 U+FFFD: {sum(1 for r in rows if chr(0xfffd) in r['text'])} 条")

    with open(os.path.join(ROOT, "fuqinju", "extend_report.json"),
              'w', encoding='utf-8') as f:
        json.dump({'n_fixed': len(fixed), 'fixed': fixed,
                   'skipped': [{'hui': r['hui'], 'reason': s,
                                'text': r['text']} for r, s in skipped]},
                  f, ensure_ascii=False, indent=1)
    log(f"  报告: extend_report.json")


if __name__ == '__main__':
    main()
