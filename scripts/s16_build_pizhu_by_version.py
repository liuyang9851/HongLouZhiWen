# -*- coding: utf-8 -*-
"""
S16 · 癸酉本批语接入阅读界面侧边栏
==================================

问题
----
用户反馈：「癸酉本没有侧边栏的批语，补上」

诊断结果：
  · `web/data/pizhu.json` 只有一个全局 `by_hui`，**只含抚琴居校本批语**
    （79 回 / 1–80 回，来源 `码点核对/pizhu_redecoded.jsonl`）
  · 癸酉本批语（223 条）虽已进 RAG 索引（`guiyouben/pizhu`），
    但**从未生成到前端数据**
  · `versions/guiyouben/chapter_091.json` 连 `pizhu` 字段都没有

修复方案
--------
把 `pizhu.json` 从「单一全局表」改为「**按版本分组**」：

```json
{
  "by_version": {
    "fuqinju":   { "by_hui": { "1": [...], ... } },
    "guiyouben": { "by_hui": { "81": [...], ... } },
    "chenggao":  { "by_hui": {} }
  },
  "note": "..."
}
```

同时保留旧的 `by_hui` 字段（= fuqinju），向后兼容。

数据来源：
  · 抚琴居 → `码点核对/pizhu_redecoded.jsonl`（3,234 条，1–80 回）
  · 癸酉本 → `hlm_corpus/guiyouben/pizhu.jsonl`（223 条，含前80回摘录）
  · 程高本 → 无脂批（删改本），显式给空表

运行：
  python s16_build_pizhu_by_version.py
"""
import os
import re
import sys
import json
import collections

sys.stdout.reconfigure(encoding="utf-8")

ROOT = r"D:\学校事务\大数据原理与应用\红楼智问"
WEB_DATA = os.path.join(ROOT, "web", "data")

FUQ_JSONL = r"D:\Programming\Red\hlm_corpus\pizhu_v5.jsonl"   # S28：按字号重提取（修复截断），旧文件 pizhu_redecoded.jsonl 保留可回滚
GUIYOU_JSONL = r"D:\Programming\Red\hlm_corpus\guiyouben\pizhu.jsonl"

# 每回最多展示条数（侧边栏太长不好读）
MAX_PER_HUI = 250   # S28：数据修复后放开条数（回1 实测 224 条），仅作渲染上限保护


def log(m):
    print(m, flush=True)


def load_jsonl(p):
    out = []
    with open(p, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def build_fuqinju():
    """抚琴居校本批语：1–80 回"""
    rows = load_jsonl(FUQ_JSONL)
    by_hui = collections.defaultdict(list)
    for r in rows:
        hui = int(r.get('hui') or 0)
        if hui <= 0:
            continue
        vers = r.get('versions_legend') or []
        if isinstance(vers, str):
            try:
                vers = json.loads(vers)
            except Exception:
                vers = [vers]
        by_hui[hui].append({
            'text': (r.get('text') or ''),
            'versions': vers,
            'page': r.get('page'),
            'conf': r.get('conf'),
            'src_tag': '抚琴居校本',
        })
    out = {}
    for h, lst in by_hui.items():
        out[str(h)] = lst[:MAX_PER_HUI]
    log(f"  抚琴居校本: {len(rows):,} 条 → {len(out)} 回")
    return out


def build_guiyouben():
    """
    癸酉本批语：223 条
    注意：其中 120 条是「前80回批语摘录」，hui 为 1–80，
    但癸酉本正文只有 81–108 回。这些摘录条目挂在其标注的 hui 上，
    阅读 81–108 回时不会显示（因为 hui 不匹配），这是正确的。
    后 28 回相关的 103 条（98 内嵌 + 4 回前批 + 1 回末评）会正常显示。
    """
    rows = load_jsonl(GUIYOU_JSONL)
    by_hui = collections.defaultdict(list)
    for r in rows:
        hui = int(r.get('hui') or 0)
        if hui <= 0:
            continue
        src = r.get('source') or '癸酉本'
        # 从 source 里提取批型
        ptype = ''
        if '内嵌' in src:
            ptype = '内嵌批'
        elif '回前批' in src:
            ptype = '回前批'
        elif '回末评' in src:
            ptype = '回末评'
        elif '摘录' in src:
            ptype = '前80回摘录'
        by_hui[hui].append({
            'text': (r.get('text') or ''),
            'versions': ['癸酉本'],
            'page': None,
            'conf': None,
            'src_tag': '癸酉本',
            'ptype': ptype,
            'origin': src,
        })
    out = {}
    for h, lst in by_hui.items():
        out[str(h)] = lst[:MAX_PER_HUI]
    # 只统计 81–108 的
    n_hou = sum(len(v) for k, v in out.items() if 81 <= int(k) <= 108)
    log(f"  癸酉本: {len(rows):,} 条 → {len(out)} 回"
        f"（其中 81–108 回 {n_hou} 条）")
    return out


def main():
    log("=" * 74)
    log("S16 构建按版本分组的批语数据")
    log("=" * 74)

    by_version = {
        'fuqinju': {'by_hui': build_fuqinju()},
        'guiyouben': {'by_hui': build_guiyouben()},
        'chenggao': {'by_hui': {}},          # 程高本是删改本，无脂批
    }

    note = ('批语按版本隔离：抚琴居校本 3,234 条（含脂批大全互补由后端检索层处理）；'
            '癸酉本 223 条（后28回内嵌批 / 回前批 / 回末评 / 前80回摘录）；'
            '程高本为删改本，无脂批。')

    out = {
        'by_version': by_version,
        # 向后兼容：顶层 by_hui 仍指向抚琴居
        'by_hui': by_version['fuqinju']['by_hui'],
        'note': note,
    }

    path = os.path.join(WEB_DATA, 'pizhu.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False)
    log(f"\n已写 {path}  ({os.path.getsize(path):,} B)")

    # ---- 验证 ----
    log("\n=== 验证 ===")
    for ver, d in by_version.items():
        bh = d['by_hui']
        total = sum(len(v) for v in bh.values())
        log(f"  {ver:10s} {len(bh):>3} 回 / {total:>5} 条")
    log("")
    for h in ('1', '78', '81', '91', '106', '108'):
        f = len(by_version['fuqinju']['by_hui'].get(h, []))
        g = len(by_version['guiyouben']['by_hui'].get(h, []))
        log(f"  第{h:>3}回: 抚琴居 {f:>2} 条 | 癸酉本 {g:>2} 条")
    log("")
    sample = by_version['guiyouben']['by_hui'].get('91', [])
    if sample:
        log("  癸酉本第91回样例：")
        for x in sample[:3]:
            log(f"    [{x['ptype']}] {x['text'][:56]}")
    else:
        log("  ⚠ 癸酉本第91回无批语")


if __name__ == '__main__':
    main()
