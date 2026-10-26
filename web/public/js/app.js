// S29B-FONT-SCALED
/* S29-FONT-SCALED */
/**
 * app.js —— 红楼智问前端主程序（阶段 S1：界面设计预览）
 *
 * 结构：
 *   - router           hash 路由 → 6 个界面
 *   - 全局状态         语料元信息、当前模型、后端可用性
 *   - 界面模块         chat / reader / stats / graph / corpus / ops
 *   - 跨界面交互       「就这段提问」把上下文带到问答界面（R-108）
 *
 * 本阶段所有问答内容为「检索结果 + 占位」，真实 LLM 生成在 S4 接入。
 * 但检索是真的：/api/locate 会在真实语料里定位。
 */
import { bar, hbar, line, pie, heat, force, PALETTE } from './chart.js';
import { force3d, surface3d, chord } from './chart3d.js';
import {
  probeBackend, backendStatus, fetchModels, streamChat, plainChat, statsAsk,
  getProviders, saveProvider, deleteProvider, testProvider, VERSIONS,
  customQuery, fetchQueryExamples,
  getCorpusStats,
} from './rag.js';
import { renderMarkdown, escapeHtml as mdEsc } from './md.js';
import { openProviderPanel } from './providers.js';
import { renderHome } from './page_home.js';
import { renderGuiyou } from './page_guiyou.js';
import { renderPrinciple } from './page_principle.js';

/* ============================== 工具 ============================== */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const el = (tag, cls, html) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html !== undefined) n.innerHTML = html;
  return n;
};
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// ---- 供 page_*.js 使用的共享工具（避免模块循环依赖）----
window.__HLM__ = {
  esc, el, api,
  get STARTERS() { return STARTERS; },
  get state() { return state; },
  get autoGrow() { return autoGrow; },
  get send() { return send; },
};
const fmt = (n) => Number(n).toLocaleString('zh-CN');

/** 回目缩短：「第3回 金陵城起复贾雨村 荣国府收养林黛玉」→「金陵城起复贾雨村 …」 */
function shortTitle(t) {
  const m = String(t || '').match(/^第?[\d一二三四五六七八九十百]+回[\s　]*(.*)$/);
  const body = (m ? m[1] : String(t || '')).trim();
  return body.length > 14 ? body.slice(0, 14) + '…' : (body || String(t || ''));
}

function toast(msg, kind = '') {
  const t = el('div', 'toast ' + kind, esc(msg));
  $('#toastWrap').appendChild(t);
  setTimeout(() => {
    t.style.transition = 'opacity .3s, transform .3s';
    t.style.opacity = '0';
    t.style.transform = 'translateY(6px)';
    setTimeout(() => t.remove(), 320);
  }, 2200);
}

async function api(path) {
  // 加时间戳 + no-store：数据文件（graphs/stats）常被浏览器缓存导致显示过期
  const sep = path.includes('?') ? '&' : '?';
  const r = await fetch(path + sep + '_=' + Date.now(), { cache: 'no-store' });
  if (!r.ok) throw new Error(`${r.status} ${path}`);
  return r.json();
}

/* ============================== 全局状态 ============================== */
const state = {
  meta: null,        // chapters.json
  stats: null,
  graph: null,
  model: 'deepseek-flash',
  backendOk: false,
  pendingContext: null,   // 划选提问带入的上下文（R-108）
  sessions: [],
  activeSession: null,
};

/* ============================== 划选提问（R-108） ============================== */
let selTimer = null;
function initSelectionPopup() {
  const pop = $('#selPop');
  const stage = $('#stage');

  document.addEventListener('mouseup', (ev) => {
    if (pop.contains(ev.target)) return;
    clearTimeout(selTimer);
    selTimer = setTimeout(() => {
      const sel = window.getSelection();
      const text = (sel?.toString() ?? '').trim();
      if (!text || text.length < 2 || text.length > 600) { pop.hidden = true; return; }
      // 只在正文/答案区域内触发
      let inContent = false;
      const anchor = sel.anchorNode?.parentElement;
      if (anchor && (anchor.closest('.rd-paper') || anchor.closest('.msg.bot') || anchor.closest('.chart-card'))) {
        inContent = true;
      }
      if (!inContent || !stage.contains(sel.anchorNode)) { pop.hidden = true; return; }

      const rect = sel.getRangeAt(0).getBoundingClientRect();
      pop.hidden = false;
      pop.style.left = Math.min(Math.max(8, rect.left + rect.width / 2 - 70), window.innerWidth - 160) + 'px';
      pop.style.top = Math.max(8, rect.top - 44) + 'px';
      pop.dataset.text = text;
      console.debug('[sel]', text.length, text.slice(0,20));
      // 记录来源位置（供答案引用定位）
      const pEl = anchor.closest('[data-para-id]');
      if (pEl) {
        pop.dataset.hui = pEl.dataset.hui || '';
        pop.dataset.paraId = pEl.dataset.paraId || '';
      } else {
        delete pop.dataset.hui; delete pop.dataset.paraId;
      }
    }, 10);
  });

  pop.addEventListener('click', (ev) => {
    const act = ev.target.dataset.act;
    if (act === 'copy') {
      navigator.clipboard?.writeText(pop.dataset.text || '');
      toast('已复制');
      pop.hidden = true;
      return;
    }
    if (act === 'ask') {
      state.pendingContext = {
        text: pop.dataset.text,
        hui: pop.dataset.hui ? Number(pop.dataset.hui) : null,
        paraId: pop.dataset.paraId || null,
      };
      pop.hidden = true;
      location.hash = '#/chat';
      setTimeout(() => {
        const ta = $('#chatTextarea');
        if (ta) {
          ta.value = `请解释这段文字：${state.pendingContext.text.slice(0, 120)}${state.pendingContext.text.length > 120 ? '…' : ''}`;
          ta.focus();
          autoGrow(ta);
          renderContextChip();
        }
      }, 120);
    }
  });

  document.addEventListener('mousedown', (ev) => {
    if (!pop.hidden && !pop.contains(ev.target)) pop.hidden = true;
  });
}

/* ============================== 路由 ============================== */
const VIEWS = ['home', 'principle', 'chat', 'reader', 'stats', 'graph', 'guiyou', 'corpus', 'ops'];
let currentForce = null;

async function route() {
  const hash = location.hash.replace(/^#\/?/, '') || 'home';
  const view = VIEWS.includes(hash.split('/')[0]) ? hash.split('/')[0] : 'home';
  const param = hash.split('/')[1] || '';

  if (currentForce) { currentForce.destroy(); currentForce = null; }
  $$('.rail-item').forEach((a) => a.classList.toggle('active', a.dataset.view === view));

  const stage = $('#stage');
  stage.innerHTML = '<div class="loading">正在载入…</div>';

  try {
    if (view === 'home')   await renderHome(stage);
    if (view === 'principle') await renderPrinciple(stage);
    if (view === 'chat')   await renderChat(stage);
    if (view === 'reader') await renderReader(stage, param);
    if (view === 'stats')  await renderStats(stage);
    if (view === 'graph')  await renderGraph(stage);
    if (view === 'guiyou') await renderGuiyou(stage);
    if (view === 'corpus') await renderCorpus(stage);
    if (view === 'ops')    await renderOps(stage);
  } catch (err) {
    console.error(err);
    stage.innerHTML = `<div class="loading">载入失败：${esc(err.message)}</div>`;
  }
}

/* =====================================================================
   ① AI 问答界面（R-401）
   ===================================================================== */
const STARTERS = [
  { q: '贾宝玉的丫鬟有谁？', k: '事实型 · 可检索' },
  { q: '林黛玉为什么进贾府？', k: '推理型 · 需结合原文' },
  { q: '第19回为什么批语最多？', k: '统计型 · 可验证' },
  { q: '宝玉和黛玉是什么关系？', k: '图谱型 · 走知识图谱' },
  { q: '「千红一窟」有什么隐喻？', k: '推理型 · 脂批证据' },
  { q: '前80回一共多少字？', k: '事实型 · 精确可答' },
];

async function renderChat(stage) {
  stage.innerHTML = `
    <div class="view v-chat">
      <aside class="chat-side">
        <div class="chat-side-h">
          <button class="btn primary" id="newChat">＋ 新对话</button>
          <button class="btn ghost" id="btnClearHistory" title="清除全部对话记录">🗑</button>
        </div>
        <div class="chat-sessions" id="sessions"></div>
      </aside>

      <section class="chat-main">
        <div class="chat-scroll" id="chatScroll">
          <div class="chat-inner" id="chatInner"></div>
        </div>
        <div class="chat-input-wrap">
          <div class="chat-input">
            <div id="ctxChipWrap"></div>
            <textarea id="chatTextarea" rows="1"
              placeholder="问《红楼梦》的任何问题…  Enter 发送 / Shift+Enter 换行"></textarea>
            <div class="chat-input-bar">
              <div class="left">
                <button class="icon-btn" id="btnEvidence" title="显示检索证据">🔍</button>
                <button class="icon-btn" id="btnThink" title="显示思考过程">🧠</button>
                <button class="icon-btn" id="btnCustomQuery" title="自定义查询（AI 写代码统计）">🔬</button>
                <span class="hint" id="modeHint">思考过程：开</span>
              </div>
              <button class="btn primary sm" id="btnSend">发送</button>
            </div>
          </div>
          <div class="chat-foot-note">
            答案均基于语料检索生成并附出处 · 检索范围：<span id="verScope">全部三版本</span>
          </div>
        </div>
      </section>

      <aside class="chat-evidence" id="evidence">
        <div class="ev-inner">
          <div class="ev-h">
            <span>检索证据</span>
            <button class="icon-btn" id="evClose">✕</button>
          </div>
          <div class="ev-b" id="evBody">
            <div class="muted" style="font-size:17px">发送一条问题后，这里会显示三路检索的明细。</div>
          </div>
        </div>
      </aside>
    </div>`;

  // 会话列表（本地）
  state.sessions = JSON.parse(localStorage.getItem('hlm:sessions') || '[]');
  renderSessions();

  $('#newChat').onclick = () => {
    state.sessions.unshift({ id: Date.now(), title: '新对话', time: Date.now(), msgs: [] });
    state.activeSession = state.sessions[0];
    saveSessions(); renderSessions(); renderWelcome();
  };

  // 清除全部对话记录
  $('#btnClearHistory').onclick = () => openClearHistoryDialog();
  if (!state.sessions.length) {
    state.sessions.unshift({ id: Date.now(), title: '红楼梦问答', time: Date.now(), msgs: [] });
    state.activeSession = state.sessions[0];
    saveSessions();
  } else {
    state.activeSession = state.sessions[0];
  }
  renderSessions();
  // 有历史消息就回放，别把用户的对话丢掉
  if (state.activeSession && state.activeSession.msgs && state.activeSession.msgs.length) {
    replaySession();
  } else {
    renderWelcome();
  }

  const ta = $('#chatTextarea');
  ta.addEventListener('input', () => autoGrow(ta));
  ta.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  });
  // 显示当前检索版本范围
  const vs = $('#verScope');
  if (vs) {
    const V = VERSIONS.find((x) => x.id === state.currentVersion);
    vs.textContent = V ? `${V.label}（${V.maxHui} 回）` : '全部三版本';
  }

  $('#btnSend').onclick = send;

  $('#btnEvidence').onclick = () => {
    const ev = $('#evidence');
    ev.classList.toggle('open');
    $('#btnEvidence').classList.toggle('on', ev.classList.contains('open'));
  };
  $('#evClose').onclick = () => {
    $('#evidence').classList.remove('open');
    $('#btnEvidence').classList.remove('on');
  };
  // ---- 自定义查询（AI 写代码并执行）----
  $('#btnCustomQuery').onclick = () => {
    const q = $('#chatTextarea').value.trim();
    if (!q) {
      toast('请先输入要查询的内容，例如「全文出现了几次"之"字」');
      $('#chatTextarea').focus();
      return;
    }
    runCustomQuery(q);
  };

  $('#btnThink').onclick = () => {
    const on = $('#btnThink').classList.toggle('on');
    state.thinkOn = on;
    $('#modeHint').textContent = `思考过程：${on ? '开' : '关'}`;
  };
  $('#btnThink').classList.add('on');

  if (state.pendingContext) renderContextChip();
}

function renderContextChip() {
  const wrap = $('#ctxChipWrap');
  if (!wrap) return;
  if (!state.pendingContext) { wrap.innerHTML = ''; return; }
  const c = state.pendingContext;
  wrap.innerHTML = `
    <div style="display:flex;align-items:center;gap:8px;padding:9px 14px 0;font-size:16.5px;color:var(--ink-3)">
      <span class="tag red">划选上下文</span>
      <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
        ${esc(c.text.slice(0, 60))}${c.text.length > 60 ? '…' : ''}
      </span>
      ${c.hui ? `<span class="mono" style="font-size:14.5px">第${c.hui}回</span>` : ''}
      <button class="icon-btn" id="ctxClear" style="padding:0 4px">✕</button>
    </div>`;
  $('#ctxClear').onclick = () => { state.pendingContext = null; renderContextChip(); };
}

function saveSessions() {
  localStorage.setItem('hlm:sessions', JSON.stringify(state.sessions.slice(0, 30)));
}

function renderSessions() {
  const box = $('#sessions');
  if (!box) return;
  box.innerHTML = '';
  state.sessions.slice(0, 20).forEach((s) => {
    const d = el('div', 'sess' + (s === state.activeSession ? ' active' : ''));
    d.innerHTML = `<div class="t">${esc(s.title)}</div>
      <div class="m">${new Date(s.time).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })} · ${s.msgs.length} 条</div>`;
    d.onclick = () => { state.activeSession = s; renderSessions(); replaySession(); };
    box.appendChild(d);
  });
}

/**
 * 清除全部对话记录。
 *
 * 设计要点：
 *   · 二次确认（防误点），显示将删除多少条对话
 *   · 明确列出「会删什么」和「不会删什么」——
 *     只删 localStorage 里的对话，不动语料/索引/模型配置
 *   · 清除后自动建一个空对话，避免界面空白
 *   · 顺带清理各版本的「上次阅读回次」记录（hlm:lastHui:*）
 */
function openClearHistoryDialog() {
  const n = state.sessions.length;
  const nMsgs = state.sessions.reduce((a, s) => a + (s.msgs?.length || 0), 0);

  // 遮罩
  const mask = el('div', 'modal-mask');
  const box = el('div', 'modal-box');

  box.innerHTML = `
    <div class="modal-h">清除历史对话记录</div>
    <div class="modal-b">
      <p>将删除 <b>${n}</b> 个对话（共 <b>${nMsgs}</b> 条消息）。</p>
      <div class="modal-note">
        <div class="mn-h">只清除本地对话记录</div>
        <ul>
          <li>✅ 删除：对话列表与消息内容</li>
          <li>✅ 删除：各版本的「上次阅读回次」</li>
          <li>❌ 保留：语料、索引、向量、模型配置</li>
        </ul>
      </div>
      <p class="modal-warn">此操作不可撤销。</p>
    </div>
    <div class="modal-f">
      <button class="btn ghost" id="mCancel">取消</button>
      <button class="btn danger" id="mOk">确认清除</button>
    </div>`;
  mask.appendChild(box);
  document.body.appendChild(mask);

  const close = () => mask.remove();
  box.querySelector('#mCancel').onclick = close;
  mask.onclick = (e) => { if (e.target === mask) close(); };
  document.addEventListener('keydown', function esc_(e) {
    if (e.key === 'Escape') { close(); document.removeEventListener('keydown', esc_); }
  });

  box.querySelector('#mOk').onclick = () => {
    doClearHistory();
    close();
    toast(`已清除 ${n} 个对话记录`);
  };
}

/** 真正执行清除 */
function doClearHistory() {
  // 1) 清对话
  state.sessions = [];
  state.activeSession = null;
  try {
    localStorage.removeItem('hlm:sessions');
    // 2) 顺带清「上次阅读回次」（属于历史痕迹，一并清掉更彻底）
    Object.keys(localStorage)
      .filter((k) => k.startsWith('hlm:lastHui:'))
      .forEach((k) => localStorage.removeItem(k));
  } catch (e) {
    console.warn('清除 localStorage 失败', e);
  }

  // 3) 建一个空的初始对话，避免界面空白
  state.sessions.unshift({
    id: Date.now(), title: '红楼梦问答', time: Date.now(), msgs: [],
  });
  state.activeSession = state.sessions[0];
  saveSessions();
  renderSessions();
  renderWelcome();
}

/**
 * 自定义查询：AI 写代码 → 沙箱执行 → 展示代码与结果。
 *
 * 界面设计：
 *   · 代码默认折叠（用户可在需要时展开审计）
 *   · 结果卡片高亮显示 result
 *   · 显示执行耗时、尝试次数、是否被安全检查拦截
 */
async function runCustomQuery(question) {
  const inner = $('#chatInner');
  appendMsg('user', esc(question));
  const bot = el('div', 'msg bot');
  const body = el('div', 'bubble');
  bot.appendChild(body);
  inner.appendChild(bot);

  body.innerHTML = `<div class="cq-loading">
      <span class="spinner"></span> AI 正在编写查询代码…
    </div>`;

  const ver = state.currentVersion || 'fuqinju';
  let data = null;
  let err = null;
  try {
    data = await customQuery({
      question,
      model: state.model || 'deepseek-flash',
      version: ver,
    });
  } catch (e) {
    err = e.message || String(e);
  }

  if (err) {
    body.innerHTML = `<div class="cq-error">
        <b>自定义查询失败</b>
        <div>${esc(err)}</div>
      </div>`;
  } else {
    body.innerHTML = renderCustomQuery(data);
    // 代码折叠
    body.querySelectorAll('.cq-toggle').forEach((btn) => {
      btn.onclick = () => {
        const pre = btn.closest('.cq-code').querySelector('pre');
        const hidden = pre.style.display === 'none';
        pre.style.display = hidden ? '' : 'none';
        btn.textContent = hidden ? '收起代码' : '展开代码';
      };
    });
  }

  state.activeSession?.msgs.push({
    role: 'assistant',
    html: body.innerHTML,
    meta: { customQuery: true, question },
  });
  saveSessions();
  scrollBottom();
}

/** 渲染自定义查询结果 */
function renderCustomQuery(d) {
  const ok = d.ok;
  const icon = ok ? '🔬' : (d.blocked ? '🛡' : '⚠️');
  const title = ok ? '自定义查询' : (d.blocked ? '安全检查拦截' : '执行失败');

  // 代码块
  const codeHtml = d.code
    ? `<div class="cq-code">
         <div class="cq-code-h">
           <span>AI 生成的代码</span>
           <button class="cq-toggle">收起代码</button>
         </div>
         <pre><code>${esc(d.code)}</code></pre>
       </div>`
    : '';

  // 结果区
  let resultHtml = '';
  if (ok) {
    const r = d.result;
    let rtxt;
    if (r !== null && typeof r === 'object') {
      rtxt = '<pre class="cq-result-json">' + esc(JSON.stringify(r, null, 2)) + '</pre>';
    } else {
      rtxt = `<div class="cq-result-val">${esc(String(r))}</div>`;
    }
    resultHtml = `<div class="cq-result">
        <div class="cq-result-h">执行结果</div>
        ${rtxt}
      </div>`;
  } else {
    resultHtml = `<div class="cq-error">
        <b>${esc(d.error || '未知错误')}</b>
        ${d.blocked ? '<div class="cq-hint">代码包含危险操作（如 import、open、网络访问），已被安全检查拒绝执行。这是保护机制，不是故障。</div>' : ''}
      </div>`;
  }

  // 解释
  const explHtml = d.explanation
    ? `<div class="cq-expl">${renderMarkdown(d.explanation)}</div>`
    : '';

  // 元信息
  const meta = [];
  meta.push(`<span class="tag">${esc(d.version || 'fuqinju')}</span>`);
  if (d.elapsed_ms) meta.push(`<span class="tag">⏱ ${Math.round(d.elapsed_ms)} ms</span>`);
  if (d.attempts && d.attempts.length > 1) {
    meta.push(`<span class="tag warn">重试 ${d.attempts.length - 1} 次</span>`);
  }

  return `<div class="cq-wrap">
      <div class="cq-head">${icon} <b>${title}</b> ${meta.join('')}</div>
      ${codeHtml}
      ${resultHtml}
      ${explHtml}
      <div class="cq-foot">代码在受限沙箱中执行：禁止 import / open / 外网访问；
        可调 Hadoop API（hdfs_* / hbase_*）实时查询集群。超时 60s</div>
    </div>`;
}

function replaySession() {
  const inner = $('#chatInner');
  inner.innerHTML = '';
  const s = state.activeSession;
  // 清掉历史里可能残留的占位消息（旧版本写入的）
  if (s && Array.isArray(s.msgs)) {
    s.msgs = s.msgs.filter((m) => !(m.meta && m.meta.placeholder));
  }
  if (!s || !s.msgs.length) return renderWelcome();
  s.msgs.forEach((m) => appendMsg(m.role, m.html, m.meta, false));
  scrollBottom();
}

function renderWelcome() {
  const inner = $('#chatInner');
  inner.innerHTML = `
    <div class="chat-welcome">
      <div class="big">紅樓智問</div>
      <div class="sub">基于《红楼梦》三版本正文 167.8 万字 · 脂批 8,510 条（三源去重）· 人物关系图谱的可追溯问答</div>
      <div class="starters">
        ${STARTERS.map((s) => `
          <button class="starter" data-q="${esc(s.q)}">
            <div class="q">${esc(s.q)}</div>
            <div class="k">${esc(s.k)}</div>
          </button>`).join('')}
      </div>
    </div>`;
  $$('.starter', inner).forEach((b) => {
    b.onclick = () => { $('#chatTextarea').value = b.dataset.q; autoGrow($('#chatTextarea')); send(); };
  });
}

function autoGrow(ta) {
  ta.style.height = 'auto';
  ta.style.height = Math.min(ta.scrollHeight, 180) + 'px';
}

function scrollBottom() {
  const s = $('#chatScroll');
  if (s) requestAnimationFrame(() => { s.scrollTop = s.scrollHeight; });
}

function appendMsg(role, html, meta = {}, record = true) {
  const inner = $('#chatInner');
  if (inner.querySelector('.chat-welcome')) inner.innerHTML = '';
  const m = el('div', 'msg ' + role);
  const isBot = role === 'bot';
  m.innerHTML = `
    <div class="av">${isBot ? '紅' : '我'}</div>
    <div class="bd">
      <div class="who">
        <span>${isBot ? '红楼智问' : '你'}</span>
        ${meta.model ? `<span class="tag blue">${esc(meta.model)}</span>` : ''}
        ${meta.ms ? `<span class="muted mono" style="font-size:14.5px">${meta.ms} ms</span>` : ''}
      </div>
      <div class="body">${html}</div>
    </div>`;
  inner.appendChild(m);
  if (record && state.activeSession && !meta.placeholder) {
    state.activeSession.msgs.push({ role, html, meta });
    if (state.activeSession.msgs.length === 1 && role === 'user') {
      state.activeSession.title = (new DOMParser().parseFromString(html, 'text/html').body.textContent || '新对话').slice(0, 22);
      saveSessions(); renderSessions();
    }
    saveSessions();
  }
  scrollBottom();
  return m;
}

/* ---- 发送：真实 RAG（流式）+ 统计路由 + 兜底 ---- */
async function send() {
  const ta = $('#chatTextarea');
  const q = ta.value.trim();
  if (!q) return;

  const ctx = state.pendingContext;
  ta.value = ''; autoGrow(ta);
  $('#ctxChipWrap').innerHTML = '';

  appendMsg('user', esc(q));
  const t0 = performance.now();

  // 「正在检索…」只是占位，不能写进会话历史
  const thinking = appendMsg('bot',
    `<div class="muted" style="font-size:18px">正在检索语料…</div>`,
    { model: state.model, placeholder: true }, false);

  try {
    // ---------- 路径 A：统计型问题 → 后端真实统计（MapReduce 产出）----------
    const sq = parseStatQuery(q);
    if (sq && backendStatus.ok) {
      try {
        const st = await statsAsk(sq.topic || q);
        if (st && st.facts && Object.keys(st.facts).length) {
          thinking.remove();
          const ms0 = Math.round(performance.now() - t0);
          appendMsg('bot', renderBackendStats(st, q), { model: state.model + ' · 统计路由', ms: ms0 });
          renderEvidence(q, [], null, ['统计事实']);
          state.pendingContext = null;
          return;
        }
      } catch (e) { console.warn('后端统计失败，回退本地', e); }
    }
    if (sq) {
      const st = await buildStatAnswer(sq);
      if (st) {
        thinking.remove();
        const ms0 = Math.round(performance.now() - t0);
        appendMsg('bot', st.html, { model: state.model + ' · 统计路由', ms: ms0 });
        renderEvidence(q, st.hits, null, st.kws);
        state.pendingContext = null;
        return;
      }
    }

    // ---------- 路径 B：后端可用 → 真实 RAG 流式 ----------
    if (backendStatus.ok) {
      thinking.remove();
      // 建立"空壳"消息，后续增量往里面填
      const holder = appendMsg('bot', '', { model: state.model, placeholder: true }, false);
      const bodyEl = holder.querySelector('.body');
      const txtEl = el('div', 'txt');
      bodyEl.appendChild(txtEl);
      let reasonEl = null;
      let answer = '';

      const history = (state.activeSession?.msgs || [])
        .filter((m) => m.meta && !m.meta.placeholder)
        .map((m) => ({
          role: m.role === 'user' ? 'user' : 'assistant',
          content: m.plain || stripHtml(m.html || ''),
        }))
        .slice(-8);

      try {
        const res = await streamChat({
          question: q,
          model: state.model,
          history,
          contextText: ctx?.text || null,
          topk: 5,
          version: state.currentVersion || null,   // 版本限定（在阅读界面选的版本）
          thinking: state.thinkOn ?? true,
          onDelta(d) {
            answer += d;
            // 问题 4 修复：实时 markdown 渲染（原来用 textContent，残留 ** ## 等符号）
            txtEl.innerHTML = renderMarkdown(answer);
            scrollBottom();
          },
          onReasoning(d) {
            if (!reasonEl) {
              const det = el('details', 'think');
              det.innerHTML = `<summary>思考过程</summary><div class="body"></div>`;
              bodyEl.insertBefore(det, txtEl);
              reasonEl = det.querySelector('.body');
            }
            reasonEl.textContent += d;
          },
        });

        // 渲染引用（R-103）
        const cits = res.meta?.citations || [];
        if (cits.length) {
          const wrap = el('div', 'cites');
          wrap.innerHTML = `<div class="cites-h">引用出处（点击跳转并高亮）</div>` +
            cits.map((c) => {
            const isPz = c.source === 'pizhu';
            const verLabel = c.version_label || c.version || '抚琴居校本';
            // 问题 1 修复：用后端算出的「命中段落」para_id（不是 chunk 起点）
            // 脂批：显示「来源标签 + 版本标注 + 批型」
            const pzSrc = c.pizhu_src_tag || '';
            const pzVer = c.pizhu_versions?.length ? c.pizhu_versions.join('/') : '';
            const pzType = c.pizhu_ptype || '';
            const loc = isPz
              ? `第${c.hui}回 · 脂批` +
                (pzSrc ? ` · <b class="pz-src">${esc(pzSrc)}</b>` : '') +
                (pzVer ? ` · ${esc(pzVer)}` : '') +
                (pzType ? ` · ${esc(pzType)}` : '')
              : `第${c.hui}回 · 段${(c.hit_start ?? c.para_start) + 1} · ${esc(verLabel)}`;
            // 问题 1 修复：带上 hit_text，前端按文本匹配段落（免疫下标偏移）
            const findQ = c.hit_text || c.snippet || '';
            const href = isPz
              ? `#/reader/${c.hui}?ver=${c.version || 'fuqinju'}`
              : `#/reader/${c.hui}?para=${encodeURIComponent(c.para_id)}` +
                `&find=${encodeURIComponent(findQ.slice(0, 80))}&ver=${c.version || 'fuqinju'}`;
            // 方案 B：跨版本对照（同段落其他版本）
            const ovs = c.other_versions || [];
            const ovHtml = ovs.length
              ? `<span class="ov-wrap">${ovs.map((o) => {
                  const oh = `#/reader/${c.hui}?para=${encodeURIComponent(c.para_id)}` +
                             `&find=${encodeURIComponent((c.hit_text || '').slice(0, 80))}` +
                             `&ver=${o.version}`;
                  return `<a class="ov" href="${oh}" data-ver="${o.version}"
                            title="在${esc(o.version_label)}中查看同一段落">${esc(o.version_label)}</a>`;
                }).join('')}</span>`
              : '';
            return `
              <a class="cite${isPz ? ' pz' : ''}" href="${href}" data-ver="${c.version || 'fuqinju'}">
                <span class="idx">${c.idx}</span>
                <span class="cx">
                  <span class="loc">${isPz ? '<b class="pz-tag">脂批</b>' : ''}${loc}${ovHtml}</span>
                  <span class="snip">${esc(c.snippet || '')}</span>
                </span>
                <span class="sc">${(c.rrf ?? 0).toFixed(4)}</span>
              </a>`;
          }).join('');
          bodyEl.appendChild(wrap);
        }

        // 把占位消息转正（写进历史）
        holder.dataset.final = '1';
        const ms = Math.round(performance.now() - t0);
        holder.querySelector('.who').insertAdjacentHTML('beforeend',
          `<span class="muted mono" style="font-size:14.5px">${ms} ms</span>`);
        if (state.activeSession) {
          state.activeSession.msgs.push({
            role: 'bot',
            html: bodyEl.innerHTML,
            plain: answer,
            meta: { model: state.model, ms },
          });
          saveSessions(); renderSessions();
        }
        renderEvidenceFromRag(q, res.meta);
        state.pendingContext = null;
        return;
      } catch (err) {
        // 流式失败 → 清掉空壳，转降级
        holder.remove();
        console.warn('流式失败，降级：', err);
        const warn = appendMsg('bot',
          `<div class="muted" style="font-size:17px">流式失败（${esc(err.message)}），改用检索模式</div>`,
          { placeholder: true }, false);
        await fallbackRetrieval(q, ctx, t0, warn);
        state.pendingContext = null;
        return;
      }
    }

    // ---------- 路径 C：后端不可用 → 本地检索兜底 ----------
    thinking.remove();
    await fallbackRetrieval(q, ctx, t0, null);
    state.pendingContext = null;
  } catch (err) {
    thinking.remove();
    appendMsg('bot', `<div class="muted">失败：${esc(err.message)}</div>`, { model: state.model });
  }
}

/** 本地检索兜底（后端挂掉时仍能演示引用跳转） */
async function fallbackRetrieval(q, ctx, t0, placeholderEl) {
  if (placeholderEl) placeholderEl.remove();
  const loc = await recallByKeywords(q, ctx?.text);
  const ms = Math.round(performance.now() - t0);
  const html = buildAnswer(q, loc.hits || [], null, ctx, loc.kws);
  appendMsg('bot', html.replace(
    '阶段 S1 · 检索已真实运行，生成待 S4 接入',
    '后端未连接 · 纯检索模式（无 LLM 生成）'
  ), { model: state.model + ' · 检索模式', ms });
  renderEvidence(q, loc.hits || [], null, loc.kws);
}

function stripHtml(html) {
  const d = document.createElement('div');
  d.innerHTML = html;
  return d.textContent || '';
}

/** 渲染后端统计事实（来自 MapReduce → HBase hlm_stats） */
function renderBackendStats(st, q) {
  const f = st.facts || {};
  let rows = '';
  if (f.chunk_per_hui_top5) {
    rows += `<table class="table" style="margin:10px 0">
      <thead><tr><th>回次</th><th class="num">chunk 数</th><th class="num">字节数</th><th class="num">字数</th></tr></thead>
      <tbody>${f.chunk_per_hui_top5.map((x) => `<tr>
        <td>第 ${x.hui} 回</td>
        <td class="num">${x.n_chunk}</td>
        <td class="num">${fmt(x.n_bytes)}</td>
        <td class="num">${fmt(x.n_chars)}</td></tr>`).join('')}</tbody></table>`;
  }
  if (f.person_top10) {
    rows += `<table class="table" style="margin:10px 0">
      <thead><tr><th>人物</th><th class="num">提及次数</th></tr></thead>
      <tbody>${f.person_top10.map((x) => `<tr>
        <td>${esc(x.name)}</td><td class="num">${fmt(x.count)}</td></tr>`).join('')}</tbody></table>`;
  }
  return `<div class="txt">关于「${esc(q)}」的统计事实（来自 MapReduce 作业产出）：

${rows}

<div class="tag green" style="margin-top:8px">数据链路：MapReduce → HDFS /hlm/mrout → HBase hlm_stats</div>
</div>
<div class="cites">
  <div class="cites-h">数据来源</div>
  <a class="cite" href="#/stats"><span class="idx">↗</span><span class="cx">
    <span class="loc">统计信息界面</span><span class="snip">全部统计图表与明细</span></span></a>
</div>`;
}

/** 用后端返回的 citations + retrieval 渲染证据抽屉 */
function renderEvidenceFromRag(q, meta) {
  const box = $('#evBody');
  if (!box || !meta) return;
  const ret = meta.retrieval || {};
  const cits = meta.citations || [];
  box.innerHTML = `
    <div class="ev-sec">
      <h4>查询</h4>
      <div style="font-size:17px;color:var(--ink)">${esc(q)}</div>
      <div class="ev-row"><span class="k">模型</span><span class="v">${esc(meta.model || state.model)}</span></div>
      <div class="ev-row"><span class="k">别名展开</span><span class="v">${ret.expanded_aliases ?? '-'} 种</span></div>
    </div>

    <div class="ev-sec">
      <h4>L1 关键词召回 <span class="tag green" style="font-size:13px">真实</span></h4>
      <div class="ev-row"><span class="k">命中 chunk</span><span class="v">${ret.kw?.hits ?? '-'}</span></div>
      <div class="ev-row"><span class="k">bigram 命中</span><span class="v">${ret.kw?.hit_bigrams ?? '-'} / ${ret.kw?.bigrams ?? '-'}</span></div>
      <div class="ev-row"><span class="k">耗时</span><span class="v">${ret.kw?.ms ?? '-'} ms</span></div>
    </div>

    <div class="ev-sec">
      <h4>L2 向量召回 <span class="tag green" style="font-size:13px">真实</span></h4>
      <div class="ev-row"><span class="k">候选数</span><span class="v">${ret.vec?.candidates ?? '-'}</span></div>
      <div class="ev-row"><span class="k">耗时</span><span class="v">${ret.vec?.ms ?? '-'} ms</span></div>
      <div class="muted" style="font-size:15.5px;margin-top:4px">bge-small-zh-v1.5 · 512 维 · HBase hlm_vectors</div>
    </div>

    <div class="ev-sec">
      <h4>L3 图谱召回</h4>
      <div class="ev-row"><span class="k">命中</span><span class="v">${ret.graph?.hits ?? '-'}</span></div>
    </div>

    <div class="ev-sec">
      <h4>融合与输出</h4>
      <div class="ev-row"><span class="k">融合策略</span><span class="v">RRF (k=60)</span></div>
      <div class="ev-row"><span class="k">检索总耗时</span><span class="v">${ret.total_ms ?? '-'} ms</span></div>
      <div class="ev-row"><span class="k">引用条数</span><span class="v">${cits.length}</span></div>
    </div>

    <div class="ev-sec">
      <h4>Top 引用</h4>
      ${cits.map((c) => `
        <div style="margin-bottom:9px">
          <div style="display:flex;justify-content:space-between;font-size:15.5px">
            <span>第${c.hui}回 段${c.para_start + 1}-${c.para_end + 1}</span>
            <span class="mono muted">${(c.rrf ?? 0).toFixed(4)}</span>
          </div>
          <div class="bar"><i style="width:${Math.min(100, (c.rrf ?? 0) * 2800).toFixed(0)}%"></i></div>
        </div>`).join('') || '<div class="muted" style="font-size:16.5px">无</div>'}
    </div>`;
  $('#evidence').classList.add('open');
  $('#btnEvidence').classList.add('on');
}

/**
 * 识别「统计型问题」，直接用统计接口回答，而不是去全文里搜关键词。
 *
 * 反例（实测）：「第19回为什么批语最多？」被当全文检索处理 -> 抽词
 * 「第19回」「批语最多」-> 正文里当然搜不到 -> 0 命中。
 * 正确做法是路由到统计事实：第19回批语 160 条、全书 max/mean 4.861 等。
 */
function parseStatQuery(q) {
  const huiM = q.match(/第\s*(\d{1,3})\s*回/);
  const hui = huiM ? Number(huiM[1]) : null;
  const asksPizhu = /批语|脂批|批注/.test(q);
  const asksChars = /字数|多少字|篇幅/.test(q);
  const asksPerson = /谁|哪个人物|人物|出场|提到/.test(q);
  const asksMax = /最多|最少|最高|最低|排名/i.test(q);
  const asksSkew = /不均|偏斜|倾斜|分布/.test(q);

  if (hui != null && (asksPizhu || asksChars)) return { kind: 'chapter', hui };
  if (asksMax && (asksPizhu || asksPerson)) return { kind: 'rank' };
  if (asksSkew) return { kind: 'skew' };
  if (/多少字|总字数|一共多少/.test(q)) return { kind: 'total' };
  return null;
}

/** 统计型答案（数据全部来自 /api/stats 的真实统计） */
async function buildStatAnswer(sq) {
  const s = state.stats || (state.stats = await api('/api/stats'));
  const sk = s.skew;

  if (sq.kind === 'chapter') {
    const c = s.per_chapter.find((x) => x.hui === sq.hui);
    if (!c) return { html: `<div class="txt">统计里没有第 ${sq.hui} 回的数据（本书共 80 回）。</div>`, hits: [], kws: [] };
    const pz = s.pizhu_by_hui.find((x) => x.hui === sq.hui)?.count ?? 0;
    const sorted = [...s.pizhu_by_hui].sort((a, b) => b.count - a.count);
    const rank = sorted.findIndex((x) => x.hui === sq.hui) + 1;
    const avg = Math.round(s.pizhu_by_hui.reduce((a, b) => a + b.count, 0) / s.pizhu_by_hui.length);
    const html = `<div class="txt">第 ${sq.hui} 回「${esc(c.huimu)}」的统计：

<div style="margin:12px 0;padding:12px 15px;background:var(--paper-2);border-radius:8px;font-family:var(--font-sans)">
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
    <div><span class="muted" style="font-size:16.5px">正文字数</span><div style="font-size:24px;font-family:var(--font-serif)">${fmt(c.chars)}<small>字</small></div></div>
    <div><span class="muted" style="font-size:16.5px">段落数</span><div style="font-size:24px;font-family:var(--font-serif)">${c.n_para}<small>段</small></div></div>
    <div><span class="muted" style="font-size:16.5px">脂批条数</span><div style="font-size:24px;font-family:var(--font-serif);color:var(--cinnabar)">${pz}<small>条</small></div></div>
    <div><span class="muted" style="font-size:16.5px">批语数排名</span><div style="font-size:24px;font-family:var(--font-serif)">第 ${rank}<small>/ 79 回</small></div></div>
  </div>
</div>
${pz > avg * 2 ? `<div style="font-size:18px;color:var(--ink-2)">本回批语数（${pz}）是全书平均（${avg}）的 <b>${(pz / avg).toFixed(1)} 倍</b>，属于批语热点。原因可解释：批者关注重心集中在小说前段，前 27 回占全部批语的 ${(s.hotspot_explain.early_27_share * 100).toFixed(0)}%。</div>` : ''}
</div>
<div class="cites">
  <div class="cites-h">数据来源</div>
  <a class="cite" href="#/reader/${sq.hui}"><span class="idx">↗</span><span class="cx"><span class="loc">第${sq.hui}回 原文</span><span class="snip">跳转到该回正文，右侧可见本回全部脂批</span></span></a>
  <a class="cite" href="#/stats"><span class="idx">↗</span><span class="cx"><span class="loc">统计信息</span><span class="snip">逐回字数分布 / 逐回批语数 / 批语热度热力图</span></span></a>
</div>`;
    return { html, hits: [], kws: [`第${sq.hui}回`, `批语 ${pz} 条`], stat: true };
  }

  if (sq.kind === 'rank') {
    const top = s.pizhu_by_hui.slice().sort((a, b) => b.count - a.count).slice(0, 10);
    const html = `<div class="txt">按脂批条数排名，前 10 回是：

<table class="table" style="margin:12px 0">
  <thead><tr><th>名次</th><th>回次</th><th class="num">批语条数</th></tr></thead>
  <tbody>${top.map((x, i) => `<tr><td>${i + 1}</td><td>第 ${x.hui} 回</td><td class="num">${x.count}</td></tr>`).join('')}</tbody>
</table>
第 ${top[0].hui} 回以 <b>${top[0].count}</b> 条居首。全书 max/mean = ${sk.pizhu_per_hui.ratio}，远超教材阈值 2，必须解释：
<ul style="margin:8px 0 0;padding-left:20px;font-size:18px">${s.hotspot_explain.reason.map((r) => `<li>${esc(r)}</li>`).join('')}</ul>
</div>`;
    return { html, hits: [], kws: ['批语 Top-10'], stat: true };
  }

  if (sq.kind === 'skew') {
    const html = `<div class="txt">批语分布确实很不均匀，但<b>可解释</b>：

<div style="margin:12px 0;padding:12px 15px;background:var(--gold-bg);border-radius:8px;font-size:18px;line-height:1.9">
逐回批语 max/mean = <b>${sk.pizhu_per_hui.ratio}</b>（第 ${s.hotspot_explain.top10_pizhu_hui[0].hui} 回 ${s.hotspot_explain.top10_pizhu_hui[0].count} 条 vs 最少 2 条）<br>
版本维度 max/mean = <b>${sk.pizhu_per_version.ratio}</b>（蒙府本 1,612 vs 杨藏本 2）
</div>
三个领域原因：
<ul style="margin:8px 0;padding-left:20px;font-size:18px">${s.hotspot_explain.reason.map((r) => `<li>${esc(r)}</li>`).join('')}</ul>
<div style="font-size:17.5px;color:var(--ink-3)">${esc(s.hotspot_explain.chapter67_note)}</div>
</div>`;
    return { html, hits: [], kws: ['偏斜解释'], stat: true };
  }

  if (sq.kind === 'total') {
    const total = s.per_chapter.reduce((a, b) => a + b.chars, 0);
    // 口径二/三改为从实测文件读，不再写死
    let hanTxt = '（见「语料版本」页实测值）';
    let puncTxt = '（见「语料版本」页实测值）';
    let note = '';
    try {
      const r = await fetch('/data/corpus_stats.json?v=' + Date.now());
      if (r.ok) {
        const csd = await r.json();
        const fj = (csd.versions || []).find((v) => v.id === 'fuqinju');
        if (fj) {
          puncTxt = `${fmt(fj.chars)} 字（段落合计，含标点）`;
          note = `实测于 ${csd.generated_at}，`;
        }
      }
    } catch (e) { /* 忽略，用默认文案 */ }
    const html = `<div class="txt">前 80 回正文段落合计 <b>${fmt(total)}</b> 字。

<div style="margin:12px 0;padding:12px 15px;background:var(--paper-2);border-radius:8px;font-size:18px;line-height:1.9">
口径一：按本页逐回统计相加 <b>${fmt(total)}</b> 字（含标点）<br>
口径二：按语料实测文件 <b>${puncTxt}</b>
</div>
<div style="font-size:17.5px;color:var(--ink-3)">${note}两个数字都对，差异来自「凡例是否计入」「段落切分方式」等口径——
写报告时必须说清用的是哪一个。详见「语料版本」页。</div>
</div>`;
    return { html, hits: [], kws: ['字数口径'], stat: true };
  }
  return null;
}

/**
 * 关键词抽取（阶段 S1 的轻量实现）
 *
 * 教训：整句当查询词去正文里精确匹配，召回率是 0。
 * 真正要做的是「先抽出实体与核心名词，再用多个关键词分别召回，取并集」。
 * S4 会换成分词 + BM25；这里先用人物表 + 停用词 + 引号内容，够验证链路。
 */
const PERSON_NAMES = [
  '贾宝玉', '林黛玉', '薛宝钗', '王熙凤', '贾母', '贾探春', '贾迎春', '贾惜春',
  '李纨', '妙玉', '袭人', '晴雯', '平儿', '鸳鸯', '紫鹃', '香菱', '贾琏', '贾珍',
  '贾蓉', '贾政', '王夫人', '邢夫人', '赵姨娘', '薛姨妈', '薛蟠', '刘姥姥',
  '尤二姐', '尤三姐', '贾环', '贾兰', '贾雨村', '甄士隐', '秦可卿', '秦钟',
  '柳湘莲', '蒋玉菡', '焦大', '巧姐',
  // 常用简称
  '宝玉', '黛玉', '宝钗', '凤姐', '湘云', '探春', '迎春', '惜春', '可卿',
];

const STOP_WORDS = [
  '为什么', '什么', '怎么样', '怎么', '如何', '哪些', '哪个', '多少', '多大',
  '是不是', '有没有', '能不能', '请问', '一下', '告诉', '我', '你', '他', '她',
  '的', '了', '是', '在', '有', '和', '与', '及', '或', '吗', '呢', '吧', '啊',
  '一共', '总共', '几个', '几个', '这', '那', '个', '请', '说', '讲',
];

/** 返回候选关键词列表（按优先级排序，去重） */
/** 从划选上下文里抽检索词：按标点切句，取最长的几个片段 */
function contextKeywords(text) {
  const parts = String(text)
    .split(/[，,。、；;：:！!？?“”"'「」『』（）()\s]+/)
    .map((x) => x.trim())
    .filter((x) => x.length >= 2 && x.length <= 16);
  parts.sort((a, b) => b.length - a.length);
  return parts.slice(0, 4);
}

function extractKeywords(q) {
  const out = [];
  const push = (s) => {
    const t = (s || '').trim();
    if (t.length >= 2 && !out.includes(t)) out.push(t);
  };

  // 1) 书名号/引号里的内容优先
  for (const m of q.matchAll(/[「『“"《]([^」』”"》]{2,20})[」』”"》]/g)) push(m[1]);

  // 2) 人物名（长名优先，避免「宝玉」吃掉「贾宝玉」）
  const hits = [];
  for (const p of [...PERSON_NAMES].sort((a, b) => b.length - a.length)) {
    if (q.includes(p)) hits.push(p);
  }
  hits.forEach(push);

  // 3) 去掉停用词与人物名后的剩余片段
  let rest = q;
  [...STOP_WORDS, ...hits].sort((a, b) => b.length - a.length)
    .forEach((w) => { rest = rest.split(w).join('\u0000'); });
  rest.split(/[\u0000\s，,。、；;：:！!？?（）()]+/)
    .filter((x) => x.length >= 2 && x.length <= 12)
    .forEach(push);

  return out.slice(0, 6);
}

/** 多关键词召回：逐个查，合并去重 */
async function recallByKeywords(q, contextText) {
  // 有划选上下文时，上下文才是真正的检索依据：
  // 用户的问题往往是「请解释这一段」，抽词只会抽出「解释」「一段」这种废话。
  const kws = contextText && contextText.length >= 4
    ? [...new Set([...contextKeywords(contextText), ...extractKeywords(q)])].slice(0, 6)
    : extractKeywords(q);
  if (!kws.length) return { kws: [], hits: [] };
  const seen = new Set();
  const merged = [];
  for (const kw of kws) {
    try {
      const r = await api('/api/locate?q=' + encodeURIComponent(kw));
      for (const h of r.hits || []) {
        const key = h.para_id;
        if (seen.has(key)) continue;
        seen.add(key);
        merged.push({ ...h, kw });
      }
    } catch { /* 单个关键词失败不影响整体 */ }
    if (merged.length >= 20) break;
  }
  return { kws, hits: merged };
}

function buildAnswer(q, hits, rag, ctx, kws = []) {
  if (rag?.answer) {
    return renderRagAnswer(rag);
  }
  const cits = hits.slice(0, 4);
  if (!cits.length) {
    return `<div class="txt">在语料中没有检索到与「${esc(q)}」直接相关的内容。

这可能是提问超出了《红楼梦》文本范围（如跨作品比较、纯主观评价），或该说法在通行本中并不存在。

<ul style="margin:10px 0 0;padding-left:20px;font-size:18px">
  <li>可以换个更具体的说法，例如带上人物名或回次</li>
  <li>也可以直接在「原文阅读」里划选一段文字来提问</li>
</ul></div>`;
  }

  const first = cits[0];
  return `<div class="txt">关于「${esc(q)}」，语料中共定位到 <b>${hits.length}</b> 处相关原文。

${ctx ? `<div class="think" open style="border-color:#b0730f;background:#f6efdc">
  <summary style="color:#b0730f">已带入你划选的上下文（${ctx.hui ? '第' + ctx.hui + '回' : '答案片段'}）</summary>
  <div class="body" style="color:#6b4f12">${esc(ctx.text.slice(0, 200))}</div>
</div>` : ''}

<div style="font-size:17px;color:var(--ink-3);margin-bottom:6px">
  抽取关键词：${kws.map((k) => `<span class="tag blue">${esc(k)}</span>`).join(' ')}
</div>
最相关的一处出自<b>第 ${first.hui} 回</b>「${esc(first.huimu)}」：

<blockquote style="margin:10px 0;padding:10px 14px;border-left:3px solid var(--cinnabar);background:var(--paper-2);font-size:19px;line-height:1.9">
${esc(first.snippet)}
</blockquote>

<div class="tag gold" style="margin-top:6px">阶段 S1 · 检索已真实运行，生成待 S4 接入</div>
</div>
<div class="cites">
  <div class="cites-h">检索到 ${hits.length} 处原文（点击跳转并高亮）</div>
  ${cits.map((h, i) => `
    <a class="cite" href="#/reader/${h.hui}?para=${encodeURIComponent(h.para_id)}&q=${encodeURIComponent(h.matched || h.kw || q)}">
      <span class="idx">${i + 1}</span>
      <span class="cx">
        <span class="loc">第${h.hui}回 · 段${h.para_index + 1} · 抚琴居校本</span>
        <span class="snip">${esc(h.snippet)}</span>
      </span>
      <span class="sc">${h.char_start}-${h.char_end}</span>
    </a>`).join('')}
</div>`;
}

function renderRagAnswer(rag) {
  const cits = (rag.citations || []);
  return `<div class="txt">${rag.answer}</div>
  ${rag.reasoning ? `<details class="think"><summary>思考过程</summary><div class="body">${esc(rag.reasoning)}</div></details>` : ''}
  ${cits.length ? `<div class="cites">
    <div class="cites-h">引用出处</div>
    ${cits.map((c, i) => `
      <a class="cite" href="#/reader/${c.hui}?para=${encodeURIComponent(c.para_id)}">
        <span class="idx">${i + 1}</span>
        <span class="cx">
          <span class="loc">第${c.hui}回 · 段${c.para_index + 1} · ${esc(c.version || '抚琴居校本')}</span>
          <span class="snip">${esc(c.snippet)}</span>
        </span>
        <span class="sc">${(c.score ?? 0).toFixed(3)}</span>
      </a>`).join('')}
  </div>` : ''}`;
}

function renderEvidence(q, hits, rag, kws = []) {
  const box = $('#evBody');
  if (!box) return;
  const fakeRank = (i) => Math.max(0.32, 0.94 - i * 0.11);
  box.innerHTML = `
    <div class="ev-sec">
      <h4>查询</h4>
      <div style="font-size:17px;color:var(--ink)">${esc(q)}</div>
      <div class="ev-row"><span class="k">模型</span><span class="v">${esc(state.model)}</span></div>
      <div class="ev-row"><span class="k">抽取关键词</span><span class="v" style="font-family:var(--font-sans)">${kws.length ? kws.map((k) => esc(k)).join(' · ') : '—'}</span></div>
    </div>

    <div class="ev-sec">
      <h4>L1 关键词召回（真实）</h4>
      ${hits.length ? hits.slice(0, 4).map((h, i) => `
        <div style="margin-bottom:9px">
          <div style="display:flex;justify-content:space-between;font-size:15.5px">
            <span>第${h.hui}回 · 段${h.para_index + 1}</span>
            <span class="mono muted">${h.char_start}-${h.char_end}</span>
          </div>
          <div class="bar"><i style="width:${(fakeRank(i) * 100).toFixed(0)}%"></i></div>
        </div>`).join('')
      : '<div class="muted" style="font-size:16.5px">无命中</div>'}
    </div>

    <div class="ev-sec">
      <h4>L2 向量召回 <span class="tag" style="font-size:13px">S2 实装</span></h4>
      <div class="muted" style="font-size:16.5px">HBase hlm_vectors · 2048 维 · 余弦相似</div>
    </div>

    <div class="ev-sec">
      <h4>L3 图谱召回 <span class="tag" style="font-size:13px">S2 实装</span></h4>
      <div class="muted" style="font-size:16.5px">HBase hlm_kg · 一度/二度邻居</div>
    </div>

    <div class="ev-sec">
      <h4>融合与重排 <span class="tag" style="font-size:13px">S4 实装</span></h4>
      <div class="ev-row"><span class="k">融合策略</span><span class="v">RRF (k=60)</span></div>
      <div class="ev-row"><span class="k">重排模型</span><span class="v">bge-reranker</span></div>
      <div class="ev-row"><span class="k">生成</span><span class="v">${rag ? '已接入' : '未接入'}</span></div>
    </div>`;
  $('#evidence').classList.add('open');
  $('#btnEvidence').classList.add('on');
}

/* =====================================================================
   ② 原文阅读界面（R-402）
   ===================================================================== */
async function renderReader(stage, param) {
  // param 形如 "3?para=p3_14&ver=fuqinju"
  const qIdx0 = param.indexOf('?');
  const rawHui = qIdx0 > -1 ? param.slice(0, qIdx0) : param;
  const qs = new URLSearchParams(qIdx0 > -1 ? param.slice(qIdx0) : '');

  // 版本：URL 参数 > state > 默认
  const ver = qs.get('ver') || state.currentVersion || 'fuqinju';
  state.currentVersion = ver;
  const V = VERSIONS.find((x) => x.id === ver) || VERSIONS[0];

  const target = rawHui ? Number(rawHui) : Number(localStorage.getItem('hlm:lastHui:' + ver) || 1);
  const hui = Number.isInteger(target) && target >= 0 && target <= V.maxHui ? target : 1;

  stage.innerHTML = `
    <div class="view v-reader">
      <aside class="rd-toc">
        <div class="rd-toc-h">
          <div class="t">回目</div>
          <select id="verSelect" aria-label="选择语料版本">
            ${VERSIONS.map((v) => `<option value="${v.id}"${v.id === ver ? ' selected' : ''}>${v.label}（${v.maxHui} 回${v.id === 'guiyouben' ? '，81-108' : ''}）</option>`).join('')}
          </select>
          <div class="muted" id="verHint" style="font-size:15px;margin-top:4px">${esc(V.desc)}</div>
        </div>
        <div class="rd-list" id="rdList"></div>
      </aside>

      <section class="rd-main">
        <div class="rd-bar">
          <div class="rd-nav">
            <button class="btn sm" id="prevHui">← 上一回</button>
            <button class="btn sm" id="nextHui">下一回 →</button>
          </div>
          <div class="rd-title">
            <div class="h" id="rdTitle">—</div>
            <div class="m" id="rdMeta">—</div>
          </div>
          <div class="rd-nav">
            <button class="btn sm" id="askThis">🎯 就本回提问</button>
          </div>
        </div>
        <div class="rd-scroll" id="rdScroll">
          <div class="rd-paper" id="rdPaper"><div class="loading">载入正文…</div></div>
        </div>
      </section>

      <aside class="rd-pizhu" id="pzAside">
        <div class="rd-pizhu-h">
          <span id="pzTitle">脂批</span>
          <span class="tag red" id="pzCount">—</span>
        </div>
        <div class="rd-pizhu-list" id="pzBody"></div>
      </aside>
    </div>`;

  // ---- 回目列表（按版本） ----
  const list = $('#rdList');
  const toc = await api(`/api/chapters?ver=${encodeURIComponent(ver)}`);
  (toc.chapters || toc || []).forEach((c) => {
    const d = el('div', 'rd-item' + (c.hui === hui ? ' active' : ''));
    d.innerHTML = `<span class="n">${c.hui === 0 ? '凡' : c.hui}</span>
      <span class="t">${esc(shortTitle(c.huimu))}</span>
      <span class="c">${fmt(c.chars)}</span>`;
    d.onclick = () => { location.hash = `#/reader/${c.hui}?ver=${ver}`; };
    list.appendChild(d);
  });
  const activeItem = list.querySelector('.rd-item.active');
  if (activeItem) activeItem.scrollIntoView({ block: 'center' });

  // ---- 版本切换（问题 2 修复：原来 select 没有 change 绑定）----
  $('#verSelect').onchange = (e) => {
    const nv = e.target.value;
    state.currentVersion = nv;
    const nV = VERSIONS.find((x) => x.id === nv) || VERSIONS[0];
    // 切版本时把回次夹到新版本的合法范围（癸酉本 81-108）
    let nh = hui;
    if (nv === 'guiyouben') nh = Math.min(Math.max(hui, 81), 108);
    if (nh > nV.maxHui) nh = nV.maxHui;
    toast(`切换到 ${nV.label}`);
    location.hash = `#/reader/${nh}?ver=${nv}`;
  };

  $('#prevHui').onclick = () => {
    const minHui = ver === 'guiyouben' ? 81 : 0;
    if (hui > minHui) location.hash = `#/reader/${hui - 1}?ver=${ver}`;
  };
  $('#nextHui').onclick = () => {
    const maxHui = V.maxHui;
    if (hui < maxHui) location.hash = `#/reader/${hui + 1}?ver=${ver}`;
  };
  $('#prevHui').disabled = hui <= (ver === 'guiyouben' ? 81 : 0);
  $('#nextHui').disabled = hui >= V.maxHui;

  $('#askThis').onclick = () => {
    const t = $('#rdTitle').textContent;
    state.pendingContext = { text: t, hui, paraId: null, version: ver };
    location.hash = '#/chat';
  };

  await loadChapter(hui, param, ver);

  // ---- 引用跳转定位（问题 1 修复）----
  // 优先用 find（命中段落文本）匹配，paraId 只作兜底。
  // 原因：后端 chunk 的 para_start 含回目行，与前端段落下标有偏移（实测差 8 段）。
  const paraId = qs.get('para');
  const findText = qs.get('find') || qs.get('q');
  if (paraId || findText) {
    setTimeout(() => highlightPara(paraId, findText), 320);
  }
}

async function loadChapter(hui, param, ver) {
  const d = await api(`/api/chapter?hui=${hui}&ver=${encodeURIComponent(ver)}`);
  localStorage.setItem('hlm:lastHui:' + ver, String(hui));

  const V = VERSIONS.find((x) => x.id === ver) || VERSIONS[0];
  $('#rdTitle').textContent = d.huimu;
  $('#rdMeta').textContent =
    `${fmt(d.char_count)} 字 · ${d.paragraphs.length} 段 · ${V.label} · hui=${d.hui}`;

  const paper = $('#rdPaper');
  paper.innerHTML = `<div class="huimu">${esc(d.huimu)}</div>`;
  d.paragraphs.forEach((p) => {
    const pe = el('p');
    pe.dataset.paraId = p.id;
    pe.dataset.hui = String(hui);
    // 问题 3 修复：正文里也不能有替换符（源数据若有，这里兜底显示为 □ 并标注）
    const clean = String(p.t).replace(/\ufffd/g, '□');
    pe.innerHTML = `<span class="pn">${p.i + 1}</span>${esc(clean)}`;
    pe.addEventListener('click', () => openParaAsk(pe, p, hui));
    paper.appendChild(pe);
  });
  $('#rdScroll').scrollTop = 0;

  // ---- 批语侧边栏（按版本隔离）----
  //   fuqinju   → 3,234 条（抚琴居校本脂批，1–80 回）
  //   guiyouben → 223 条（后28回内嵌批 / 回前批 / 回末评 / 前80回摘录）
  //   chenggao  → 0 条（删改本无脂批）→ 显示说明而非隐藏
  const pz = d.pizhu || [];
  const aside = $('#pzAside');
  const pzTitle = $('#pzTitle');
  const VER_PZ_NAME = {
    fuqinju: '脂批',
    guiyouben: '癸酉本批语',
    chenggao: '批语',
  };
  if (pzTitle) pzTitle.textContent = VER_PZ_NAME[ver] || '批语';

  aside.style.display = '';
  $('#pzCount').textContent = pz.length + ' 条';
  const body = $('#pzBody');

  if (!pz.length) {
    // 程高本是删改本，没有脂批 —— 明确告知，而不是默默隐藏
    const why = ver === 'chenggao'
      ? '程高本是删改本，<b>没有脂批</b>。<br>脂批仅见于脂评本系统（甲戌/庚辰/蒙府等）。'
      : '本回无批语记录。';
    body.innerHTML = `<div class="muted" style="font-size:17px;padding:10px;line-height:1.8">${why}</div>`;
  } else {
    body.innerHTML = pz.map((p) => {
      // 版本标签去重：src_tag 与 versions 常相同（如都叫「癸酉本」）
      const seenTags = new Set();
      const tagHtml = [];
      if (p.src_tag) {
        tagHtml.push(`<span class="tag green">${esc(p.src_tag)}</span>`);
        seenTags.add(p.src_tag);
      }
      for (const v of (p.versions || [])) {
        if (seenTags.has(v)) continue;
        seenTags.add(v);
        tagHtml.push(`<span class="tag red">${esc(v)}</span>`);
      }
      if (p.ptype) tagHtml.push(`<span class="tag">${esc(p.ptype)}</span>`);
      if (p.page != null) tagHtml.push(`<span class="tag">p.${p.page}</span>`);
      return `
        <div class="pz">
          <div class="tx">${esc(String(p.text).replace(/\ufffd/g, '□'))}</div>
          <div class="mt">${tagHtml.join('')}</div>
        </div>`;
    }).join('');
  }
}

/**
 * 段落级「就这段提问」浮层。
 * 比划选更稳的交互：单击任意段落即在段落右上角出现按钮，
 * 用户不必精确拖选；要精确上下文时再用划选。
 */
// 记录最后一次点击的视口坐标，供浮层定位
document.addEventListener('click', (e) => {
  window.__lastClickX = e.clientX;
  window.__lastClickY = e.clientY;
}, true);

function openParaAsk(pe, p, hui) {
  document.querySelectorAll('.para-ask').forEach((n) => n.remove());
  // 正在划选时不干扰（划选有自己的浮层）
  if ((window.getSelection()?.toString().trim().length || 0) >= 2) return;

  const btn = el('button', 'para-ask', '🎯 就这段提问');
  // 用 fixed 定位挂在 body 上：段落有 text-indent、行高很大，
  // 把按钮 append 进 <p> 很容易被挤出可视区或被裁掉。
  // 用鼠标事件的视口坐标定位（比元素 rect 更准：段落可能很长，
  // rect.top 是段落顶端，用户点的位置在段落中间）
  const rect = pe.getBoundingClientRect();
  const px = (window.__lastClickX ?? (rect.right - 60));
  const py = (window.__lastClickY ?? (rect.top + 8));
  btn.style.position = 'fixed';
  btn.style.left = Math.max(8, Math.min(px + 10, window.innerWidth - 118)) + 'px';
  btn.style.top = Math.max(8, py - 34) + 'px';

  btn.onclick = (ev) => {
    ev.stopPropagation();
    state.pendingContext = { text: p.t, hui, paraId: p.id };
    btn.remove();
    location.hash = '#/chat';
    setTimeout(() => {
      const ta = $('#chatTextarea');
      if (ta) {
        ta.value = `请解释这一段（第${hui}回 第${p.i + 1}段）：${p.t.slice(0, 60)}${p.t.length > 60 ? '…' : ''}`;
        autoGrow(ta); ta.focus();
        renderContextChip();
      }
    }, 140);
  };
  document.body.appendChild(btn);
  // 不自动消失：用户可能要点它。点空白处 / 换段时才收起。
  const dismiss = (e) => {
    if (e.target === btn || btn.contains(e.target)) return;
    if (e.target.closest('p[data-para-id]')) return;   // 点段落：由 openParaAsk 自己替换
    btn.remove();
    document.removeEventListener('mousedown', dismiss, true);
  };
  setTimeout(() => document.addEventListener('mousedown', dismiss, true), 0);
}

function highlightPara(paraId, kw) {
  const paper = $('#rdPaper');
  if (!paper) return;

  // 1) 先按文本匹配（最可靠）
  let target = null;
  let hitKw = kw || '';
  if (kw && kw.length >= 4) {
    // 取前 40 字做匹配键（去掉空白和标点差异）
    const norm = (x) => String(x || '').replace(/[\s\u3000]+/g, '');
    const key = norm(kw).slice(0, 40);
    const paras = Array.from(paper.querySelectorAll('p[data-para-id]'));
    // 先找「包含完整 key」的段
    for (const p of paras) {
      if (norm(p.textContent).includes(key)) { target = p; break; }
    }
    // 再降级：找与 key 前缀 20 字匹配的段
    if (!target && key.length > 12) {
      const k2 = key.slice(0, 20);
      for (const p of paras) {
        if (norm(p.textContent).includes(k2)) { target = p; break; }
      }
    }
  }

  // 2) 文本匹配失败，退回 para_id
  if (!target && paraId) {
    target = paper.querySelector(`[data-para-id="${paraId}"]`);
  }
  // 3) 再兜底：按 para_id 里的下标猜
  if (!target && paraId && /^p\d+_(\d+)$/.test(paraId)) {
    const idx = Number(RegExp.$1);
    const paras = Array.from(paper.querySelectorAll('p[data-para-id]'));
    target = paras[idx] || null;
  }

  if (!target) {
    toast('未能在本回定位到该段落，已跳到回目开头');
    return;
  }

  // 高亮关键字（重建 innerHTML，保留段号 span）
  const pn = target.querySelector('.pn');
  const pnHtml = pn ? pn.outerHTML : '';
  let text = target.textContent || '';
  if (pn) text = text.slice(pn.textContent.length);

  if (hitKw && hitKw.length >= 2) {
    const key = hitKw.slice(0, 30);
    const i = text.indexOf(key);
    if (i >= 0) {
      const before = esc(text.slice(0, i));
      const mid = esc(text.slice(i, i + key.length));
      const after = esc(text.slice(i + key.length));
      target.innerHTML = `${pnHtml}${before}<mark class="hl">${mid}</mark>${after}`;
    }
  }

  target.scrollIntoView({ block: 'center', behavior: 'smooth' });
  target.classList.add('para-flash');
  setTimeout(() => target.classList.remove('para-flash'), 2600);
}

/* =====================================================================
   ③ 统计信息界面（R-403）
   ===================================================================== */
async function renderStats(stage) {
  if (!state.stats) state.stats = await api('/api/stats');
  const s = state.stats;
  const sk = s.skew;

  const totalChars = s.per_chapter.reduce((a, b) => a + b.chars, 0);
  // 注意：一条批语可能同时见于多个版本（versions_legend 是多值），
  // 所以「版本分布求和」≠「批语条数」。两者必须分开表述，否则就是重复计数。
  const pzVersionSum = s.pizhu_by_version.reduce((a, b) => a + b.count, 0);
  const pzUnique = 3234;   // 抚琴居校本批语条数（进入 RAG 索引的部分）
  const pzDedupAll = 8510; // 三源脂批合并去重后的条数（抚琴居 3,219 + 癸酉本 222 + 脂批大全 5,069）

  stage.innerHTML = `
    <div class="view v-stats">
      <div class="stats-wrap">
        <div class="page-h">
          <h2>统计信息</h2>
          <p>全部数据来自真实语料与已完成的 MapReduce 作业 · 每张图表都可以「就这个数据提问」</p>
        </div>

        <div class="kpi-row">
          <div class="kpi">
            <div class="k">正文总字数</div>
            <div class="v" id="k1">${fmt(totalChars)}<small>字</small></div>
            <div class="d">80 回 · 2,512 段 · 抚琴居校本（不含凡例）</div>
          </div>
          <div class="kpi">
            <div class="k">脂批条数</div>
            <div class="v">${fmt(pzUnique)}<small>条</small></div>
            <div class="d">抚琴居校本 · 已重解码<br>两套脂批去重后共 ${fmt(pzDedupAll)} 条</div>
          </div>
          <div class="kpi">
            <div class="k">批语偏斜 max/mean</div>
            <div class="v" style="color:var(--amber)">${sk.pizhu_per_hui.ratio}<small>倍</small></div>
            <div class="d">教材阈值 2 · <b>需解释热点</b></div>
          </div>
          <div class="kpi">
            <div class="k">人物提及 Top-1</div>
            <div class="v" style="font-size:25px">${esc(s.top_person_body[0]?.name || '—')}</div>
            <div class="d">${fmt(s.top_person_body[0]?.count || 0)} 次（别名已归并）</div>
          </div>
        </div>

        <div class="stats-grid">
          <div class="card chart-card col-12">
            <button class="ask-btn" data-ask="主要人物在各回的出场分布">🎯 就此提问</button>
            <div class="card-h">
              <h3>人物出场曲面（3D）</h3>
              <span class="tag">高度=该回提及次数 · 拖拽旋转 · 滚轮缩放 · 悬停看数值</span>
            </div>
            <div class="card-b">
              <div class="chart-box tall"><canvas class="chart" id="c-terrain"></canvas></div>
              <div class="surf-note" id="surfNote">点击人物名可高亮其出场曲线</div>
            </div>
          </div>

          <div class="card chart-card col-7">
            <button class="ask-btn" data-ask="逐回字数分布">🎯 就此提问</button>
            <div class="card-h"><h3>逐回字数分布</h3><span class="tag">max/mean = ${sk.chapter_chars.ratio}</span></div>
            <div class="card-b"><div class="chart-box"><canvas class="chart" id="c-chars"></canvas></div></div>
          </div>

          <div class="card chart-card col-5">
            <button class="ask-btn" data-ask="脂批版本分布">🎯 就此提问</button>
            <div class="card-h"><h3>脂批版本分布</h3><span class="tag red">重解码</span></div>
            <div class="card-b"><div class="chart-box"><canvas class="chart" id="c-ver"></canvas></div></div>
          </div>

          <div class="card chart-card col-6">
            <button class="ask-btn" data-ask="人物提及频次">🎯 就此提问</button>
            <div class="card-h"><h3>人物提及频次 Top-15</h3><span class="tag">别名归并</span></div>
            <div class="card-b"><div class="chart-box tall"><canvas class="chart" id="c-person"></canvas></div></div>
          </div>

          <div class="card chart-card col-6">
            <button class="ask-btn" data-ask="逐回批语数">🎯 就此提问</button>
            <div class="card-h"><h3>逐回批语数</h3><span class="tag gold">max/mean = ${sk.pizhu_per_hui.ratio}</span></div>
            <div class="card-b"><div class="chart-box tall"><canvas class="chart" id="c-pzhui"></canvas></div></div>
          </div>

          <div class="card chart-card col-12">
            <div class="card-b">
              <div class="skew-note">
                <b>⚠ 偏斜必须解释（教材 V3 硬指标：max/mean &gt; 2）</b>
                <div style="margin-top:6px">
                  逐回批语 max/mean = <b>${sk.pizhu_per_hui.ratio}</b>（第 ${s.hotspot_explain.top10_pizhu_hui[0]?.hui} 回 ${s.hotspot_explain.top10_pizhu_hui[0]?.count} 条 vs 最少 2 条）；
                  版本维度 max/mean = <b>${sk.pizhu_per_version.ratio}</b>。热点来源<u>可解释</u>，不是「数据就是这样」：
                </div>
                <ul>${s.hotspot_explain.reason.map((r) => `<li>${esc(r)}</li>`).join('')}</ul>
                <div style="margin-top:6px;font-size:16.5px;opacity:.85">${esc(s.hotspot_explain.chapter67_note)}</div>
                <button class="btn sm" style="margin-top:9px" data-ask="批语热点成因">🎯 就这个偏斜提问</button>
              </div>
            </div>
          </div>

          <div class="card chart-card col-6">
            <button class="ask-btn" data-ask="全书词频">🎯 就此提问</button>
            <div class="card-h"><h3>全书词频 Top-20</h3><span class="tag green">MapReduce 产出</span></div>
            <div class="card-b"><div class="chart-box"><canvas class="chart" id="c-words"></canvas></div></div>
          </div>

          <div class="card chart-card col-6">
            <button class="ask-btn" data-ask="批语热度热力图">🎯 就此提问</button>
            <div class="card-h"><h3>批语热度（回次 × 版本）</h3><span class="tag">稀疏矩阵</span></div>
            <div class="card-b"><div class="chart-box"><canvas class="chart" id="c-heat"></canvas></div></div>
          </div>
        </div>
      </div>
    </div>`;

  // ---- 绘图 ----
  const chapters = s.per_chapter;
  // 3D 人物出场曲面（需要 person_hui_matrix.json）
  if (state.terrainInst) { state.terrainInst.destroy(); state.terrainInst = null; }
  try {
    const r = await fetch('/data/person_hui_matrix.json?v=' + Date.now());
    if (r.ok) {
      const M = await r.json();
      state.terrainInst = surface3d($('#c-terrain'), M);
    } else {
      const sn = $('#surfNote');
      if (sn) sn.textContent = '⚠ 人物分布数据未生成（运行 scripts/s33_1_build_person_hui_matrix.py）';
    }
  } catch (e) { console.warn('人物曲面图失败:', e); }

  bar($('#c-chars'), chapters.map((c) => ({ label: String(c.hui), value: c.chars })), { rotate: true });

  pie($('#c-ver'), s.pizhu_by_version.filter((v) => v.count > 1).map((v, i) => ({
    label: v.name, value: v.count, color: PALETTE[i % PALETTE.length],
  })), { donut: true, center: fmt(pzUnique), centerSub: '条脂批' });

  hbar($('#c-person'), s.top_person_body.slice(0, 15).map((p, i) => ({
    label: p.name, value: p.count, color: PALETTE[i % 5],
  })), { labelW: 68 });

  bar($('#c-pzhui'), s.pizhu_by_hui.map((p) => ({ label: String(p.hui), value: p.count })), {
    rotate: true, color: '#2f4858',
  });

  hbar($('#c-words'), s.word_freq_top100.slice(0, 20).map((w) => ({
    label: w.w, value: w.c,
  })), { labelW: 56, color: '#a3802c' });

  // 热力图：版本 × 回次（每 10 回一列，用 version_matrix）
  const vm = await api('/api/version-matrix');
  const vers = Object.keys(vm.version_to_hui).filter((v) => v !== '(未识别)');
  const buckets = 8;
  const matrix = vers.map((v) => {
    const row = new Array(buckets).fill(0);
    vm.version_to_hui[v].forEach((h) => { row[Math.min(buckets - 1, Math.floor((h - 1) / 10))]++; });
    return row;
  });
  heat($('#c-heat'), matrix, {
    rowLabels: vers,
    colLabels: Array.from({ length: buckets }, (_, i) => `${i * 10 + 1}-${(i + 1) * 10}`),
  });

  // 「就此提问」统一绑定
  $$('[data-ask]').forEach((b) => {
    b.onclick = () => askFromStats(b.dataset.ask);
  });
}

function askFromStats(topic) {
  const map = {
    '逐回字数分布': '第67回为什么字数是他回的三倍？',
    '脂批版本分布': '为什么蒙府本的脂批数量最多？',
    '人物提及频次': '宝玉在全书里一共被提到多少次？',
    '逐回批语数': '第19回为什么批语最多？',
    '批语热点成因': '批语分布为什么这么不均匀？版本存世范围有什么影响？',
    '全书词频': '全书出现频率最高的词是哪个？说明了什么？',
    '批语热度热力图': '哪些回次的批语密度最高？为什么？',
  };
  state.pendingContext = { text: `【统计上下文】${topic}`, hui: null, paraId: null };
  location.hash = '#/chat';
  setTimeout(() => {
    const ta = $('#chatTextarea');
    if (ta) {
      ta.value = map[topic] || `请解释这个统计：${topic}`;
      autoGrow(ta); ta.focus();
      renderContextChip();
    }
  }, 120);
}

/* =====================================================================
   ④ 人物关系图界面（R-404）
   ===================================================================== */
async function renderGraph(stage) {
  if (!state.graph) state.graph = await api('/api/graph');
  const g = state.graph;

  const relTypes = g.relations.slice(0, 40);

  stage.innerHTML = `
    <div class="view v-graph">
      <aside class="gr-side">
        <div class="gr-sec">
          <h4>搜索人物</h4>
          <input class="gr-search" id="gSearch" placeholder="如：宝玉 / 黛玉" list="gList">
          <datalist id="gList">${g.nodes.map((n) => `<option value="${esc(n.name)}">`).join('')}</datalist>
        </div>
        <div class="gr-sec">
          <h4>家族</h4>
          <div id="famBox"></div>
        </div>
        <div class="gr-sec">
          <h4>关系类型 <span id="relToggle" class="rel-toggle">展开 ▾</span></h4>
          <div id="relBox" class="rel-scroll collapsed"></div>

          <!-- S29 共现叠加层（MapReduce 产出）-->
          <h4 style="margin-top:14px">MapReduce 共现</h4>
          <label class="co-toggle">
            <input type="checkbox" id="coOn"> <span>叠加共现边</span>
          </label>
          <div class="co-hint" id="coHint">加载中…</div>
        </div>
        <div class="gr-sec">
          <h4>图谱统计</h4>
          <div class="ev-row"><span class="k">节点</span><span class="v">${g.n_nodes} / ${g.n_nodes_total || g.n_nodes}</span></div>
          <div class="ev-row"><span class="k">边</span><span class="v">${g.n_edges} / ${g.n_edges_total}</span></div>
          <div class="ev-row"><span class="k">关系类型</span><span class="v">${g.relations.length}</span></div>
        </div>
      </aside>

      <section class="gr-main">
        <div class="gr-dim">
          <button class="gr-dim-btn active" data-dim="2d">2D 平面</button>
          <button class="gr-dim-btn" data-dim="3d">3D 立体</button>
          <button class="gr-dim-btn" data-dim="chord">和弦图</button>
        </div>
        <canvas id="graphCanvas"></canvas>
        <div class="gr-tip" id="gTip">
          拖拽空白处平移 · 拖拽节点移动 · 滚轮缩放 · 点击看详情 · 双击空白重置
        </div>
        <button class="gr-reset" id="gReset" title="重置视图与布局">⟲ 重置</button>
        <div class="gr-legend" id="gLegend"></div>
      </section>

      <aside class="gr-detail">
        <div class="gr-detail-h">
          <span style="font-size:18px;font-weight:600">人物详情</span>
          <button class="btn sm" id="askRel">🎯 问他</button>
        </div>
        <div class="gr-detail-b" id="gDetail">
          <div class="muted" style="font-size:17px">点击左侧图中的任意人物节点查看详情与关系。</div>
        </div>
      </aside>
    </div>`;

  const FAM_COLOR = { 贾: '#b03a2e', 史: '#2f4858', 王: '#7b4b94', 薛: '#a3802c', 林: '#3f6a55', 其他: '#78716c' };
  const activeFams = new Set(g.families);
  const activeRels = new Set(relTypes);

  // 家族勾选
  const famBox = $('#famBox');
  g.families.forEach((f) => {
    const lab = el('label', 'chk');
    const cnt = g.nodes.filter((n) => n.family === f).length;
    lab.innerHTML = `<input type="checkbox" checked value="${f}">
      <span class="sw" style="background:${FAM_COLOR[f]}"></span>
      <span>${f}</span><span class="muted" style="font-size:15px">${cnt}</span>`;
    lab.querySelector('input').onchange = (e) => {
      e.target.checked ? activeFams.add(f) : activeFams.delete(f);
      rebuild();
    };
    famBox.appendChild(lab);
  });

  // 关系勾选（默认只开高频的）
  const relBox = $('#relBox');
  relTypes.forEach((r, i) => {
    const cnt = g.edges.filter((e) => e.relation === r).length;
    const on = i < 18;
    if (!on) activeRels.delete(r);
    const lab = el('label', 'chk');
    lab.innerHTML = `<input type="checkbox" ${on ? 'checked' : ''} value="${esc(r)}">
      <span>${esc(r)}</span><span class="muted" style="font-size:15px">${cnt}</span>`;
    lab.querySelector('input').onchange = (e) => {
      e.target.checked ? activeRels.add(r) : activeRels.delete(r);
      rebuild();
    };
    relBox.appendChild(lab);
  });
  $('#relToggle').onclick = () => {
    const collapsed = relBox.classList.toggle('collapsed');
    $('#relToggle').textContent = collapsed ? '展开 ▾' : '收起 ▴';
  };

  // 图例
  $('#gLegend').innerHTML = g.families.map((f) => `
    <div class="row"><span class="sw" style="background:${FAM_COLOR[f]}"></span>${f}</div>`).join('')
    + `<div class="row co-legend"><span class="sw co-sw"></span>共现边（MapReduce）</div>`;

  let inst = null;
  // ---- S29 共现叠加层 ----
  let coData = null;          // { edges: [{source,target,count}], ... }
  let coOn = false;
  (async () => {
    try {
      const r = await fetch('/data/cooccur_overlay.json?v=' + Date.now());
      if (r.ok) coData = await r.json();
    } catch (e) { /* 忽略 */ }
    const hint = $('#coHint');
    if (coData && coData.edges && coData.edges.length) {
      hint.innerHTML = `MapReduce 产出 · <b>${coData.n_pairs}</b> 对（≥${coData.min_count} 次）`;
    } else {
      hint.textContent = '无共现数据';
    }
  })();

  $('#gReset').onclick = () => {
    if (inst) { inst.resetView(); inst.reheat(); }
  };

  // 当前维度：'2d' | '3d'
  let dim = '2d';

  function rebuild() {
    // 防御性去重：同一人物只保留一个节点（优先保留度更高的）
    const _seenName = new Set();
    const _seenId = new Set();
    const nodes = g.nodes
      .filter((n) => activeFams.has(n.family))
      .slice()
      .sort((a, b) => (b.degree || 0) - (a.degree || 0))
      .filter((n) => {
        if (_seenId.has(n.id)) return false;
        if (_seenName.has(n.name)) return false;   // 同名视为同一人
        _seenId.add(n.id);
        _seenName.add(n.name);
        return true;
      });
    const ids = new Set(nodes.map((n) => n.id));
    const edges = g.edges.filter((e) => ids.has(e.source) && ids.has(e.target) && activeRels.has(e.relation));
    // 共现叠加：只保留两端节点都在当前视图内的边
    // 只叠加「强共现」边（≥30 次），否则 250 条虚线会糊成一片
    const CO_MIN = 30;
    const co = (coOn && coData && coData.edges)
      ? coData.edges.filter((e) => e.count >= CO_MIN
                                && ids.has(e.source) && ids.has(e.target))
      : [];
    if (inst) { inst.destroy(); inst = null; }
    const cv = $('#graphCanvas');
    // 切换维度时 canvas 需要重建上下文：2D 用 2d，WebGL 用 webgl
    // 同一个 canvas 一旦取了某类 context 就不能换，故替换节点
    const fresh = cv.cloneNode(false);
    cv.parentNode.replaceChild(fresh, cv);

    if (dim === 'chord') {
      // 家族 × 家族 关系矩阵
      const fams = g.families.filter((f) => activeFams.has(f));
      const famIdx = new Map(fams.map((f, i) => [f, i]));
      const M = fams.map(() => fams.map(() => 0));
      const nodeFam = new Map(g.nodes.map((n) => [n.id, n.family]));
      for (const e of edges) {
        const fa = nodeFam.get(e.source);
        const fb = nodeFam.get(e.target);
        if (famIdx.has(fa) && famIdx.has(fb)) {
          M[famIdx.get(fa)][famIdx.get(fb)] += 1;
          if (fa !== fb) M[famIdx.get(fb)][famIdx.get(fa)] += 1;
        }
      }
      inst = chord(fresh, M, fams, {});
      currentForce = inst;
      return;
    }

    if (dim === '3d') {
      try {
        inst = force3d(fresh, { nodes, edges }, { onSelect: showDetail, cooccur: co });
      } catch (err) {
        // WebGL 不可用 → 自动退回 2D，并提示
        console.warn('3D 渲染失败，退回 2D:', err);
        const tip = $('#gTip');
        if (tip) tip.textContent = '⚠ 当前环境不支持 WebGL，已退回 2D 视图';
        dim = '2d';
        document.querySelectorAll('.gr-dim-btn').forEach((b) => {
          b.classList.toggle('active', b.dataset.dim === '2d');
        });
        inst = force(fresh, { nodes, edges }, { onSelect: showDetail, cooccur: co });
      }
    } else {
      inst = force(fresh, { nodes, edges }, { onSelect: showDetail, cooccur: co });
    }
    currentForce = inst;
  }

  // 2D / 3D 切换
  document.querySelectorAll('.gr-dim-btn').forEach((btn) => {
    btn.onclick = () => {
      if (dim === btn.dataset.dim) return;
      dim = btn.dataset.dim;
      document.querySelectorAll('.gr-dim-btn').forEach((b) => {
        b.classList.toggle('active', b.dataset.dim === dim);
      });
      const tip = $('#gTip');
      if (tip) {
        tip.textContent =
          dim === '3d' ? '拖拽旋转 · 滚轮缩放 · 点击看详情 · 节点按家族着色，大小=关系数'
          : dim === 'chord' ? '悬停扇区看该家族 · 弦宽=两家关系条数 · 环内为同族关系'
          : '拖拽空白处平移 · 拖拽节点移动 · 滚轮缩放 · 点击看详情 · 双击空白重置';
      }
      rebuild();
    };
  });

  // 共现开关
  $('#coOn').onchange = (e) => {
    coOn = e.target.checked;
    rebuild();
  };

  function showDetail(n) {
    const box = $('#gDetail');
    if (!n) {
      box.innerHTML = '<div class="muted" style="font-size:17px">点击图中人物节点查看详情。</div>';
      return;
    }
    const rels = g.edges.filter((e) => e.source === n.id || e.target === n.id);
    const out = rels.filter((e) => e.source === n.id);
    const inc = rels.filter((e) => e.target === n.id);
    box.innerHTML = `
      <div class="person-name">${esc(n.name)}</div>
      <div class="person-meta">
        <span class="tag red" style="background:${FAM_COLOR[n.family]}22;color:${FAM_COLOR[n.family]}">${esc(n.family)}家</span>
        <span class="tag">度 ${n.degree}</span>
        <span class="tag">提及 ${fmt(n.mention)}</span>
      </div>
      ${n.role ? `<div class="person-role">${esc(n.role)}</div>` : ''}
      <div class="rel-list">
        ${out.length ? `<h4 style="font-size:15px;color:var(--ink-3);margin:0 0 4px;text-transform:uppercase;letter-spacing:.05em">→ 指出（${out.length}）</h4>` : ''}
        ${out.map((e) => `<div class="rel-row">
          <span class="dir">→</span>
          <span class="n2" data-goto="${esc(e.target)}">${esc(e.target)}</span>
          <span class="r2">${esc(e.relation)}</span></div>`).join('')}
        ${inc.length ? `<h4 style="font-size:15px;color:var(--ink-3);margin:12px 0 4px;text-transform:uppercase;letter-spacing:.05em">← 指入（${inc.length}）</h4>` : ''}
        ${inc.map((e) => `<div class="rel-row">
          <span class="dir">←</span>
          <span class="n2" data-goto="${esc(e.source)}">${esc(e.source)}</span>
          <span class="r2">${esc(e.relation)}</span></div>`).join('')}
        ${!rels.length ? '<div class="muted" style="font-size:17px">暂无关系记录</div>' : ''}
      </div>`;
    $$('[data-goto]', box).forEach((a) => {
      a.onclick = () => { inst?.focus(a.dataset.goto); };
    });
    $('#askRel').onclick = () => {
      const q = `在《红楼梦》里，${n.name}和谁是什么关系？`;
      state.pendingContext = { text: `【图谱上下文】${n.name}`, hui: null, paraId: null };
      location.hash = '#/chat';
      setTimeout(() => {
        const ta = $('#chatTextarea');
        if (ta) { ta.value = q; autoGrow(ta); renderContextChip(); }
      }, 120);
    };
  }

  // 搜索
  $('#gSearch').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      const v = e.target.value.trim();
      const hit = g.nodes.find((n) => n.name === v || n.name.includes(v));
      if (hit) { activeFams.add(hit.family); rebuild(); setTimeout(() => inst?.focus(hit.id), 60); }
      else toast('图中未找到该人物', 'red');
    }
  });
  document.querySelectorAll('#famBox input').forEach((inp) => {
    inp.addEventListener('change', () => { rebuild(); });
  });

  rebuild();
  setTimeout(() => showDetail(g.nodes[0]), 300);
}

/* =====================================================================
   ⑤ 语料与版本界面（R-405）
   ===================================================================== */
/** 渲染索引盘点卡片（s10 三源脂批） */
function renderCorpusStatsBlock(cs) {
  const by = cs.by_version_source || {};
  const pzBy = cs.pizhu_by_source_tag || {};
  const labels = cs.version_labels || {};
  const TEXT_ROWS = [
    ['fuqinju', '抚琴居校本', '前 80 回'],
    ['chenggao', '程高本', '1–120 回'],
    ['guiyouben', '癸酉本', '第 81–108 回'],
  ];
  const PZ_ROWS = [
    ['fuqinju', '抚琴居校本脂批', '3,219', 'PDF 码点重解码，版本归属最可信'],
    ['guiyouben', '癸酉本批语', '222', '后28回内嵌批 / 回前批 / 回末评 / 前80回摘录'],
    ['zhipi', '脂批大全', '5,069', '独立汇编，补前两源之缺'],
  ];
  const nText = TEXT_ROWS.reduce((a, [k]) => a + (by[k + '/text'] || 0), 0);
  const nPz = PZ_ROWS.reduce((a, [k]) => a + (by[k + '/pizhu'] || 0), 0);

  return `
    <div class="card col-7">
      <div class="card-h"><h3>RAG 索引盘点（正文 + 三源脂批）</h3><span class="tag green">实测</span></div>
      <div class="card-b">
        <table class="table">
          <thead><tr><th>类别</th><th>版本 / 来源</th><th class="num">chunk 数</th><th>说明</th></tr></thead>
          <tbody>
            ${TEXT_ROWS.map(([k, label, range]) => `
              <tr>
                <td><span class="tag">正文</span></td>
                <td>${esc(label)}</td>
                <td class="num">${fmt(by[k + '/text'] || 0)}</td>
                <td class="muted">${esc(range)}</td>
              </tr>`).join('')}
            ${PZ_ROWS.map(([k, label, expect, note]) => `
              <tr style="background:rgba(176,58,46,.02)">
                <td><span class="tag red">脂批</span></td>
                <td>${esc(label)}</td>
                <td class="num">${fmt(by[k + '/pizhu'] || 0)}</td>
                <td class="muted">${esc(note)}</td>
              </tr>`).join('')}
            <tr style="font-weight:600;background:var(--paper2)">
              <td colspan="2">合计</td>
              <td class="num">${fmt(cs.total_chunks)}</td>
              <td class="muted">正文 ${fmt(nText)} + 脂批 ${fmt(nPz)}</td>
            </tr>
          </tbody>
        </table>

        <div class="skew-note" style="margin-top:12px">
          <div class="sn-h">检索隔离规则（选正文版本时，哪些脂批参与）</div>
          <table class="table" style="margin-top:8px">
            <thead><tr><th>选中正文版本</th><th>参与检索的脂批来源</th></tr></thead>
            <tbody>
              <tr><td>抚琴居校本</td><td>抚琴居校本脂批 + 脂批大全 <span class="muted">（都是脂评本系统，互补）</span></td></tr>
              <tr><td>程高本</td><td><b>无</b> <span class="muted">（程高本是删改本，没有脂批）</span></td></tr>
              <tr><td>癸酉本</td><td>癸酉本批语 <span class="muted">（癸酉本独有）</span></td></tr>
              <tr><td>不限版本</td><td>三源全部</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>`;
}

async function renderCorpus(stage) {
  // s10：接入后端真实索引盘点（解决「没有通栏数据」的问题）
  let cs = null;
  try { cs = await getCorpusStats(); } catch (e) { console.warn('corpus/stats 不可用', e); }
  const csHtml = cs ? renderCorpusStatsBlock(cs) : '';

  // s26：三版本规模/质量规则改为读实测文件（不再写死）
  let csd = null;
  try {
    const r = await fetch('/data/corpus_stats.json?v=' + Date.now());
    if (r.ok) csd = await r.json();
  } catch (e) { console.warn('corpus_stats.json 不可用', e); }
  if (!csd) {
    csd = { versions: [], totals: { records: 0, chars: 0, han: 0 },
            quality: [], generated_at: '（未生成，请运行 scripts/s26_1_build_corpus_stats.py）' };
  }

  const [vm, mani] = await Promise.all([api('/api/version-matrix'), api('/api/manifest')]);
  const vers = Object.keys(vm.version_to_hui);

  stage.innerHTML = `
    <div class="view v-corpus">
      <div class="panel-wrap">
        <div class="page-h">
          <h2>语料与版本</h2>
          <p>版本只作元数据记载，不做校勘 · 事实源可追溯，派生层可重建</p>
        </div>

        <div class="stats-grid">
          <div class="card col-12">
            <div class="card-h"><h3>三版本正文规模</h3><span class="tag">实测</span></div>
            <div class="card-b">
              <table class="table">
                <thead><tr><th>版本</th><th class="num">条数</th><th class="num">字数</th><th class="num">汉字数</th><th>覆盖</th><th>用途</th></tr></thead>
                <tbody>
                  ${csd.versions.map((v) => `<tr>
                    <td>${esc(v.label)}${v.note ? ` <span class="muted" style="font-size:15px">（${esc(v.note)}）</span>` : ''}</td>
                    <td class="num">${fmt(v.records)}</td>
                    <td class="num">${fmt(v.chars)}</td>
                    <td class="num">${fmt(v.han)}</td>
                    <td>${esc(v.range)} 回</td>
                    <td>${v.use === 'RAG 主链'
                      ? `<span class="tag red">${esc(v.use)}</span>`
                      : `<span class="tag">${esc(v.use)}</span>`}</td>
                  </tr>`).join('')}
                  <tr style="font-weight:600">
                    <td>合计</td>
                    <td class="num">${fmt(csd.totals.records)}</td>
                    <td class="num">${fmt(csd.totals.chars)}</td>
                    <td class="num">${fmt(csd.totals.han)}</td>
                    <td colspan="2"></td>
                  </tr>
                </tbody>
              </table>
              <div class="muted" style="font-size:15px;margin-top:8px;line-height:1.7">
                实测于 ${esc(csd.generated_at)}。「字数」为段落合计（含标点），
                「汉字数」仅统计 CJK 统一表意文字 —— 两种口径不同，勿混用。
              </div>
            </div>
          </div>

          ${csHtml}

          <div class="card col-5">
            <div class="card-h"><h3>数据来源与许可</h3></div>
            <div class="card-b" style="font-size:17px;line-height:1.9">
              <div>正文 / 脂批：<b>公版</b>（曹雪芹，保护期已过）</div>
              <div>评测集：<b>CC-BY-4.0</b>，须署名 CongJ-Pan</div>
              <div>真实提问：混合许可，仅本地教学</div>
              <div>三元组：3 个 GitHub 来源，注明出处</div>
              <div style="margin-top:8px;padding-top:8px;border-top:1px dashed var(--line);color:var(--ink-3)">
                依《著作权法》第二十四条，课堂教学合理使用，不出版发行。
              </div>
            </div>
          </div>

          <div class="card col-12">
            <div class="card-h">
              <h3>脂批版本 × 回次覆盖矩阵</h3>
              <span class="tag">颜色越深 = 该脂批版本在该回段批语越多</span>
            </div>
            <div class="card-b matrix-wrap" id="matrixBox"></div>
            <div class="card-b muted" style="font-size:15.5px;line-height:1.8;border-top:1px dashed var(--line)">
              横轴为回次区间（1–10、11–20 …），纵轴为<b>脂批来源版本</b>
              （甲戌本 / 庚辰本 / 蒙府本 / 戚序本等），
              与上方「三版本正文规模」是<b>两个不同维度</b>：
              上表讲正文全本，此表讲脂批在各抄本中的回次分布。
            </div>
          </div>

          <div class="card col-6">
            <div class="card-h"><h3>数据质量规则（Q1–Q8）</h3><span class="tag gold">如实报告</span></div>
            <div class="card-b" id="qualityBox"></div>
          </div>

          <div class="card col-6">
            <div class="card-h"><h3>溯源清单</h3><span class="tag">${mani.sources.length} 项</span></div>
            <div class="card-b">
              <table class="table">
                <thead><tr><th>来源</th><th class="num">条数</th><th>说明</th></tr></thead>
                <tbody>
                  ${mani.sources.map((s) => `<tr>
                    <td class="mono" style="font-size:15px">${esc(s.path.split('/').pop())}</td>
                    <td class="num">${s.n != null ? fmt(s.n) : '—'}</td>
                    <td class="muted" style="font-size:16.5px">${esc(s.desc)}</td>
                  </tr>`).join('')}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </div>`;

  // 覆盖矩阵
  const buckets = 8;
  const rows = vers.filter((v) => v !== '(未识别)');
  const box = $('#matrixBox');
  box.innerHTML = `<div class="matrix">
    <div class="maxis">${Array.from({ length: buckets }, (_, i) => `<span>${i * 10 + 1}</span>`).join('')}</div>
    ${rows.map((v) => {
      const row = new Array(buckets).fill(0);
      vm.version_to_hui[v].forEach((h) => { row[Math.min(buckets - 1, Math.floor((h - 1) / 10))]++; });
      const mx = Math.max(...row, 1);
      return `<div class="mrow"><span class="mlabel">${esc(v)}</span>${row.map((c) => {
        const a = c / mx;
        return `<span class="mcell" title="${esc(v)} ${c} 条" style="background:${c === 0 ? 'var(--paper-3)' : `rgba(176,58,46,${0.12 + a * 0.78})`}"></span>`;
      }).join('')}</div>`;
    }).join('')}
  </div>`;

  // 质量规则（读实测文件，不再写死）
  $('#qualityBox').innerHTML = csd.quality.map((q) => `
    <div class="quality-row">
      <span class="qid">${esc(q.id)}</span>
      <span class="qd">${esc(q.desc)}</span>
      <span class="qv" style="color:${q.level === 'warn' ? 'var(--amber)' : q.level === 'muted' ? 'var(--ink-3)' : 'var(--jade)'}">${esc(q.value)}</span>
    </div>`).join('')
    + `<div class="muted" style="font-size:15px;margin-top:8px;line-height:1.7">
        实测于 ${esc(csd.generated_at)}。Q3–Q5 的状态由 S12–S15 多轮修复后更新。
       </div>`;
}

/* =====================================================================
   ⑥ 系统与运维界面（R-406）
   ===================================================================== */
async function renderOps(stage) {
  const health = await api('/api/health');
  const mani = await api('/api/manifest');

  stage.innerHTML = `
    <div class="view v-ops">
      <div class="panel-wrap">
        <div class="page-h">
          <h2>系统与运维</h2>
          <p>Hadoop 集群 · RAG 链路诊断 · 阶段进度（数据为 S1 静态快照，S6 接实时探针）</p>
        </div>

        <div class="stats-grid">
          <div class="card col-12">
            <div class="card-h"><h3>Hadoop 集群组件</h3>
              <span class="tag green">docker compose: hadoop-lab</span></div>
            <div class="card-b">
              <div class="svc-grid">
                ${[
                  ['HDFS', 'NameNode + 2 DataNode · 副本 2 · 1.97 TB', '9870', 'ok'],
                  ['YARN', '2 NodeManager RUNNING', '18088', 'ok'],
                  ['MapReduce', 'JobHistoryServer 19888', '19888', 'ok'],
                  ['HBase', '1 master / 2 RegionServer', '16010', 'ok'],
                  ['ZooKeeper', '单机模式（非集群）', '12181', 'warn'],
                  ['Spark', 'Standalone · 2 Worker', '18080', 'ok'],
                ].map(([n, d, p, k]) => `
                  <div class="svc">
                    <div class="ic ${k}">${k === 'ok' ? '✅' : '⚠️'}</div>
                    <div style="flex:1;min-width:0">
                      <div class="nm">${n}</div>
                      <div class="st">${d}</div>
                      <div class="st"><a href="http://127.0.0.1:${p}" target="_blank" rel="noopener">127.0.0.1:${p} ↗</a></div>
                    </div>
                  </div>`).join('')}
              </div>
            </div>
          </div>

          <div class="card col-6">
            <div class="card-h"><h3>RAG 链路状态</h3><span class="tag">S1 骨架</span></div>
            <div class="card-b">
              ${[
                ['语料切分（Chunk）', 'S2', false],
                ['向量化（Embedding）', 'S2', false],
                ['HBase 索引写入', 'S2', false],
                ['混合检索（三路）', 'S4', false],
                ['DeepSeek 生成', 'S4', false],
                ['引用溯源', 'S1 已可演示', true],
              ].map(([n, s, ok]) => `
                <div class="quality-row">
                  <span class="qid">${ok ? '●' : '○'}</span>
                  <span class="qd">${n}</span>
                  <span class="qv" style="color:${ok ? 'var(--jade)' : 'var(--ink-3)'}">${s}</span>
                </div>`).join('')}
            </div>
          </div>

          <div class="card col-6">
            <div class="card-h"><h3>存储规划</h3></div>
            <div class="card-b">
              <div class="arch-box">HDFS 事实源            HBase 派生索引
<span class="hi">/hlm/raw</span>       原始语料   <span class="ok">hlm_chunks</span>   chunk 正文
<span class="hi">/hlm/clean</span>     规范化     <span class="ok">hlm_vectors</span>  float32 向量
<span class="hi">/hlm/warehouse</span> chunks     <span class="ok">hlm_terms</span>    倒排索引
<span class="hi">/hlm/derived</span>   派生层     <span class="ok">hlm_kg</span>        三元组
<span class="hi">/hlm/manifest</span>  分区校验   <span class="ok">hlm_qa</span>        问答反馈</div>
            </div>
          </div>

          <div class="card col-12">
            <div class="card-h"><h3>接口自检</h3></div>
            <div class="card-b">
              <table class="table">
                <thead><tr><th>接口</th><th>状态</th><th>说明</th></tr></thead>
                <tbody>
                  <tr><td class="mono">GET /api/health</td><td><span class="tag green">200</span></td><td>阶段：${esc(health.stage)}</td></tr>
                  <tr><td class="mono">GET /api/chapters</td><td><span class="tag green">200</span></td><td>${mani.stats_summary.chapters} 回</td></tr>
                  <tr><td class="mono">GET /api/chapter?hui=N</td><td><span class="tag green">200</span></td><td>按回懒加载</td></tr>
                  <tr><td class="mono">GET /api/stats</td><td><span class="tag green">200</span></td><td>统计全集</td></tr>
                  <tr><td class="mono">GET /api/graph</td><td><span class="tag green">200</span></td><td>${mani.stats_summary.graph_nodes} 节点 / ${mani.stats_summary.graph_edges} 边</td></tr>
                  <tr><td class="mono">GET /api/locate?q=</td><td><span class="tag green">200</span></td><td>真实语料定位（引用溯源用）</td></tr>
                  <tr><td class="mono">POST /api/backend/chat</td><td><span class="tag gold">503</span></td><td>Python RAG 后端（S4 实装）</td></tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </div>`;
}

/* ============================== 启动 ============================== */
async function boot() {
  initSelectionPopup();

  // 全局元信息
  try {
    const [mani, ch] = await Promise.all([api('/api/manifest'), api('/api/chapters')]);
    state.manifest = mani;
    state.meta = ch;
    $('#ms-chapters').textContent = mani.stats_summary.chapters;
    // s10：批语数改用后端真实索引（三源合计 9,193）
    try {
      const st = await getCorpusStats();
      const nPz = Object.entries(st.by_version_source || {})
        .filter(([k]) => k.endsWith('/pizhu'))
        .reduce((a, [, v]) => a + v, 0);
      $('#ms-pizhu').textContent = fmt(nPz || mani.stats_summary.pizhu);
    } catch {
      $('#ms-pizhu').textContent = fmt(mani.stats_summary.pizhu);
    }
    // s31：人物节点数改用图谱真实数据（manifest 里是静态旧值）
    try {
      const gg = await api('/api/graph');
      $('#ms-nodes').textContent = fmt(gg.n_nodes || mani.stats_summary.graph_nodes);
    } catch {
      $('#ms-nodes').textContent = mani.stats_summary.graph_nodes;
    }
  } catch (e) {
    console.warn('元信息载入失败', e);
  }

  // 后端探测（S4：真实 RAG 服务）
  await probeBackend();
  state.backendOk = backendStatus.ok;
  const pill = $('#backendStatus');
  if (state.backendOk) {
    const info = backendStatus.info || {};
    pill.innerHTML = `<span class="dot ok"></span><span class="txt">RAG 就绪 · ${info.n_chunks || '?'} chunk / ${info.n_vectors || '?'} 向量</span>`;
    pill.title = `HBase ${info.hbase} · 模型 ${(info.models || []).join(', ')}`;
  } else {
    pill.innerHTML = '<span class="dot warn"></span><span class="txt">后端未连接 · 纯检索模式</span>';
    pill.title = '启动 backend/run_backend.sh 后刷新';
  }

  // 模型下拉（问题 5：从 /providers 读，含用户自定义）
  async function refreshModelSelect() {
    try {
      const data = await getProviders();
      const sel = $('#modelSelect');
      const list = data.providers || [];
      if (!list.length) return;
      sel.innerHTML = list.map((x) =>
        `<option value="${esc(x.id)}" ${x.id === (state.model || data.default) ? 'selected' : ''}
          ${x.has_key ? '' : 'disabled'}>${esc(x.label)}${x.has_key ? '' : '（未配密钥）'}</option>`).join('');
      // 当前选中的模型若已不可用，回落到第一个可用的
      const cur = list.find((x) => x.id === state.model && x.has_key);
      state.model = cur ? cur.id : (list.find((x) => x.has_key)?.id || data.default);
      sel.value = state.model;
    } catch (e) {
      console.warn('拉取模型列表失败', e);
    }
  }
  await refreshModelSelect();

  // ⚙ 配置按钮 → 打开模型配置面板
  const btnCfg = $('#btnModelCfg');
  if (btnCfg) {
    btnCfg.onclick = () => openProviderPanel(document.body, async () => {
      await refreshModelSelect();
      toast('模型配置已更新');
    });
  }

  // ⛶ 全屏切换
  initFullscreen();

  // 模型切换
  $('#modelSelect').onchange = (e) => {
    state.model = e.target.value;
    const label = e.target.selectedOptions[0]?.textContent || state.model;
    toast(`已切换：${label}`);
  };

  // 思考开关状态
  state.thinkOn = true;

  // 模型选择
  $('#modelSelect').onchange = (e) => {
    state.model = e.target.value;
    toast('已切换模型：' + e.target.selectedOptions[0].textContent);
  };

  window.addEventListener('hashchange', route);
  if (!location.hash) location.hash = '#/home';
  route();
}

boot();

/* =====================================================================
   ⛶ 全屏切换
   ---------------------------------------------------------------------
   浏览器全屏 API 需要用户手势触发（按钮点击满足）。
   状态变化通过 fullscreenchange 事件同步 —— 包括用户按 Esc 退出时。
   ===================================================================== */
function initFullscreen() {
  const btn = document.getElementById('btnFullscreen');
  if (!btn) return;

  const ico = btn.querySelector('.fs-ico');
  const txt = btn.querySelector('.fs-txt');

  /** 当前是否处于全屏（兼容 WebKit 前缀） */
  function isFs() {
    return !!(document.fullscreenElement ||
              document.webkitFullscreenElement ||
              document.msFullscreenElement);
  }

  /** 同步按钮外观 */
  function sync() {
    const on = isFs();
    if (ico) ico.textContent = on ? '⛶' : '⛶';
    if (txt) txt.textContent = on ? '退出全屏' : '全屏';
    btn.title = on ? '退出全屏（Esc）' : '进入全屏（Esc 退出）';
    btn.classList.toggle('active', on);
    // 全屏后高度变化，让画布重算尺寸
    window.dispatchEvent(new Event('resize'));
  }

  async function enter() {
    const el = document.documentElement;
    try {
      if (el.requestFullscreen) await el.requestFullscreen();
      else if (el.webkitRequestFullscreen) el.webkitRequestFullscreen();
      else if (el.msRequestFullscreen) el.msRequestFullscreen();
      else { toast('当前浏览器不支持全屏'); return; }
      toast('已进入全屏，按 Esc 退出');
    } catch (e) {
      console.warn('进入全屏失败', e);
      toast('进入全屏失败：' + (e.message || e));
    }
  }

  async function exit() {
    try {
      if (document.exitFullscreen) await document.exitFullscreen();
      else if (document.webkitExitFullscreen) document.webkitExitFullscreen();
      else if (document.msExitFullscreen) document.msExitFullscreen();
    } catch (e) {
      console.warn('退出全屏失败', e);
    }
  }

  btn.onclick = () => (isFs() ? exit() : enter());

  // 状态同步（含 Esc 退出）
  ['fullscreenchange', 'webkitfullscreenchange', 'MSFullscreenChange'].forEach((ev) => {
    document.addEventListener(ev, sync);
  });

  sync();
}
