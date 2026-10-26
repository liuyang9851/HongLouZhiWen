/**
 * md.js —— 轻量 Markdown 渲染器（零依赖）
 *
 * 为什么自己写而不用 marked.js：
 *   1. 前端要求零第三方依赖（离线可用、无 CDN）
 *   2. 只需要渲染 LLM 输出这一种场景，不需要完整 CommonMark
 *
 * 支持的语法（覆盖 DeepSeek/GLM 实际输出的全部形态）
 * --------------------------------------------------
 *   # / ## / ### / ####   标题
 *   **粗体**  *斜体*  ~~删除线~~  `行内代码`
 *   ```lang ... ```       代码块
 *   > 引用（支持多行）
 *   - / * / +             无序列表（支持嵌套缩进）
 *   1. 2) 3、             有序列表
 *   ---                   分割线
 *   | a | b |             表格（含对齐行）
 *   [文字](url)           链接
 *   段落                  其余
 *
 * **安全**：所有文本先 escapeHtml 再插标签，不允许原始 HTML 直通。
 *          （LLM 可能输出 <script>，必须防）
 */

const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

export function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ESC[c]);
}

/** 行内元素：粗体/斜体/删除线/代码/链接 */
function inline(text) {
  // 先保护行内代码，避免里面的 * _ 被当格式
  const codes = [];
  let s = String(text).replace(/`([^`]+)`/g, (_, c) => {
    codes.push(c);
    return `\u0000C${codes.length - 1}\u0000`;
  });

  s = escapeHtml(s);

  // 链接 [text](url) —— 只允许 http/https/# 开头，防 javascript:
  s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, label, url) => {
    const safe = /^(https?:\/\/|#|\/)/i.test(url) ? url : '#';
    return `<a href="${safe}" target="_blank" rel="noopener noreferrer">${label}</a>`;
  });

  // 粗体 **x** 或 __x__
  s = s.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/__([^_\n]+)__/g, '<strong>$1</strong>');
  // 斜体 *x* 或 _x_（要求两侧不是字母/星号，避免误伤）
  s = s.replace(/(^|[\s（(，。；：、!?])\*([^*\n]+)\*(?=$|[\s）)，。；：、!?])/g,
                '$1<em>$2</em>');
  s = s.replace(/(^|[\s（(])\_([^_\n]+)\_(?=$|[\s）)])/g, '$1<em>$2</em>');
  // 删除线
  s = s.replace(/~~([^~\n]+)~~/g, '<del>$1</del>');

  // 还原行内代码
  s = s.replace(/\u0000C(\d+)\u0000/g, (_, i) => `<code>${escapeHtml(codes[+i])}</code>`);
  return s;
}

/** 判断是否是表格分隔行：| --- | :--: | */
function isTableSep(line) {
  return /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(line) && line.includes('-');
}

/** 解析表格行为单元格数组 */
function cells(line) {
  let s = line.trim();
  if (s.startsWith('|')) s = s.slice(1);
  if (s.endsWith('|')) s = s.slice(0, -1);
  return s.split('|').map((x) => x.trim());
}

/**
 * 主渲染函数：Markdown 文本 → HTML
 * @param {string} src
 * @returns {string}
 */
export function renderMarkdown(src) {
  if (!src) return '';
  const lines = String(src).replace(/\r\n?/g, '\n').split('\n');
  const out = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // ---- 空行 ----
    if (!line.trim()) { i++; continue; }

    // ---- 代码块 ----
    const fence = line.match(/^\s*(```+|~~~+)\s*([\w+-]*)\s*$/);
    if (fence) {
      const marker = fence[1][0];
      const lang = fence[2] || '';
      const buf = [];
      i++;
      while (i < lines.length && !new RegExp('^\\s*' + marker + '{3,}').test(lines[i])) {
        buf.push(lines[i]); i++;
      }
      i++; // 跳过结束围栏
      out.push(
        `<pre class="md-pre"><code${lang ? ` class="lang-${escapeHtml(lang)}"` : ''}>` +
        escapeHtml(buf.join('\n')) + '</code></pre>'
      );
      continue;
    }

    // ---- 标题 ----
    const h = line.match(/^\s*(#{1,6})\s+(.+?)\s*#*\s*$/);
    if (h) {
      const lv = Math.min(h[1].length + 1, 6);   // h1 保留给页面标题，正文从 h2 起
      out.push(`<h${lv} class="md-h">${inline(h[2])}</h${lv}>`);
      i++; continue;
    }

    // ---- 分割线 ----
    if (/^\s*([-*_])\s*\1\s*\1[\s\1-*_]*$/.test(line)) {
      out.push('<hr class="md-hr">'); i++; continue;
    }

    // ---- 表格 ----
    if (line.includes('|') && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      const head = cells(line);
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) {
        rows.push(cells(lines[i])); i++;
      }
      out.push(
        '<div class="md-table-wrap"><table class="md-table"><thead><tr>' +
        head.map((c) => `<th>${inline(c)}</th>`).join('') +
        '</tr></thead><tbody>' +
        rows.map((r) => '<tr>' + r.map((c) => `<td>${inline(c)}</td>`).join('') + '</tr>').join('') +
        '</tbody></table></div>'
      );
      continue;
    }

    // ---- 引用块 ----
    if (/^\s*>\s?/.test(line)) {
      const buf = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
        buf.push(lines[i].replace(/^\s*>\s?/, '')); i++;
      }
      out.push(`<blockquote class="md-quote">${renderMarkdown(buf.join('\n'))}</blockquote>`);
      continue;
    }

    // ---- 列表（有序 / 无序，支持嵌套） ----
    // 注意：`**1. 袭人**` 是「粗体包编号」，不是列表。
    // 所以行首是 ** 或 __ 时，一律不按列表处理。
    const boldStart = /^\s*(\*\*|__)/.test(line);
    const isUl = !boldStart && /^\s*[-*+]\s+/.test(line);
    const isOl = !boldStart && /^\s*\d+[.)、]\s+/.test(line);
    if (isUl || isOl) {
      const ordered = isOl;
      const items = [];
      while (i < lines.length) {
        const cur = lines[i];
        const m = ordered
          ? cur.match(/^(\s*)\d+[.)、]\s+(.*)$/)
          : cur.match(/^(\s*)[-*+]\s+(.*)$/);
        if (!m) {
          // 续行（缩进的非列表行）并入上一项
          if (items.length && /^\s{2,}\S/.test(cur)) {
            items[items.length - 1].text += '\n' + cur.trim();
            i++; continue;
          }
          break;
        }
        items.push({ indent: m[1].length, text: m[2] });
        i++;
      }
      const tag = ordered ? 'ol' : 'ul';
      const html = items.map((it) => {
        const body = it.text.includes('\n')
          ? renderMarkdown(it.text)
          : inline(it.text);
        return `<li>${body}</li>`;
      }).join('');
      out.push(`<${tag} class="md-list">${html}</${tag}>`);
      continue;
    }

    // ---- 段落（连续非空行合并） ----
    const buf = [line];
    i++;
    while (i < lines.length && lines[i].trim() &&
           !/^\s*(?:\*\*|__)/.test(lines[i]) &&
           !/^\s*(#{1,6}\s|[-*+]\s|\d+[.)、]\s|>|```|~~~)/.test(lines[i]) &&
           !(lines[i].includes('|') && i + 1 < lines.length && isTableSep(lines[i + 1]))) {
      buf.push(lines[i]); i++;
    }
    out.push(`<p class="md-p">${inline(buf.join('\n')).replace(/\n/g, '<br>')}</p>`);
  }

  return out.join('\n');
}

/** 给正文容器注入渲染结果 */
export function setMarkdown(container, text) {
  container.innerHTML = renderMarkdown(text);
  // 让代码块/表格可以横向滚动而不撑破布局
  container.querySelectorAll('table').forEach((t) => {
    if (!t.parentElement.classList.contains('md-table-wrap')) {
      const w = document.createElement('div');
      w.className = 'md-table-wrap';
      t.parentNode.insertBefore(w, t);
      w.appendChild(t);
    }
  });
}
