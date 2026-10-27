# deploy/data —— 数据快照

> 这个目录放「本地模式 / HDFS 模式」运行所需的数据快照（JSONL 格式）。

## 文件清单

| 文件 | 大小 | 内容 |
|:---|---:|:---|
| `chunks_3ver.jsonl` | 6.03 MB | 正文 chunk 3,295 条（三版本） |
| `pizhu_3src.jsonl` | 4.56 MB | 脂批 chunk 8,662 条（三源） |
| `vectors_3ver.jsonl` | 9.01 MB | 正文向量（512 维，base64） |
| `pizhu_3src_vectors.jsonl` | 23.67 MB | 脂批向量 |
| `chapters_fuqinju.jsonl` | 1.80 MB | 抚琴居本章节 |
| `chapters_chenggao.jsonl` | 2.49 MB | 程高本章节 |
| `chapters_guiyouben.jsonl` | 0.62 MB | 癸酉本章节 |
| **合计** | **48.2 MB** | |

## 为什么不在 Git 仓库里

这 48 MB 数据**不在 git 仓库**（见 `.gitignore` 的 `deploy/data/*.jsonl`），原因：

1. git 不适合存大二进制/大文本（每次改动都会存全量副本）
2. 仓库已有 27 MB，再加 48 MB 会让 clone 变慢

## 怎么获取

**方式 A：下载 Release 附件（推荐）**

到项目的 GitHub Releases 页面，下载 `hlm-data-v1.0.zip`，
解压到本目录：

```bash
cd 项目根目录
unzip hlm-data-v1.0.zip -d deploy/
# 确认
ls deploy/data/    # 应看到 7 个 .jsonl
```

**方式 B：从本机集群导出**

如果你有 Hadoop 集群且已跑过完整 pipeline：

```bash
docker cp master:/workspace/pipeline/out/chunks_3ver.jsonl        deploy/data/
docker cp master:/workspace/pipeline/out/pizhu_3src.jsonl         deploy/data/
docker cp master:/workspace/pipeline/out/vectors_3ver.jsonl       deploy/data/
docker cp master:/workspace/pipeline/out/pizhu_3src_vectors.jsonl deploy/data/
docker cp master:/workspace/pipeline/out/chapters_fuqinju.jsonl   deploy/data/
docker cp master:/workspace/pipeline/out/chapters_chenggao.jsonl  deploy/data/
docker cp master:/workspace/pipeline/out/chapters_guiyouben.jsonl deploy/data/
```

**方式 C：自己跑 pipeline 生成**

见 `pipeline/` 目录和 `docs/03-S2-数据管道.md`。
需要原始语料（`hlm_corpus/`）和 PDF 源文件。

## 数据校验

下载后建议校验行数：

```bash
wc -l deploy/data/*.jsonl
# 期望：
#   3295 chunks_3ver.jsonl
#   8662 pizhu_3src.jsonl
#   3295 vectors_3ver.jsonl
#   8662 pizhu_3src_vectors.jsonl
```

（`chapters_*.jsonl` 的行数是章节数：81 / 120 / 28）

## 数据来源

| 数据 | 来源 | 许可 |
|:---|:---|:---|
| 抚琴居校本正文 | 《抚琴居校本（簡體大字本）》PDF 提取 | 教学研究用 |
| 程高本正文 | 公开电子版（多源校对） | 公版 |
| 癸酉本正文 | 网络流传本（第81–108回） | 争议版本，仅研究用 |
| 脂批（三源） | 抚琴居 PDF + 脂批大全 PDF + 癸酉本内嵌 | 教学研究用 |
| 向量 | bge-small-zh-v1.5 本地 ONNX 生成 | MIT |

**仅用于课程学习与教学研究，不得商用。**
