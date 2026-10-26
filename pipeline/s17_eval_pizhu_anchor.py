# -*- coding: utf-8 -*-
"""
S17-评估 · 批语定位到正文段落的可行性验证（原型，不写生产数据）
================================================================

用户需求
--------
「能在正文中指出批注的位置吗？」

现有数据
--------
批语：只有 `page`（PDF 物理页），**没有页内坐标**
正文：`078.txt` 按行切段 → 前端 72 段（每段 = 一个自然段）

PDF 版式实测（p.2090）
----------------------
```
y=283.5 x=54.2  SUN  '凤姐儿道：“还不曾呢，如今还是吃汤药。太太'
y=243.4 x=373.8 KAI  '总是勉强。'                    ← 批语
y=243.4 x=471.6 SUN  '王夫人'                        ← 同一行的正文（右侧）
y=243.4 x=54.2  SUN  '只管放心，我已大好了。”'          ← 同一行的正文（左侧）
y=243.4 x=54.2  EUDC '\ue052\ue053'                  ← 批语代号
y=203.4 x=54.2  SUN  '见他精神复初，也就信了。'
y=203.4 x=330.1 EUDC '\ue052\ue053'
y=203.4 x=368.6 KAI  '只用此一句，便入后'
y=163.3 x=54.2  KAI  '文。'                          ← 批语续行
y=163.3 x=90.6  SUN  '因告诉撵逐晴雯等事，又说：“怎么宝丫头私'
```

**关键发现：批语与正文在同一个 y 坐标上。
批语是「双行夹批」，以小字插在正文行之间，位置就在被批的正文行内。**

可行性判断
----------
**可行**，理由：
  1. 批语的 y 与正文行 y 精确对齐（同一基线）
  2. 正文段落可从 PDF 逐行重建，每行有 y 坐标
  3. 因此：批语 y → 找到同 y 的正文行 → 该行属于哪个段落 → 段内第几字

需要补的数据
------------
重新提取批语时，额外记录：
  · `page_y`：批语首行的 y 坐标
  · `page_x`：批语首行的 x 坐标（用于判断在左侧还是右侧）

精度分层
--------
  L1 段落级（推荐）：批语 y → 正文段序号  ← 最稳
  L2 字符级：还需 x 坐标映射到该行的字符位置  ← 较难，误差大
  L3 行级：介于两者之间

本原型验证 L1（段落级）的准确率。

运行：
  python s17_eval_pizhu_anchor.py
"""
import os
import re
import sys
import json
import collections

sys.stdout.reconfigure(encoding="utf-8")

PDF = r"D:\Programming\Red\抚琴居校本（簡體大字本）.pdf"
FUQ_JSONL = r"D:\Programming\Red\hlm_corpus\fuqinju\pizhu.jsonl"
WEB = r"D:\学校事务\大数据原理与应用\红楼智问\web\data\versions\fuqinju"


def log(m):
    print(m, flush=True)


def main():
    from pypdf import PdfReader
    log("=" * 74)
    log("S17 评估：批语能否锚定到正文段落")
    log("=" * 74)

    reader = PdfReader(PDF)

    def ops_of(pi):
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
        return [z for z in out if 58 <= z[0] <= 755]

    def is_sun(bf):
        return ('SimSun' in bf) or ('Sun-Ext' in bf)

    def is_kai(bf):
        return ('KaiTi' in bf) or ('SimHei' in bf)

    # ---- 实验对象：第 78 回 ----
    hui = 78
    rows = [json.loads(l) for l in open(FUQ_JSONL, encoding='utf-8')]
    pz = [r for r in rows if int(r['hui']) == hui]
    log(f"\n第{hui}回批语 {len(pz)} 条")

    # 前端段落
    ch = json.load(open(os.path.join(WEB, f"chapter_{hui:03d}.json"), encoding='utf-8'))
    paras = ch['paragraphs']
    log(f"前端段落 {len(paras)} 段")

    # ---- 重建 PDF 正文行（带 y 坐标）----
    # 需要知道第78回在 PDF 的页码范围
    pages = sorted(set(int(r['page']) for r in pz))
    log(f"批语分布在 {len(pages)} 页: {min(pages)}~{max(pages)}")

    # 收集这些页的正文行
    body_lines = []      # [(page, y, x, text)]
    for pi in range(min(pages) - 1, max(pages) + 2):
        for y, x, bf, t in ops_of(pi):
            if is_sun(bf):
                body_lines.append((pi, y, x, re.sub(r'\s+', '', t)))

    log(f"PDF 正文行 {len(body_lines)} 行")

    # ---- 把 PDF 正文行拼成全文，与前端段落对齐 ----
    body_full = ''.join(t for _, _, _, t in body_lines)
    para_full = ''.join(p['t'] for p in paras)

    # 用最长公共子序列思路：逐行在段落拼接文本里查找位置
    log(f"\nPDF 正文 {len(body_full):,} 字 / 前端段落拼合 {len(para_full):,} 字")

    # 为每个 PDF 行找它在 para_full 中的位置
    pos = 0
    line_para = []       # [(page, y, x, text, para_idx)]
    ok, miss = 0, 0
    for pi, y, x, t in body_lines:
        idx = para_full.find(t, max(0, pos - 200))
        if idx < 0:
            idx = para_full.find(t)
        if idx < 0:
            line_para.append((pi, y, x, t, None))
            miss += 1
            continue
        # 该位置属于哪个段落
        acc = 0
        pidx = None
        for p in paras:
            if acc <= idx < acc + len(p['t']):
                pidx = p['i']
                break
            acc += len(p['t'])
        line_para.append((pi, y, x, t, pidx))
        pos = idx + len(t)
        if pidx is not None:
            ok += 1

    log(f"正文行对齐: 成功 {ok} / 失败 {miss}")

    # ---- 对每条批语，找它的 y 对应的正文行 ----
    log("\n" + "=" * 74)
    log("锚定实验：批语 → 段落")
    log("=" * 74)

    anchored, failed = [], []
    for r in pz:
        pg = int(r['page'])
        text_head = re.sub(r'\s+', '', r['text'])[:12]
        # 找批语在 PDF 中的 y（用文本匹配回到 PDF）
        found_y = None
        for pi in range(pg, min(pg + 2, len(reader.pages))):
            for y, x, bf, t in ops_of(pi):
                if not is_kai(bf):
                    continue
                if text_head[:8] in re.sub(r'\s+', '', t):
                    found_y = (pi, y)
                    break
            if found_y:
                break

        if not found_y:
            failed.append((r, 'pizhu y not found'))
            continue

        ppi, py = found_y
        # 在同页找 y 最接近的正文行
        cands = [(abs(ly - py), lp, ly, lt, lidx)
                 for lp, ly, lx, lt, lidx in line_para
                 if lp == ppi and lidx is not None]
        if not cands:
            failed.append((r, 'no body line on page'))
            continue
        cands.sort()
        dist, lp, ly, lt, lidx = cands[0]
        if dist > 12:          # 行距约 40，>12 说明不同行
            failed.append((r, f'nearest body too far ({dist:.1f})'))
            continue
        anchored.append({'hui': hui, 'text': r['text'][:40],
                         'page': pg, 'y': py, 'para': lidx,
                         'line': lt[:30], 'dist': round(dist, 1)})

    log(f"\n可锚定: {len(anchored)} / {len(pz)} 条"
        f"（{len(anchored)/len(pz):.1%}）")
    log(f"失败: {len(failed)} 条")

    if anchored:
        log("\n=== 成功样例 ===")
        for x in anchored[:12]:
            log(f"  第{x['para']}段 (dist={x['dist']}) 批语: {x['text'][:36]}")
            log(f"      同行正文: {x['line']}")

    if failed:
        log("\n=== 失败原因 ===")
        c = collections.Counter(reason for _, reason in failed)
        for k, v in c.most_common():
            log(f"  {k}: {v}")

    # ---- 段落分布检查（是否过于集中）----
    if anchored:
        log("\n=== 锚定到的段落分布 ===")
        c = collections.Counter(x['para'] for x in anchored)
        log(f"  覆盖 {len(c)} 个段落 / 共 {len(paras)} 段")
        for pidx, n in sorted(c.items())[:15]:
            log(f"    第{pidx}段: {n} 条")

    with open(os.path.join(os.path.dirname(FUQ_JSONL), 'anchor_eval.json'),
              'w', encoding='utf-8') as f:
        json.dump({'n_pizhu': len(pz), 'n_anchored': len(anchored),
                   'rate': len(anchored) / len(pz) if pz else 0,
                   'anchored': anchored, 'failed': len(failed)},
                  f, ensure_ascii=False, indent=1)
    log(f"\n报告: anchor_eval.json")


if __name__ == '__main__':
    main()
