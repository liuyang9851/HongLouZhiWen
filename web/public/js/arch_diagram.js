/**
 * S24 · 欢迎页架构图（SVG）
 *
 * 设计原则
 * --------
 *  · 重点突出 Hadoop 生态（占图面 55%），RAG 应用层在下，表现"数据往上流"
 *  · 所有组件、版本号、表名、路径均来自**实际集群实测**，不虚构
 *  · 纯手写 SVG，零依赖，可缩放、可打印
 *
 * 实测数据来源
 * ------------
 *   Hadoop 3.3.6 / HBase 2.5.10-hadoop3 / Spark 3.5.8
 *   dfs.replication = 2, dfs.blocksize = 134217728 (128MB)
 *   3 节点：master / slave1 / slave2
 *   HBase 表：hlm_chunks / hlm_vectors / hlm_terms / hlm_wordfreq / hlm_stats
 *   HDFS 目录：/hlm/{derived,logs,manifest,mrin,mrout,warehouse}
 */
export function renderArchDiagram() {
  // 颜色
  const C = {
    hadoop: '#2f6f4e',      // Hadoop 绿
    hadoopBg: '#f0f7f2',
    hbase: '#b03a2e',       // HBase 红
    hbaseBg: '#fdf3f1',
    spark: '#c1622a',       // Spark 橙
    analyze: '#7a6a52',     // 分析层
    analyzeBg: '#f7f4ee',
    app: '#2f4858',         // 应用层
    appBg: '#eef3f6',
    front: '#5b6b7a',
    line: '#b8c4cc',
    text: '#2c3e50',
    muted: '#7a8a96',
  };

  // 小工具：矩形+文字
  const box = (x, y, w, h, fill, stroke, rx = 6) =>
    `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${rx}" fill="${fill}" stroke="${stroke}" stroke-width="1.3"/>`;

  const txt = (x, y, s, size = 13.5, fill = C.text, weight = 400, anchor = 'middle') =>
    `<text x="${x}" y="${y}" font-size="${size}" fill="${fill}" font-weight="${weight}" text-anchor="${anchor}" font-family="system-ui,-apple-system,'Segoe UI',sans-serif">${s}</text>`;

  const mono = (x, y, s, size = 12, fill = C.muted, anchor = 'middle') =>
    `<text x="${x}" y="${y}" font-size="${size}" fill="${fill}" text-anchor="${anchor}" font-family="Consolas,Monaco,monospace">${s}</text>`;

  // 箭头
  const arrow = (x1, y1, x2, y2, color = C.line, dashed = false) =>
    `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${color}" stroke-width="1.5"
      ${dashed ? 'stroke-dasharray="4 3"' : ''} marker-end="url(#arr)"/>`;

  return `
<svg id="archSvg" viewBox="0 0 900 720" xmlns="http://www.w3.org/2000/svg"
     style="width:100%;height:auto;max-width:1400px;display:block;margin:0 auto">
  <defs>
    <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5"
            markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="${C.line}"/>
    </marker>
    <marker id="arrG" viewBox="0 0 10 10" refX="9" refY="5"
            markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="${C.hadoop}"/>
    </marker>
  </defs>

  <!-- ============ 0. 集群物理层（3 节点） ============ -->
  <rect x="18" y="14" width="864" height="82" rx="9" fill="#f7f9fa" stroke="#dde4e8" stroke-width="1.2"/>
  ${txt(34, 34, '集群 · Docker Compose（hadoop-lab：2026.10）', 14, C.muted, 700, 'start')}

  <!-- master -->
  ${box(44, 44, 240, 40, '#fff', C.hadoop, 5)}
  ${txt(164, 60, 'master', 14, C.hadoop, 700)}
  ${mono(164, 74, 'NameNode·ResourceMgr·HMaster·ZK', 9.5)}

  <!-- slave1 -->
  ${box(330, 44, 240, 40, '#fff', C.hadoop, 5)}
  ${txt(450, 60, 'slave1', 14, C.hadoop, 700)}
  ${mono(450, 74, 'DataNode·NodeManager·RegionServer', 11)}

  <!-- slave2 -->
  ${box(616, 44, 240, 40, '#fff', C.hadoop, 5)}
  ${txt(736, 60, 'slave2', 14, C.hadoop, 700)}
  ${mono(736, 74, 'DataNode·NodeManager·RegionServer', 11)}

  <!-- ============ 1. HDFS 存储层 ============ -->
  <rect x="18" y="108" width="864" height="120" rx="9" fill="${C.hadoopBg}" stroke="${C.hadoop}" stroke-width="1.6" stroke-opacity=".45"/>
  ${txt(34, 128, '① HDFS 3.3.6 · 分布式存储', 15.5, C.hadoop, 700, 'start')}
  ${mono(880, 128, 'dfs.replication=2  blocksize=128MB', 12, C.muted, 'end')}

  ${box(44, 138, 200, 76, '#fff', C.hadoop, 6)}
  ${txt(144, 156, '/hlm/ 原始语料', 13.5, C.text, 700)}
  ${mono(144, 172, '三版本正文 167.8 万字', 12)}
  ${mono(144, 186, '三源脂批 12,457 条', 12)}
  ${mono(144, 200, '/hlm/manifest', 12, C.muted)}

  ${arrow(248, 176, 300, 176, C.hadoop)}

  ${box(304, 138, 200, 76, '#fff', C.hadoop, 6)}
  ${txt(404, 156, 'MapReduce 作业', 13.5, C.text, 700)}
  ${mono(404, 172, '/hlm/mrin → /hlm/mrout', 12)}
  ${mono(404, 186, 'wordfreq 133,673 行', 12)}
  ${mono(404, 200, 'personfreq 41 · cooccur 10,742', 12)}

  ${arrow(508, 176, 560, 176, C.hadoop)}

  ${box(564, 138, 300, 76, '#fff', C.hadoop, 6)}
  ${txt(714, 156, '/hlm/derived + /hlm/warehouse', 13.5, C.text, 700)}
  ${mono(714, 172, '派生索引 88.7 MB · Hive 数仓 27.7 MB', 12)}
  ${mono(714, 186, 'chunks · vectors · terms', 12)}
  ${mono(714, 200, '/hlm/logs 问答记录', 12, C.muted)}

  <!-- ============ 2. HBase 服务层 ============ -->
  <rect x="18" y="242" width="864" height="126" rx="9" fill="${C.hbaseBg}" stroke="${C.hbase}" stroke-width="1.6" stroke-opacity=".45"/>
  ${txt(34, 262, '② HBase 2.5.10 · 在线检索存储', 15.5, C.hbase, 700, 'start')}
  ${mono(880, 262, 'Thrift :9090 · 5 张表', 12, C.muted, 'end')}

  <!-- 5 张表 -->
  ${box(44, 272, 160, 78, '#fff', C.hbase, 6)}
  ${txt(124, 290, 'hlm_chunks', 13, C.text, 700)}
  ${mono(124, 306, '11,780 行', 12.5, C.hbase)}
  ${mono(124, 320, 'd:text  m:hui/version', 11)}
  ${mono(124, 334, 'chunk 正文 + 元数据', 11, C.muted)}

  ${box(216, 272, 160, 78, '#fff', C.hbase, 6)}
  ${txt(296, 290, 'hlm_vectors', 13, C.text, 700)}
  ${mono(296, 306, '11,780 行', 12.5, C.hbase)}
  ${mono(296, 320, 'v:emb  2048 B', 11)}
  ${mono(296, 334, '512 维 float32', 11, C.muted)}

  ${box(388, 272, 160, 78, '#fff', C.hbase, 6)}
  ${txt(468, 290, 'hlm_terms', 13, C.text, 700)}
  ${mono(468, 306, '451,817 行', 12.5, C.hbase)}
  ${mono(468, 320, 'p:postings  p:df', 11)}
  ${mono(468, 334, 'bigram 倒排索引', 11, C.muted)}

  ${box(560, 272, 148, 78, '#fff', C.hbase, 6)}
  ${txt(634, 290, 'hlm_wordfreq', 12.5, C.text, 700)}
  ${mono(634, 306, '133,673 行', 12.5, C.hbase)}
  ${mono(634, 320, '词频统计', 11)}
  ${mono(634, 334, '来自 MapReduce', 11, C.muted)}

  ${box(720, 272, 144, 78, '#fff', C.hbase, 6)}
  ${txt(792, 290, 'hlm_stats', 12.5, C.text, 700)}
  ${mono(792, 306, '709 行', 12.5, C.hbase)}
  ${mono(792, 320, 'cooccur/personfreq', 11)}
  ${mono(792, 334, '聚合索引', 11, C.muted)}

  <!-- ============ 3. 计算分析层 ============ -->
  <rect x="18" y="382" width="864" height="74" rx="9" fill="${C.analyzeBg}" stroke="${C.analyze}" stroke-width="1.4" stroke-opacity=".40"/>
  ${txt(34, 402, '③ 计算与分析层', 15, C.analyze, 700, 'start')}

  ${box(44, 412, 152, 32, '#fff', C.analyze, 5)}
  ${txt(120, 432, 'MapReduce 批统计', 13, C.text, 600)}
  ${box(210, 412, 132, 32, '#fff', C.analyze, 5)}
  ${txt(276, 432, 'Spark 3.5.8', 13, C.text, 600)}
  ${box(356, 412, 132, 32, '#fff', C.analyze, 5)}
  ${txt(422, 432, 'Hive 3.1.3', 13, C.text, 600)}
  ${box(502, 412, 116, 32, '#fff', C.analyze, 5)}
  ${txt(560, 432, 'ZooKeeper', 13, C.text, 600)}
  ${box(632, 412, 110, 32, '#fff', C.analyze, 5)}
  ${txt(687, 432, 'Flink 1.20', 13, C.text, 600)}
  ${box(756, 412, 108, 32, '#fff', C.analyze, 5)}
  ${txt(810, 432, 'Storm 1.2', 13, C.text, 600)}

  <!-- ============ 4. RAG 应用层 ============ -->
  <rect x="18" y="470" width="864" height="150" rx="9" fill="${C.appBg}" stroke="${C.app}" stroke-width="1.6" stroke-opacity=".40"/>
  ${txt(34, 490, '④ RAG 检索与生成', 15.5, C.app, 700, 'start')}
  ${mono(880, 490, 'FastAPI · Python 3.10', 12, C.muted, 'end')}

  <!-- 三路检索 -->
  ${box(44, 500, 178, 106, '#fff', C.app, 6)}
  ${txt(133, 518, 'L1 关键词召回', 13, C.text, 700)}
  ${mono(133, 534, 'hlm_terms bigram', 11.5)}
  ${mono(133, 548, '命中 30 chunk / 813 bigram', 11, C.muted)}
  ${mono(133, 562, '≈ 1.2 s', 11.5, C.app)}

  ${box(232, 500, 178, 106, '#fff', C.app, 6)}
  ${txt(321, 518, 'L2 向量召回', 13, C.text, 700)}
  ${mono(321, 534, 'bge-small-zh-v1.5 ONNX', 11)}
  ${mono(321, 548, '512 维 · 候选 30', 11, C.muted)}
  ${mono(321, 562, '≈ 115 ms', 11.5, C.app)}

  ${box(420, 500, 178, 106, '#fff', C.app, 6)}
  ${txt(509, 518, 'L3 图谱召回', 13, C.text, 700)}
  ${mono(509, 534, 'cooccur + 人物关系', 11.5)}
  ${mono(509, 548, '350 节点 / 675 边', 11, C.muted)}
  ${mono(509, 562, '关系路径', 11.5, C.app)}

  <!-- 融合 -->
  ${box(608, 500, 256, 106, '#fff', C.app, 6)}
  ${txt(736, 518, 'RRF 融合（k=60）+ 同回次去重', 13, C.text, 700)}
  ${mono(736, 536, '每回正文 ≤2 · 每回批语 ≤1', 11)}
  ${mono(736, 552, 'Top-5 上下文 ≈ 3,270 字', 11, C.muted)}
  ${mono(736, 568, '引用：版本·回次·段号·字符区间', 11)}
  ${mono(736, 586, '→ 可点击跳转原文', 11.5, C.app, 700)}

  <!-- ============ 5. LLM + 前端 ============ -->
  <rect x="18" y="632" width="864" height="74" rx="9" fill="#f7f9fa" stroke="#dde4e8" stroke-width="1.2"/>
  ${txt(34, 652, '⑤ 生成与展示', 15, C.front, 700, 'start')}

  ${box(44, 662, 300, 32, '#fff', C.front, 5)}
  ${txt(194, 682, 'DeepSeek（默认）/ GLM-4 / 多协议 · 失败降级', 12.5, C.text, 600)}

  ${arrow(348, 678, 396, 678, C.line)}

  ${box(400, 662, 464, 32, '#fff', C.front, 5)}
  ${txt(632, 682, '浏览器前端 · 原生 ES Module（零框架）· 8 个视图', 12.5, C.text, 600)}

  <!-- ============ 数据流标注（左侧竖箭头） ============ -->
  <line x1="8" y1="172" x2="8" y2="678" stroke="${C.line}" stroke-width="2"
        stroke-dasharray="5 4" marker-end="url(#arr)" opacity=".7"/>
  <text x="14" y="420" font-size="9.5" fill="${C.muted}" text-anchor="middle"
        transform="rotate(-90 14 420)" font-family="system-ui,sans-serif">数据流：存储 → 索引 → 检索 → 生成</text>
</svg>`;
}
// S31-ARCH-FONT-SCALED
