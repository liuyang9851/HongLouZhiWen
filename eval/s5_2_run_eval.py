# -*- coding: utf-8 -*-
"""
S5 评测 · 阶段 2：执行评测
===========================

对应需求 R-801 ~ R-806
对应难点分析§四：**事实型与推理型分开报告，绝不合成单一准确率**

三类评测各行其道
----------------
  事实型（27 条）· 自动评
     - keyword_coverage : 答案里命中了几个答案关键词（宽松「答到点没」）
     - citation_hit@k   : Top-k 引用里有没有落在正确回次（可追溯性）
     - answer_in_ref    : 标准答案关键片段是否出现在回答里
     - 统计型单独看（f027-f030 的 golden 来自 MapReduce，需人工核对口径）

  推理型（15 条）· 人工评
     - 只导出「问题 + 参考要点 + 系统回答」给人工打分
     - 不自动评分（没有客观标准）

  应拒答（10 条）· 自动判定
     - correct_refusal : 系统是否明确说「语料未找到」而不是编造
     - 关键词检测 + 长度检测

关键设计
--------
  - **通过 API 调真实后端**，不是 mock
  - 保存每条完整记录（问题/答案/引用/耗时/检索debug），供人工复核
  - 断点续跑：已完成的条目跳过（省钱、可中断）

运行（Windows 本地，需后端 8000 在线）
    python s5_2_run_eval.py --set factual
    python s5_2_run_eval.py --set reasoning
    python s5_2_run_eval.py --set refuse
    python s5_2_run_eval.py --set all
"""
import os
import sys
import json
import time
import argparse
import urllib.request
import urllib.error

sys.stdout.reconfigure(encoding="utf-8")

EVAL_DIR = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(EVAL_DIR, "results")
os.makedirs(OUT, exist_ok=True)

BACKEND = os.getenv("HLM_BACKEND", "http://127.0.0.1:8000")
MODEL = os.getenv("HLM_EVAL_MODEL", "deepseek-flash")

# 拒答判定的关键词（系统应说明「语料没有」而不是硬答）
REFUSE_MARKERS = [
    "未找到", "没有找到", "未检索到", "没有检索到", "语料中未",
    "无法回答", "不能据此", "没有直接", "无法确定", "不理解",
    "不在《红楼梦》", "超出", "无法从", "没有相关", "不存在",
    "不能回答", "无从", "未提及", "没有提及",
]


def post(path, body, timeout=180):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        BACKEND + path, data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def get(path, timeout=60):
    with urllib.request.urlopen(BACKEND + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def load(name):
    with open(os.path.join(EVAL_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def save(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


# ============================================================
# 事实型
# ============================================================
def eval_factual():
    items = load("eval_factual.json")
    done_path = os.path.join(OUT, "factual_results.json")
    done = {}
    if os.path.exists(done_path):
        for r in json.load(open(done_path, encoding="utf-8")):
            done[r["id"]] = r
        print(f"已加载已完成 {len(done)} 条（断点续跑）")

    results = []
    for i, it in enumerate(items, 1):
        qid = it["id"]
        if qid in done:
            results.append(done[qid])
            continue

        print(f"[{i}/{len(items)}] {qid}  {it['question'][:40]}")
        t0 = time.time()
        try:
            resp = post("/chat", {
                "question": it["question"],
                "model": MODEL,
                "stream": False,
                "topk": 5,
                "thinking": False,
            })
            answer = resp.get("answer", "")
            cits = resp.get("citations", [])
            refused = resp.get("refused", False)
            err = None
        except Exception as e:
            answer, cits, refused = "", [], True
            err = str(e)
            print(f"   ❌ {err}")

        # ---- 自动指标 ----
        kws = it.get("answer_keywords", [])
        hit_kws = [k for k in kws if k in answer]
        kw_cov = round(len(hit_kws) / max(len(kws), 1), 3)

        ev = it.get("evidence")
        cite_hui = [c.get("hui") for c in cits]
        cite_hit = None
        if ev:
            cite_hit = 1 if ev["hui"] in cite_hui else 0
        # 是否命中了正确段落（更严格）
        cite_para_hit = None
        if ev:
            cite_para_hit = 1 if any(
                c.get("hui") == ev["hui"] and
                c.get("para_start", -1) <= ev["para_index"] <= c.get("para_end", -1)
                for c in cits) else 0

        # 标准答案是否被覆盖（宽松：答案关键片段出现）
        ans_ok = 1 if any(k in answer for k in kws) else 0

        rec = {
            "id": qid,
            "question": it["question"],
            "golden_answer": it["answer"],
            "system_answer": answer,
            "citations": [{k: c.get(k) for k in
                           ("idx", "hui", "para_start", "para_end", "rrf", "version")}
                          for c in cits],
            "metrics": {
                "keyword_coverage": kw_cov,
                "hit_keywords": hit_kws,
                "cite_hui_hit": cite_hit,
                "cite_para_hit": cite_para_hit,
                "answer_ok": ans_ok,
                "refused": refused,
            },
            "evidence": ev,
            "stat_based": it.get("stat_based", False),
            "note": it.get("note", ""),
            "elapsed_s": round(time.time() - t0, 2),
            "error": err,
        }
        results.append(rec)
        save(done_path, results)
        time.sleep(0.5)

    save(done_path, results)
    summarize_factual(results)
    return results


def summarize_factual(results):
    n = len(results)
    auto = [r for r in results if not r["stat_based"]]
    stat = [r for r in results if r["stat_based"]]

    def avg(lst, key):
        vals = [r["metrics"][key] for r in lst if r["metrics"].get(key) is not None]
        return round(sum(vals) / len(vals), 4) if vals else None

    print("\n" + "=" * 74)
    print("事实型评测结果（自动评）")
    print("=" * 74)
    print(f"  样本数: {n}（正文型 {len(auto)} + 统计型 {len(stat)}）")
    print(f"\n  【正文型】")
    print(f"    答案关键词覆盖率 (keyword_coverage) : {avg(auto, 'keyword_coverage')}")
    print(f"    引用回次命中率 (cite_hui_hit)        : {avg(auto, 'cite_hui_hit')}")
    print(f"    引用段落命中率 (cite_para_hit)       : {avg(auto, 'cite_para_hit')}")
    print(f"    答案命中率 (answer_ok)               : {avg(auto, 'answer_ok')}")
    print(f"\n  【统计型】(需人工核对口径)")
    print(f"    答案关键词覆盖率                     : {avg(stat, 'keyword_coverage')}")
    print(f"    答案命中率                           : {avg(stat, 'answer_ok')}")
    print(f"\n  有引用的回答占比: "
          f"{round(sum(1 for r in results if r['citations']) / n, 3)}")
    print(f"  拒答率: {round(sum(1 for r in results if r['metrics']['refused']) / n, 3)}")

    # 失败清单
    bad = [r for r in results if r["metrics"]["answer_ok"] == 0]
    if bad:
        print(f"\n  ⚠ 未答到的 {len(bad)} 条：")
        for r in bad[:10]:
            print(f"    {r['id']}  {r['question'][:36]}")
            print(f"      期望: {r['golden_answer'][:50]}")
            print(f"      实答: {r['system_answer'][:80]}")
    print("=" * 74)


# ============================================================
# 推理型（只导出，不自动评）
# ============================================================
def eval_reasoning():
    items = load("eval_reasoning.json")
    out = []
    print(f"推理型 {len(items)} 条（只生成，不自动评分）")
    for i, it in enumerate(items, 1):
        print(f"[{i}/{len(items)}] {it['id']}  {it['question'][:40]}")
        t0 = time.time()
        try:
            resp = post("/chat", {
                "question": it["question"], "model": MODEL,
                "stream": False, "topk": 5, "thinking": False,
            })
            answer = resp.get("answer", "")
            cits = resp.get("citations", [])
            err = None
        except Exception as e:
            answer, cits, err = "", [], str(e)
            print(f"   ❌ {err}")
        out.append({
            "id": it["id"],
            "question": it["question"],
            "reference_answer": it["reference_answer"],
            "system_answer": answer,
            "citations": [{k: c.get(k) for k in ("idx", "hui", "para_start", "rrf")}
                          for c in cits],
            "human_score": None,     # 待人工填 1-5
            "human_scorer": None,
            "human_note": None,
            "elapsed_s": round(time.time() - t0, 2),
            "error": err,
        })
        time.sleep(0.5)

    p = os.path.join(OUT, "reasoning_results.json")
    save(p, out)
    print(f"\n已保存: {p}")
    print("⚠ 推理型需人工打分：编辑 reasoning_results.json 的 human_score 字段（1-5）")
    return out


# ============================================================
# 应拒答
# ============================================================
def eval_refuse():
    items = load("eval_refuse.json")
    out = []
    print(f"应拒答 {len(items)} 条")
    for i, it in enumerate(items, 1):
        print(f"[{i}/{len(items)}] {it['id']}  {it['question'][:40]}")
        try:
            resp = post("/chat", {
                "question": it["question"], "model": MODEL,
                "stream": False, "topk": 5, "thinking": False,
            })
            answer = resp.get("answer", "")
            refused_flag = resp.get("refused", False)
            err = None
        except Exception as e:
            answer, refused_flag, err = "", True, str(e)

        # 拒答判定：显式标记 OR 文本里含拒答词
        marker_hit = [m for m in REFUSE_MARKERS if m in answer]
        judged_refuse = bool(refused_flag or marker_hit)
        out.append({
            "id": it["id"],
            "question": it["question"],
            "reason": it["reason"],
            "system_answer": answer,
            "refused_flag": refused_flag,
            "refuse_markers": marker_hit,
            "correct_refusal": 1 if judged_refuse else 0,
            "error": err,
        })
        time.sleep(0.5)

    p = os.path.join(OUT, "refuse_results.json")
    save(p, out)
    rate = sum(r["correct_refusal"] for r in out) / max(len(out), 1)
    print(f"\n正确拒答率: {round(rate, 3)}  ({sum(r['correct_refusal'] for r in out)}/{len(out)})")
    wrong = [r for r in out if not r["correct_refusal"]]
    if wrong:
        print(f"⚠ 未正确拒答 {len(wrong)} 条：")
        for r in wrong:
            print(f"  {r['id']}  {r['question'][:36]}")
            print(f"    实答: {r['system_answer'][:100]}")
    print(f"已保存: {p}")
    return out


# ============================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="all",
                    choices=["all", "factual", "reasoning", "refuse"])
    args = ap.parse_args()

    # 后端健康检查
    try:
        h = get("/health")
        print(f"后端: {h.get('n_chunks')} chunk / {h.get('n_vectors')} 向量 / "
              f"模型 {h.get('default_model')}")
    except Exception as e:
        print(f"❌ 后端不可用: {e}")
        sys.exit(1)

    if args.set in ("all", "factual"):
        eval_factual()
    if args.set in ("all", "reasoning"):
        eval_reasoning()
    if args.set in ("all", "refuse"):
        eval_refuse()


if __name__ == "__main__":
    main()
