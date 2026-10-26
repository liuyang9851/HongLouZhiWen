/**
 * rag.js —— 前端对接 RAG 后端（S4）
 *
 * 设计要点
 * --------
 * 1. 所有请求经 Node 服务代理（/api/backend/*），前端不直连后端
 *    → 避免 CORS，也避免密钥/内网地址暴露给浏览器
 * 2. 优先用流式（SSE）拿答案，失败自动降级到非流式
 * 3. 后端不可用时，前端用本地 /api/locate 做「纯检索模式」兜底
 *    → 保证演示时即使后端挂了，引用跳转仍能工作
 */

const BACKEND = '/api/backend';

/** 后端是否可用（启动时探测一次，之后每 30s 刷新） */
export const backendStatus = { ok: false, info: null, checkedAt: 0 };

export async function probeBackend(timeoutMs = 2500) {
  try {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), timeoutMs);
    const r = await fetch(`${BACKEND}/health`, { signal: ctl.signal });
    clearTimeout(t);
    if (!r.ok) throw new Error(String(r.status));
    backendStatus.ok = true;
    backendStatus.info = await r.json();
  } catch {
    backendStatus.ok = false;
    backendStatus.info = null;
  }
  backendStatus.checkedAt = Date.now();
  return backendStatus;
}

/** 拉取可用模型列表 */
export async function fetchModels() {
  try {
    const r = await fetch(`${BACKEND}/models`);
    if (!r.ok) return null;
    return await r.json();
  } catch {
    return null;
  }
}

/**
 * 流式问答
 * @param {object} p
 * @param {string} p.question
 * @param {string} p.model
 * @param {Array}  p.history      [{role, content}]
 * @param {string} [p.contextText]  划选/段落的原文
 * @param {number} [p.topk]
 * @param {boolean}[p.thinking]
 * @param {(delta:string)=>void} p.onDelta        正文增量
 * @param {(reason:string)=>void} [p.onReasoning] 思考增量
 * @param {(meta:object)=>void} [p.onMeta]        结束时的引用/检索元信息
 */
export async function streamChat(p) {
  const body = {
    question: p.question,
    model: p.model || 'deepseek-flash',
    history: p.history || [],
    context_text: p.contextText || null,
    use_graph: true,
    thinking: !!p.thinking,
    stream: true,
    topk: p.topk || 5,
    version: p.version || null,
  };

  const resp = await fetch(`${BACKEND}/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!resp.ok || !resp.body) {
    const txt = await resp.text().catch(() => '');
    throw new Error(`后端返回 ${resp.status}: ${txt.slice(0, 200)}`);
  }

  const reader = resp.body.getReader();
  const dec = new TextDecoder('utf-8');
  let buf = '';
  let meta = null;
  let answer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });

    // SSE：按空行切事件
    let idx;
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const raw = buf.slice(0, idx).trim();
      buf = buf.slice(idx + 2);
      if (!raw.startsWith('data:')) continue;
      const payload = raw.slice(5).trim();
      if (payload === '[DONE]') continue;
      let obj;
      try { obj = JSON.parse(payload); } catch { continue; }

      if (obj.error) throw new Error(obj.error);
      if (obj.delta) { answer += obj.delta; p.onDelta?.(obj.delta); }
      if (obj.reasoning) p.onReasoning?.(obj.reasoning);
      if (obj.meta) { meta = obj.meta; }
    }
  }

  // meta 里带 answer_full（后端拼接的完整答案），比对一下
  if (meta?.answer_full && !answer) { answer = meta.answer_full; p.onDelta?.(answer); }
  p.onMeta?.(meta || {});
  return { answer, meta };
}

/** 非流式问答（降级用） */
export async function plainChat(p) {
  const body = {
    question: p.question,
    model: p.model || 'deepseek-flash',
    history: p.history || [],
    context_text: p.contextText || null,
    use_graph: true,
    thinking: !!p.thinking,
    stream: false,
    topk: p.topk || 5,
    version: p.version || null,
  };
  const r = await fetch(`${BACKEND}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`后端 ${r.status}`);
  return await r.json();
}

/** 只检索（调试/演示用） */
export async function retrieveOnly(q, topk = 5, version = null) {
  let u = `${BACKEND}/retrieve?q=${encodeURIComponent(q)}&topk=${topk}`;
  if (version) u += `&version=${encodeURIComponent(version)}`;
  const r = await fetch(u);
  if (!r.ok) throw new Error(`后端 ${r.status}`);
  return await r.json();
}

/** 统计问答（R-208） */
export async function statsAsk(topic) {
  const r = await fetch(`${BACKEND}/stats/ask?topic=${encodeURIComponent(topic)}`);
  if (!r.ok) throw new Error(`后端 ${r.status}`);
  return await r.json();
}

/* ============================================================
 * 模型提供方管理（问题 5：让用户自己填 API 地址 / 密钥 / 模型名）
 * ============================================================ */

/** 拉取全部模型配置（含内置 + 自定义，不含密钥明文） */
export async function getProviders() {
  const r = await fetch(`${BACKEND}/providers`);
  if (!r.ok) throw new Error(`后端 ${r.status}`);
  return await r.json();
}

/** 新增/修改一个模型配置 */
export async function saveProvider(cfg) {
  const r = await fetch(`${BACKEND}/providers`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(cfg),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || `后端 ${r.status}`);
  return j;
}

/** 删除一个自定义模型配置 */
export async function deleteProvider(id) {
  const r = await fetch(`${BACKEND}/providers/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || `后端 ${r.status}`);
  return j;
}

/** 测试一个模型配置是否可用（填完立刻知道对不对） */
export async function testProvider(cfg) {
  const r = await fetch(`${BACKEND}/providers/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(cfg),
  });
  return await r.json().catch(() => ({ ok: false, error: `后端 ${r.status}` }));
}

/** 三个正文版本（s10 后，脂批来源单独列） */
export const VERSIONS = [
  { id: 'fuqinju', label: '抚琴居校本', desc: '前 80 回 · 脂批 3,219 条 + 脂批大全 5,069 条', maxHui: 80 },
  { id: 'chenggao', label: '程高本', desc: '120 回全本 · 删改本，无脂批', maxHui: 120 },
  { id: 'guiyouben', label: '癸酉本', desc: '28 回（第 81–108 回）· 自带批语 222 条', maxHui: 108 },
];

/** 脂批来源（s10 三源统一后） */
export const PIZHU_SOURCES = [
  { id: 'fuqinju', label: '抚琴居校本脂批', desc: '3,219 条 · PDF 码点重解码，版本归属最可信', ver: 'fuqinju' },
  { id: 'guiyouben', label: '癸酉本批语', desc: '222 条 · 后28回内嵌批/回前批/回末评/前80回摘录', ver: 'guiyouben' },
  { id: 'zhipi', label: '脂批大全', desc: '5,069 条 · 独立汇编，补前两源之缺', ver: 'zhipi' },
];

/** 拉取语料/索引盘点 */
export async function getCorpusStats() {
  const r = await fetch(`${BACKEND}/corpus/stats`);
  if (!r.ok) throw new Error(`后端 ${r.status}`);
  return await r.json();
}

/** 自定义查询：让 AI 写代码并执行 */
export async function customQuery({ question, model, version, signal }) {
  const r = await fetch(`${BACKEND}/query/custom`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, model, version }),
    signal,
  });
  if (!r.ok) {
    const t = await r.text().catch(() => '');
    throw new Error(`自定义查询失败 ${r.status}: ${t.slice(0, 200)}`);
  }
  return await r.json();
}

/** 自定义查询示例 */
export async function fetchQueryExamples() {
  const r = await fetch(`${BACKEND}/query/examples`);
  if (!r.ok) return { examples: [] };
  return await r.json();
}
