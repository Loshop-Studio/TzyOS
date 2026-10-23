#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tzy OS 真实浏览器验证 · 可续跑执行器

设计目标
--------
* 断电/关机安全：进度只追加写入 state/progress.jsonl，任何时候被杀都能续跑。
* 幂等：每个测试用例有稳定 id，已 PASS 的用例默认跳过（--force 重跑）。
* 无人值守：可被 cron/systemd-timer 反复调用；同一时间只允许一个实例。
* 留档：每个用例的日志、截图、结论分别落盘，便于次日复查。

用法
----
    python3 verify/runner.py --list          # 列出全部用例与状态
    python3 verify/runner.py                 # 跑到没有可跑的为止（自动续跑）
    python3 verify/runner.py --only T10      # 只跑某几个
    python3 verify/runner.py --force         # 忽略 PASS 重跑
    python3 verify/runner.py --status        # 打印当前进度摘要

调度
----
    systemd timer 每天 09:00 触发一次（见 verify/install_timer.sh）。
    若 21:20 关机时仍在跑，进程被杀，下次启动后从第一个未 PASS 的用例继续。
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import time
import traceback
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STATE = os.path.join(HERE, "state")
LOGS = os.path.join(HERE, "logs")
SHOTS = os.path.join(LOGS, "shots")
PROGRESS = os.path.join(STATE, "progress.jsonl")
SUMMARY = os.path.join(STATE, "summary.json")
LOCKFILE = os.path.join(STATE, "runner.lock")

# 依赖路径（此前会话里装好的，机器重启后仍在 /tmp 会丢，故支持回退系统 node）
NODE_BINS = [
    "/tmp/node-v20.18.1-linux-x64/bin",
    "/tmp/node-v18.20.4-linux-x64/bin",
]
CHROMIUM = "/snap/bin/chromium"
PW_MODULE = "/tmp/node_modules/playwright-core"

for d in (STATE, LOGS, SHOTS):
    os.makedirs(d, exist_ok=True)


# ----------------------------------------------------------------- 工具
def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg):
    line = "[%s] %s" % (now(), msg)
    print(line, flush=True)
    with open(os.path.join(LOGS, "runner.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def load_progress():
    """返回 {case_id: 最后一条记录}。"""
    out = {}
    if not os.path.isfile(PROGRESS):
        return out
    with open(PROGRESS, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue          # 断电导致的半行，直接跳过
            out[rec.get("id", "?")] = rec
    return out


def append_progress(rec):
    """单行追加 + fsync：断电最多丢这一条。"""
    with open(PROGRESS, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def node_env():
    env = dict(os.environ)
    extra = [p for p in NODE_BINS if os.path.isdir(p)]
    if extra:
        env["PATH"] = ":".join(extra) + ":" + env.get("PATH", "")
    return env


def node_ok():
    try:
        subprocess.run(["node", "-v"], env=node_env(), capture_output=True, check=True)
        return True
    except Exception:
        return False


def has_playwright():
    return os.path.isdir(PW_MODULE)


# ----------------------------------------------------------------- 服务管理
SERVER_PORT = 8097
SERVER_SCRIPT = os.path.join(HERE, "server.py")


def server_up():
    import urllib.request
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:%d/__nova__/nova.html" % SERVER_PORT,
            headers={"Host": "web-alicdn.zyai.cc"})
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def server_start(force=False):
    if not force and server_up():
        return True
    if force:
        # 上一个用例可能留了残进程，或服务已被 pkill 打断；先清干净再起，
        # 否则端口被占用会让新进程起不来，用例在无服务下假失败。
        server_stop()
    log("启动后端服务 :%d" % SERVER_PORT)
    subprocess.Popen([sys.executable, SERVER_SCRIPT],
                     stdout=open(os.path.join(LOGS, "server.log"), "a"),
                     stderr=subprocess.STDOUT,
                     start_new_session=True)
    for _ in range(30):
        time.sleep(0.4)
        if server_up():
            return True
    return False


def server_stop():
    subprocess.run(["pkill", "-f", SERVER_SCRIPT], capture_output=True)
    time.sleep(0.5)


# ----------------------------------------------------------------- 用例执行
def run_node_case(case):
    """调用 verify/cases/<id>.js，返回 (ok, detail)。

    约定：脚本以 JSON 输出到 stdout 的最后一行，形如
        __RESULT__ {"ok": true, "detail": "..."}
    """
    js = os.path.join(HERE, "cases", case["id"] + ".js")
    if not os.path.isfile(js):
        return False, "缺少用例脚本 %s" % js
    try:
        p = subprocess.run(["node", js], env=node_env(), cwd=HERE,
                           capture_output=True, text=True, timeout=case.get("timeout", 180))
    except subprocess.TimeoutExpired:
        return False, "超时（%ss）" % case.get("timeout", 180)
    out = (p.stdout or "") + "\n" + (p.stderr or "")
    detail = ""
    for line in reversed(out.splitlines()):
        if line.startswith("__RESULT__"):
            try:
                r = json.loads(line[len("__RESULT__"):].strip())
                return bool(r.get("ok")), r.get("detail", "")
            except ValueError:
                pass
    tail = "\n".join(out.strip().splitlines()[-12:])
    return False, "未取到结果；尾部输出：\n" + tail


def case_needs_server(case_id):
    """所有用例开始前都需要后端在线。

    注意：T05/T06 虽然验证“服务不可用”，但它们自己会在用例内部
    （预热完成之后）去 pkill 服务。若这里把它们排除，用例一开场就是
    “服务不可达”，在线阶段必然失败（T06 曾因此 100% 假失败）。
    """
    return True


def execute(case, force=False):
    cid = case["id"]
    t0 = time.time()
    log("▶ 开始 %s —— %s" % (cid, case["title"]))
    try:
        if case_needs_server(cid):
            # 前一个用例可能故意杀掉服务（T05/T06 会 pkill），这里必须强制
            # 重新确认并必要时重启，否则后续用例会在“服务不可达”下假失败。
            if not server_start(force=True):
                raise RuntimeError("后端服务未能在 :%d 启动" % SERVER_PORT)
        ok, detail = run_node_case(case)
    except Exception as e:
        ok, detail = False, "%s: %s" % (type(e).__name__, e) + "\n" + traceback.format_exc()[-1200:]
    finally:
        if case.get("stop_server_after"):
            server_stop()

    rec = {
        "id": cid,
        "title": case["title"],
        "ok": bool(ok),
        "detail": str(detail)[:4000],
        "ms": int((time.time() - t0) * 1000),
        "at": now(),
        "host": socket.gethostname(),
        "run": os.environ.get("RUN_ID", ""),
    }
    append_progress(rec)
    log(("✔ PASS " if ok else "✘ FAIL ") + cid + ("  " + str(detail)[:300] if not ok else ""))
    return rec


def update_summary(cases):
    prog = load_progress()
    passed = [c for c in cases if prog.get(c["id"], {}).get("ok")]
    failed = [c for c in cases if prog.get(c["id"]) and not prog[c["id"]]["ok"]]
    pending = [c for c in cases if c["id"] not in prog]
    write_json(SUMMARY, {
        "updated": now(),
        "total": len(cases),
        "passed": len(passed),
        "failed": len(failed),
        "pending": len(pending),
        "failed_ids": [c["id"] for c in failed],
        "pending_ids": [c["id"] for c in pending],
    })
    return passed, failed, pending


# ----------------------------------------------------------------- 锁
def acquire_lock():
    """同一时刻只允许一个 runner。返回 fd 或 None。"""
    import fcntl
    fd = open(LOCKFILE, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fd.write(str(os.getpid()))
        fd.flush()
        return fd
    except OSError:
        fd.close()
        return None


# ----------------------------------------------------------------- 用例清单
def load_cases():
    with open(os.path.join(HERE, "cases.json"), "r", encoding="utf-8") as f:
        return json.load(f)


# ----------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--max-seconds", type=int, default=0,
                    help="本次最多运行多少秒后主动收尾（默认不限；关机前保护用）")
    args = ap.parse_args()

    cases = load_cases()

    if args.list:
        prog = load_progress()
        for c in cases:
            r = prog.get(c["id"])
            st = "PASS" if r and r["ok"] else ("FAIL" if r else "TODO")
            print("%-22s %-6s %s" % (c["id"], st, c["title"]))
        return 0

    if args.status:
        p, f, n = update_summary(cases)
        print("总 %d / 通过 %d / 失败 %d / 待跑 %d" %
              (len(cases), len(p), len(f), len(n)))
        if f:
            print("失败：", ", ".join(c["id"] for c in f))
        if n:
            print("待跑：", ", ".join(c["id"] for c in n))
        return 0

    lock = acquire_lock()
    if lock is None:
        log("已有 runner 在跑，退出")
        return 0

    if not node_ok():
        log("错误：找不到 node（期望 %s）" % ", ".join(NODE_BINS))
        return 2
    if not has_playwright():
        log("错误：缺少 playwright-core（期望 %s）" % PW_MODULE)
        return 2
    if not os.path.isfile(CHROMIUM):
        log("错误：找不到 chromium（%s）" % CHROMIUM)
        return 2

    os.environ.setdefault("RUN_ID", datetime.now().strftime("%Y%m%d-%H%M%S"))
    log("==== runner 启动 run=%s 主机=%s ====" % (os.environ["RUN_ID"], socket.gethostname()))

    prog = load_progress()
    todo = []
    for c in cases:
        if args.only and c["id"] not in args.only:
            continue
        if not args.force and prog.get(c["id"], {}).get("ok"):
            continue
        todo.append(c)

    log("本次待执行 %d 个用例（总 %d）" % (len(todo), len(cases)))
    started = time.time()
    for c in todo:
        if args.max_seconds and (time.time() - started) > args.max_seconds:
            log("达到本次时间上限，收尾（下次继续）")
            break
        execute(c, force=args.force)

    p, f, n = update_summary(cases)
    log("==== 收尾：通过 %d / 失败 %d / 待跑 %d ====" % (len(p), len(f), len(n)))
    server_stop()
    lock.close()
    return 0 if not n else (1 if f else 0)


if __name__ == "__main__":
    sys.exit(main())
