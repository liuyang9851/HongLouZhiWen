# -*- coding: utf-8 -*-
"""
S21-1 · 自定义查询：语料 API（沙箱内可用的只读接口）
====================================================

设计原则
--------
沙箱代码**不能直接 open()**，只能通过本模块提供的函数读数据。
这样即使 AI 生成了恶意代码，也无法碰文件系统。

暴露给沙箱代码的 API（放在 builtins 里，沙箱代码直接调用）
----------------------------------------------------------
  load_chapter(ver, hui) -> str          读某回全文
  load_all_text(ver) -> str              读某版本全书正文
  load_pizhu(ver) -> list[str]           读批语
  load_person_roles() -> dict            人物身份表
  load_stats() -> dict                   统计汇总
  load_chapters(ver) -> list[dict]       逐回结构化数据
  VERSIONS                               可用版本列表

数据来源
--------
  /workspace/pipeline/out/chapters_{ver}.jsonl   逐回正文（挂载卷）
  /app/person_roles.json                          人物身份表
  /app/stats_summary.json                         统计汇总

运行位置：hlm-api 容器内
"""
import os
import json
import functools

WEB_DATA = os.environ.get("HLM_WEB_DATA", "/workspace/pipeline/out")
APP_DIR = "/app"

VERSIONS = ["fuqinju", "chenggao", "guiyouben"]
VERSION_LABELS = {
    "fuqinju": "抚琴居校本",
    "chenggao": "程高本",
    "guiyouben": "癸酉本",
}


def _chapter_path(ver):
    return os.path.join(WEB_DATA, f"chapters_{ver}.jsonl")


@functools.lru_cache(maxsize=8)
def _load_chapters_cached(ver):
    """载入某版本全部回次（带缓存，同一进程内复用）"""
    if ver not in VERSIONS:
        raise ValueError(f"未知版本 {ver!r}，可用：{VERSIONS}")
    p = _chapter_path(ver)
    if not os.path.exists(p):
        raise FileNotFoundError(f"语料不存在：{p}")
    raw = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    raw.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    # ---- 去重（按 hui）----
    # 实测问题：第67回在原语料里有 3 条记录（21,705 / 12,487 / 9,195 字）。
    # 原因：hlm_corpus/fuqinju/ 下同时存在 067_合并.txt、067a.txt、067b.txt，
    #       原提取脚本把三个文件都当成了独立的「回」。
    # 修法：同一 hui 只保留**字数最多**的那条（合并版最完整）。
    best = {}
    for r in raw:
        h = int(r.get("hui") or 0)
        n = sum(len(x) for x in (r.get("paragraphs") or []))
        if h not in best or n > best[h][0]:
            best[h] = (n, r)
    out = [best[h][1] for h in sorted(best)]
    return out


def load_chapters(ver="fuqinju"):
    """
    返回某版本的全部回次数据（深拷贝，防止沙箱代码改动缓存）。

    返回：[{"version":"fuqinju","hui":1,"huimu":"...","paragraphs":["...",...]}, ...]
    """
    import copy
    return copy.deepcopy(_load_chapters_cached(ver))


def load_chapter(ver, hui):
    """
    读某一回全文（段落用换行拼接）。

    >>> load_chapter("fuqinju", 3)[:20]
    """
    for c in _load_chapters_cached(ver):
        if int(c.get("hui") or 0) == int(hui):
            return "\n".join(c.get("paragraphs") or [])
    raise ValueError(f"{ver} 第 {hui} 回不存在")


def load_all_text(ver="fuqinju"):
    """
    读某版本全书正文（所有回次段落拼接）。

    >>> len(load_all_text("fuqinju")) > 600000
    True
    """
    parts = []
    for c in _load_chapters_cached(ver):
        parts.extend(c.get("paragraphs") or [])
    return "\n".join(parts)


def load_pizhu(ver="fuqinju"):
    """
    读某版本批语文本列表。

    数据来源：
      · fuqinju   → /app/pizhu_fuqinju.json（构建镜像时生成）
      · guiyouben → /app/pizhu_guiyouben.json
      · chenggao  → 空（删改本无脂批）
    """
    p = os.path.join(APP_DIR, f"pizhu_{ver}.json")
    if ver == "chenggao":
        return []
    if not os.path.exists(p):
        return []
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        return data.get("texts") or []
    except Exception:
        return []


def load_person_roles():
    """人物身份聚合表"""
    p = os.path.join(APP_DIR, "person_roles.json")
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def load_stats():
    """统计汇总（词频 / 人物频次 / 批语分布等）"""
    p = os.path.join(APP_DIR, "stats_summary.json")
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def husk(name):
    """把某个名字转成「之」这种可统计的形式时用不到，仅保留扩展位"""


# 沙箱代码可见的 API 字典
SANDBOX_API = {
    "load_chapters": load_chapters,
    "load_chapter": load_chapter,
    "load_all_text": load_all_text,
    "load_pizhu": load_pizhu,
    "load_person_roles": load_person_roles,
    "load_stats": load_stats,
    "VERSIONS": VERSIONS,
    "VERSION_LABELS": VERSION_LABELS,
}


if __name__ == "__main__":
    # 自检
    print(f"VERSIONS = {VERSIONS}")
    for v in VERSIONS:
        try:
            cs = load_chapters(v)
            txt = load_all_text(v)
            pz = load_pizhu(v)
            print(f"  {v:10s} {len(cs):>3} 回 / {len(txt):>8,} 字 / 批语 {len(pz):>5} 条")
        except Exception as e:
            print(f"  {v:10s} ERR: {e}")
    print(f"  person_roles: {len(load_person_roles())} 人")
    print(f"  stats keys: {list(load_stats().keys())[:8]}")
    # 实测题：全文「之」字
    t = load_all_text("fuqinju")
    print(f"\n[实测] 抚琴居本全文「之」字出现 {t.count('之')} 次（共 {len(t):,} 字）")
