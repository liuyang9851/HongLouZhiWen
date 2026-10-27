import { renderSequenceDiagram, renderDeploymentDiagram } from './web/public/js/diagrams_simple.js';
import fs from 'node:fs';

const seq = renderSequenceDiagram();
const dep = renderDeploymentDiagram();

// 包成独立 HTML（内联 SVG），供浏览器直接打开
const wrap = (title, svg) => `<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><title>${title}</title>
<style>body{margin:0;padding:18px;background:#faf7f2;font-family:system-ui,sans-serif}
h2{font-size:18px;color:#b03a2e;margin:0 0 10px}</style></head>
<body><h2>${title}</h2>${svg}</body></html>`;

fs.writeFileSync('./web/public/_seq_diagram.html', wrap('顺序图', seq), 'utf8');
fs.writeFileSync('./web/public/_dep_diagram.html', wrap('部署图', dep), 'utf8');
console.log('seq svg 长度', seq.length);
console.log('dep svg 长度', dep.length);
// 粗略自检：标签配对
for (const [name, s] of [['seq', seq], ['dep', dep]]) {
  const open = (s.match(/<svg/g) || []).length;
  const close = (s.match(/<\/svg>/g) || []).length;
  console.log(`${name}: <svg>=${open} </svg>=${close} ${open === close ? 'OK' : 'MISMATCH'}`);
  if (s.includes('undefined')) console.log(`${name}: ⚠ 含 undefined`);
  if (s.includes('NaN')) console.log(`${name}: ⚠ 含 NaN`);
}
