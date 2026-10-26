# -*- coding: utf-8 -*-
"""
S21-2 · 自定义查询：AST 安全检查器 + 沙箱执行器
===============================================

五层防护
--------
  第 1 层  AST 静态检查（拒绝危险语法，不执行）
  第 2 层  受限 builtins（只给安全函数 + 语料 API）
  第 3 层  数据入口白名单（只能通过 API 读语料，不能 open）
  第 4 层  subprocess 资源限制（prlimit + timeout）
  第 5 层  网络拦截（builtins 里无 socket；子进程内 patch）

关键设计
--------
**AST 检查在父进程做，子进程只执行已通过的代码。**
这样即使有检查漏洞，还有 prlimit 兜底。

威胁模型
--------
假设 AI 可能生成恶意代码（被 prompt 注入、或模型本身跑偏）。
所有防护必须是「默认拒绝」，不能依赖黑名单的完备性。
"""
import os
import re
import ast
import sys
import json
import shutil
import tempfile
import subprocess

# ============================================================
# 第 1 层：AST 静态检查
# ============================================================

# 绝对禁止的名字（出现在 Name / Attribute 里就拒绝）
FORBIDDEN_NAMES = {
    # 文件 / 系统
    "open", "file", "input", "breakpoint",
    "exec", "eval", "compile", "__import__",
    "globals", "locals", "vars", "dir",
    "getattr", "setattr", "delattr", "hasattr",
    # 进程 / 网络
    "system", "popen", "spawn", "fork", "execv", "execve",
    "socket", "connect", "listen", "bind", "send", "recv",
    "urllib", "requests", "httplib", "ftplib", "telnetlib",
    "subprocess", "multiprocessing", "threading", "ctypes",
    # 危险反射
    "__class__", "__bases__", "__subclasses__", "__mro__",
    "__globals__", "__code__", "__closure__", "__builtins__",
    "__reduce__", "__reduce_ex__", "__getattribute__",
    "__dict__", "__init_subclass__", "__loader__", "__spec__",
    # 退出
    "exit", "quit", "sys", "os",
}

# 绝对禁止的模块名（import 时检查）
FORBIDDEN_MODULES = {
    "os", "sys", "subprocess", "socket", "shutil", "pathlib",
    "urllib", "requests", "http", "ftplib", "telnetlib", "smtplib",
    "multiprocessing", "threading", "ctypes", "importlib", "gc",
    "pickle", "marshal", "shelve", "sqlite3", "tempfile",
    "signal", "resource", "platform", "pwd", "grp", "pty",
}

# 允许的 builtins（第 2 层：白名单）
SAFE_BUILTINS = {
    "len", "sum", "max", "min", "sorted", "reversed", "enumerate",
    "range", "zip", "map", "filter", "any", "all", "abs", "round",
    "print", "int", "float", "str", "bool", "list", "dict", "set",
    "tuple", "frozenset", "bytes", "bytearray", "type", "isinstance",
    "ord", "chr", "repr", "format", "hash", "divmod", "pow",
    "True", "False", "None", "Exception", "ValueError", "TypeError",
    "KeyError", "IndexError", "StopIteration", "ZeroDivisionError",
    "AttributeError", "RuntimeError", "NotImplementedError",
}


class SecurityError(Exception):
    """代码未通过安全检查"""


class Checker(ast.NodeVisitor):
    """遍历 AST，发现危险结构就抛 SecurityError"""

    def __init__(self):
        self.violations = []

    def _bad(self, node, reason):
        line = getattr(node, "lineno", 0)
        self.violations.append(f"第{line}行：{reason}")

    # ---- 禁止 import ----
    def visit_Import(self, node):
        names = ", ".join(a.name for a in node.names)
        self._bad(node, f"禁止 import（{names}）")
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        self._bad(node, f"禁止 from ... import（{node.module}）")
        self.generic_visit(node)

    # ---- 检查所有名字 ----
    def visit_Name(self, node):
        if node.id in FORBIDDEN_NAMES:
            self._bad(node, f"禁止使用 `{node.id}`")
        elif node.id.startswith("__") and node.id.endswith("__"):
            self._bad(node, f"禁止使用双下划线名字 `{node.id}`")
        self.generic_visit(node)

    # ---- 检查属性访问（obj.xxx）----
    def visit_Attribute(self, node):
        attr = node.attr
        if attr in FORBIDDEN_NAMES:
            self._bad(node, f"禁止访问属性 `.{attr}`")
        elif attr.startswith("__") and attr.endswith("__"):
            self._bad(node, f"禁止访问双下划线属性 `.{attr}`")
        self.generic_visit(node)

    # ---- 禁止 with（常用于文件操作）----
    def visit_With(self, node):
        self._bad(node, "禁止 with 语句（可能用于文件操作）")
        self.generic_visit(node)

    # ---- 禁止 async / await（可能用于并发攻击）----
    def visit_AsyncFunctionDef(self, node):
        self._bad(node, "禁止 async 函数")
        self.generic_visit(node)

    # ---- 禁止 try 里套 while True（部分绕过尝试）----
    def visit_While(self, node):
        # 允许 while，但要求有比较条件（禁止 while True）
        if isinstance(node.test, ast.Constant) and node.test.value is True:
            self._bad(node, "禁止 `while True`（死循环）")
        self.generic_visit(node)

    # ---- 禁止调用某些内建 ----
    def visit_Call(self, node):
        f = node.func
        name = None
        if isinstance(f, ast.Name):
            name = f.id
        elif isinstance(f, ast.Attribute):
            name = f.attr
        if name in FORBIDDEN_NAMES:
            self._bad(node, f"禁止调用 `{name}()`")
        # 禁止 getattr(obj, "危险名") 形式的动态取属性
        if name in ("getattr", "__getattribute__"):
            self._bad(node, "禁止动态取属性")
        self.generic_visit(node)

    # ---- 拒绝超大常量（防内存炸弹，如 "A"*10**8）----
    def visit_Constant(self, node):
        if isinstance(node.value, (str, bytes)) and len(node.value) > 100000:
            self._bad(node, "字符串常量过长（>100KB）")
        self.generic_visit(node)


def check_code(code: str) -> None:
    """
    静态检查代码。不通过则抛 SecurityError（含全部违规项）。
    通过则返回 None。
    """
    if not code or not code.strip():
        raise SecurityError("代码为空")

    if len(code) > 12000:
        raise SecurityError(f"代码过长（{len(code)} 字符，上限 12000）")

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise SecurityError(f"语法错误：第{e.lineno}行 {e.msg}")

    ck = Checker()
    ck.visit(tree)
    if ck.violations:
        raise SecurityError("；".join(ck.violations[:6]))

    # 必须给 result 赋值
    has_result = any(
        isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "result" for t in n.targets)
        for n in ast.walk(tree)
    )
    if not has_result:
        raise SecurityError("代码必须给变量 `result` 赋值")


# ============================================================
# 第 2/3/5 层：沙箱内运行器（注入受限 builtins）
# ============================================================

RUNNER_TEMPLATE = '''# -*- coding: utf-8 -*-
"""沙箱执行器（自动生成，不手工编辑）"""
import sys, json, io

# ---------- 第 5 层：网络拦截（只放行集群内网）----------
# 背景：HBase 通过 Thrift(9090) 访问，必须允许 socket。
#       但必须挡住对外网（防数据外传）。
# 策略：只允许连接集群内网的私网地址，其余一律拒绝。
try:
    import socket as _sk

    _ALLOW_NETS = (
        "172.16.", "172.17.", "172.18.", "172.19.", "172.20.",  # docker 网络段
        "127.", "10.", "192.168.",
    )
    _ALLOW_HOSTS = {{"master", "slave1", "slave2", "localhost", "127.0.0.1"}}

    def _host_allowed(host):
        if host is None:
            return True
        h = str(host)
        if h in _ALLOW_HOSTS:
            return True
        return any(h.startswith(p) for p in _ALLOW_NETS)

    _orig_socket = _sk.socket
    _orig_create = _sk.create_connection

    class _GuardedSocket(_orig_socket):
        def connect(self, addr):
            host = addr[0] if isinstance(addr, (tuple, list)) and addr else addr
            if not _host_allowed(host):
                raise OSError(f"沙箱内禁止访问外网地址：{{host}}")
            return super().connect(addr)

        def connect_ex(self, addr):
            host = addr[0] if isinstance(addr, (tuple, list)) and addr else addr
            if not _host_allowed(host):
                raise OSError(f"沙箱内禁止访问外网地址：{{host}}")
            return super().connect_ex(addr)

    def _guarded_create(addr, *a, **k):
        host = addr[0] if isinstance(addr, (tuple, list)) and addr else addr
        if not _host_allowed(host):
            raise OSError(f"沙箱内禁止访问外网地址：{{host}}")
        return _orig_create(addr, *a, **k)

    _sk.socket = _GuardedSocket
    _sk.create_connection = _guarded_create
except Exception:
    pass

# ---------- 语料 API ----------
sys.path.insert(0, "/app")
from query_api import SANDBOX_API

# ---------- Hadoop API（HDFS + HBase 只读封装）----------
# 这是「能力开放」不是「放开 import」：
#   沙箱代码仍不能 import，但可以调用下面这些**受限只读**的函数
try:
    from hadoop_api import SANDBOX_HADOOP_API
except Exception as _e:
    SANDBOX_HADOOP_API = {{}}
    print(f"[warn] Hadoop API 未加载: {{_e}}", file=sys.stderr)

# ---------- 第 3 层：数据入口 ----------
_NS = dict(SANDBOX_API)
_NS.update(SANDBOX_HADOOP_API)

# ---------- 第 2 层：受限 builtins ----------
SAFE = set({safe_builtins!r})
import builtins as _bi
_safe_builtins = {{k: getattr(_bi, k) for k in SAFE if hasattr(_bi, k)}}
# 保留 isinstance/type 等需要的少数字段
for _k in ("__build_class__",):
    if hasattr(_bi, _k):
        _safe_builtins[_k] = getattr(_bi, _k)

_NS["__builtins__"] = _safe_builtins
_NS["result"] = None

# ---------- 执行 ----------
_code = {code!r}
_buf = io.StringIO()
_old_stdout = sys.stdout
sys.stdout = _buf
_err = None
try:
    exec(compile(_code, "<ai_query>", "exec"), _NS)
except Exception as e:
    _err = f"{{type(e).__name__}}: {{e}}"
finally:
    sys.stdout = _old_stdout

# ---------- 输出 ----------
_out = {{
    "ok": _err is None,
    "error": _err,
    "result": _NS.get("result"),
    "stdout": _buf.getvalue()[:4000],
}}
try:
    print(json.dumps(_out, ensure_ascii=False, default=str))
except Exception as e:
    print(json.dumps({{"ok": False, "error": f"结果序列化失败: {{e}}", "result": None, "stdout": ""}}))
'''


# ============================================================
# 第 4 层：资源限制执行
# ============================================================

# 资源限制参数
LIMITS = {
    "cpu": 20,                 # CPU 秒（Hadoop 查询需要更多时间）
    "as": 512 * 1024 * 1024,   # 地址空间（纯计算模式用）
    "nproc": 16,               # 进程数（hdfs 会 fork JVM）
    "fsize": 1024 * 1024,      # 单文件 1MB
    "nofile": 64,              # 文件描述符（纯计算模式）
    "wall": 60,                # 墙钟秒（HDFS/HBase RPC 较慢）
}

# 检测代码是否要调 Hadoop
# 为什么需要特殊处理：
#   实测 JVM 在地址空间限制下无法启动：
#     --as=512MB → "Could not reserve enough space for 262144KB object heap"
#     --as=2GB   → "Could not allocate metaspace: 1073741824 bytes"
#   只有不限制 --as 才能跑 hdfs/hbase 客户端。
#   因此 Hadoop 模式改用：CPU 限制 + 墙钟超时 + 进程数限制，
#   放弃 --as 硬限制（保留 --nproc/--fsize/--nofile 防滥用）。
HADOOP_FUNCS = (
    "hdfs_ls", "hdfs_cat", "hdfs_count_lines", "hdfs_du",
    "hbase_count", "hbase_get", "hbase_scan", "hadoop_status",
)


def _uses_hadoop(code: str) -> bool:
    """判断代码是否调用了 Hadoop API"""
    return any(fn in code for fn in HADOOP_FUNCS)


def run_sandboxed(code: str, timeout=None):
    """
    在受限子进程里执行代码。

    返回 dict:
      ok      : bool
      result  : 任意（代码赋给 result 的值）
      stdout  : 代码的 print 输出
      error   : 错误信息（ok=False 时）
      elapsed : 耗时 ms
      blocked : 被安全检查拦下则为 True
      violations : 安全违规详情
    """
    import time
    t0 = time.time()

    # ---- 第 1 层：静态检查 ----
    try:
        check_code(code)
    except SecurityError as e:
        return {
            "ok": False, "blocked": True,
            "error": f"安全检查未通过：{e}",
            "violations": str(e),
            "result": None, "stdout": "",
            "elapsed": round((time.time() - t0) * 1000, 1),
        }

    # ---- 生成运行器 ----
    runner = RUNNER_TEMPLATE.format(
        safe_builtins=sorted(SAFE_BUILTINS),
        code=code,
    )

    tmpdir = tempfile.mkdtemp(prefix="hlm_query_")
    path = os.path.join(tmpdir, "run.py")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(runner)

        # ---- 第 4 层：prlimit + timeout ----
        # Hadoop 模式不给 --as（JVM 起不来）；纯计算模式给 --as 防内存炸弹
        wall = timeout or LIMITS["wall"]
        hadoop_mode = _uses_hadoop(code)
        cmd = ["prlimit", f"--cpu={LIMITS['cpu']}"]
        if not hadoop_mode:
            cmd.append(f"--as={LIMITS['as']}")
        # 实测：JVM 需要 ≥512 个文件描述符才能启动
        #   nofile=64  → rc=1（Java 类加载失败）
        #   nofile=256 → rc=1
        #   nofile=512 → 成功
        nofile = 1024 if hadoop_mode else LIMITS["nofile"]
        cmd += [
            f"--nproc={LIMITS['nproc']}",
            f"--fsize={LIMITS['fsize']}",
            f"--nofile={nofile}",
            "--",
            "timeout", str(wall),
            sys.executable, path,
        ]
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=wall + 5, cwd=tmpdir,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin",
                 "PYTHONPATH": "/app",
                 "HOME": tmpdir,
                 "LANG": "C.UTF-8"},
        )

        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()

        # prlimit CPU 超限 → 无输出 / 非 0 退出
        if not out:
            reason = err or f"执行异常（exit={proc.returncode}）"
            if proc.returncode == 124 or "timed out" in err.lower():
                reason = f"执行超时（>{wall} 秒）"
            elif proc.returncode in (-9, 137) or "Killed" in err:
                reason = "超出资源限制被终止（CPU 或内存）"
            return {
                "ok": False, "blocked": False, "error": reason,
                "result": None, "stdout": err[:2000],
                "elapsed": round((time.time() - t0) * 1000, 1),
            }

        try:
            payload = json.loads(out.splitlines()[-1])
        except Exception:
            return {
                "ok": False, "blocked": False,
                "error": f"沙箱输出无法解析：{out[:300]}",
                "result": None, "stdout": out[:2000],
                "elapsed": round((time.time() - t0) * 1000, 1),
            }

        payload["blocked"] = False
        payload["elapsed"] = round((time.time() - t0) * 1000, 1)
        payload["hadoop_mode"] = hadoop_mode
        return payload

    except subprocess.TimeoutExpired:
        return {
            "ok": False, "blocked": False,
            "error": f"执行超时（>{timeout or LIMITS['wall']} 秒）",
            "result": None, "stdout": "",
            "elapsed": round((time.time() - t0) * 1000, 1),
        }
    except Exception as e:
        return {
            "ok": False, "blocked": False,
            "error": f"沙箱内部错误：{type(e).__name__}: {e}",
            "result": None, "stdout": "",
            "elapsed": round((time.time() - t0) * 1000, 1),
        }
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ============================================================
# 自检
# ============================================================
if __name__ == "__main__":
    print("=" * 70)
    print("安全检查器 + 沙箱自检")
    print("=" * 70)

    CASES = [
        # (说明, 代码, 期望通过?)
        ("正常统计", 'text = load_all_text("fuqinju")\nresult = text.count("之")', True),
        ("正常列表", 'result = sorted(VERSIONS)', True),
        ("读某回", 'result = load_chapter("fuqinju", 3)[:20]', True),
        ("import os", 'import os\nresult = 1', False),
        ("from x import y", 'from os import system\nresult = 1', False),
        ("open 读文件", 'result = open("/etc/passwd").read()', False),
        ("open 写文件", 'open("/tmp/x","w").write("hi")\nresult = 1', False),
        ("exec", 'exec("import os")\nresult = 1', False),
        ("eval", 'result = eval("1+1")', False),
        ("__import__", 'result = __import__("os")', False),
        ("__class__", 'result = ().__class__.__bases__', False),
        ("__subclasses__", 'result = ().__class__.__bases__[0].__subclasses__()', False),
        ("getattr 绕过", 'result = getattr(__builtins__, "open")', False),
        ("while True", 'while True:\n    pass\nresult = 1', False),
        ("socket", 'import socket\nresult = 1', False),
        ("subprocess", 'import subprocess\nresult = 1', False),
        ("无 result", 'x = 1 + 1', False),
        ("空代码", '', False),
        ("死循环(有超时)", 'result = sum(range(10**9))', True),   # 语法通过，但会超时
        ("大字符串", 'result = "A" * 200000', False),
    ]

    for desc, code, should_pass in CASES:
        if should_pass:
            try:
                check_code(code)
                print(f"  ✅ {desc:20s} 通过检查")
            except SecurityError as e:
                print(f"  ❌ {desc:20s} 应该通过但被拒: {e}")
        else:
            try:
                check_code(code)
                print(f"  ❌ {desc:20s} 应该拒绝但通过了！")
            except SecurityError as e:
                print(f"  ✅ {desc:20s} 已拒绝: {str(e)[:50]}")
