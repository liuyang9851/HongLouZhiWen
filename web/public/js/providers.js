/**
 * providers.js —— 模型配置面板（问题 5）
 *
 * 需求：用户能自己填 API 网址 / 密钥 / 模型名，并区分 OpenAI 与 Antigravity 协议。
 *
 * 两种协议的关键差异（面板上直接写明，避免用户填错）
 * ---------------------------------------------------
 *   OpenAI 兼容                     Antigravity / Google
 *   ─────────────────────────       ─────────────────────────
 *   端点  /chat/completions         端点  /v1beta/models/{model}:generateContent
 *   鉴权  Authorization: Bearer     鉴权  x-goog-api-key
 *   请求  {model, messages}         请求  {contents:[{role,parts}]}
 *   响应  choices[0].message        响应  candidates[0].content.parts
 *   角色  user/assistant/system     角色  user/model + systemInstruction
 */

import {
  getProviders, saveProvider, deleteProvider, testProvider,
} from './rag.js';

const PRESETS = [
  {
    id: 'deepseek-flash', kind: 'openai',
    label: 'DeepSeek · deepseek-flash',
    base_url: 'https://api.deepseek.com', model: 'deepseek-flash',
    note: '官方 API（默认）',
  },
  {
    id: 'deepseek-v4-pro', kind: 'openai',
    label: 'DeepSeek · deepseek-v4-pro',
    base_url: 'https://api.deepseek.com', model: 'deepseek-v4-pro',
    note: '官方 API，推理更强',
  },
  {
    id: 'glm-4-flash', kind: 'openai',
    label: '智谱 · glm-4-flash',
    base_url: 'https://open.bigmodel.cn/api/paas/v4', model: 'glm-4-flash',
    note: '智谱 AI',
  },
  {
    id: 'gemini-flash', kind: 'antigravity',
    label: 'Antigravity · gemini-flash',
    base_url: 'https://generativelanguage.googleapis.com',
    model: 'gemini-2.0-flash',
    note: 'Antigravity / Google 协议',
  },
];

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/**
 * 打开模型配置面板
 * @param {HTMLElement} host 挂载点（通常是 document.body）
 * @param {Function} onChanged 配置变化后的回调（刷新下拉框）
 */
export async function openProviderPanel(host, onChanged) {
  // 先移除已有面板
  host.querySelector('#provPanel')?.remove();

  const mask = document.createElement('div');
  mask.id = 'provPanel';
  mask.className = 'prov-mask';
  mask.innerHTML = `
    <div class="prov-dialog" role="dialog" aria-modal="true" aria-label="模型配置">
      <div class="prov-h">
        <div>
          <div class="t">模型配置</div>
          <div class="m">自定义 API 地址 / 密钥 / 模型名 · 支持 OpenAI 与 Antigravity 两种协议</div>
        </div>
        <button class="btn sm" id="provClose">关闭</button>
      </div>

      <div class="prov-body">
        <div class="prov-sec">
          <div class="prov-sec-h">当前已配置</div>
          <div id="provList" class="prov-list"><div class="loading">载入中…</div></div>
        </div>

        <div class="prov-sec">
          <div class="prov-sec-h">添加 / 修改模型</div>

          <div class="prov-row">
            <label>协议类型</label>
            <div class="prov-kinds">
              <label class="prov-kind">
                <input type="radio" name="pkind" value="openai" checked>
                <span>OpenAI 兼容</span>
                <small>DeepSeek / 智谱 / Moonshot / Ollama / vLLM / one-api</small>
              </label>
              <label class="prov-kind">
                <input type="radio" name="pkind" value="antigravity">
                <span>Antigravity</span>
                <small>Google Gemini 系（generativelanguage API）</small>
              </label>
            </div>
          </div>

          <div class="prov-row">
            <label>预设</label>
            <div id="provPresets" class="prov-presets"></div>
          </div>

          <div class="prov-grid">
            <div class="prov-row">
              <label for="pId">ID<em>唯一标识，用于选择</em></label>
              <input id="pId" placeholder="例如 my-deepseek" autocomplete="off">
            </div>
            <div class="prov-row">
              <label for="pModel">模型名<em>API 里的 model 字段</em></label>
              <input id="pModel" placeholder="例如 deepseek-flash" autocomplete="off">
            </div>
          </div>

          <div class="prov-row">
            <label for="pBase">API 地址<em id="pBaseHint">OpenAI：填到域名或 /v4，程序自动补 /chat/completions</em></label>
            <input id="pBase" placeholder="https://api.deepseek.com" autocomplete="off">
          </div>

          <div class="prov-row">
            <label for="pKey">API 密钥<em>只存在后端，不会回传浏览器</em></label>
            <input id="pKey" type="password" placeholder="留空表示沿用已保存的密钥" autocomplete="new-password">
          </div>

          <div class="prov-row">
            <label for="pLabel">显示名<em>可选</em></label>
            <input id="pLabel" placeholder="例如 DeepSeek · 我的 key" autocomplete="off">
          </div>

          <div class="prov-actions">
            <button class="btn" id="provTest">测试连通</button>
            <button class="btn primary" id="provSave">保存</button>
            <span id="provMsg" class="prov-msg"></span>
          </div>

          <details class="prov-help">
            <summary>两种协议有什么不同（点开看）</summary>
            <table class="prov-table">
              <thead><tr><th></th><th>OpenAI 兼容</th><th>Antigravity / Google</th></tr></thead>
              <tbody>
                <tr><td>端点</td><td><code>/chat/completions</code></td><td><code>/v1beta/models/{model}:generateContent</code></td></tr>
                <tr><td>鉴权头</td><td><code>Authorization: Bearer &lt;key&gt;</code></td><td><code>x-goog-api-key: &lt;key&gt;</code></td></tr>
                <tr><td>请求体</td><td><code>{model, messages:[{role,content}]}</code></td><td><code>{contents:[{role,parts:[{text}]}]}</code></td></tr>
                <tr><td>角色名</td><td>user / assistant / system</td><td>user / <b>model</b>；system 放 <code>systemInstruction</code></td></tr>
                <tr><td>响应取文</td><td><code>choices[0].message.content</code></td><td><code>candidates[0].content.parts[].text</code></td></tr>
                <tr><td>流式</td><td>SSE，<code>data: {delta:{content}}</code></td><td>SSE (<code>?alt=sse</code>)，每块取 parts</td></tr>
              </tbody>
            </table>
          </details>
        </div>
      </div>
    </div>`;
  host.appendChild(mask);

  const $ = (s) => mask.querySelector(s);

  // ---- 预设按钮 ----
  $('#provPresets').innerHTML = PRESETS.map((p) =>
    `<button class="prov-chip" data-preset="${p.id}">${esc(p.label)}
      <small>${p.kind === 'openai' ? 'OpenAI' : 'Antigravity'}</small></button>`).join('');

  function fillPreset(p) {
    $('#pId').value = p.id;
    $('#pModel').value = p.model;
    $('#pBase').value = p.base_url;
    $('#pLabel').value = p.label;
    mask.querySelector(`input[name=pkind][value="${p.kind}"]`).checked = true;
    updateKindHint();
  }
  $('#provPresets').addEventListener('click', (e) => {
    const b = e.target.closest('[data-preset]');
    if (!b) return;
    const p = PRESETS.find((x) => x.id === b.dataset.preset);
    if (p) fillPreset(p);
  });

  function currentKind() {
    return mask.querySelector('input[name=pkind]:checked').value;
  }
  function updateKindHint() {
    const k = currentKind();
    $('#pBaseHint').innerHTML = k === 'openai'
      ? 'OpenAI：填到域名或 <code>/v4</code>，程序自动补 <code>/chat/completions</code>'
      : 'Antigravity：填到域名，程序自动补 <code>/v1beta/models/{model}:generateContent</code>';
  }
  mask.querySelectorAll('input[name=pkind]').forEach((r) =>
    r.addEventListener('change', updateKindHint));
  updateKindHint();

  function readForm() {
    return {
      id: $('#pId').value.trim(),
      label: $('#pLabel').value.trim(),
      kind: currentKind(),
      base_url: $('#pBase').value.trim(),
      model: $('#pModel').value.trim(),
      api_key: $('#pKey').value,
    };
  }

  function msg(text, kind = '') {
    const m = $('#provMsg');
    m.textContent = text;
    m.className = 'prov-msg ' + kind;
  }

  // ---- 列表 ----
  async function refreshList() {
    const box = $('#provList');
    try {
      const data = await getProviders();
      const list = data.providers || [];
      box.innerHTML = list.map((p) => `
        <div class="prov-item">
          <div class="l">
            <div class="n">
              ${esc(p.label)}
              ${p.builtin ? '<span class="tag">内置</span>' : '<span class="tag red">自定义</span>'}
              <span class="tag ${p.kind === 'openai' ? '' : 'gold'}">${p.kind === 'openai' ? 'OpenAI' : 'Antigravity'}</span>
            </div>
            <div class="m">${esc(p.model)} · ${esc(p.base_url)}</div>
          </div>
          <div class="r">
            <span class="${p.has_key ? 'ok' : 'warn'}">${p.has_key ? '● 密钥已配' : '○ 无密钥'}</span>
            ${p.builtin ? '' : `<button class="btn xs danger" data-del="${esc(p.id)}">删除</button>`}
            <button class="btn xs" data-use="${esc(p.id)}">填入</button>
          </div>
        </div>`).join('') || '<div class="muted">暂无配置</div>';

      box.querySelectorAll('[data-del]').forEach((b) => {
        b.onclick = async () => {
          if (!confirm(`删除模型 ${b.dataset.del}？`)) return;
          try {
            await deleteProvider(b.dataset.del);
            msg('已删除', 'ok');
            await refreshList();
            onChanged?.();
          } catch (e) { msg('删除失败：' + e.message, 'err'); }
        };
      });
      box.querySelectorAll('[data-use]').forEach((b) => {
        b.onclick = () => {
          const p = list.find((x) => x.id === b.dataset.use);
          if (!p) return;
          fillPreset({ ...p, note: '' });
          msg('已填入表单，可修改后保存', 'ok');
        };
      });
    } catch (e) {
      box.innerHTML = `<div class="prov-msg err">载入失败：${esc(e.message)}</div>`;
    }
  }

  // ---- 测试 ----
  $('#provTest').onclick = async () => {
    const f = readForm();
    if (!f.base_url || !f.model) return msg('请先填 API 地址和模型名', 'err');
    if (!f.api_key && !f.id) return msg('请填 API 密钥', 'err');
    msg('测试中…');
    $('#provTest').disabled = true;
    try {
      const r = await testProvider({
        id: f.id || '__test__', label: f.label || 'test',
        kind: f.kind, base_url: f.base_url, model: f.model, api_key: f.api_key,
      });
      if (r.ok) {
        msg(`✅ 连通正常（${r.elapsed_ms} ms）· 回复「${r.reply || ''}」`, 'ok');
      } else {
        msg('❌ ' + (r.error || '未知错误'), 'err');
      }
    } catch (e) {
      msg('❌ ' + e.message, 'err');
    } finally {
      $('#provTest').disabled = false;
    }
  };

  // ---- 保存 ----
  $('#provSave').onclick = async () => {
    const f = readForm();
    if (!f.id) return msg('请填 ID', 'err');
    if (!f.model) return msg('请填模型名', 'err');
    if (!f.base_url) return msg('请填 API 地址', 'err');
    msg('保存中…');
    try {
      await saveProvider(f);
      msg('✅ 已保存', 'ok');
      $('#pKey').value = '';
      await refreshList();
      onChanged?.();
    } catch (e) {
      msg('❌ ' + e.message, 'err');
    }
  };

  $('#provClose').onclick = () => mask.remove();
  mask.addEventListener('click', (e) => { if (e.target === mask) mask.remove(); });
  document.addEventListener('keydown', function onEsc(e) {
    if (e.key === 'Escape') { mask.remove(); document.removeEventListener('keydown', onEsc); }
  });

  await refreshList();
}
