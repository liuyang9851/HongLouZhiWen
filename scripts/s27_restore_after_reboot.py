# -*- coding: utf-8 -*-
"""
S27 · 一键恢复脚本（重启电脑后运行）
====================================

背景
----
用户反馈：「忘记固化镜像就关闭电脑了，现在项目的情况如何？」

实测结论：**数据全部安全，只是有 3 件事需要手动做**：

  1. Hadoop 三容器（master/slave1/slave2）需要 `docker compose up -d`
  2. **HBase Thrift 9090 不会自动启动**（容器重启后必做）
  3. 前端 Node 服务需要启动，且 **3000 端口可能被 Hyper-V 保留**（需换端口）

本脚本把这三件事自动化。

用法
----
  # 完整恢复
  python scripts/s27_restore_after_reboot.py

  # 只检查不修复
  python scripts/s27_restore_after_reboot.py --check

  # 指定前端端口（默认自动找可用端口）
  python scripts/s27_restore_after_reboot.py --port 5173
"""
import os
import re
import sys
import json
import time
import socket
import subprocess
import argparse

sys.stdout.reconfigure(encoding="utf-8")

ROOT = r"D:\学校事务\大数据原理与应用\红楼智问"
WEB_DIR = os.path.join(ROOT, "web")
COMPOSE_DIR = r"D:\Programming\hadoop\cluster"
COMPOSE_FILE = os.path.join(COMPOSE_DIR, "compose.yaml")

# 候选端口（避开常见 Hyper-V 保留段 2003-3790）
PORT_CANDIDATES = [5173, 4173, 5050, 7070, 8090, 9091, 12345]


def log(msg=""):
    print(msg, flush=True)


def run(cmd, timeout=180, cwd=None):
    """执行命令，返回 (ok, stdout, stderr)"""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, cwd=cwd, shell=isinstance(cmd, str))
        return r.returncode == 0, (r.stdout or ""), (r.stderr or "")
    except subprocess.TimeoutExpired:
        return False, "", f"超时（>{timeout}s）"
    except Exception as e:
        return False, "", str(e)


def port_listening(port, host="127.0.0.1", timeout=1):
    """检测端口是否有服务在监听"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def find_free_port():
    """找一个不在 Hyper-V 保留段且未被占用的端口"""
    for p in PORT_CANDIDATES:
        if port_listening(p):
            continue
        # 尝试绑定，确认可用
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", p))
            s.close()
            return p
        except OSError:
            continue
    return None


# ============================================================
# 步骤
# ============================================================
def step_containers(check_only=False):
    """1. 启动 Hadoop 三容器"""
    log("\n[1/5] Hadoop 三容器")
    ok, out, _ = run(["docker", "ps", "-a", "--format", "{{.Names}}\t{{.Status}}"])
    lines = [l for l in out.splitlines() if l.strip()]
    container_status = {}
    for l in lines:
        parts = l.split("\t")
        if len(parts) == 2:
            container_status[parts[0]] = parts[1]

    need_start = []
    for name in ("master", "slave1", "slave2"):
        st = container_status.get(name, "不存在")
        if "Up" in st:
            log(f"  ✅ {name}: {st}")
        else:
            log(f"  ⚠️  {name}: {st}")
            need_start.append(name)

    if not need_start:
        return True
    if check_only:
        log(f"  → 需要启动 {len(need_start)} 个容器")
        return False

    log("  → 执行 docker compose up -d")
    ok, out, err = run(["docker", "compose", "-f", COMPOSE_FILE, "up", "-d"],
                       timeout=300, cwd=COMPOSE_DIR)
    if not ok:
        log(f"  ❌ 启动失败: {err[:300]}")
        return False

    # 等待健康
    log("  → 等待容器就绪（最多 120s）")
    for i in range(24):
        time.sleep(5)
        ok2, out2, _ = run(["docker", "ps", "--format", "{{.Names}}\t{{.Status}}"])
        st = {}
        for l in out2.splitlines():
            parts = l.split("\t")
            if len(parts) == 2:
                st[parts[0]] = parts[1]
        healthy = sum(1 for n in ("master", "slave1", "slave2")
                      if "Up" in st.get(n, ""))
        if healthy == 3:
            log(f"  ✅ 三容器已启动（{i*5+5}s）")
            # 再等 HDFS 退出安全模式
            for j in range(12):
                ok3, out3, _ = run(["docker", "exec", "master", "bash", "-lc",
                                    "hdfs dfsadmin -safemode get 2>/dev/null"])
                if "OFF" in out3:
                    log(f"  ✅ HDFS 安全模式已关闭")
                    return True
                time.sleep(5)
            return True
    log("  ⚠️  超时，容器可能未完全就绪")
    return False


def step_thrift(check_only=False):
    """2. 启动 HBase Thrift 9090（重启后必做）"""
    log("\n[2/5] HBase Thrift (9090)")
    ok, out, _ = run(["docker", "exec", "master", "bash", "-lc",
                      "bash -c 'cat < /dev/null > /dev/tcp/127.0.0.1/9090' "
                      "2>&1 && echo LISTENING || echo DOWN"], timeout=30)
    if "LISTENING" in out:
        log("  ✅ 已在监听")
        return True
    log("  ⚠️  未启动（容器重启后的已知问题）")
    if check_only:
        return False

    log("  → 启动 hbase thrift")
    run(["docker", "exec", "-d", "master", "bash", "-lc",
         "hbase thrift start > /logs/hbase-thrift.log 2>&1"], timeout=30)

    for i in range(12):
        time.sleep(5)
        ok, out, _ = run(["docker", "exec", "master", "bash", "-lc",
                          "bash -c 'cat < /dev/null > /dev/tcp/127.0.0.1/9090' "
                          "2>&1 && echo LISTENING || echo DOWN"], timeout=30)
        if "LISTENING" in out:
            log(f"  ✅ 已启动（{i*5+5}s）")
            return True
    log("  ❌ 启动失败，请查看 /logs/hbase-thrift.log")
    return False


def step_backend(check_only=False):
    """3. 检查 hlm-api 后端"""
    log("\n[3/5] 后端 hlm-api (:8000)")
    if port_listening(8000):
        try:
            import urllib.request
            with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=15) as r:
                d = json.loads(r.read())
                if d.get("n_chunks", -1) > 0:
                    log(f"  ✅ chunk={d.get('n_chunks')} 向量={d.get('n_vectors')}")
                    return True
                # chunk=-1 说明连不上 HBase（集群刚启动时需要重连）
                log(f"  ⚠️  后端在但连不上 HBase（chunk={d.get('n_chunks')}），需重启")
        except Exception as e:
            log(f"  ⚠️  端口在但健康检查失败: {e}")

        if check_only:
            return False

        # 自动重启后端（它需要重连刚启动的 HBase）
        log("  → 重启 hlm-api 以重连 HBase")
        run(["docker", "restart", "hlm-api"], timeout=120)
        for i in range(18):
            time.sleep(5)
            try:
                import urllib.request
                with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=20) as r:
                    d = json.loads(r.read())
                    if d.get("n_chunks", -1) > 0:
                        log(f"  ✅ 重启后 ok: chunk={d.get('n_chunks')}（{i*5+5}s）")
                        return True
            except Exception:
                pass
        log("  ❌ 重启后仍不健康")
        return False
    else:
        log("  ⚠️  未运行")

    if check_only:
        return False

    # 尝试启动容器
    ok, out, _ = run(["docker", "ps", "-a", "--filter", "name=hlm-api",
                      "--format", "{{.Names}}\t{{.Status}}"])
    if "hlm-api" in out and "Up" not in out:
        log("  → 启动已有容器 hlm-api")
        run(["docker", "start", "hlm-api"], timeout=120)
        for i in range(12):
            time.sleep(5)
            if port_listening(8000):
                log(f"  ✅ 已启动（{i*5+5}s）")
                return True
    log("  ❌ 需要手动重建：见 pipeline/run_backend.sh")
    return False


def step_frontend(check_only=False, force_port=None):
    """4. 启动前端 Node 服务（3000 可能被 Hyper-V 保留）"""
    log("\n[4/5] 前端 Node 服务")
    # 先看已有实例
    ok, out, _ = run(["powershell", "-NoProfile", "-Command",
                      "Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | "
                      "Select-Object -ExpandProperty CommandLine"])
    running_port = None
    for p in PORT_CANDIDATES + [3000]:
        if port_listening(p):
            running_port = p
            break
    if running_port:
        log(f"  ✅ 已在 :{running_port} 运行")
        return True

    if check_only:
        log("  ⚠️  未运行")
        return False

    port = force_port or find_free_port()
    if not port:
        log("  ❌ 找不到可用端口")
        return False

    # 3000 若失败，提示原因
    log(f"  → 在 :{port} 启动")
    env = os.environ.copy()
    env["HLM_WEB_PORT"] = str(port)
    srv_log = os.path.join(WEB_DIR, "_srv.log")
    srv_err = os.path.join(WEB_DIR, "_srv.err")
    try:
        with open(srv_log, "w", encoding="utf-8") as fout, \
             open(srv_err, "w", encoding="utf-8") as ferr:
            subprocess.Popen(
                ["node", "server.js"], cwd=WEB_DIR, env=env,
                stdout=fout, stderr=ferr,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
    except Exception as e:
        log(f"  ❌ 启动失败: {e}")
        return False

    for i in range(12):
        time.sleep(2)
        if port_listening(port):
            log(f"  ✅ 已启动 http://127.0.0.1:{port}")
            return True
    log("  ❌ 启动超时")
    try:
        with open(srv_err, encoding="utf-8") as f:
            log("  " + f.read()[-400:])
    except Exception:
        pass
    return False


def step_verify(check_only=False):
    """5. 端到端验证"""
    log("\n[5/5] 端到端验证")

    # HDFS
    ok, out, _ = run(["docker", "exec", "master", "bash", "-lc",
                      "hdfs dfs -ls -R /hlm/ 2>/dev/null | grep -vc '^d'"], timeout=60)
    n_files = out.strip() if ok else "?"
    log(f"  HDFS: {n_files} 个文件")

    # DataNode
    ok, out, _ = run(["docker", "exec", "master", "bash", "-lc",
                      "hdfs dfsadmin -report 2>/dev/null | grep -c 'Live datanodes'"], timeout=60)
    log(f"  DataNode: {'2 个存活' if ok else '检查失败'}")

    # HBase（经后端）
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:8000/corpus/stats", timeout=20) as r:
            d = json.loads(r.read())
            log(f"  HBase: {d.get('total_chunks')} chunk 索引正常")
    except Exception as e:
        log(f"  HBase: 查询失败 {e}")

    # 索引与展示数字一致性校验（防止旧脚本污染 / 硬编码过期）
    vp = os.path.join(ROOT, "scripts", "s32_verify_diagram_numbers.py")
    if os.path.exists(vp):
        ok, out, _ = run([sys.executable, vp], timeout=420)
        last = [l for l in out.splitlines() if l.strip()][-1:] or [""]
        log(f"  数字校验: {last[0].strip()}")
        if "不一致" in (last[0] if last else ""):
            log("  ⚠ 存在不一致，可运行: python scripts/s32_verify_diagram_numbers.py --fix")

    # 检索
    try:
        import urllib.request, urllib.parse
        q = urllib.parse.quote("宝玉和黛玉是什么关系")
        with urllib.request.urlopen(
                f"http://127.0.0.1:8000/retrieve?q={q}&topk=3", timeout=40) as r:
            d = json.loads(r.read())
            log(f"  检索: 召回 {len(d.get('contexts', []))} 条")
    except Exception as e:
        log(f"  检索: 失败 {e}")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只检查不修复")
    ap.add_argument("--port", type=int, default=None, help="前端端口")
    args = ap.parse_args()

    log("=" * 68)
    log("红楼智问 · 重启恢复" + ("（仅检查）" if args.check else ""))
    log("=" * 68)

    results = {}
    results["containers"] = step_containers(args.check)
    if not results["containers"] and not args.check:
        log("\n❌ 容器未就绪，后续步骤可能失败，继续尝试…")

    results["thrift"] = step_thrift(args.check)
    results["backend"] = step_backend(args.check)
    results["frontend"] = step_frontend(args.check, args.port)
    step_verify(args.check)

    log("\n" + "=" * 68)
    log("结果汇总")
    log("=" * 68)
    for k, v in results.items():
        log(f"  {'✅' if v else '❌'} {k}")

    port = None
    for p in PORT_CANDIDATES + [3000]:
        if port_listening(p):
            port = p
            break
    if port:
        log(f"\n👉 打开前端：http://127.0.0.1:{port}")
    if all(results.values()):
        log("\n✅ 全部就绪")
        return 0
    log("\n⚠️  有项目未就绪，请查看上面的详细输出")
    return 1


if __name__ == "__main__":
    sys.exit(main())
