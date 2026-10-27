import { renderSequenceDiagram, renderBuildSequenceDiagram,
         renderQuestionDrivenDiagram, renderDeploymentDiagram,
         renderEngineBenchmark }
  from '../web/public/js/diagrams_simple.js';
import fs from 'node:fs';

const items = [
  ['_seq_diagram.html',   '① 查询顺序图',   renderSequenceDiagram()],
  ['_bseq_diagram.html',  '② 建库顺序图',   renderBuildSequenceDiagram()],
  ['_qd_diagram.html',    '③ 问题驱动图',   renderQuestionDrivenDiagram()],
  ['_dep_diagram.html',   '④ 部署图',       renderDeploymentDiagram()],
  ['_bench_diagram.html', '⑤ 引擎对比图',   renderEngineBenchmark()],
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
  console.log(`${file}: ${svg.length} B | svg ${open}/${close} ${open === close ? 'OK' : 'MISMATCH'}` + (bad.length ? ` ⚠ ${bad}` : ''));
}
