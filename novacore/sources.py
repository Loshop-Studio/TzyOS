# -*- coding: utf-8 -*-
"""Tzy OS 四源文件 API：电脑本地 / FTP / SMB / 外网代取，与统一分发。"""
import base64
import ftplib
import io
import json
import mimetypes
import os
import re
import subprocess
import threading
import time

import requests
from flask import Response, abort, jsonify, request, send_file

from novacore.configutil import cfg, fs_root
from novacore.netutil import hotspot_ip, answer_ip, local_ip

_FTP_LIMIT = threading.BoundedSemaphore(12)
_SMB_LIMIT = threading.BoundedSemaphore(12)
_SMB_AUTH_LOCK = threading.Lock()

# ----------------------------- Tzy OS 应用 API -----------------------------
# 全部在挂载点下同源提供（前端 fetch 相对路径 api/...），离线必然不可用，
# 前端做优雅降级即可。响应一律 no-store，避免被缓存投毒成"陈旧数据"。

def _api_json(data, status=200):
    resp = Response(json.dumps(data, ensure_ascii=False), status=status)
    resp.headers["Content-Type"] = "application/json; charset=utf-8"
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


def api_preflight():
    """跨域（平板用 http://192.168.137.1 直连，脱离域名劫持后）POST 预检。"""
    resp = Response(status=204)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET,POST,HEAD,OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Max-Age"] = "86400"
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _all_local_ipv4():
    """枚举本机全部 IPv4（含热点、多网卡、虚拟网卡）。
    仅靠 local_ip() 单播出口地址会漏掉平板实际使用的网卡地址，
    导致 IP 直连打到“非白名单”时整个挂载点被当外部流量透传而 502。"""
    ips = set()
    try:
        import socket as _s
        host = _s.gethostname()
        for info in _s.getaddrinfo(host, None, _s.AF_INET):
            ips.add(info[4][0])
    except Exception:
        pass
    # 再补充常见探测路径（不依赖主机名解析是否完整）
    for probe in ("223.5.5.5", "8.8.8.8"):
        try:
            import socket as _s
            sk = _s.socket(_s.AF_INET, _s.SOCK_DGRAM)
            sk.connect((probe, 53))
            ips.add(sk.getsockname()[0])
            sk.close()
        except Exception:
            pass
    return ips


def lan_hosts():
    """所有可直连本机服务的 Host：热点网关 / 本机全部网卡 IP / 应答 IP / 回环。
    断 DNS 劫持后，平板凭这些地址（无需域名解析）仍能访问挂载点与 API。"""
    s = {"127.0.0.1", "localhost"}
    for fn in (hotspot_ip, local_ip, answer_ip):
        try:
            v = fn()
            if v:
                s.add(str(v).lower())
        except Exception:
            pass
    for ip in _all_local_ipv4():
        if ip:
            s.add(str(ip).lower())
    return s


def _api_body():
    return request.get_json(force=True, silent=True) or {}


def _safe_fs_path(rel):
    """把相对路径解析到 fs_root 内，越界抛 ValueError（防目录穿越）。"""
    rel = str(rel or "").replace("\\", "/").strip().lstrip("/")
    # __ALL_DRIVES__ 仅 Windows 有意义；其它平台由 fs_root() 回退到主目录。
    if os.name == "nt" and str(cfg("fs_root", "") or "").strip() == "__ALL_DRIVES__":
        if not rel:
            return None
        m = re.match(r"^([A-Za-z]):(?:/(.*))?$", rel)
        if not m:
            raise ValueError("需要先选择盘符")
        drive = m.group(1).upper() + ":\\"
        if not os.path.isdir(drive):
            raise ValueError("盘符不可用")
        tail = (m.group(2) or "").replace("/", os.sep)
        fp = os.path.abspath(os.path.join(drive, tail))
        bound = os.path.abspath(drive).rstrip(os.sep) + os.sep
        if fp != os.path.abspath(drive) and not fp.startswith(bound):
            raise ValueError("路径越界")
        return fp
    root = os.path.abspath(fs_root())
    fp = os.path.abspath(os.path.join(root, rel.replace("/", os.sep)))
    bound = root.rstrip(os.sep) + os.sep
    # 盘符根（如 D:\\）末尾自带分隔符，需 rstrip 后再拼，否则根下一级全部误判
    bound = root.rstrip(os.sep) + os.sep
    if fp != root and not fp.startswith(bound):
        raise ValueError("路径越界")
    return fp


def _entry_info(name, fp):
    try:
        st = os.stat(fp)
        return {"name": name, "dir": os.path.isdir(fp), "size": st.st_size,
                "mtime": int(st.st_mtime)}
    except OSError:
        return {"name": name, "dir": False, "size": 0, "mtime": 0}


def _sort_entries(items):
    items.sort(key=lambda x: (not x["dir"], x["name"].lower()))
    return items


def api_fs(op, method):
    """电脑本地文件系统（根目录由 config.fs_root 限定）。"""
    b = _api_body() if method == "POST" else {}

    def arg(k, d=""):
        v = b.get(k, None)
        return d if v is None else v

    try:
        if op == "list":
            if (os.name == "nt" and str(cfg("fs_root", "") or "").strip() == "__ALL_DRIVES__"
                    and not str(arg("path") or "").strip()):
                entries = []
                if os.name == "nt":
                    for code in range(ord("A"), ord("Z") + 1):
                        drive = chr(code) + ":\\"
                        if os.path.isdir(drive):
                            entries.append(_entry_info(chr(code) + ":", drive))
                return _api_json({"ok": True, "root": "__ALL_DRIVES__", "path": "", "entries": _sort_entries(entries)})
            fp = _safe_fs_path(arg("path"))
            if not os.path.isdir(fp):
                return _api_json({"ok": False, "error": "不是目录"}, 400)
            items = [_entry_info(n, os.path.join(fp, n)) for n in sorted(os.listdir(fp))]
            if str(cfg("fs_root", "") or "").strip() == "__ALL_DRIVES__":
                drive_root = os.path.splitdrive(fp)[0] + os.sep
                rel_tail = os.path.relpath(fp, drive_root).replace(os.sep, "/")
                rel = os.path.splitdrive(fp)[0] + ("" if rel_tail == "." else "/" + rel_tail)
            else:
                rel = os.path.relpath(fp, fs_root()).replace(os.sep, "/")
            return _api_json({"ok": True, "root": fs_root(),
                              "path": "" if rel == "." else rel,
                              "entries": _sort_entries(items)})
        if op == "get":
            fp = _safe_fs_path(arg("path"))
            if not os.path.isfile(fp):
                return _api_json({"ok": False, "error": "文件不存在"}, 404)
            with open(fp, "rb") as f:
                data = f.read()
            try:
                return _api_json({"ok": True, "text": True, "name": os.path.basename(fp),
                                  "content": data.decode("utf-8")})
            except UnicodeDecodeError:
                return _api_json({"ok": True, "text": False, "name": os.path.basename(fp),
                                  "data": base64.b64encode(data).decode("ascii")})
        if op == "put":
            fp = _safe_fs_path(arg("path"))
            d = os.path.dirname(fp)
            if d and not os.path.isdir(d):
                os.makedirs(d, exist_ok=True)
            if arg("data", None) is not None:
                raw = base64.b64decode(arg("data"))
            else:
                raw = str(arg("content", "")).encode("utf-8")
            with open(fp, "wb") as f:
                f.write(raw)
            return _api_json({"ok": True, "size": len(raw)})
        if op == "del":
            fp = _safe_fs_path(arg("path"))
            if not os.path.exists(fp):
                return _api_json({"ok": False, "error": "文件或目录不存在"}, 404)
            if os.path.isdir(fp):
                if os.listdir(fp):
                    return _api_json({"ok": False, "error": "目录非空"}, 400)
                os.rmdir(fp)
            else:
                os.remove(fp)
            return _api_json({"ok": True})
        if op == "mkdir":
            os.makedirs(_safe_fs_path(arg("path")), exist_ok=True)
            return _api_json({"ok": True})
        if op == "rename":
            fp = _safe_fs_path(arg("path"))
            new_name = str(arg("newName", "")).strip().replace("/", "").replace("\\", "")
            if not new_name:
                return _api_json({"ok": False, "error": "新名称不能为空"}, 400)
            if not os.path.exists(fp):
                return _api_json({"ok": False, "error": "源文件或目录不存在"}, 404)
            new_fp = os.path.join(os.path.dirname(fp), new_name)
            if os.path.exists(new_fp):
                return _api_json({"ok": False, "error": "目标名称已存在"}, 409)
            try:
                os.rename(fp, new_fp)
            except OSError as e:
                return _api_json({"ok": False, "error": "重命名失败：%s" % e.strerror}, 400)
            return _api_json({"ok": True})
        return _api_json({"ok": False, "error": "未知操作"}, 400)
    except ValueError as e:
        return _api_json({"ok": False, "error": str(e)}, 403)
    except FileNotFoundError as e:
        # 不把服务器绝对路径回传给前端
        return _api_json({"ok": False, "error": "文件或目录不存在"}, 404)
    except PermissionError:
        return _api_json({"ok": False, "error": "没有权限访问该路径"}, 403)
    except Exception as e:
        return _api_json({"ok": False, "error": "操作失败：%s" % type(e).__name__}, 500)


def _ftp_connect(b):
    host = str(b.get("host", "")).strip()
    if not host:
        raise ValueError("缺少主机")
    ftp = ftplib.FTP()
    ftp.connect(host, int(b.get("port", 21) or 21), timeout=10)
    ftp.login(str(b.get("user", "") or "anonymous"), str(b.get("pass", "") or ""))
    # 许多局域网 FTP 服务仍使用 GBK/CP936；UTF-8 服务通常会在 FEAT 中声明。
    # 允许前端显式传 encoding，同时自动避免中文目录名被 latin-1 解码成乱码。
    requested_encoding = str(b.get("encoding", "") or "").strip()
    ftp._nova_auto_encoding = not bool(requested_encoding)
    if requested_encoding:
        ftp.encoding = requested_encoding
    else:
        try:
            feat = ftp.sendcmd("FEAT").upper()
            ftp.encoding = "utf-8" if "UTF8" in feat or "UTF-8" in feat else "cp936"
        except Exception:
            ftp.encoding = "utf-8"
    ftp._nova_list_encoding = ftp.encoding
    return ftp


def _ftp_path_call(ftp, call):
    """执行含路径的 FTP 命令；自动模式兼容“命令 CP936、列表 UTF-8”的服务器。"""
    primary = getattr(ftp, "_nova_command_encoding", ftp.encoding)
    encodings = [primary]
    if getattr(ftp, "_nova_auto_encoding", False) and primary.lower() != "cp936":
        encodings.append("cp936")
    last_error = None
    for encoding in encodings:
        ftp.encoding = encoding
        try:
            result = call()
            ftp._nova_command_encoding = encoding
            return result
        except ftplib.error_perm as e:
            last_error = e
    if last_error is not None:
        raise last_error


def _ftp_cwd(ftp, path):
    if path:
        # Gene6 等服务对带中文的多级路径不接受一次性 CWD；逐段进入也
        # 能让自动编码在第一个中文目录成功后复用正确的命令编码。
        parts = [p for p in str(path).replace("\\", "/").split("/") if p]
        for part in parts:
            _ftp_path_call(ftp, lambda part=part: ftp.cwd(part))
        return None
    return None


def _ftp_at_path(ftp, path, call):
    """逐级进入父目录后，对末级名称执行命令，兼容中文多级路径。"""
    parts = [p for p in str(path or "").replace("\\", "/").split("/") if p]
    if not parts:
        raise ValueError("缺少 FTP 路径")
    _ftp_cwd(ftp, "/".join(parts[:-1]))
    name = parts[-1]
    return _ftp_path_call(ftp, lambda: call(name))


def _ftp_list_current(ftp):
    """列当前目录；数据列表使用服务端声明的编码，与 CWD 命令编码分离。"""
    ftp.encoding = getattr(ftp, "_nova_list_encoding", ftp.encoding)
    try:
        return list(ftp.mlsd())
    except UnicodeDecodeError:
        if not getattr(ftp, "_nova_auto_encoding", False) or ftp.encoding.lower() == "cp936":
            raise
        ftp.encoding = "cp936"
        ftp._nova_list_encoding = "cp936"
        return list(ftp.mlsd())


def api_ftp(method):
    """FTP 客户端（stdlib ftplib）。凭据由前端随请求传入，不落 config。"""
    b = _api_body()
    op = str(b.get("op", "")).strip()
    path = str(b.get("path", "") or "")
    ftp = None
    acquired = _FTP_LIMIT.acquire(timeout=10)
    if not acquired:
        return _api_json({"ok": False, "error": "FTP 服务繁忙，请稍后重试"}, 503)
    try:
        ftp = _ftp_connect(b)
        if op == "list":
            items = []
            _ftp_cwd(ftp, path)
            try:
                for name, facts in _ftp_list_current(ftp):
                    entry_type = str(facts.get("type") or "").lower()
                    if name in (".", "..") or entry_type in ("cdir", "pdir"):
                        continue
                    items.append({"name": name, "dir": entry_type == "dir",
                                  "size": int(facts.get("size") or 0), "mtime": 0})
            except (ftplib.error_perm, ftplib.error_reply, NotImplementedError):
                base = ftp.pwd()
                try:
                    ftp.encoding = getattr(ftp, "_nova_list_encoding", ftp.encoding)
                    names = ftp.nlst()
                except UnicodeDecodeError:
                    # 部分服务器声明 UTF8，但 NLST 实际仍返回 GBK/CP936。
                    # 只有自动模式才重试，用户显式选择的编码保持不变。
                    if str(b.get("encoding", "") or "").strip() or ftp.encoding == "cp936":
                        raise
                    ftp.encoding = "cp936"
                    names = ftp.nlst(path or ".")
                for raw_name in names:
                    name = str(raw_name).replace("\\", "/").rstrip("/").split("/")[-1]
                    if name in (".", ".."):
                        continue
                    is_dir = False
                    try:
                        _ftp_cwd(ftp, name)
                        is_dir = True
                        ftp.cwd(base)
                    except Exception:
                        try:
                            ftp.cwd(base)
                        except Exception:
                            pass
                    items.append({"name": name, "dir": is_dir, "size": 0, "mtime": 0})
            return _api_json({"ok": True, "path": path, "entries": _sort_entries(items)})
        if op == "get":
            buf = io.BytesIO()
            _ftp_at_path(ftp, path, lambda name: ftp.retrbinary("RETR " + name, buf.write))
            data = buf.getvalue()
            try:
                return _api_json({"ok": True, "text": True, "content": data.decode("utf-8")})
            except UnicodeDecodeError:
                return _api_json({"ok": True, "text": False,
                                  "data": base64.b64encode(data).decode("ascii")})
        if op == "put":
            raw = (base64.b64decode(b.get("data", "")) if b.get("data")
                   else str(b.get("content", "")).encode("utf-8"))
            _ftp_at_path(ftp, path, lambda name: ftp.storbinary("STOR " + name, io.BytesIO(raw)))
            return _api_json({"ok": True})
        if op == "del":
            try:
                _ftp_at_path(ftp, path, ftp.delete)
            except Exception:
                _ftp_at_path(ftp, path, ftp.rmd)
            return _api_json({"ok": True})
        if op == "mkdir":
            _ftp_at_path(ftp, path, ftp.mkd)
            return _api_json({"ok": True})
        if op == "rename":
            new_name = str(b.get("newName", "")).strip().replace("/", "").replace("\\", "")
            if not new_name:
                return _api_json({"ok": False, "error": "新名称不能为空"}, 400)
            parent = path.rsplit("/", 1)[0] if "/" in path else ""
            _ftp_cwd(ftp, parent)
            old_name = path.replace("\\", "/").rstrip("/").split("/")[-1]
            _ftp_path_call(ftp, lambda: ftp.rename(old_name, new_name))
            return _api_json({"ok": True})
        return _api_json({"ok": False, "error": "未知操作"}, 400)
    except Exception as e:
        return _api_json({"ok": False, "error": str(e)}, 502)
    finally:
        if ftp:
            try:
                ftp.quit()
            except Exception:
                try:
                    ftp.close()
                except Exception:
                    pass
        _FTP_LIMIT.release()


def _smb_unc(b):
    """构造 \\\\host\\share\\path 形式的 UNC 路径。"""
    host = str(b.get("host", "")).strip().lstrip("\\")
    share = str(b.get("share", "")).strip().strip("\\/")
    sub = str(b.get("path", "") or "").strip().replace("/", "\\").strip("\\")
    if not host or not share:
        raise ValueError("缺少主机或共享名")
    unc = "\\\\%s\\%s" % (host, share)
    return unc + ("\\" + sub if sub else "")


_SMB_AUTH = {}   # 已建立会话的 \\host\share，避免重复 net use


def _smb_auth(b):
    """需要凭据时用系统 net use 建立会话（Windows 自带，不引入 pip 依赖）。"""
    key = "\\\\%s\\%s" % (str(b.get("host", "")).strip().lstrip("\\"),
                          str(b.get("share", "")).strip().strip("\\/"))
    with _SMB_AUTH_LOCK:
        if key in _SMB_AUTH:
            return
        user = str(b.get("user", "") or "").strip()
        cmd = ["net", "use", key]
        if user:
            cmd.append(str(b.get("pass", "") or ""))
            cmd.append("/user:" + user)
        result = subprocess.run(cmd, capture_output=True, timeout=15,
                                text=True, errors="ignore")
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "net use 认证失败").strip()
            raise RuntimeError(detail)
        _SMB_AUTH[key] = True


def api_smb(method):
    r"""Windows UNC 共享直读（\\host\share），凭据走 net use。"""
    b = _api_body()
    op = str(b.get("op", "")).strip()
    acquired = _SMB_LIMIT.acquire(timeout=10)
    if not acquired:
        return _api_json({"ok": False, "error": "SMB 服务繁忙，请稍后重试"}, 503)
    try:
        try:
            unc = _smb_unc(b)
        except ValueError as e:
            return _api_json({"ok": False, "error": str(e)}, 400)
        _smb_auth(b)
        if op == "list":
            if not os.path.isdir(unc):
                return _api_json({"ok": False, "error": "无法访问 %s" % unc}, 404)
            items = [_entry_info(n, os.path.join(unc, n)) for n in sorted(os.listdir(unc))]
            return _api_json({"ok": True, "path": b.get("path", ""),
                              "entries": _sort_entries(items)})
        if op == "get":
            with open(unc, "rb") as f:
                data = f.read()
            try:
                return _api_json({"ok": True, "text": True, "content": data.decode("utf-8")})
            except UnicodeDecodeError:
                return _api_json({"ok": True, "text": False,
                                  "data": base64.b64encode(data).decode("ascii")})
        if op == "put":
            raw = (base64.b64decode(b.get("data", "")) if b.get("data")
                   else str(b.get("content", "")).encode("utf-8"))
            with open(unc, "wb") as f:
                f.write(raw)
            return _api_json({"ok": True})
        if op == "del":
            if os.path.isdir(unc):
                os.rmdir(unc)
            else:
                os.remove(unc)
            return _api_json({"ok": True})
        if op == "mkdir":
            os.makedirs(unc, exist_ok=True)
            return _api_json({"ok": True})
        if op == "rename":
            new_name = str(b.get("newName", "")).strip().replace("/", "").replace("\\", "")
            if not new_name:
                return _api_json({"ok": False, "error": "新名称不能为空"}, 400)
            parent = unc.rsplit("\\", 1)[0] if "\\" in unc else ""
            new_unc = (parent + "\\" + new_name) if parent else new_name
            os.rename(unc, new_unc)
            return _api_json({"ok": True})
        return _api_json({"ok": False, "error": "未知操作"}, 400)
    except Exception as e:
        return _api_json({"ok": False, "error": str(e)}, 502)
    finally:
        _SMB_LIMIT.release()


def wan_reachable():
    """探测电脑自身外网是否通（平板据此判断能否借本机网络访问外网）。
    先用真实 HTTP(S) 请求判定（最贴近"能否代取外网"），再退 TCP 直连：
    部分网络封 TCP 53/对特定 IP 的 443，但正常网站访问一切正常。"""
    for url in ("https://www.baidu.com", "https://www.qq.com",
                "http://connect.rom.miui.com/generate_204"):
        try:
            r = requests.get(url, timeout=2.5, allow_redirects=True,
                             verify=False, stream=True)
            r.close()
            if r.status_code < 500:
                return True
        except Exception:
            continue
    for host, port in (("223.5.5.5", 443), ("119.29.29.29", 443),
                       ("223.5.5.5", 53), ("119.29.29.29", 53)):
        try:
            s = socket.create_connection((host, port), timeout=2)
            s.close()
            return True
        except Exception:
            continue
    return False


WEB_MAX_BYTES = 200 * 1024 * 1024   # 外网代取单次上限 200MB，防止内存打满
# 部分音乐/图床 CDN 按 User-Agent 过滤（酷我音频 CDN 对 python UA 直接 403），
# 代取时默认携带浏览器 UA，可用参数 ua 覆盖。
WEB_DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/99.0.4844.51 Safari/537.36")


def api_web():
    """走电脑网络代取外网资源（HTTP/HTTPS）。
    断劫持后平板仍可借热点主机的网络访问外网；CORS 全开放。"""
    if request.method == "POST":
        b = _api_body()
        url = str(b.get("url", "") or "").strip()
        method = str(b.get("method", "GET") or "GET").upper()
        fwd_body = b.get("data")
        ua = str(b.get("ua", "") or "").strip()
    else:
        url = str(request.args.get("u") or request.args.get("url") or "").strip()
        method = "GET"
        fwd_body = None
        ua = str(request.args.get("ua", "") or "").strip()
    if not re.match(r"^https?://", url, re.I):
        return _api_json({"ok": False, "error": "仅支持 http/https 网址"}, 400)
    if method not in ("GET", "POST"):
        return _api_json({"ok": False, "error": "仅支持 GET/POST"}, 400)
    try:
        up = requests.request(method=method, url=url, timeout=(10, 90),
                              headers={"User-Agent": ua or WEB_DEFAULT_UA},
                              data=(fwd_body.encode("utf-8")
                                    if isinstance(fwd_body, str) else fwd_body),
                              stream=True, allow_redirects=True, verify=False)
    except Exception as e:
        return _api_json({"ok": False, "error": "电脑无法访问该网址：%s" % e}, 502)
    # 上游失败必须显式报错：曾经 403 也带着空 body 回 200，浏览器端
    # 会误存一个 0 字节"成功"文件（音乐广场下载空文件就是这个根因）。
    if up.status_code >= 400:
        up.close()
        return _api_json({"ok": False,
                          "error": "远程服务器返回 HTTP %d" % up.status_code},
                         502 if up.status_code >= 500 else 403)

    chunks, total = [], 0
    too_big = False
    for chunk in up.iter_content(65536):
        if not chunk:
            continue
        total += len(chunk)
        if total > WEB_MAX_BYTES:
            too_big = True
            break
        chunks.append(chunk)
    up.close()
    if too_big:
        return _api_json({"ok": False, "error": "文件超过 200MB 上限"}, 413)

    body = b"".join(chunks)
    ctype = (up.headers.get("Content-Type") or "application/octet-stream").split(";")[0].strip()
    # 部分 OSS/CDN 对无后缀资源统一返回 octet-stream；根据文件签名恢复常见媒体类型。
    if ctype in ("", "application/octet-stream", "binary/octet-stream"):
        if body.startswith(b"\xff\xd8\xff"):
            ctype = "image/jpeg"
        elif body.startswith(b"\x89PNG\r\n\x1a\n"):
            ctype = "image/png"
        elif body[:6] in (b"GIF87a", b"GIF89a"):
            ctype = "image/gif"
        elif body.startswith(b"RIFF") and body[8:12] == b"WEBP":
            ctype = "image/webp"
        elif body.startswith(b"%PDF-"):
            ctype = "application/pdf"
        elif len(body) > 12 and body[4:8] == b"ftyp":
            ctype = "video/mp4"
    name = ""
    cd = up.headers.get("Content-Disposition") or ""
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', cd, re.I)
    if m:
        from urllib.parse import unquote
        name = unquote(m.group(1))
    if not name:
        from urllib.parse import urlparse, unquote
        name = unquote(os.path.basename(urlparse(url).path)) or "download"
    ext = {
            "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
            "image/webp": ".webp", "application/pdf": ".pdf", "video/mp4": ".mp4",
        }.get(ctype) or mimetypes.guess_extension(ctype) or ""
    if ext and re.search(r"\.(?:bin|dat)$", name, re.I):
        name = re.sub(r"\.[^.]+$", ext, name)
    elif not re.search(r"\.[A-Za-z0-9]{1,8}$", name):
        name += ext
    resp = Response(body, status=200)
    resp.headers["Content-Type"] = ctype
    resp.headers["Content-Length"] = str(len(body))
    resp.headers["X-File-Name"] = name
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Expose-Headers"] = "X-File-Name,Content-Type"
    return resp


def serve_nova_api(rel, method):
    """挂载点下 api/ 路由分发：fs（本地）/ ftp / smb / web（外网代取）/ music / ping。"""
    # GET 路径常带 query string（如 api/music/search?src=netease），解析前先去掉
    path_only = rel.split("?")[0]
    parts = [p for p in path_only.split("/") if p]
    if not parts:
        return _api_json({"ok": False, "error": "缺少端点"}, 404)
    kind = parts[0].lower()
    op = parts[1].lower() if len(parts) > 1 else ""
    if kind == "fs":
        return api_fs(op, method)
    if kind == "ftp":
        return api_ftp(method)
    if kind == "smb":
        return api_smb(method)
    if kind == "web":
        return api_web()
    if kind == "music":
        from novacore import music_api
        return music_api.music_api(op)
    if kind == "search":
        from novacore import search_api
        return search_api.search_api(op, method)
    if kind == "ping":
        return _api_json({"ok": True, "fs_root": fs_root(),
                          # Keep the liveness probe local and cheap.  WAN
                          # probing performs several external requests and
                          # must not block every tablet reconnect.
                          "wan": None,
                          "hotspot": hotspot_ip(), "answer": answer_ip(),
                           "local": local_ip(), "ts": int(time.time())})
    if kind == "system-info":
        import socket
        try:
            from novacore.proxy_engine import PROXY
            proxy = {"running": bool(PROXY.running), "ip": PROXY.host if PROXY.running else None,
                     "port": PROXY.port if PROXY.running else None}
        except Exception:
            proxy = {"running": False, "ip": None, "port": None}
        dns = list(cfg("dns_upstreams", []) or [])
        try:
            host = socket.gethostname()
        except Exception:
            host = ""
        return _api_json({"ok": True, "hostname": host, "local_ip": local_ip(),
                          "hotspot_ip": hotspot_ip(), "answer_ip": answer_ip(),
                          "dns_servers": dns, "proxy": proxy,
                          "lan_mode": bool(cfg("lan_mode", False)),
                          "dns_enabled": bool(cfg("dns_enable", True)),
                          "http_port": cfg("http_port", 80), "ts": int(time.time())})
    return _api_json({"ok": False, "error": "未知端点"}, 404)
