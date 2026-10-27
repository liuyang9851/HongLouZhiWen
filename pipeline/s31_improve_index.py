# -*- coding: utf-8 -*-
"""
S31 · 索引与检索改进（两项）
============================

背景（S30 遗留的两个建议）
--------------------------
S30 修复了「宝玉为什么挨打」的检索失败，但留了两个待办：

  ① 回目词检索不到
     「笞挞」「不肖种种」这些词只出现在 huimu（回目）字段，
     而倒排索引只对 text 建 → 用户用回目词检索必然 0 命中。

  ② 关键词打分无 IDF 加权
     `scores[cid] += 1.0 + min(tf,2)*0.1` —— 所有 bigram 权重相同。
     实测：「宝玉」df=596（处处有），「打死」df=24（稀有），
     但两者权重都是 1.0，导致泛用词主导排序，
     「宝玉为什么挨打」修复后第33回排第一，但第67回仍居第二。

本脚本做的
----------
本脚本只负责**诊断与验证**（不直接改 HBase，避免误操作）。

真正的改动：
  · ① 需重建索引（把 huimu 加入 bigram 文本源）
  · ② 需改 backend/app.py 的 search_keyword 打分

运行：
  python s31_improve_index.py --check     # 诊断当前状态
  python s31_improve_index.py --patch     # 只打 backend 的 IDF 补丁
"""
import os
import sys
import json

APP = r"D:\学校事务\大数据原理与应用\红楼智问\backend\app.py"


def log(m):
    print(m, flush=True)


# ============================================================
# 改动① 的代码说明（需在 pipeline/s10_1_unify_pizhu.py 里改）
# ============================================================
INDEX_CHANGE_DOC = """
【改动①：把 huimu 纳入倒排索引】

原代码（pipeline/s10_1_unify_pizhu.py:493-495）：
    for bg, tf in bigrams(ch["text"]).items():
        postings[bg].append([ch["chunk_id"], tf])

问题：只用 text，回目词（笞挞、不肖种种、风尘怀闺秀…）进不了索引。

修改为：
    # 正文 + 回目都建索引：回目里的词（如「笞挞」）用户可能直接问
    _idx_text = (ch["text"] or "") + " " + (ch.get("huimu") or "")
    for bg, tf in bigrams(_idx_text).items():
        postings[bg].append([ch["chunk_id"], tf])

注意：**只影响倒排索引的召回面，不改变 chunk 正文内容**。
（用户在原文阅读里看到的仍是正文，不含回目）
"""


# ============================================================
# 改动② IDF 加权补丁
# ============================================================
OLD_SCORE_BLOCK = '''                for cid, tf in json.loads(row[b"p:postings"].decode()):
                    scores[cid] = scores.get(cid, 0) + 1.0 + min(tf, 2) * 0.1'''

NEW_SCORE_BLOCK = '''                # S31：IDF 加权
                # 原实现所有 bigram 权重都是 1.0，导致泛用词（宝玉 df=596）
                # 与情节专有词（打死 df=24）等权，泛用词主导排序。
                # 改为 idf = log(1 + N/df)，稀有词权重高。
                df = int(row.get(b"p:df", b"0").decode() or 0)
                idf = 1.0 + math.log((N_CHUNKS + 1.0) / (df + 1.0))
                for cid, tf in json.loads(row[b"p:postings"].decode()):
                    scores[cid] = scores.get(cid, 0) + idf * (1.0 + min(tf, 2) * 0.1)'''

# 需要在文件里补 math 导入与 N_CHUNKS 常量
OLD_HEADER = '''def search_keyword(query: str, topk: int = 30,
                   version: Optional[str] = None) -> List[Dict]:
    qgrams = list(bigrams(query).keys())'''

NEW_HEADER = '''# 语料 chunk 总数（用于 IDF 归一；启动后由 health 校验）
N_CHUNKS = int(os.getenv("HLM_N_CHUNKS", "11780"))


def search_keyword(query: str, topk: int = 30,
                   version: Optional[str] = None) -> List[Dict]:
    qgrams = list(bigrams(query).keys())'''


def check():
    log("=" * 70)
    log("S31 诊断：索引与打分现状")
    log("=" * 70)

    src = open(APP, encoding="utf-8").read()

    log("\n【改动② 状态】关键词打分是否已 IDF 加权")
    if "S31：IDF 加权" in src:
        log("  ✅ 已应用")
    elif OLD_SCORE_BLOCK in src:
        log("  ❌ 未应用（仍是等权打分）")
        log(f"     当前代码：{OLD_SCORE_BLOCK.strip()[:80]}...")
    else:
        log("  ⚠ 未找到预期代码块，可能已被其他改动覆盖")

    log("\n【改动① 状态】回目是否纳入索引")
    pipe = r"D:\学校事务\大数据原理与应用\红楼智问\pipeline\s10_1_unify_pizhu.py"
    if os.path.exists(pipe):
        psrc = open(pipe, encoding="utf-8").read()
        if "_idx_text" in psrc:
            log("  ✅ 已纳入（_idx_text 存在）")
        else:
            log("  ❌ 未纳入（只用 ch['text']）")
    else:
        log("  ⚠ 未找到 pipeline 脚本")

    log("\n" + "=" * 70)
    log("说明")
    log("=" * 70)
    log(INDEX_CHANGE_DOC)


def patch():
    log("=" * 70)
    log("S31 打补丁：IDF 加权")
    log("=" * 70)

    src = open(APP, encoding="utf-8").read()

    if "S31：IDF 加权" in src:
        log("✅ 已应用过，跳过")
        return

    if OLD_SCORE_BLOCK not in src:
        log("❌ 未找到预期代码块，放弃（请手工核对）")
        return

    if "import math" not in src:
        log("⚠ 文件未导入 math，需先补 import")
        src = src.replace("import os\n", "import os\nimport math\n", 1)

    if "N_CHUNKS" not in src:
        src = src.replace(OLD_HEADER, NEW_HEADER, 1)

    src = src.replace(OLD_SCORE_BLOCK, NEW_SCORE_BLOCK, 1)

    import shutil
    shutil.copyfile(APP, APP + ".bak_s31")
    open(APP, "w", encoding="utf-8").write(src)
    log(f"✅ 已写入（备份 {APP}.bak_s31）")

    import ast
    try:
        ast.parse(src)
        log("✅ 语法检查通过")
    except SyntaxError as e:
        log(f"❌ 语法错误：{e}")


if __name__ == "__main__":
    if "--patch" in sys.argv:
        patch()
    else:
        check()
