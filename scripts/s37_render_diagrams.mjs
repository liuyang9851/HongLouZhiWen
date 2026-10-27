import { renderSequenceDiagram, renderBuildSequenceDiagram, renderDeploymentDiagram }
  from '../web/public/js/diagrams_simple.js';
import fs from 'node:fs';

const items = [
  ['_seq_diagram.html', '① 顺序图（查询链路）', renderSequenceDiagram()],
  ['_bseq_diagram.html', '② 顺序图（建库链路）', renderBuildSequenceDiagram()],
  ['_dep_diagram.html', '③ 部署图', renderDeploymentDiagram()],
];

const wrap = (title, svg) => `<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><title>${title}</title>
<style>body{margin:0;padding:18px;background:#faf7f2;font-family:system-ui,sans-serif}
h2{font-size:18px;color:#b03a2e;margin:0 0 10px}</style></head>
<body><h2>${title}</h2>${svg}</body></html>`;

for (const [file, title, svg] of items) {
  fs.writeFileSync('../web/public/' + file, wrap(title, svg), 'utf8');
  const open = (svg.match(/<svg/g) || []).length;
  const close = (svg.match(/<\/svg>/g) || []).length;
  const bad = ['undefined', 'NaN'].filter((x) => svg.includes(x));
  console.log(`${file}: ${svg.length} B, <svg>=${open} </svg>=${close} ${open === close ? 'OK' : 'MISMATCH'}` + (bad.length ? ` ⚠ 含 ${bad}` : ''));
}
