# -*- coding: utf-8 -*-
"""
S12 · 修复抚琴居校本批语被「宋体补字」截断的问题
==================================================

用户反馈
--------
「我发现这里脂批好像有些不完整，可能是从pdf处理时有问题」

实测确认：**用户是对的**，第78回第一条批语被截断：

  数据里：'文有宾主，不可误。此文以《芙蓉诔》为主，以《'      ← 22 字，断了
  PDF 原文：'文有宾主，不可误。此文以《芙蓉诔》为主，以《姽婳词》为宾，
            以宝玉古歌为主，以贾环贾兰诗绝为宾。文有宾中宾，不可误。
            以清客作序为宾，以宝玉出游作诗为宾中宾。由虚入实，可歌可咏。'  ← 完整

根因
----
PDF 文本层里，批语是 KaiTi（楷体），但**生僻字用 SimSun（宋体）补字**：

    y=433.5  KaiTi_GB2312  '主，以《'          ← 批语
    y=433.5  SimSun        '姽婳'              ← 生僻字补字！（同一行 y 完全相同）
    y=433.5  KaiTi_GB2312  '词》为宾，以宝玉古歌为主，以贾'

旧脚本 `_extract_fuqinju_pizhu.py` 的状态机是：

    elif 'KaiTi' in bf or 'SimHei' in bf: ... 收集
    else:                                  flush()   ← 遇到宋体立即结束

→ 遇到这两个宋体补字就 flush，批语被腰斩。

修复思路
--------
**同一行（y 相同）里夹在楷体中间的宋体字，是补字，应并入批语，不 flush。**

判定条件（保守，避免误收正文）：
  1. 当前状态是 'text'（正在收集批语）
  2. 该宋体原语与「上一条楷体原语」的 y 差 < 1.0（同一行）
  3. 该宋体原语宽度很窄（≤ 6 个字，生僻补字一般 1~2 字）
  4. 之后紧跟的仍是楷体（否则说明真的是正文开始了 —— 这一条靠"回看"实现）

若条件不满足 → 按原逻辑 flush。

产出
----
  fuqinju/pizhu.jsonl         原地修复（备份为 pizhu.jsonl.bak）
  fuqinju/pizhu_repair_report.json   修复明细（哪条被补了多少字）

运行：
  python s12_1_fix_pizhu_truncation.py
"""
import os
import re
import sys
import json
import shutil
import collections

sys.stdout.reconfigure(encoding="utf-8")

SRC = r"D:\Programming\Red\抚琴居校本（簡體大字本）.pdf"
ROOT = r"D:\Programming\Red\hlm_corpus"
OUTDIR = os.path.join(ROOT, "fuqinju")
TITLES = {int(k): v for k, v in json.load(
    open(os.path.join(ROOT, "huimu.json"), encoding='utf-8'))['zhiping'].items()}

CN = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9,
      '零': 0, '〇': 0, '十': 10, '百': 100}
HUI_RE = re.compile(r'第\s*([一二三四五六七八九十百零][一二三四五六七八九十百零\s]*?)\s*回')

SAME_LINE = 1.0        # y 差 < 1.0 视为同一行
MAX_INLINE_CHARS = 8   # 同一行补字最多 8 字（生僻字补字一般 1~2）


def cn2num(s):
    tot = cur = 0
    for ch in s:
        if ch == '十':
            tot += (cur or 1) * 10
            cur = 0
        elif ch == '百':
            tot += (cur or 1) * 100
            cur = 0
        elif ch in CN:
            cur = CN[ch]
    return tot + cur


def norm_key(t):
    return re.sub(r'[\s。，、！？；：“”‘’（）《》〈〉【】〖〗…—·~～□〇◇]', '', str(t or ''))


# ============================================================
# 1) 读 PDF 原语
# ============================================================
def read_ops(pi, reader):
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
    return out


def is_kai(bf):
    return ('KaiTi' in bf) or ('SimHei' in bf)


# ============================================================
# 2) 带「补字合并」的候选段提取
# ============================================================
def extract_with_inline_merge(reader, pages, page_hui):
    """
    与旧逻辑相同，但：同一行夹在楷体中的窄宋体原语 → 补字并入，不 flush。

    难点：需要「往后看」判断后面是否还是楷体。做法：
      先算出每个原语的「下列有效原语」，若紧随的是楷体，则视为补字。
    """
    records = []
    n_merged = 0
    n_side_skip = 0
    n_inline_eudc = 0

    # 跨页续接：把上一页未结束的批语状态带过来
    carry = None          # (code_chars, buf, last_kai_y)
    n_cross_page = 0

    for pi in pages:
        h = page_hui.get(pi, 0)
        ops = [z for z in read_ops(pi, reader) if 58 <= z[0] <= 755]
        if not ops:
            continue

        if carry is not None:
            st = {'state': 'text', 'code_chars': list(carry[0]),
                  'buf': list(carry[1]), 'last_kai_y': carry[2],
                  'last_kai_x': carry[5] if len(carry) > 5 else None,
                  'start_pi': carry[3], 'hui': carry[4]}
            carry = None
            n_cross_page += 1
        else:
            st = {'state': 'idle', 'code_chars': [], 'buf': [],
                  'last_kai_y': None, 'last_kai_x': None,
                  'start_pi': pi, 'hui': h}

        def flush():
            if st['state'] == 'text' and st['code_chars']:
                code = ''.join(st['code_chars'])
                txt = re.sub(r'\s+', '', ''.join(st['buf']))
                if len(norm_key(txt)) >= 2:
                    # page 用「起始页」（跨页续接时 ≠ 当前页），
                    # 否则新旧数据对齐 key 会错位
                    records.append({'hui': st.get('hui') or h or None,
                                    'code': code, 'text': txt,
                                    'page': st.get('start_pi', pi)})
            st['state'] = 'idle'
            st['code_chars'] = []
            st['buf'] = []
            st['last_kai_y'] = None
            st['last_kai_x'] = None

        for idx, (y, x, bf, t) in enumerate(ops):
            t = t.strip()
            if not t:
                continue

            if 'EUDC' in bf:
                # 判断是否「行内版型标记」：
                #   正在收集批语 + 与上一条楷体同一 y + 后面紧跟仍是楷体
                inline_eudc = False
                if (st['state'] == 'text' and st['last_kai_y'] is not None
                        and abs(y - st['last_kai_y']) < SAME_LINE):
                    for k in range(idx + 1, min(idx + 3, len(ops))):
                        ny, nx, nbf, nt = ops[k]
                        if not nt.strip():
                            continue
                        inline_eudc = is_kai(nbf)
                        break
                if inline_eudc:
                    n_inline_eudc += 1     # 忽略这个标记，批语继续
                    continue

                if st['state'] == 'text':
                    flush()
                st['state'] = 'eudc'
                st['code_chars'].append(t)
                st['last_kai_y'] = None
                st['start_pi'] = pi      # 新批语从本页开始
                st['hui'] = h

            elif is_kai(bf):
                if st['state'] == 'eudc':
                    st['state'] = 'text'
                if st['state'] == 'text':
                    st['buf'].append(t)
                    st['last_kai_y'] = y
                    st['last_kai_x'] = x

            else:
                # ---- 宋体/其他字体 ----
                # 判断是否是「夹在楷体中的补字」：
                #   ① 正在收集批语
                #   ② 与上一条楷体同一行
                #   ③ 本原语很窄
                #   ④ 后面还有楷体原语（同一页、字号相近的行）
                is_inline = (
                    st['state'] == 'text'
                    and st['last_kai_y'] is not None
                    and abs(y - st['last_kai_y']) < SAME_LINE
                    and len(t) <= MAX_INLINE_CHARS
                )
                if is_inline:
                    # 再看后面是否有楷体（往后最多 3 个原语）
                    nxt_kai = False
                    for k in range(idx + 1, min(idx + 4, len(ops))):
                        ny, nx, nbf, nt = ops[k]
                        if not nt.strip():
                            continue
                        if is_kai(nbf):
                            nxt_kai = True
                        break
                    if nxt_kai:
                        st['buf'].append(t)      # 补字并入
                        n_merged += 1
                        continue

                # ---- 双行夹批：同一 y 上，楷体批语在左、正文在右 ----
                # 判据：同一 y（与上一条楷体同行）+ 该宋体 x 明显更靠右
                if (st['state'] == 'text' and st['last_kai_y'] is not None
                        and abs(y - st['last_kai_y']) < SAME_LINE
                        and x is not None and st['last_kai_x'] is not None
                        and x > st['last_kai_x'] + 60):
                    # 这是同行的正文，跳过（不加入 buf，也不 flush）
                    st['skip_x'] = True
                    n_side_skip += 1
                    continue

                flush()

        # ---- 页末：若仍在收集批语，且下一有效原语仍是楷体，则跨页续接 ----
        if st['state'] == 'text' and st['code_chars'] and st['buf']:
            nxt_kai = False
            for npi in range(pi + 1, min(pi + 3, max(pages) + 1)):
                nops = [z for z in read_ops(npi, reader) if 58 <= z[0] <= 755]
                if not nops:
                    continue
                # 找下一页第一个非 EUDC 的原语
                for ny, nx, nbf, nt in nops:
                    if not nt.strip():
                        continue
                    if 'EUDC' in nbf:
                        continue          # 新批语的代号 → 说明是另一条
                    nxt_kai = is_kai(nbf)
                    break
                break
            if nxt_kai:
                carry = (st['code_chars'], st['buf'], st['last_kai_y'],
                         st.get('start_pi', pi), st.get('hui') or h,
                         st.get('last_kai_x'))
                continue              # 不 flush，带状态去下一页
            else:
                flush()
        else:
            flush()

    return records, n_merged, n_cross_page, n_side_skip, n_inline_eudc


# ============================================================
# 3) 正文过滤（沿用旧逻辑）
# ============================================================
def load_bodies():
    import difflib
    bodies = {}
    fanli = ''
    fn = os.path.join(OUTDIR, '000_凡例.txt')
    if os.path.exists(fn):
        fanli = norm_key(open(fn, encoding='utf-8').read())
    for h in range(1, 81):
        f = os.path.join(OUTDIR, f"{h:03d}.txt")
        body = norm_key(open(f, encoding='utf-8').read()) if os.path.exists(f) else ''
        bodies[h] = fanli + body if h == 1 else body
    return bodies


def looks_like_body(key, body):
    import difflib
    if not body or not key:
        return False
    if len(key) <= 25:
        return key in body
    if key in body:
        return True
    sm = difflib.SequenceMatcher(None, key, body, autojunk=False)
    i, j, n = sm.find_longest_match(0, len(key), 0, len(body))
    ratio = n / len(key)
    return ratio >= (0.75 if len(key) > 120 else 0.82)


# ============================================================
def main():
    from pypdf import PdfReader

    print("=" * 74)
    print("S12-1 修复批语被「宋体补字」截断")
    print("=" * 74)

    # 旧数据（用于对比）
    old_path = os.path.join(OUTDIR, "pizhu.jsonl")
    old_rows = [json.loads(l) for l in open(old_path, encoding='utf-8')]
    print(f"现有批语: {len(old_rows):,} 条")

    print("\n读取 PDF（约 2272 页，需 1~2 分钟）...")
    reader = PdfReader(SRC)
    N = len(reader.pages)
    PAGE_FROM, PAGE_TO = 17, min(2270, N)

    # 回次定位
    page_hui, cur = {}, 0
    for pi in range(PAGE_FROM, PAGE_TO):
        ops = [z for z in read_ops(pi, reader) if 58 <= z[0] <= 755]
        head = ' '.join(''.join(t.strip() for _, _, _, t in ops[:3]).split())
        m = HUI_RE.search(head[:80])
        if m:
            h = cn2num(m.group(1))
            if h == cur + 1:
                cur = h
        page_hui[pi] = cur
    print(f"回次定位至第 {cur} 回")

    print("\n重新抽取候选段（含补字合并）...")
    raw, n_merged, n_cross, n_side, n_ieudc = extract_with_inline_merge(
        reader, range(PAGE_FROM, PAGE_TO), page_hui)
    print(f"  候选段 {len(raw):,}  合并补字 {n_merged:,}  跨页续接 {n_cross:,}  "
          f"跳过同行正文 {n_side:,}  行内标记 {n_ieudc:,}")

    # 正文过滤
    print("\n正文过滤...")
    BODIES = load_bodies()
    filtered = []
    for r in raw:
        h = r['hui']
        if not h:
            continue
        key = norm_key(r['text'])
        if len(key) < 4:
            continue
        if looks_like_body(key, BODIES.get(h, '')):
            continue
        filtered.append(r)
    print(f"  过滤后 {len(filtered):,}")

    # 按 ◇ 拆分
    BLACKLIST_PREFIX = ('此开卷第一回也作者自云',)
    seen, out = set(), []
    for r in filtered:
        for part in [p.strip() for p in r['text'].split('◇') if p.strip()]:
            kp = norm_key(part)
            if len(kp) < 2:
                continue
            if looks_like_body(kp, BODIES.get(r['hui'], '')):
                continue
            if any(kp.startswith(p) for p in BLACKLIST_PREFIX):
                continue
            k = (r['hui'], r['code'], re.sub(r'\s', '', part))
            if k in seen:
                continue
            seen.add(k)
            out.append({
                'hui': r['hui'],
                'huimu': TITLES.get(r['hui'], '') if r['hui'] else '',
                'code': r['code'].encode('unicode_escape').decode(),
                'text': part,
                'page': r['page'],
            })
    print(f"  拆分后 {len(out):,} 条")

    # ---- 与旧数据比对：按 (hui, page, 文本前缀) 对齐 ----
    print("\n对比新旧数据...")
    # 旧数据按键 (hui, page, 前10字) 索引
    old_idx = {}
    for r in old_rows:
        k = (r['hui'], r.get('page'), norm_key(r['text'])[:10])
        old_idx.setdefault(k, []).append(r)

    # 二级索引：只用 (hui, 前10字)，应对 page 因跨页续接而变化
    old_idx2 = {}
    for r in old_rows:
        k2 = (r['hui'], norm_key(r['text'])[:10])
        old_idx2.setdefault(k2, []).append(r)

    # 第三级索引：按 (hui, page) 分桶，用「旧文本是否是新文本的子串」配对
    old_by_page = {}
    for r in old_rows:
        old_by_page.setdefault((r['hui'], r.get('page')), []).append(r)

    repaired = []
    for n in out:
        cand = []
        k = (n['hui'], n.get('page'), norm_key(n['text'])[:10])
        cand = old_idx.get(k, [])
        if not cand:
            k2 = (n['hui'], norm_key(n['text'])[:10])
            c2 = old_idx2.get(k2, [])
            if len(c2) == 1:
                cand = c2
        if not cand:
            # 第三级：同页桶内一对一最优匹配
            # 条件：该旧记录未被用过 + 旧文本是新文本的**真子串** + 取最长
            nk = norm_key(n['text'])
            pool = old_by_page.get((n['hui'], n.get('page')), [])
            best, best_len = None, 0
            for o in pool:
                if o.get('_used'):
                    continue
                ok_ = norm_key(o['text'])
                if len(ok_) < 3:
                    continue
                if ok_ == nk:
                    continue          # 完全相同不需要"修复"
                if ok_ in nk and len(ok_) > best_len:
                    best, best_len = o, len(ok_)
            if best is not None:
                cand = [best]

        if not cand:
            # 第四级：相似度匹配（同页桶，未使用，ratio ≥ 0.75 且新更长）
            import difflib
            nk = norm_key(n['text'])
            pool = old_by_page.get((n['hui'], n.get('page')), [])
            best, best_ratio = None, 0.0
            for o in pool:
                if o.get('_used'):
                    continue
                ok_ = norm_key(o['text'])
                if len(ok_) < 3 or len(nk) <= len(ok_):
                    continue
                r = difflib.SequenceMatcher(None, ok_, nk, autojunk=False).ratio()
                if r >= 0.75 and r > best_ratio:
                    best, best_ratio = o, r
            if best is not None:
                cand = [best]

        if not cand:
            continue
        o = cand[0]
        if o.get('_used'):
            continue
        nk_, ok_ = norm_key(n['text']), norm_key(o['text'])
        if not (len(nk_) > len(ok_)):
            continue
        # 校验分两种：
        #   ① 前三级（精确/前缀/子串）→ 要求旧文本是新文本子串
        #   ② 第四级（相似度）→ 允许不包含，只要 ratio ≥ 0.75
        if ok_ in nk_:
            pass                                  # ① 通过
        else:
            import difflib
            rr = difflib.SequenceMatcher(None, ok_, nk_, autojunk=False).ratio()
            if rr < 0.75:
                continue                          # ② 也不像 → 放弃
            o['_sim'] = round(rr, 3)
        o['_used'] = True
        repaired.append({
            'hui': n['hui'], 'page': n.get('page'),
            'old_len': len(o['text']), 'new_len': len(n['text']),
            'old': o['text'], 'new': n['text'],
        })

    print(f"  可修复（新文本更长）{len(repaired):,} 条")
    if repaired:
        print("\n  修复样例（前 8 条）：")
        for x in repaired[:8]:
            print(f"    第{x['hui']}回 p.{x['page']}  {x['old_len']} → {x['new_len']} 字")
            print(f"      旧: {x['old']}")
            print(f"      新: {x['new']}")

    # ---- 备份 + 写回 ----
    if repaired:
        bak = old_path + ".bak"
        if not os.path.exists(bak):
            shutil.copyfile(old_path, bak)
            print(f"\n已备份: {bak}")

        # 构造新数据：以旧数据为基底，替换被修复的条目
        rep_map = {}
        rep_map2 = {}
        for x in repaired:
            rep_map[(x['hui'], x['page'], norm_key(x['old'])[:10])] = x['new']
            rep_map2[(x['hui'], norm_key(x['old'])[:10])] = x['new']

        # 第四级替换索引：旧文本 → 新文本（直接按旧文本内容映射，最可靠）
        rep_by_old = {}
        for x in repaired:
            rep_by_old[(x['hui'], x.get('page'), norm_key(x['old']))] = x['new']

        new_rows = []
        n_replaced = 0
        for r in old_rows:
            k = (r['hui'], r.get('page'), norm_key(r['text'])[:10])
            k2 = (r['hui'], norm_key(r['text'])[:10])
            k3 = (r['hui'], r.get('page'), norm_key(r['text']))
            if k3 in rep_by_old:
                r2 = dict(r)
                r2['text'] = rep_by_old[k3]
                r2['repaired'] = True
                r2['old_text'] = r['text']
                new_rows.append(r2)
                n_replaced += 1
                continue
            if k in rep_map or k2 in rep_map2:
                k = k if k in rep_map else k2
                rep_map = rep_map if k in rep_map else rep_map2
                r2 = dict(r)
                r2['text'] = rep_map[k]
                r2['repaired'] = True
                r2['old_text'] = r['text']
                new_rows.append(r2)
                n_replaced += 1
            else:
                new_rows.append(r)
        print(f"已替换 {n_replaced} 条")

        with open(old_path, "w", encoding='utf-8') as f:
            for r in new_rows:
                f.write(json.dumps(r, ensure_ascii=False) + '\n')
        print(f"已写回 {old_path}（{len(new_rows):,} 条）")

        with open(os.path.join(OUTDIR, "pizhu_repair_report.json"), "w",
                  encoding='utf-8') as f:
            json.dump({
                "n_candidates": len(raw),
                "n_inline_merged": n_merged,
                "n_repaired": len(repaired),
                "samples": repaired[:40],
            }, f, ensure_ascii=False, indent=1)
        print(f"报告: {os.path.join(OUTDIR, 'pizhu_repair_report.json')}")
    else:
        print("\n⚠ 没有找到可修复项 —— 请检查判定条件")

    # ---- 重点验证第78回 ----
    print("\n" + "=" * 74)
    print("重点验证：第 78 回（用户反馈处）")
    print("=" * 74)
    final = [json.loads(l) for l in open(old_path, encoding='utf-8')]
    for r in [x for x in final if int(x['hui']) == 78][:5]:
        print(f"  [{len(r['text'])}字] {r['text'][:100]}")


if __name__ == "__main__":
    main()
