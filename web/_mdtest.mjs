import { renderMarkdown } from './public/js/md.js';

// 正确做法：分别验证「记号残留」和「结构正确」。
// 「1. 」出现在段首是合法的（粗体包编号），无法从扁平文本区分，
// 所以对列表只做结构断言（必须存在 <li>），不做文本残留检查。
function marksLeft(html) {
  const text = html.replace(/<[^>]+>/g, '\u0001');
  const found = [];
  if (/\*\*/.test(text)) found.push('**');
  if (/(^|\u0001)\s*#{1,6}\s/.test(text)) found.push('#');
  if (/(^|\u0001)\s*\|/.test(text)) found.push('|');
  if (/(^|\u0001)\s*>\s/.test(text)) found.push('>');
  if (/```/.test(text)) found.push('```');
  if (/~~/.test(text)) found.push('~~');
  if (/(^|\u0001)\s*[-*+]\s+\S/.test(text)) found.push('- 列表记号');
  return [...new Set(found)];
}
const cases = [
  ['标题+粗体', '## 通灵宝玉\n\n铭文是**莫失莫忘，仙寿恒昌**。', h => h.includes('<strong>') && h.includes('md-h')],
  ['无序列表', '- 甲\n- 乙', h => (h.match(/<li>/g)||[]).length === 2],
  ['有序列表', '1. 甲\n2. 乙', h => h.startsWith('<ol')],
  ['表格', '| 项 | 值 |\n|---|---|\n| 玉 | 莫失莫忘 |', h => h.includes('<table') && h.includes('<td>玉</td>')],
  ['行内代码', '用 `hdfs dfs -ls` 查看。', h => h.includes('<code>')],
  ['代码块', '```sql\nSELECT 1;\n```', h => h.includes('<pre') && h.includes('lang-sql')],
  ['引用', '> 大旨谈情。', h => h.includes('<blockquote')],
  ['分割线', '上\n\n---\n\n下', h => h.includes('<hr')],
  ['粗体包编号不是列表', '**1. 袭人**（最确凿）', h => h.includes('<strong>1. 袭人</strong>') && !h.includes('<li>')],
  ['真实LLM输出', '## 丫鬟\n\n**先说明**：只有**袭人**。\n\n### 一、依据\n\n**1. 袭人**\n\n- 第3回："亦是贾母之婢"[1]\n\n---\n\n> 提示：第20回。', h => h.includes('<strong>') && h.includes('<li>') && h.includes('<blockquote') && h.includes('<hr')],
  ['斜体删除线', '*斜体* 和 ~~删除~~', h => h.includes('<em>') && h.includes('<del>')],
  ['嵌套列表', '- 一级\n  - 二级\n- 一级B', h => (h.match(/<li>/g)||[]).length === 3],
  ['链接', '见 [红楼梦](https://example.com) 。', h => h.includes('href="https://example.com"')],
  ['XSS 防护', '危险 <script>alert(1)</script> 文本', h => !h.includes('<script')],
  ['防 javascript: 链接', '[点我](javascript:alert(1))', h => !h.includes('javascript:')],
];
let pass = 0;
for (const [name, src, struct] of cases) {
  const html = renderMarkdown(src);
  const left = marksLeft(html);
  const structOk = struct(html);
  const ok = left.length === 0 && structOk;
  if (ok) pass++;
  const why = [];
  if (left.length) why.push('残留:' + left.join(','));
  if (!structOk) why.push('结构不符');
  console.log(`${ok ? '✅' : '❌'} ${name}${ok ? '' : '  ' + why.join(' ')}`);
}
console.log(`\n通过 ${pass}/${cases.length}`);
