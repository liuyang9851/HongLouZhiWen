import { renderMarkdown } from './public/js/md.js';
const src = '### 一、有依据的\n\n**1. 袭人**（最确凿）\n\n- 第3回："原来这袭人亦是贾母之婢"[1]';
const html = renderMarkdown(src);
console.log('HTML:', html);
console.log('\n可见文本:');
console.log(html.replace(/<[^>]+>/g, '|'));
