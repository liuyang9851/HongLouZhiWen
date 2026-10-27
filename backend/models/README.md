# backend/models —— embedding 模型

> 项目用 **bge-small-zh-v1.5** 做本地向量化（ONNX，512 维，约 90 MB）。

## 为什么不在 Git 仓库里

`model.onnx` 是 90.5 MB 二进制文件，不适合放 git（见 `.gitignore` 的 `backend/models/` 和 `*.onnx`）。

## 怎么获取（三选一）

### 方式 A：从 HuggingFace 下载（推荐）

```bash
# 需要 git-lfs 或直接下 onnx 文件
cd backend/models
git clone https://huggingface.co/BAAI/bge-small-zh-v1.5 bge-small-zh-v1.5-full

# 只取需要的文件
mkdir -p bge-small-zh-v1.5
cp bge-small-zh-v1.5-full/{config.json,tokenizer.json,tokenizer_config.json,special_tokens_map.json,vocab.txt} bge-small-zh-v1.5/
cp bge-small-zh-v1.5-full/onnx/model.onnx bge-small-zh-v1.5/model.onnx
rm -rf bge-small-zh-v1.5-full
```

> 仓库里 `model.onnx` 位于 `onnx/` 子目录，需要拷到根。

### 方式 B：从 ModelScope 下载（国内快）

```bash
pip install modelscope
python -c "
from modelscope import snapshot_download
p = snapshot_download('BAAI/bge-small-zh-v1.5', cache_dir='./_dl')
print(p)
"
# 然后把文件拷到 backend/models/bge-small-zh-v1.5/
```

### 方式 C：从项目 Release 附件下载

到 GitHub Releases 下载 `hlm-model-bge-small-zh-v1.5.zip`，解压到本目录：

```bash
unzip hlm-model-bge-small-zh-v1.5.zip -d backend/models/
```

## 目录结构（必须有这 6 个文件）

```
backend/models/bge-small-zh-v1.5/
├── config.json                ~0.7 KB
├── model.onnx                 90.5 MB   ← 核心
├── special_tokens_map.json    ~0.1 KB
├── tokenizer.json             ~0.4 MB
├── tokenizer_config.json      ~1 KB
└── vocab.txt                  ~0.1 MB
```

## 校验

```bash
ls -lh backend/models/bge-small-zh-v1.5/model.onnx
# 应约 90M

# 更严格：比对 SHA256
# （Release 附件里附带 SHA256SUMS）
sha256sum backend/models/bge-small-zh-v1.5/model.onnx
```

## 为什么要本地 ONNX 而不是调 API

| 方案 | 优点 | 缺点 |
|:---|:---|:---|
| **本地 ONNX（采用）** | 离线可用、零调用成本、无隐私问题 | 首次要下 90 MB |
| 云端 embedding API | 无需下载 | 每次检索都要网络、按量计费 |

项目选择本地方案，因为：
1. 检索是高频操作（每次问答都要编码 query）
2. 512 维小模型在 CPU 上约 100ms，够快
3. 不受网络波动影响

## 换用其他模型

改 `backend/app.py` 里的 `HLM_MODEL_DIR`，并保证：
1. 目录下有 `config.json` / `tokenizer.json` / `vocab.txt` / `model.onnx`
2. `HLM_EMB_DIM` 与新模型维度一致
3. **必须重建全部向量**（`pipeline/s7_2_index_versions.py`），
   因为旧向量是用旧模型算的，维度或语义空间不同

参考：`bge-base-zh-v1.5`（768 维，效果更好但慢 3 倍）、
`bge-large-zh-v1.5`（1024 维，CPU 上明显吃力）。
