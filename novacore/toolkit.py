# -*- coding: utf-8 -*-
"""升级（入口 HTML 引用版本号刷新）与备份打包，HTTP 接口与 CLI 共用。"""
import io
import os
import re
import shutil
import time
import zipfile

from novacore.configutil import novaos_dir
from novacore.paths import HERE, CONFIG_PATH, LOADER_PATH, BOOT_STUB_PATH, BRIDGE_PATH


def bump_entry_versions():
    """把 novaos2 下所有入口 HTML 中本地引用的 ?v=N 重写为文件 mtime。
    递归扫描全部 .html（含 apps/、lib/ 等子目录内的页面），引用按该 HTML
    所在目录解析；跳过 dist/archive/logs/node_modules 等非运行目录。
    返回 (changed:list[str], errors:list[str])。"""
    root = novaos_dir()
    changed = []
    errors = []
    # Match every quoted local src/href, whether it already has a numeric,
    # hand-written, or no cache version.  Older code only matched ?v=digits,
    # leaving core files such as os.js?v=release-name and unversioned local
    # libraries permanently pinned in the tablet's one-year HTTP cache.
    pat = re.compile(r'((?:src|href)=")((?!https?:|data:|blob:|#|//)[^"?#]+)(?:\?[^"#]*)?(#[^"]*)?(")', re.I)
    skip_dirs = {"dist", "archive", "logs", "node_modules", "__pycache__", ".git"}
    # 第三方静态站（Vite 构建产物、文件名自带 hash）：不参与 ?v= 刷新，
    # 否则每次更新都会改写上游文件，产生无意义 diff 且会污染落库副本。
    skip_rel_prefixes = ("lib/phigros/",)

    def bump_html(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            html = f.read()
        base_dir = os.path.dirname(html_path)

        def repl(m):
            rel = m.group(2)
            if rel.startswith("/"):                 # 根相对 → 以 novaos2 根为基准
                fp = os.path.join(root, rel.lstrip("/").replace("/", os.sep))
            else:                                   # 普通相对 → 以该 HTML 所在目录为基准
                fp = os.path.normpath(os.path.join(base_dir, rel.replace("/", os.sep)))
            if not os.path.isfile(fp):
                return m.group(0)
            v = str(int(os.path.getmtime(fp)))
            old = m.group(0)
            new = m.group(1) + rel + "?v=" + v + (m.group(3) or "") + m.group(4)
            if new != old and rel not in changed:
                changed.append(rel)
            return new

        new_html = pat.sub(repl, html)
        if new_html != html:
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(new_html)

    try:
        root_norm = os.path.abspath(root)
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in skip_dirs]
            for name in filenames:
                if not name.endswith(".html"):
                    continue
                fp_html = os.path.join(dirpath, name)
                rel_html = os.path.relpath(fp_html, root_norm).replace(os.sep, "/")
                if any(rel_html.startswith(p) for p in skip_rel_prefixes):
                    continue
                try:
                    bump_html(fp_html)
                except Exception as e:
                    errors.append("%s: %s" % (name, e))
    except Exception as e:
        errors.append(str(e))
    return changed, errors


def build_backup_zip():
    """打包 novaos2 全量 + 引导/配置/服务端源码与脚本，返回 zip 字节。
    不含 logs、dist、archive（体积大或与运行无关）。"""
    mem = io.BytesIO()
    root = novaos_dir()

    def add_dir(src_dir, arc_prefix, skip_dirs=()):
        if not os.path.isdir(src_dir):
            return
        for dirpath, dirnames, filenames in os.walk(src_dir):
            dirnames[:] = [d for d in dirnames if d not in skip_dirs]
            for name in filenames:
                if name.endswith((".pyc", ".pyo")):
                    continue
                fp = os.path.join(dirpath, name)
                arc = os.path.join(arc_prefix, os.path.relpath(fp, src_dir))
                z.write(fp, arc.replace(os.sep, "/"))

    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as z:
        add_dir(root, os.path.basename(root))
        add_dir(os.path.join(HERE, "novacore"), "novacore",
                skip_dirs=("__pycache__",))
        add_dir(os.path.join(HERE, "novelsrc"), "novelsrc",
                skip_dirs=("__pycache__",))
        add_dir(os.path.join(HERE, "tools"), "tools",
                skip_dirs=("__pycache__",))
        for name in ("config.json", "loader.js", "boot-stub.js", "bridge.js",
                     "novaosd.py", "server.py", "novel_server.py",
                     "requirements.txt", "README.md", "BUILD.md",
                     "install.bat", "start.bat", "stop-hotspot.bat",
                     "build_all.bat", "build_runtime.ps1",
                     "enable-hotspot.ps1", "disable-hotspot.ps1",
                     "set-hotspot-credentials.ps1"):
            fp = os.path.join(HERE, name)
            if os.path.isfile(fp):
                z.write(fp, name)
    return mem.getvalue()


def backup_filename():
    return "novaos-backup-%s.zip" % time.strftime("%Y%m%d-%H%M%S")


# ----------------------------- 平板文件备份到电脑 -----------------------------
TABLET_BACKUP_DIR = os.path.join(HERE, "backups", "tablet")
TABLET_KEEP = 10                    # 最多保留 10 次平板备份
PUSH_MAX_BYTES = 1024 * 1024 * 1024  # 单文件 1GB 上限

_backup_sessions = {}
_backup_lock = __import__("threading").Lock()


def _safe_rel(vpath):
    """把平板虚拟路径规整为相对路径，禁止任何盘符/绝对路径/越界写法。"""
    p = str(vpath or "").replace("\\", "/").lstrip("/")
    parts = []
    for seg in p.split("/"):
        if seg in ("", "."):
            continue
        if seg == ".." or ":" in seg or ord(seg[0]) == 0:
            raise ValueError("非法路径")
        parts.append(seg)
    if not parts:
        raise ValueError("空路径")
    return os.path.join(*parts)


def tablet_backup_begin(manifest):
    """开一次平板备份会话。manifest: {device, scope, files:[{path,name,size,mime,mtime}]}
    scope="music" 为音乐歌单单独备份，会话目录/zip 名带 music- 前缀。"""
    # 白名单前缀，防止 manifest 注入非法文件名
    prefix = "music-" if str((manifest or {}).get("scope", "")) == "music" else ""
    sid = prefix + time.strftime("%Y%m%d-%H%M%S")
    # 同一秒重复开始：追加序号。除活动会话外，还要避开上一次备份已落盘的
    # 同名目录/zip（会话 commit 后即从内存移除），否则两次备份会互相覆盖。
    with _backup_lock:
        base = sid
        n = 1
        while sid in _backup_sessions:
            n += 1
            sid = "%s-%d" % (base, n)
        d = os.path.join(TABLET_BACKUP_DIR, sid)
        n = 1
        while os.path.exists(d) or os.path.exists(d + ".zip"):
            n += 1
            sid = "%s-%d" % (base, n)
            d = os.path.join(TABLET_BACKUP_DIR, sid)
        os.makedirs(os.path.join(d, "vfs"), exist_ok=True)
        _backup_sessions[sid] = {
            "dir": d, "manifest": manifest or {}, "got": 0,
            "bytes": 0, "started": time.time()}
    try:
        from novacore.paths import slog
        slog("info", "backup", "开始备份会话 %s scope=%s 文件数=%d" % (
            sid, (manifest or {}).get("scope", "all"),
            len((manifest or {}).get("files") or [])))
    except Exception:
        pass
    return sid


def tablet_backup_push(sid, vpath, stream):
    """接收一个文件，流式写入会话目录（避免大文件打爆内存）。"""
    with _backup_lock:
        sess = _backup_sessions.get(sid)
    if not sess:
        raise KeyError("备份会话不存在或已过期，请重新开始")
    rel = _safe_rel(vpath)
    dst = os.path.join(sess["dir"], "vfs", rel)
    real_dir = os.path.dirname(dst)
    os.makedirs(real_dir, exist_ok=True)
    # 最终落盘路径必须仍在会话目录内
    if os.path.commonpath([os.path.abspath(dst),
                           os.path.abspath(sess["dir"])]) != os.path.abspath(sess["dir"]):
        raise ValueError("非法路径")
    total = 0
    with open(dst, "wb") as f:
        while True:
            chunk = stream.read(1024 * 256)
            if not chunk:
                break
            total += len(chunk)
            if total > PUSH_MAX_BYTES:
                f.close()
                try:
                    os.remove(dst)
                except OSError:
                    pass
                raise ValueError("单文件超过 1GB 上限")
            f.write(chunk)
    with _backup_lock:
        sess["got"] += 1
        sess["bytes"] += total
    return {"rel": rel.replace(os.sep, "/"), "size": total}


def tablet_backup_commit(sid):
    """收尾：写清单与 localStorage 快照，打包 zip，清理旧会话。"""
    import json
    with _backup_lock:
        sess = _backup_sessions.pop(sid, None)
    if not sess:
        raise KeyError("备份会话不存在")
    d = sess["dir"]
    with open(os.path.join(d, "_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(sess.get("manifest", {}), f, ensure_ascii=False, indent=1)
    # 统计实际落盘文件
    file_count = 0
    for _, _, files in os.walk(os.path.join(d, "vfs")):
        file_count += len(files)
    zip_path = d + ".zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for dp, _, fns in os.walk(d):
            for fn in fns:
                fp = os.path.join(dp, fn)
                z.write(fp, os.path.join(sid, os.path.relpath(fp, d)))
    # 清理最旧的若干次
    try:
        entries = []
        for name in os.listdir(TABLET_BACKUP_DIR):
            p = os.path.join(TABLET_BACKUP_DIR, name)
            if os.path.isdir(p):
                entries.append((os.path.getmtime(p), p))
        entries.sort()
        for _, p in entries[:-TABLET_KEEP] if len(entries) > TABLET_KEEP else []:
            import shutil
            shutil.rmtree(p, ignore_errors=True)
    except Exception:
        pass
    return {"name": sid + ".zip", "files": file_count,
            "bytes": sess.get("bytes", 0), "zip": os.path.getsize(zip_path)}


def tablet_backup_catalog():
    """列出电脑 backups/tablet 下可供平板还原的 zip。"""
    os.makedirs(TABLET_BACKUP_DIR, exist_ok=True)
    items = []
    for name in os.listdir(TABLET_BACKUP_DIR):
        if not name.lower().endswith(".zip"):
            continue
        fp = os.path.join(TABLET_BACKUP_DIR, name)
        if not os.path.isfile(fp):
            continue
        try:
            st = os.stat(fp)
            with zipfile.ZipFile(fp, "r") as z:
                files = sum(1 for i in z.infolist()
                            if not i.is_dir() and "/vfs/" in i.filename.replace("\\", "/"))
            items.append({"name": name, "size": st.st_size,
                          "mtime": int(st.st_mtime), "files": files})
        except (OSError, zipfile.BadZipFile):
            continue
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


def tablet_backup_package(name):
    """返回目录内指定备份 zip 的绝对路径；非法名称或非 zip 返回 None。"""
    safe = os.path.basename(str(name or "").strip())
    if safe != str(name or "").strip() or not safe.lower().endswith(".zip"):
        return None
    fp = os.path.abspath(os.path.join(TABLET_BACKUP_DIR, safe))
    root = os.path.abspath(TABLET_BACKUP_DIR)
    if os.path.commonpath([fp, root]) != root or not os.path.isfile(fp):
        return None
    return fp


# ----------------------------- 平板调试日志 -----------------------------
def append_tablet_log(remote, lines, ua=""):
    """调试模式下平板回传的 console/异常日志，按天落 logs/tablet-YYYYMMDD.log。"""
    log_dir = os.path.join(HERE, "logs")
    os.makedirs(log_dir, exist_ok=True)
    fp = os.path.join(log_dir, "tablet-%s.log" % time.strftime("%Y%m%d"))
    ts = time.strftime("%H:%M:%S")
    rows = []
    for ln in (lines or [])[:500]:
        s = str(ln).replace("\r", " ").replace("\n", " ")[:4000]
        rows.append("[%s] %s UA=%s | %s\n" % (ts, remote or "-", (ua or "")[:80], s))
    with open(fp, "a", encoding="utf-8") as f:
        f.write("".join(rows))
    try:
        from novacore.paths import slog
        if lines:
            slog("debug", "tablog", "%s 回传 %d 条 -> %s | 首条: %s" % (
                remote or "-", len(lines), os.path.basename(fp),
                str(lines[0]).replace("\n", " ")[:160]))
    except Exception:
        pass
    return len(rows)


# ----------------------------- Tzy 应用仓库（.tzyp） -----------------------------
# 电脑端把制作好的应用包放进 apps_repo/ 即对外成仓，平板在「应用商店」安装/更新。
APP_REPO_DIR = os.path.join(HERE, "apps_repo")
APP_PKG_MAX = 64 * 1024 * 1024
_APP_ID_RE = __import__("re").compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

# 应用安装时把包内文件原样解到电脑磁盘，平板通过 HTTP 直接加载这些真实文件，
# 不再每次运行时从 IndexedDB 读出字节再生成 blob URL（无法被 iframe 直接复用、
# 也不参与 HTTP 缓存）。目录结构与包内一致。
APP_DATA_DIR = os.path.join(HERE, "apps_data")


def app_data_root(app_id):
    """返回应用磁盘目录（已做 id 校验）；非法返回 None。"""
    app_id = str(app_id or "").strip()
    if not _APP_ID_RE.match(app_id):
        return None
    root = os.path.abspath(os.path.join(APP_DATA_DIR, app_id))
    base = os.path.abspath(APP_DATA_DIR)
    try:
        if os.path.commonpath([root, base]) != base:
            return None
    except ValueError:
        return None
    return root


def app_data_begin(app_id):
    """清空并重建某应用的磁盘目录（安装/更新前调用）。"""
    root = app_data_root(app_id)
    if not root:
        return {"ok": False, "error": "应用 id 非法"}, 400
    try:
        if os.path.isdir(root):
            shutil.rmtree(root, ignore_errors=True)
        os.makedirs(root, exist_ok=True)
    except OSError as exc:
        return {"ok": False, "error": "创建应用目录失败：%s" % exc}, 500
    return {"ok": True, "id": app_id}, 200


def app_data_put(app_id, rel, stream):
    """写入应用目录下的单个文件；rel 必须是应用内的安全相对路径。"""
    root = app_data_root(app_id)
    if not root:
        return {"ok": False, "error": "应用 id 非法"}, 400
    rel = str(rel or "").replace("\\", "/").strip().lstrip("/")
    if not rel or rel.endswith("/"):
        return {"ok": False, "error": "路径非法"}, 400
    parts = [p for p in rel.split("/") if p]
    if any(p in ("", ".", "..") or ":" in p for p in parts):
        return {"ok": False, "error": "路径非法"}, 400
    dest = os.path.abspath(os.path.join(root, *parts))
    try:
        if os.path.commonpath([dest, root]) != root:
            return {"ok": False, "error": "路径越界"}, 403
    except ValueError:
        return {"ok": False, "error": "路径越界"}, 403
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    size = 0
    tmp = dest + ".part"
    try:
        with open(tmp, "wb") as f:
            while True:
                chunk = stream.read(64 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > APP_PKG_MAX:
                    raise ValueError("文件过大")
                f.write(chunk)
        os.replace(tmp, dest)
    except ValueError as exc:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return {"ok": False, "error": str(exc)}, 413
    except OSError as exc:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return {"ok": False, "error": "写入失败：%s" % exc}, 500
    return {"ok": True, "path": rel, "size": size}, 200


def app_data_file(app_id, rel):
    """定位应用目录内的文件；越界或不存在返回 None。"""
    root = app_data_root(app_id)
    if not root:
        return None
    rel = str(rel or "").replace("\\", "/").strip().lstrip("/")
    if not rel:
        return None
    dest = os.path.abspath(os.path.join(root, rel.replace("/", os.sep)))
    try:
        if os.path.commonpath([dest, root]) != root:
            return None
    except ValueError:
        return None
    return dest if os.path.isfile(dest) else None


def app_data_remove(app_id):
    root = app_data_root(app_id)
    if root and os.path.isdir(root):
        shutil.rmtree(root, ignore_errors=True)
    return {"ok": True}, 200


# 内置应用的静态站来源目录（novaos2/lib/<id>/）。首次需要落库时整体复制到
# apps_data/<id>/，之后 iframe 直接加载磁盘上的真实 HTML（不再依赖 VFS/blob）。
LIB_APP_IDS = ("mojuan", "quickdraw", "phigros")


def _lib_dir(app_id):
    from novacore.configutil import novaos_dir
    if app_id not in LIB_APP_IDS:
        return None
    path = os.path.join(novaos_dir(), "lib", app_id)
    return path if os.path.isdir(path) else None


def _lib_stamp(app_id):
    """用目录内最新 mtime 作为版本戳：来源更新后会重新落库。"""
    src = _lib_dir(app_id)
    if not src:
        return 0
    latest = 0
    for dirpath, _dirs, files in os.walk(src):
        for name in files:
            try:
                latest = max(latest, int(os.path.getmtime(os.path.join(dirpath, name))))
            except OSError:
                pass
    return latest


def app_data_ensure(app_id):
    """确保内置应用的静态站已落库到 apps_data/<id>/。

    返回 {ok, id, path, stamp, copied}；不动则 copied=False。
    使用 <root>/.nova_stamp 记录来源 mtime，避免每次启动重复复制。
    """
    if app_id not in LIB_APP_IDS:
        return {"ok": False, "error": "不是内置落库应用"}, 400
    src = _lib_dir(app_id)
    if not src:
        return {"ok": False, "error": "来源目录不存在"}, 404
    dest = app_data_root(app_id)
    if not dest:
        return {"ok": False, "error": "应用目录不可用"}, 500
    stamp = _lib_stamp(app_id)
    stamp_file = os.path.join(dest, ".nova_stamp")
    try:
        if os.path.isfile(stamp_file):
            with open(stamp_file, "r", encoding="utf-8") as f:
                if f.read().strip() == str(stamp):
                    return {"ok": True, "id": app_id, "stamp": stamp,
                            "copied": False}, 200
    except OSError:
        pass
    try:
        if os.path.isdir(dest):
            shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(src, dest)
        with open(stamp_file, "w", encoding="utf-8") as f:
            f.write(str(stamp))
    except OSError as exc:
        return {"ok": False, "error": "落库失败：%s" % exc}, 500
    return {"ok": True, "id": app_id, "stamp": stamp, "copied": True}, 200


def _ver_key(v):
    out = []
    for seg in str(v or "0").split("."):
        num = ""
        for ch in seg:
            if ch.isdigit():
                num += ch
            else:
                break
        out.append(int(num) if num else 0)
    return out


def read_app_manifest(fp):
    """读取 .tzyp 内 app.json 并做基础校验，返回清单 dict。非法抛 ValueError。"""
    import json
    try:
        with zipfile.ZipFile(fp) as z:
            for n in z.namelist():
                if n.startswith("/") or n.startswith("\\") or ".." in n.split("/") \
                        or ":" in n:
                    raise ValueError("包内非法路径: %s" % n)
            info = z.getinfo("app.json")
            if info.file_size > 64 * 1024:
                raise ValueError("app.json 过大")
            meta = json.loads(z.read("app.json").decode("utf-8"))
    except ValueError:
        raise
    except Exception as e:
        raise ValueError("无法读取应用包: %s" % e)
    if not isinstance(meta, dict) or not meta.get("id") or not meta.get("name"):
        raise ValueError("清单缺少 id/name")
    if not _APP_ID_RE.match(str(meta["id"])):
        raise ValueError("id 非法")
    return meta


def app_repo_catalog():
    """扫描 apps_repo/*.tzyp，同一 id 取版本号最高者，返回清单列表。"""
    if not os.path.isdir(APP_REPO_DIR):
        return []
    best = {}   # id -> (ver_key, item)
    for name in os.listdir(APP_REPO_DIR):
        if not name.lower().endswith(".tzyp"):
            continue
        fp = os.path.join(APP_REPO_DIR, name)
        if not os.path.isfile(fp) or os.path.getsize(fp) > APP_PKG_MAX:
            continue
        try:
            meta = read_app_manifest(fp)
        except Exception:
            continue
        item = {
            "id": str(meta["id"]),
            "name": str(meta.get("name") or meta["id"]),
            "version": str(meta.get("version") or "1.0.0"),
            "main": str(meta.get("main") or "main.js"),
            "icon": str(meta.get("icon") or "📦"),
            "tone": str(meta.get("tone") or "tone-blue"),
            "maximize": bool(meta.get("maximize")),
            "desktop": meta.get("desktop", True) is not False,
            "preinstall": bool(meta.get("preinstall")),
            "desc": str(meta.get("desc") or ""),
            "author": str(meta.get("author") or ""),
            "file": name,
            "size": os.path.getsize(fp),
        }
        cur = best.get(item["id"])
        if not cur or _ver_key(item["version"]) > _ver_key(cur["version"]):
            best[item["id"]] = item
    return [best[k] for k in sorted(best.keys())]


def app_repo_package(name):
    """安全定位仓库内包文件；越界/不存在返回 None。"""
    name = os.path.basename(str(name or ""))
    if not name.lower().endswith(".tzyp"):
        return None
    fp = os.path.abspath(os.path.join(APP_REPO_DIR, name))
    try:
        if os.path.commonpath([fp, os.path.abspath(APP_REPO_DIR)]) != \
                os.path.abspath(APP_REPO_DIR):
            return None
    except ValueError:
        return None
    return fp if os.path.isfile(fp) else None
