# -*- coding: utf-8 -*-
"""
S13 · 脂批大全乱码修复 + 抚琴居残余截断修复
==========================================

一、脂批大全 U+FFFD 乱码（73 条）
--------------------------------
盘点结论：
  · 73 条**全部来自 gitbook_ch 源**（`gitbook-hongloumeng-master/ch/*.md`）
  · 源文件本身就有 U+FFFD（实测码点 0xfffd 直接存在），不是提取造成的
  · 脂批大全 PDF（`脂批大全 简体版 20241109.pdf`）里同样的条目是**完好的**

两类乱码：
  A. 「一�一」模式（48 条）：原书用**着重号**标出的单字，转文本时丢了字、只留下哑标记
     例：`浪子一�一女`  → PDF 原文 `浪子淫女`（「淫」被加了着重号）
         `秦可卿一�一丧天香楼` → PDF `秦可卿淫丧天香楼`
  B. 其他（25 条）：生僻字丢失
     例：`��之悲` → `鹡鸰之悲`
         `《��箧》` → `《胠箧》`

修复方法：
  以 PDF 全文为参照，用「乱码前后各 N 字的上下文」在 PDF 里定位，
  取出该位置的真实字形回填。

二、抚琴居校本残余截断（50 条）
------------------------------
S12 修复了 4 种截断模式（补字/跨页/双行夹批/行内标记），仍有 50 条疑似截断。
本轮改进：
  · 放宽「双行夹批」的 x 阈值（60 → 可配置，部分版面偏移更小）
  · 允许批语在「同 y 右侧宋体」之后继续接「下一行 x 靠左的楷体」
  · 对「1 字残尾」做二次合并（短尾往往是上一行的溢出）

运行：
  python s13_1_fix_pizhu_all.py --task both
  python s13_1_fix_pizhu_all.py --task daquan     # 只修脂批大全
  python s13_1_fix_pizhu_all.py --task fuqinju    # 只修抚琴居残余
"""
import os
import re
import sys
import json
import shutil
import argparse
import difflib
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")

ROOT = r"D:\Programming\Red\hlm_corpus"
SRCROOT = r"D:\Programming\Red"

DQUAN_JSONL = os.path.join(ROOT, "zhiyanzhai_pizhu", "pizhu.jsonl")
DQUAN_PDF = os.path.join(SRCROOT, "脂批大全 简体版 20241109.pdf")

FUQ_JSONL = os.path.join(ROOT, "fuqinju", "pizhu.jsonl")
FUQ_PDF = os.path.join(SRCROOT, "抚琴居校本（簡體大字本）.pdf")


def norm(t):
    return re.sub(r'[\s　]+', '', str(t or ''))


def log(m):
    print(m, flush=True)


# ============================================================
# 一、脂批大全：用 PDF 回填 U+FFFD
# ============================================================
def build_pdf_text(pdf_path):
    """把 PDF 全文抽出来，返回 (原文, 归一化文本, 归一化位置→原始位置映射)"""
    from pypdf import PdfReader
    log(f"读取 PDF: {pdf_path}")
    reader = PdfReader(pdf_path)
    parts = []
    for pi in range(len(reader.pages)):
        try:
            parts.append(reader.pages[pi].extract_text() or "")
        except Exception:
            parts.append("")
    full = "\n".join(parts)
    log(f"  {len(reader.pages)} 页 / {len(full):,} 字符")

    # 归一化并记录映射（归一化后第 i 个字符对应原文第 idx[i] 个）
    chars, idx = [], []
    for i, c in enumerate(full):
        if c in ' \u3000\t\n\r':
            continue
        chars.append(c)
        idx.append(i)
    normtxt = "".join(chars)
    # 标点归一化后的版本（长度一致，可互相对应位置）
    puncttxt = normtxt.translate(PUNCT_MAP)
    return full, normtxt, puncttxt, idx


# 着重号残留字符：原书记录「重点字」的方式，转文本后只剩下这个
EMPHASIS_LEFTOVER = '\u4e00'     # 「一」


# 标点归类：不同来源的同一批语，标点可能不同（，vs 。 ！vs 。 “”vs「」）
PUNCT_MAP = str.maketrans({
    '，': ',', '。': '.', '；': ';', '：': ':', '！': '!', '？': '?',
    '、': ',', '“': '"', '”': '"', '‘': "'", '’': "'",
    '「': '"', '」': '"', '『': '"', '』': '"',
    '（': '(', '）': ')', '［': '[', '］': ']', '【': '[', '】': ']',
    '〖': '[', '〗': ']', '—': '-', '－': '-',
})


def strip_emphasis(s):
    """
    剥离着重号残留。

    实测：gitbook 源里 `浪子一\ufffd一女`，
    PDF 原文是 `浪子淫女` —— 两侧的「一」是原书给「淫」加的**着重号**，
    转文本时着重号变成了「一」，被强调的字变成了 U+FFFD。
    所以定位上下文必须去掉这些「一」。
    """
    return s.replace(EMPHASIS_LEFTOVER, '')


def punct_norm(s):
    """标点归一化（用于跨源上下文匹配）"""
    return strip_emphasis(s).translate(PUNCT_MAP)


def fix_one(pdf_norm, ctx_pre, ctx_post, n_try=(10, 8, 6, 5, 4, 3)):
    """
    用上下文在 PDF 归一化文本里定位，返回中间那段的真实字形。

    关键改进：
      ① 先剥离上下文里的着重号残留（「一」）
      ② 双约束：前文后文都要匹配
      ③ 候选校验：中间 1~4 字、不含 U+FFFD
    """
    pre0 = punct_norm(ctx_pre)
    post0 = punct_norm(ctx_post)

    best = None
    # 后文可能为空（乱码在末尾）：此时只靠前文，且中间只允许 1 字
    if not post0:
        for n_pre in n_try:
            if len(pre0) < n_pre:
                continue
            pre = pre0[-n_pre:]
            start = 0
            while True:
                i = pdf_norm.find(pre, start)
                if i < 0:
                    break
                j = i + len(pre)
                mid = pdf_norm[j:j + 1]
                if mid and '\ufffd' not in mid:
                    return mid, n_pre
                start = i + 1
        return None, 0

    for n_pre in n_try:
        if len(pre0) < n_pre:
            continue
        pre = pre0[-n_pre:]
        start = 0
        hits = []
        while True:
            i = pdf_norm.find(pre, start)
            if i < 0:
                break
            j = i + len(pre)
            # 后文匹配：从长到短尝试
            for n_post in n_try:
                if len(post0) < n_post:
                    continue
                post = post0[:n_post]
                k = pdf_norm.find(post, j)
                if 0 <= k - j <= 6:           # 中间 1~6 字
                    mid = pdf_norm[j:k]
                    if mid and '\ufffd' not in mid and 1 <= len(mid) <= 6:
                        hits.append((n_pre, n_post, mid))
                        break
            if hits:
                break
            start = i + 1
        if hits:
            # 取前后文最长的那次匹配
            best = max(hits, key=lambda z: z[0] + z[1])
            break

    if best:
        return best[2], best[0] + best[1]
    return None, 0


def fix_daquan():
    log("=" * 74)
    log("一、脂批大全 U+FFFD 乱码修复")
    log("=" * 74)

    rows = [json.loads(l) for l in open(DQUAN_JSONL, encoding='utf-8')]
    bad = [r for r in rows if '\ufffd' in (r.get('text') or '')]
    log(f"含 U+FFFD: {len(bad)} 条 / {len(rows):,}")

    if not bad:
        log("无需修复")
        return

    full, pdf_norm, pdf_punct, idx_map = build_pdf_text(DQUAN_PDF)

    fixed, failed = 0, []
    for r in bad:
        t = r['text']
        nt = norm(t)
        # 逐个 U+FFFD 段处理（可能一条里有多个）
        n_this = 0
        skip_from = 0                        # 失败位置：跳过它继续找下一处
        for _ in range(20):                  # 最多处理 20 处
            mm = re.compile(r'(\ufffd+)').search(nt, skip_from)
            if not mm:
                break
            a, b = mm.start(), mm.end()
            pre = nt[max(0, a - 15):a]
            post = nt[b:b + 15]
            mid, used_n = fix_one(pdf_punct, pre, post)
            if mid is None:
                # 这一处定位失败 → 跳过它，继续尝试后面的乱码
                skip_from = b
                continue

            # ---- 吃掉乱码两侧的着重号残留「一」----
            # 模式：X一�一Y  →  XY 中间插入回填字
            #       X一�Y   →  X 后插入
            #       X�一Y   →  X 后插入
            left = a
            right = b
            if left > 0 and nt[left - 1] == EMPHASIS_LEFTOVER:
                left -= 1
            if right < len(nt) and nt[right] == EMPHASIS_LEFTOVER:
                right += 1
            nt = nt[:left] + mid + nt[right:]
            fixed += 1
            n_this += 1
            skip_from = left + len(mid)

        if n_this > 0 and nt != norm(t):
            # 只要修过就写回（可能仍有残余乱码，但已大幅改善）
            r['text'] = nt
            r['repaired_fffd'] = True
        if '\ufffd' in nt:
            failed.append(r)

    log(f"  修复 {fixed} 处 / 涉及 {len(bad) - len(failed)} 条")
    log(f"  仍未修复 {len(failed)} 条")

    if failed:
        log("\n  未修复清单：")
        for r in failed[:15]:
            log(f"    第{r['hui']}回 {r['text'][:60]}")

    # 备份 + 写回
    bak = DQUAN_JSONL + '.bak'
    if not os.path.exists(bak):
        shutil.copyfile(DQUAN_JSONL, bak)
        log(f"\n  备份: {bak}")

    with open(DQUAN_JSONL, "w", encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    log(f"  已写回 {DQUAN_JSONL}")

    # 残留统计
    left = sum(1 for r in rows if '\ufffd' in (r.get('text') or ''))
    log(f"  写回后仍含 U+FFFD: {left} 条")

    # 重点验证
    log("\n  重点验证（原乱码处）：")
    for r in rows:
        if int(r['hui']) == 2 and '极力称扬浪子' in r['text']:
            log(f"    第2回: {r['text'][:80]}")
            break
    for r in rows:
        if '丧天香楼' in r['text'] and r['hui'] == 13:
            log(f"    第13回: {r['text'][:80]}")
            break


# ============================================================
# 二、抚琴居校本：残余截断修复
# ============================================================
def fix_fuqinju():
    log("\n" + "=" * 74)
    log("二、抚琴居校本残余截断修复")
    log("=" * 74)

    from pypdf import PdfReader
    rows = [json.loads(l) for l in open(FUQ_JSONL, encoding='utf-8')]

    SUS = "，、；：的之与和以在是为把被从对向于其此则也"
    sus = [r for r in rows if r['text'] and r['text'][-1] in SUS]
    log(f"疑似截断: {len(sus)} 条")

    reader = PdfReader(FUQ_PDF)

    def ops_of(pi):
        out = []

        def vis(text, cm, tm, font, size, _o=out):
            tt = (text or '')
            if not tt.strip():
                return
            bf = re.sub(r'^/[A-Z]{6}\+', '', str(font.get('/BaseFont', '?'))).lstrip('/')
            _o.append((round(tm[5], 2), round(tm[4], 2), bf, tt))

        try:
            reader.pages[pi].extract_text(visitor_text=vis)
        except Exception:
            pass
        out.sort(key=lambda z: (-z[0], z[1]))
        return [z for z in out if 58 <= z[0] <= 755]

    def is_kai(bf):
        return ('KaiTi' in bf) or ('SimHei' in bf)

    # 改进版提取：更宽松的双行夹批判定 + 允许跨过正文继续接
    def extract_from_page(pi, want_start=None, max_extra=6):
        """
        从某页开始收集批语，返回 (code, text, start_pi)。
        改进点：
          ① 双行夹批的 x 阈值从 60 降到 25（部分版面偏移更小）
          ② 遇到同行右侧宋体后，继续看**下一行**是否还有 x 靠左的楷体
          ③ 页末若无终止标记则跨页续接
        """
        all_pages = [pi] + list(range(pi + 1, min(pi + 3, len(reader.pages))))
        code, buf = '', []
        last_y = last_x = None
        started = want_start is None

        for pj in all_pages:
            ops = ops_of(pj)
            for i, (y, x, bf, t) in enumerate(ops):
                tt = t.strip()
                if not tt:
                    continue

                if 'EUDC' in bf:
                    # 行内标记（同 y + 后面紧跟楷体）→ 忽略
                    inline = (buf and last_y is not None and abs(y - last_y) < 1.0)
                    nxt_kai = False
                    if inline:
                        for k in range(i + 1, min(i + 3, len(ops))):
                            ny, nx, nbf, nt = ops[k]
                            if not nt.strip():
                                continue
                            nxt_kai = is_kai(nbf)
                            break
                    if inline and nxt_kai:
                        continue
                    if not buf:
                        code += tt
                        started = True
                        continue
                    # 已有内容又遇到新代号：若当前文本已够长则结束
                    if len(norm(''.join(buf))) >= 4:
                        return code, re.sub(r'\s+', '', ''.join(buf)), last_pi
                    code += tt
                    continue

                if is_kai(bf):
                    if want_start is not None and not started:
                        # 等第一个匹配起点
                        if want_start[:6] in re.sub(r'\s+', '', tt):
                            started = True
                        else:
                            continue
                    buf.append(tt)
                    last_y, last_x = y, x
                    last_pi = pj
                    continue

                # ---- 宋体/其他 ----
                same = (last_y is not None and abs(y - last_y) < 1.0)
                right = (last_x is not None and x > last_x + 25)
                if buf and same and right:
                    continue                      # 双行夹批：跳过右侧正文
                # 若不同行但 x 靠左，且是「正文」，说明批语结束
                if buf and len(norm(''.join(buf))) >= 2:
                    return code, re.sub(r'\s+', '', ''.join(buf)), last_pi
                # 还没开始收集：跳过
                continue

            # 页末：继续下一页
            last_pi = pj

        return code, re.sub(r'\s+', '', ''.join(buf)), last_pi if buf else pi

    # ---- 补充策略：对已较长的条目，从「末尾文本」直接在 PDF 里续接 ----
    # 适用：extract_from_page(want_start=...) 因起点定位失败而无效的情况。
    # 做法：用该条最后 8 个字在 PDF 里定位，然后继续往下收集楷体，直到遇到正文。
    def continue_after(tail_text, page):
        last8 = norm(tail_text)[-8:]
        if len(last8) < 5:
            return ''
        for pj in range(page, min(page + 2, len(reader.pages))):
            ops = ops_of(pj)
            for i, (y, x, bf, t) in enumerate(ops):
                tt = re.sub(r'\s+', '', t)
                if last8 not in tt:
                    continue
                # 找到起点：从本行该位置之后 + 后续楷体行继续
                pos = tt.find(last8) + len(last8)
                seg = tt[pos:]
                extra = []
                if seg and is_kai(bf):
                    extra.append(seg)
                # 继续往后的原语
                last_y2 = y
                for k in range(i + 1, len(ops)):
                    ny, nx, nbf, nt2 = ops[k]
                    ntt = re.sub(r'\s+', '', nt2)
                    if not ntt or 'EUDC' in nbf:
                        if 'EUDC' in nbf and extra:
                            break
                        continue
                    if is_kai(nbf):
                        extra.append(ntt)
                        last_y2 = ny
                        continue
                    # 宋体：若同行且靠右 → 双行夹批正文，跳过
                    if abs(ny - last_y2) < 1.0 and nx > x + 25:
                        continue
                    break
                return ''.join(extra)
        return ''

    n_improved = 0
    improved = []
    for r in sus:
        page = r.get('page')
        if page is None:
            continue
        try:
            code, txt, _ = extract_from_page(page, want_start=r['text'])
        except Exception:
            continue
        if not txt:
            continue
        # 若原方法无效，改用「末尾续接」
        if not txt or len(norm(txt)) <= len(norm(r['text'])):
            extra = continue_after(r['text'], page)
            if extra:
                txt = r['text'] + extra

        nt, nr = norm(txt), norm(r['text'])
        if len(nt) > len(nr) and nr[:10] in nt:
            improved.append({'hui': r['hui'], 'page': page,
                             'old_len': len(r['text']), 'new_len': len(txt),
                             'old': r['text'], 'new': txt})
            r['text'] = txt
            r['repaired2'] = True
            n_improved += 1

    log(f"  可进一步修复 {n_improved} 条")
    for x in sorted(improved, key=lambda z: -(z['new_len'] - z['old_len']))[:10]:
        log(f"    第{x['hui']}回 {x['old_len']} → {x['new_len']} 字")
        log(f"      旧: {x['old'][-50:]}")
        log(f"      新: {x['new'][-50:]}")

    if n_improved:
        bak = FUQ_JSONL + '.bak2'
        if not os.path.exists(bak):
            shutil.copyfile(FUQ_JSONL, bak)
            log(f"\n  备份: {bak}")
        with open(FUQ_JSONL, "w", encoding='utf-8') as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + '\n')
        log(f"  已写回 {FUQ_JSONL}")

    # 最终统计
    left = [r for r in rows if r['text'] and r['text'][-1] in SUS]
    total = sum(len(r['text']) for r in rows)
    log(f"\n  修复后疑似截断: {len(left)} 条（原 {len(sus)}）")
    log(f"  总字数: {total:,}")


# ============================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--task', default='both',
                    choices=['both', 'daquan', 'fuqinju'])
    args = ap.parse_args()

    if args.task in ('both', 'daquan'):
        fix_daquan()
    if args.task in ('both', 'fuqinju'):
        fix_fuqinju()


if __name__ == '__main__':
    main()
