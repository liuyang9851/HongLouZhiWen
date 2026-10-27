# -*- coding: utf-8 -*-
"""
S39 · 数据源抽象层（解决 Hadoop 组件版本不一致问题）
=====================================================

问题背景
--------
S38 做了「本地模式」（读 JSONL 文件）和「HBase 模式」。
但实际约束是：

  > 同学们都有 Hadoop 或 Docker 环境，但 **Hadoop 各组件的版本可能不一致**。

这带来一个具体风险：**HBase 版本差异是致命的**。

| HBase 版本 | Thrift1 (9090) | happybase 1.3 能否连 |
|:---|:---|:---:|
| 1.x | ✅ 支持 | ✅ |
| 2.x | ✅ 支持（需手动启动） | ✅ |
| **3.x** | ❌ **已移除** | ❌ **完全不可用** |

而 `happybase` 只实现了 Thrift1 协议 —— 它**无法连接 HBase 3.x**（除非另装 Thrift2 网关）。
此外 HBase 2.x 内部 minor 版本差异也可能导致 `row_prefix` 等行为不同。

关键洞察
--------
**HDFS 比 HBase 稳定得多**：
  · HDFS 文件格式（JSONL / TSV）不随版本变化
  · **WebHDFS 是 HTTP REST API**，从 Hadoop 1.x 到 3.x 语义一致
  · 只需 `hdfs dfs -ls` / `-cat` 这类基础命令

所以我们把数据源抽象成三层，**自动降级**：

```
① HDFS（推荐）      走 WebHDFS HTTP，版本无关
      ↓ 失败
② 本地文件          读 deploy/data/*.jsonl
      ↓ 失败
③ HBase            只有版本匹配时才可用（旧路径，保留兼容）
```

设计
----
环境变量 HLM_DATA_SOURCE：
  hdfs   → 强制 HDFS
  local  → 强制本地文件
  hbase  → 强制 HBase
  auto   → 依次探测（默认）

HDFS 相关：
  HLM_WEBHDFS_URL=http://master:9870/webhdfs/v1
  HLM_HDFS_BASE=/hlm
"""
import os
import json
import math
import struct
import base64
import threading
from typing import Dict, List, Optional, Iterator

# ============================================================
# 数据源探测
# ============================================================
DATA_SOURCE_CFG = (os.getenv("HLM_DATA_SOURCE") or "auto").lower()
DATA_DIR = os.getenv("HLM_DATA_DIR", "/app/data")

WEBHDFS_URL = os.getenv("HLM_WEBHDFS_URL", "").rstrip("/")
HDFS_BASE = os.getenv("HLM_HDFS_BASE", "/hlm").rstrip("/")

# HDFS 上各文件的相对路径（与 pipeline 产出目录一致）
HDFS_PATHS = {
    "chunks_3ver.jsonl":       f"{HDFS_BASE}/warehouse/chunks3ver/chunks_3ver.jsonl",
    "pizhu_3src.jsonl":        f"{HDFS_BASE}/warehouse/pizhu3src/pizhu_3src.jsonl",
    "vectors_3ver.jsonl":      f"{HDFS_BASE}/derived/vectors3ver/model=bge-small-zh-v1.5/vectors_3ver.jsonl",
    "pizhu_3src_vectors.jsonl": f"{HDFS_BASE}/derived/vectors3ver/model=bge-small-zh-v1.5/pizhu_3src_vectors.jsonl",
}

_resolved_source = None      # 探测结果缓存
_probe_log = []


def _log(m):
    print(m, flush=True)


def resolve_source() -> str:
    """
    确定实际使用的数据源，返回 'hdfs' | 'local' | 'hbase' | 'none'。

    探测顺序：hdfs → local → hbase
    每层用「能否读到第一个文件的前 200 字节」作为可用性判据。
    """
    global _resolved_source, _probe_log
    if _resolved_source is not None:
        return _resolved_source

    cfg = DATA_SOURCE_CFG

    # S41：强制模式下也要**验证可用性**，不可用就返回 none（而不是盲信配置）
    #
    # 踩坑场景：Docker 重启后 hlm-api 先于 hlm-hdfs 起，
    #   强制 hdfs 时若不验证，会一直认为 hdfs 可用，
    #   load_all() 每次返回空 → chunk=-1 永久不恢复。
    if cfg in ("hdfs", "local", "hbase"):
        if cfg == "hdfs" and WEBHDFS_URL:
            try:
                import urllib.request
                url = f"{WEBHDFS_URL}{HDFS_PATHS['chunks_3ver.jsonl']}?op=OPEN&offset=0&length=100"
                with urllib.request.urlopen(url, timeout=5) as r:
                    if r.read(100):
                        _resolved_source = "hdfs"
                        _log(f"  数据源: HDFS（环境变量强制，已连接 {WEBHDFS_URL}）")
                        return "hdfs"
                _log("  ⚠ 强制 HDFS 但读取为空")
            except Exception as e:
                _log(f"  ⚠ 强制 HDFS 但连不上: {type(e).__name__}，将重试")
                # 不缓存失败，让下次调用重新探测
                return "none"
        elif cfg == "local":
            p0 = os.path.join(DATA_DIR, "chunks_3ver.jsonl")
            if os.path.exists(p0):
                _resolved_source = "local"
                _log(f"  数据源: 本地文件（环境变量强制，{DATA_DIR}）")
                return "local"
            _log(f"  ⚠ 强制本地但找不到 {p0}")
            return "none"
        else:
            _resolved_source = cfg
            _log(f"  数据源: {cfg}（环境变量强制）")
            return cfg

    # ---- auto：依次探测 ----
    _probe_log = []

    # ① HDFS（WebHDFS）
    if WEBHDFS_URL:
        try:
            url = f"{WEBHDFS_URL}{HDFS_PATHS['chunks_3ver.jsonl']}?op=OPEN&offset=0&length=200"
            import urllib.request
            with urllib.request.urlopen(url, timeout=8) as r:
                head = r.read(200)
            if head:
                _resolved_source = "hdfs"
                _log(f"  数据源: HDFS（WebHDFS {WEBHDFS_URL}）")
                return "hdfs"
        except Exception as e:
            _probe_log.append(f"HDFS: {type(e).__name__}")
    else:
        _probe_log.append("HDFS: HLM_WEBHDFS_URL 未配置")

    # ② 本地文件
    p = os.path.join(DATA_DIR, "chunks_3ver.jsonl")
    if os.path.exists(p):
        _resolved_source = "local"
        _log(f"  数据源: 本地文件（{DATA_DIR}）")
        return "local"
    _probe_log.append(f"local: {p} 不存在")

    # ③ HBase（最后兜底，版本风险最高）
    if os.getenv("HLM_HBASE_HOST"):
        _resolved_source = "hbase"
        _log("  数据源: HBase（版本兼容性未验证）")
        return "hbase"
    _probe_log.append("hbase: HLM_HBASE_HOST 未配置")

    # S41：**失败不缓存**，让下次请求重新探测。
    #   否则 Docker 重启后 hdfs 晚于 api 就绪，会永久卡在 none。
    _probe_log.append("→ 本次探测无可用数据源，下次请求会重试")
    _log(f"  ⚠ 无可用的数据源：{'; '.join(_probe_log)}")
    return "none"


# ============================================================
# 统一的文件读取接口（核心抽象）
# ============================================================
def open_stream(name: str) -> Iterator[bytes]:
    """
    按名字打开数据文件，产出**字节行**。

    三种来源统一接口：
      · HDFS  → WebHDFS OPEN（流式，chunked）
      · 本地  → 普通文件读
      · 都不通 → 抛异常

    为什么产出字节而不是 str：
      JSONL 是 UTF-8，但 WebHDFS 返回的流可能被分块切断在多字节字符中间。
      按**字节**流式交给 TextIOWrapper 处理，解码器会自己处理跨块边界。
    """
    src = resolve_source()

    if src == "hdfs":
        import urllib.request
        hp = HDFS_PATHS.get(name)
        if not hp:
            raise FileNotFoundError(f"HDFS 无此文件映射: {name}")
        url = f"{WEBHDFS_URL}{hp}?op=OPEN"
        # note: WebHDFS 对大于 4MB 的文件会 307 重定向到 datanode，urlopen 自动跟随
        req = urllib.request.Request(url, headers={"Accept": "application/octet-stream"})
        with urllib.request.urlopen(req, timeout=60) as r:
            while True:
                buf = r.read(65536)
                if not buf:
                    break
                yield buf
        return

    if src == "local":
        p = os.path.join(DATA_DIR, name)
        if not os.path.exists(p):
            raise FileNotFoundError(p)
        with open(p, "rb") as f:
            while True:
                buf = f.read(65536)
                if not buf:
                    break
                yield buf
        return

    raise FileNotFoundError(f"数据源不可用（{src}）")


def read_jsonl(name: str):
    """
    逐条产出 JSON 对象。

    实现说明（踩坑记录）：
      最初用 io.TextIOWrapper 包装字节流，但 TextIOWrapper 会去访问
      .closed / .writable / .seekable / .flush 等一堆文件对象属性，
      自己实现 _ByteIter 很容易漏（实测报 '_ByteIter' object has no
      attribute 'closed'）。

      改为**手动按 \n 切分字节流**，只在整行凑齐后才 decode ——
      这样天然处理「分块边界落在多字节字符中间」的情况，
      也不依赖任何文件对象协议。
    """
    buf = b""
    for chunk in open_stream(name):
        buf += chunk
        while True:
            pos = buf.find(b"\n")
            if pos < 0:
                break
            line = buf[:pos]
            buf = buf[pos + 1:]
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
    # 最后一行可能没有换行符
    rest = buf.strip()
    if rest:
        try:
            yield json.loads(rest.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass


# ============================================================
# HTTP 直读（给「配置探测」这类小请求用）
# ============================================================
def fetch_json(url: str, timeout: float = 10.0):
    import urllib.request
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def describe() -> Dict:
    """给 /health 用的数据源描述"""
    src = resolve_source()
    return {
        "source": src,
        "config": DATA_SOURCE_CFG,
        "webhdfs": WEBHDFS_URL or None,
        "hdfs_base": HDFS_BASE if WEBHDFS_URL else None,
        "local_dir": DATA_DIR if src == "local" else None,
        "probe_log": _probe_log,
    }
