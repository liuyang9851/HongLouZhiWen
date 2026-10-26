# -*- coding: utf-8 -*-
"""
S15-2 · 抚琴居校本最后 4 条长批语截断修复
==========================================

这 4 条是 S12/S13 都没能自动修复的「硬骨头」：
它们的续文在 PDF 里，但自动续接逻辑因版面复杂（跨栏/注释号干扰）未能接上。

**本轮改用「PDF 原文核对 + 定向补全」**：先逐条从 PDF 文本层读出续文，
核对无误后写入。所有续文都来自 PDF 实际文字，不是猜测。

修复清单
--------
| 回 | 页 | 现有结尾 | PDF 续文（实测） | 修复后 |
|:---|:---|:---|:---|---:|

数据来源：pypdf 提取的文本原语（KaiTi 字体 = 批语）

运行：
  python s15_2_fix_fuqinju_final4.py
"""
import os
import sys
import json
import shutil

sys.stdout.reconfigure(encoding="utf-8")

ROOT = r"D:\Programming\Red\hlm_corpus"
FUQ = os.path.join(ROOT, "fuqinju", "pizhu.jsonl")


def log(m):
    print(m, flush=True)


# (回, 现有文本的结尾片段, PDF 实测续文, PDF 页码)
# 续文全部来自 PDF 文本层 KaiTi 原语，见 S15 报告
FIXES = [
    # 第28回 p.788：`…昨阻余批《葬花吟》之客，嫡是` + `玉兄之化身无疑。余几作点金成铁之人，笨甚笨甚！`
    (28, "昨阻余批《葬花吟》之客，嫡是",
     "玉兄之化身无疑。余几作点金成铁之人，笨甚笨甚！", 788),

    # 第43回 p.1158：`…恶则无往不恶，美则无一不美，` + `何不近情理之如是耶？`
    (43, "恶则无往不恶，美则无一不美，",
     "何不近情理之如是耶？", 1158),

    # 第13回 p.365：`…凡“要”者，则身心急切；` + `急切之者，百事无成。正为后文作引线。`
    (13, "凡“要”者，则身心急切；",
     "急切之者，百事无成。正为后文作引线。", 365),

    # 第17回 p.473：`…则将日日爬山越岭矣。记清此处，` + `则知后文宝玉所行常径，非此处也。`
    (17, "则将日日爬山越岭矣。记清此处，",
     "则知后文宝玉所行常径，非此处也。", 473),
]


def main():
    log("=" * 74)
    log("S15-2 抚琴居校本最后 4 条长批语修复")
    log("=" * 74)

    rows = [json.loads(l) for l in open(FUQ, encoding='utf-8')]
    log(f"总条数: {len(rows):,}")

    n = 0
    done = []
    for hui, tail, cont, page in FIXES:
        hit = None
        for r in rows:
            if r['hui'] != hui:
                continue
            t = r['text']
            if t.endswith(tail) and cont not in t:
                hit = r
                break
        if not hit:
            log(f"  ⚠ 第{hui}回 未找到匹配（结尾 {tail[-16:]!r}）")
            continue
        old = hit['text']
        hit['text'] = old + cont
        hit['repaired3'] = True
        hit['repair_source'] = f'PDF p.{page}'
        n += 1
        done.append({'hui': hui, 'page': page,
                     'old_len': len(old), 'new_len': len(hit['text']),
                     'old': old, 'new': hit['text']})
        log(f"  ✓ 第{hui:>2}回 p.{page}  {len(old)} → {len(hit['text'])} 字")

    if not n:
        log("无变化")
        return

    log("\n=== 修复详情 ===")
    for x in done:
        log(f"  第{x['hui']}回")
        log(f"    旧: …{x['old'][-40:]}")
        log(f"    新: …{x['new'][-60:]}")

    bak = FUQ + '.bak3'
    if not os.path.exists(bak):
        shutil.copyfile(FUQ, bak)
        log(f"\n备份: {bak}")

    with open(FUQ, 'w', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    log(f"已写回 {FUQ}")

    # ---- 复查 ----
    log("\n" + "=" * 74)
    log("复查")
    log("=" * 74)
    SUS = "，、；：的之与和以在是为把被从对向于其此则也"
    sus = [r for r in rows if r['text'] and r['text'][-1] in SUS]
    sus_long = [r for r in sus if len(r['text']) > 30]

    log(f"  修复: {n} 条")
    log(f"  总字数: {sum(len(r['text']) for r in rows):,}")
    log(f"  疑似截断: {len(sus)} 条（其中 >30 字 {len(sus_long)} 条）")
    log(f"  含 U+FFFD: {sum(1 for r in rows if chr(0xfffd) in r['text'])} 条")

    if sus_long:
        log("\n  仍有长文本疑似截断：")
        for r in sorted(sus_long, key=lambda x: -len(x['text'])):
            log(f"    第{r['hui']:>2}回 [{len(r['text'])}字] …{r['text'][-46:]}")

    with open(os.path.join(ROOT, "fuqinju", "final4_report.json"),
              'w', encoding='utf-8') as f:
        json.dump({'n_fixed': n, 'items': done}, f, ensure_ascii=False, indent=1)
    log(f"\n  报告: final4_report.json")


if __name__ == '__main__':
    main()
