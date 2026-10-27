# -*- coding: utf-8 -*-
"""
S32 · RAG 流程升级：由模型生成检索关键词
========================================

改动动机
--------
原流程用**规则表**做查询改写（REWRITE_RULES），S30 已暴露其脆弱性：

  · 规则是人工枚举的，覆盖不到的领域就失效
    （如「宝玉为什么挨打」曾被注入「进/入/到/接/送」等无关词）
  · 规则无法理解语义
    （「挨打」→「笞挞/板子/打死」这种映射，人写得完吗？）

新流程
------
```
用户原问题
   ↓
① 由 LLM 生成检索关键词（理解语义 + 红楼专名归一）
   ↓
② 组合检索：原问题 + 关键词（都送 L1 关键词 + L2 向量）
   ↓
③ RRF 融合 + 同回次去重
   ↓
④ 生成回答
```

关键设计
--------
1. **保留规则改写作为补充**，不是替换
   LLM 可能超时/失败，规则是兜底；两者结果取并集。

2. **LLM 只做"关键词抽取"，不做回答**
   输出格式严格约束为 JSON，避免跑题。

3. **retrieve() 保持同步签名**
   内部用 `asyncio.run` 或线程池桥接 async 的 call_llm。
   若在已有 event loop 中（FastAPI），用 `run_in_executor`。

4. **加超时与降级**
   LLM 生成关键词给 8 秒预算；超时则只用规则改写。

输出格式（强约束）
------------------
```json
{
  "keywords": ["笞挞", "板子", "打死", "贾政", "金钏", "琪官"],
  "reasoning": "问的是宝玉挨打的原因，应检索第33回相关场景"
}
```

运行：
  python s32_llm_keywords.py --check    # 诊断
  python s32_llm_keywords.py --apply    # 写入
"""
import io
import sys

APP = r"D:\学校事务\大数据原理与应用\红楼智问\backend\app.py"


# ============================================================
# 要插入的代码块
# ============================================================

KEYWORD_PROMPT = '''KEYWORD_SYS_PROMPT = """你是《红楼梦》文本检索专家。用户会给你一个问题，
你要提取用于全文检索的**关键词**。

【任务】
把用户问题转换成一组检索词，要求：
1. **用原文可能出现的词**，不要用现代口语
   · 「挨打」→「笞挞」「板子」「打死」（第33回正文用「板子」「打死」）
   · 「被撵」→「撵出去」「逐出」「打发出去」
   · 「死了」→「没了」「去世」「亡故」「殡天」
2. **补全专名**：简称 → 全名
   · 宝玉 → 贾宝玉　黛玉 → 林黛玉　凤姐 → 王熙凤　宝钗 → 薛宝钗
3. **补上可能相关的人物/物件/地点**（帮助定位情节）
   · 「宝玉挨打」→ 贾政、金钏、琪官、忠顺王府
4. 关键词 3~8 个，每个 1~4 字
5. 不要输出解释性长句，只要词

【输出格式】严格输出 JSON，不要任何其他文字：
{"keywords": ["词1", "词2", "词3"], "reason": "一句话说明检索思路"}

【示例】
问题：宝玉为什么挨打？
{"keywords": ["板子", "打死", "贾政", "金钏", "琪官"], "reason": "第33回宝玉被贾政笞挞，起因是琪官与金钏两事"}
"""


async def gen_keywords(question: str, model_id: str,
                       timeout: float = 8.0) -> List[str]:
    """
    由 LLM 生成检索关键词。失败/超时返回空列表（调用方用规则改写兜底）。
    """
    import asyncio
    try:
        out = await asyncio.wait_for(
            call_llm(model_id,
                     [{"role": "system", "content": KEYWORD_SYS_PROMPT},
                      {"role": "user", "content": f"问题：{question}"}],
                     stream=False, thinking=False, temperature=0.1),
            timeout=timeout)
        raw = (out.get("content") or "").strip()
        # 容错：可能被 ```json 包裹
        raw = _re.sub(r"^```(?:json)?\\s*|\\s*```$", "", raw).strip()
        m = _re.search(r"\\{.*\\}", raw, _re.S)
        if not m:
            return []
        obj = json.loads(m.group(0))
        kws = obj.get("keywords") or []
        # 清洗：只保留中文，长度 1~8
        clean = []
        for k in kws:
            k = str(k).strip()
            if 1 <= len(k) <= 8 and _re.fullmatch(r"[\\u4e00-\\u9fff]+", k):
                clean.append(k)
        return clean[:8]
    except asyncio.TimeoutError:
        log(f"关键词生成超时（>{timeout}s），降级到规则改写")
        return []
    except Exception as e:
        log(f"关键词生成失败: {type(e).__name__}: {e}")
        return []


def gen_keywords_sync(question: str, model_id: str,
                      timeout: float = 8.0) -> List[str]:
    """
    同步包装（retrieve 是同步函数，需要用它）。

    两种情况：
      · 无 running loop（脚本/测试）→ asyncio.run
      · 有 running loop（FastAPI）→ 新线程里 run，避免 "already running" 错误
    """
    import asyncio
    import concurrent.futures

    try:
        asyncio.get_running_loop()
        in_loop = True
    except RuntimeError:
        in_loop = False

    if not in_loop:
        return asyncio.run(gen_keywords(question, model_id, timeout))

    # 已在 event loop 里 → 丢到线程池执行
    def _worker():
        return asyncio.run(gen_keywords(question, model_id, timeout))

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(_worker).result(timeout=timeout + 3)
'''


def main():
    apply = "--apply" in sys.argv

    src = io.open(APP, encoding="utf-8").read()

    print("=" * 72)
    print("S32 RAG 流程升级：LLM 生成检索关键词")
    print("=" * 72)

    # ---- 前置检查 ----
    print("\n【前置检查】")
    checks = [
        ("call_llm 存在", "async def call_llm(" in src),
        ("rewrite_query 存在", "def rewrite_query(" in src),
        ("_re 别名存在", "import re as _re" in src),
        ("retrieve 是同步函数", "def retrieve(question" in src),
        ("已有 KEYWORD_SYS_PROMPT", "KEYWORD_SYS_PROMPT" in src),
    ]
    for name, ok in checks:
        print(f"  {'✅' if ok else '❌'} {name}")

    if not all(ok for _, ok in checks[:4]):
        print("\n❌ 前置条件不满足，中止")
        return

    if "KEYWORD_SYS_PROMPT" in src:
        print("\n✅ 已应用过")
        return

    # ---- 找插入点：rewrite_query 函数之前 ----
    anchor = "def rewrite_query(question: str) -> List[str]:"
    idx = src.index(anchor)

    new_src = src[:idx] + KEYWORD_PROMPT + "\n\n" + src[idx:]

    if not apply:
        print("\n【将要插入的代码】")
        print("-" * 72)
        print(KEYWORD_PROMPT[:1200] + "\n...(省略)")
        print("-" * 72)
        print("\n（未写入。加 --apply 才会改）")
        return

    import shutil
    shutil.copyfile(APP, APP + ".bak_s32")
    io.open(APP, "w", encoding="utf-8").write(new_src)
    print(f"\n✅ 已插入 gen_keywords（备份 {APP}.bak_s32）")

    import ast
    try:
        ast.parse(new_src)
        print("✅ 语法检查通过")
    except SyntaxError as e:
        print(f"❌ 语法错误：{e}")


if __name__ == "__main__":
    main()
