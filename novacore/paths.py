# -*- coding: utf-8 -*-
"""部署路径常量与访问日志。
本项目直接分发源码（不编译），部署根目录即本文件的上上级目录。"""
import os
import atexit
import queue
import sys
import threading
import time
import traceback


def app_dir():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


APP_DIR = HERE = app_dir()
CONFIG_PATH = os.path.join(HERE, "config.json")
LOADER_PATH = os.path.join(HERE, "loader.js")
BRIDGE_PATH = os.path.join(HERE, "bridge.js")
BOOT_STUB_PATH = os.path.join(HERE, "boot-stub.js")
BOOT_NAME = "__boot__.js"            # no-cache：最新 loader+配置，随时可更新
BOOT_SNAPSHOT = "__boot__.snapshot.js"  # 一年长缓存：断劫持离线兜底
ADMIN_DIR = os.path.join(HERE, "admin")
LOG_DIR = os.path.join(HERE, "logs")
ACCESS_LOG = os.path.join(LOG_DIR, "access.log")
_log_lock = threading.Lock()
_access_queue = queue.Queue()
_access_thread = None
_access_start_lock = threading.Lock()
_run_logger = None


class _AsyncLog(object):
    """Non-blocking, single-writer UTF-8 log for one process run."""

    def __init__(self, prefix, mirror_stdout=True):
        os.makedirs(LOG_DIR, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.path = os.path.join(LOG_DIR, "%s-%s-%s.log" %
                                 (prefix, stamp, os.getpid()))
        self._original_out = sys.stdout
        self._original_err = sys.stderr
        self._mirror = bool(mirror_stdout)
        self._queue = queue.Queue()
        self._closed = False
        self._thread = threading.Thread(target=self._writer,
                                        name="nova-log-writer", daemon=True)
        self._thread.start()

    def write(self, value):
        text = str(value or "")
        if not text:
            return 0
        if self._mirror:
            try:
                self._original_out.write(text)
                self._original_out.flush()
            except Exception:
                pass
        if not self._closed:
            self._queue.put(text)
        return len(text)

    def flush(self):
        if self._mirror:
            try:
                self._original_out.flush()
            except Exception:
                pass

    def isatty(self):
        return False

    @property
    def encoding(self):
        return "utf-8"

    def _writer(self):
        with open(self.path, "a", encoding="utf-8", buffering=1) as stream:
            pending = []
            while True:
                try:
                    item = self._queue.get(timeout=0.25)
                except queue.Empty:
                    item = None
                if item is _LOG_STOP:
                    if pending:
                        stream.write("".join(pending))
                    stream.flush()
                    return
                if item:
                    pending.append(item)
                if pending and (item is None or len(pending) >= 64):
                    stream.write("".join(pending))
                    stream.flush()
                    pending = []

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._queue.put(_LOG_STOP)
        self._thread.join(timeout=5)


_LOG_STOP = object()


def init_run_logging(prefix="server", mirror_stdout=True):
    """Install process-wide stdout/stderr capture and return this run's path."""
    global _run_logger
    if _run_logger is not None:
        return _run_logger.path
    logger = _AsyncLog(prefix, mirror_stdout=mirror_stdout)
    _run_logger = logger
    sys.stdout = logger
    sys.stderr = logger

    old_hook = sys.excepthook

    def exception_hook(exc_type, exc, tb):
        traceback.print_exception(exc_type, exc, tb)
        logger.flush()

    sys.excepthook = exception_hook
    if hasattr(threading, "excepthook"):
        def thread_exception_hook(args):
            print("Unhandled exception in thread %s:" %
                  getattr(args.thread, "name", "unknown"))
            traceback.print_exception(args.exc_type, args.exc_value,
                                      args.exc_traceback)
        threading.excepthook = thread_exception_hook
    atexit.register(logger.close)
    print("%s INFO  [boot  ] log=%s pid=%s python=%s" %
          (time.strftime("%Y-%m-%d %H:%M:%S"), logger.path,
           os.getpid(), sys.version.split()[0]), flush=True)
    return logger.path


def _access_writer():
    pending = []
    while True:
        try:
            item = _access_queue.get(timeout=0.5)
        except queue.Empty:
            item = None
        if item:
            pending.append(item)
        if not pending or (item is not None and len(pending) < 64):
            continue
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            with _log_lock:
                if os.path.isfile(ACCESS_LOG) and os.path.getsize(ACCESS_LOG) > 2 * 1024 * 1024:
                    try:
                        if os.path.isfile(ACCESS_LOG + ".1"):
                            os.remove(ACCESS_LOG + ".1")
                        os.rename(ACCESS_LOG, ACCESS_LOG + ".1")
                    except OSError:
                        pass
                with open(ACCESS_LOG, "a", encoding="utf-8") as stream:
                    stream.write("\n".join(pending) + "\n")
            pending = []
        except OSError:
            # Keep pending records and retry after the next queue timeout.
            time.sleep(0.1)


def write_access(line):
    """Queue tablet access diagnostics without blocking request threads."""
    global _access_thread
    if _access_thread is None:
        with _access_start_lock:
            if _access_thread is None:
                _access_thread = threading.Thread(target=_access_writer,
                                                  name="nova-access-log",
                                                  daemon=True)
                _access_thread.start()
    try:
        _access_queue.put_nowait(str(line))
    except Exception:
        pass


# ----------------------------- 统一服务端日志 -----------------------------
# logs/server-YYYYMMDD.log（按天 + 1MB 轮转 .1），所有模块共用，不另建日志体系。
# 调试开关（config.json "debug"）关闭时只落 WARN/ERROR；开启后 DEBUG/INFO 全落盘。
_slog_lock = threading.Lock()
_slog_state = {"debug": False}
SLOG_MAX = 1024 * 1024
_LEVELS = {"debug": 10, "info": 20, "warn": 30, "error": 40}


def set_slog_debug(on):
    _slog_state["debug"] = bool(on)


def slog_debug():
    return _slog_state["debug"]


def slog(level, tag, msg=""):
    """level: debug/info/warn/error；tag: 模块短名（如 music/net/proxy）。"""
    lv = str(level or "info").lower()
    if lv not in _LEVELS:
        lv = "info"
    # INFO is operational history and is always retained. DEBUG remains opt-in.
    if lv == "debug" and not _slog_state["debug"]:
        return
    line = "%s %-5s [%-6s] %s" % (
        time.strftime("%Y-%m-%d %H:%M:%S"), lv.upper(), str(tag)[:6], msg)
    try:
        print(line, flush=True)
    except Exception:
        pass
