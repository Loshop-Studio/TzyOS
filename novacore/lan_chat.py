# -*- coding: utf-8 -*-
"""Small in-memory rendezvous/relay service for the LAN Chat app.

WebRTC carries normal chat traffic when possible.  This module only handles
registration, pairing, signaling and the fallback message relay.
"""
import json
import random
import os
import secrets
import tempfile
import threading
import time

from flask import jsonify, send_file


PAIR_TTL = 5 * 60
DEVICE_TTL = 10 * 60
MAX_EVENTS = 200
MAX_FILE_SIZE = 100 * 1024 * 1024
FILE_TTL = 30 * 60
TRANSFER_DIR = os.path.join(tempfile.gettempdir(), "novaos-lan-chat")
# 昵称认领用的身份快照目录：与临时文件中继不同，这里必须跨重启保留，
# 否则平板换系统后无法用昵称找回联系人与聊天记录。
IDENTITY_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "backups", "lan-identities")
MAX_IDENTITY_BYTES = 8 * 1024 * 1024



class LanChatState(object):
    def __init__(self):
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.devices = {}
        # 昵称 -> 最近一次注册的 deviceId。平板换系统/清缓存后 deviceId 会变，
        # 但昵称不变，据此把新设备接到同名身份上并更新在线地址。
        self.names = {}
        self.pair_codes = {}
        self.events = {}
        self.files = {}

    def _now(self):
        return time.time()

    def _normalize_name(self, name):
        name = " ".join(str(name or "").split())
        return name[:40]

    def _code(self):
        for _ in range(20):
            code = "%06d" % random.SystemRandom().randint(0, 999999)
            if code not in self.pair_codes:
                return code
        raise RuntimeError("无法生成配对码")

    def _cleanup(self):
        now = self._now()
        for code, item in list(self.pair_codes.items()):
            if item["expiresAt"] <= now:
                self.pair_codes.pop(code, None)
        for device_id, item in list(self.devices.items()):
            if item["lastSeen"] + DEVICE_TTL <= now:
                self.devices.pop(device_id, None)
                self.events.pop(device_id, None)
        for file_id, item in list(self.files.items()):
            if item["expiresAt"] <= now:
                self._drop_file(file_id)

    def register(self, payload):
        device_id = str(payload.get("deviceId") or "").strip()
        if not device_id or len(device_id) > 128:
            return {"ok": False, "error": "deviceId 无效"}, 400
        name = self._normalize_name(payload.get("name"))
        if not name:
            return {"ok": False, "error": "昵称不能为空"}, 400
        fingerprint = str(payload.get("fingerprint") or "")[:128]
        # reclaim=True 表示调用方已确认要用此昵称接手旧身份（换系统找回）。
        reclaim = bool(payload.get("reclaim"))
        with self.condition:
            self._cleanup()
            known = self.names.get(name)
            previous = self.devices.get(device_id)
            # 昵称已被另一个仍在线/曾注册的设备占用：默认不抢占，交由前端询问
            # 用户是否需要“用此昵称找回”。confirm 后才允许改写映射。
            if known and known != device_id:
                other = self.devices.get(known)
                if other and not reclaim:
                    return {
                        "ok": False,
                        "code": "name-taken",
                        "error": "昵称已被其他设备使用",
                        "peer": {"deviceId": known, "name": other.get("name"),
                                 "fingerprint": other.get("fingerprint"),
                                 "lastSeen": int(other.get("lastSeen", 0) * 1000)},
                    }, 409
                self.devices.pop(known, None)
                self.events.pop(known, None)
            self.names[name] = device_id
            self.devices[device_id] = {
                "deviceId": device_id, "name": name,
                "fingerprint": fingerprint, "lastSeen": self._now(),
                "capabilities": payload.get("capabilities") or {},
            }
            self.events.setdefault(device_id, [])
            if previous and previous.get("name") != name:
                for peer_id in self.devices:
                    if peer_id != device_id:
                        self._push(peer_id, "device-update", {
                            "deviceId": device_id,
                            "name": name, "fingerprint": fingerprint,
                            "previousName": previous.get("name"),
                        })
                self.condition.notify_all()
            return {"ok": True, "device": self.devices[device_id],
                    "reclaimed": bool(known and known != device_id)}, 200

    def group_message(self, source_id, message):
        with self.condition:
            self._cleanup()
            if source_id not in self.devices:
                return {"ok": False, "error": "设备尚未注册"}, 401
            if not isinstance(message, dict):
                return {"ok": False, "error": "消息无效"}, 400
            payload = {"from": source_id, "message": message}
            for device_id in self.devices:
                if device_id != source_id:
                    self._push(device_id, "group-message", payload)
            self.condition.notify_all()
            return {"ok": True}, 200

    def create_pair(self, device_id):
        with self.condition:
            self._cleanup()
            if device_id not in self.devices:
                return {"ok": False, "error": "设备尚未注册"}, 401
            code = self._code()
            self.pair_codes[code] = {"deviceId": device_id, "expiresAt": self._now() + PAIR_TTL}
            return {"ok": True, "code": code, "expiresIn": PAIR_TTL}, 200

    def join_pair(self, device_id, code):
        with self.condition:
            self._cleanup()
            if device_id not in self.devices:
                return {"ok": False, "error": "设备尚未注册"}, 401
            item = self.pair_codes.pop(str(code or "").strip(), None)
            if not item:
                return {"ok": False, "error": "配对码无效或已过期"}, 400
            source_id = item["deviceId"]
            if source_id == device_id or source_id not in self.devices:
                return {"ok": False, "error": "不能与当前设备配对"}, 400
            source = self.devices[source_id]
            target = self.devices[device_id]
            self._push(source_id, "pair-offer", {"peer": target})
            self._push(device_id, "pair-accepted", {"peer": source})
            self.condition.notify_all()
            return {"ok": True}, 200

    def signal(self, source_id, target_id, message):
        with self.condition:
            self._cleanup()
            if not self._known(source_id, target_id):
                return {"ok": False, "error": "设备未注册或已离线"}, 404
            self._push(target_id, "signal", {"from": source_id, "message": message})
            self.condition.notify_all()
            return {"ok": True}, 200

    def relay(self, source_id, target_id, message):
        with self.condition:
            self._cleanup()
            if not self._known(source_id, target_id):
                return {"ok": False, "error": "设备未注册或已离线"}, 404
            self._push(target_id, "message", {
                "from": source_id,
                "name": (self.devices.get(source_id) or {}).get("name", ""),
                "message": message,
            })
            self.condition.notify_all()
            return {"ok": True}, 200

    def upload_file(self, source_id, target_id, name, mime, stream, content_length):
        with self.condition:
            self._cleanup()
            if not self._known(source_id, target_id):
                return {"ok": False, "error": "设备未注册或已离线"}, 404
        if content_length is not None and content_length > MAX_FILE_SIZE:
            return {"ok": False, "error": "文件不能超过 100 MB"}, 413
        file_id = secrets.token_hex(16)
        os.makedirs(TRANSFER_DIR, exist_ok=True)
        path = os.path.join(TRANSFER_DIR, file_id + ".bin")
        size = 0
        try:
            with open(path, "wb") as output:
                while True:
                    chunk = stream.read(64 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_FILE_SIZE:
                        raise ValueError("文件不能超过 100 MB")
                    output.write(chunk)
        except Exception:
            try:
                os.remove(path)
            except OSError:
                pass
            raise
        safe_name = os.path.basename(str(name or "文件.bin").replace("\\", "/"))[:180] or "文件.bin"
        with self.condition:
            self.files[file_id] = {
                "id": file_id, "path": path, "from": source_id, "to": target_id,
                "name": safe_name, "mime": str(mime or "application/octet-stream")[:120],
                "size": size, "expiresAt": self._now() + FILE_TTL,
            }
            self._push(target_id, "file", {k: self.files[file_id][k]
                                             for k in ("id", "from", "name", "mime", "size", "expiresAt")})
            self.condition.notify_all()
        return {"ok": True, "fileId": file_id, "size": size, "expiresIn": FILE_TTL}, 200

    def get_file(self, file_id, device_id):
        with self.condition:
            self._cleanup()
            item = self.files.get(file_id)
            if not item or item["to"] != device_id:
                return None
            return dict(item)

    # ---------------- 昵称身份快照（换系统找回） ----------------
    def _identity_path(self, name):
        safe = "".join(ch for ch in self._normalize_name(name)
                       if ch not in '\\/:*?"<>|').strip()
        if not safe:
            return None
        import hashlib
        digest = hashlib.sha1(safe.encode("utf-8")).hexdigest()[:16]
        return os.path.join(IDENTITY_DIR, digest + ".json")

    def save_identity(self, payload):
        name = self._normalize_name(payload.get("name"))
        if not name:
            return {"ok": False, "error": "昵称不能为空"}, 400
        try:
            raw = json.dumps(payload.get("snapshot") or {}, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": "身份数据无效：%s" % exc}, 400
        if len(raw.encode("utf-8")) > MAX_IDENTITY_BYTES:
            return {"ok": False, "error": "身份数据过大"}, 413
        path = self._identity_path(name)
        if not path:
            return {"ok": False, "error": "昵称无效"}, 400
        os.makedirs(IDENTITY_DIR, exist_ok=True)
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(raw)
            os.replace(tmp, path)
        except OSError as exc:
            return {"ok": False, "error": "身份保存失败：%s" % exc}, 500
        return {"ok": True, "name": name, "size": len(raw)}, 200

    def load_identity(self, name):
        name = self._normalize_name(name)
        if not name:
            return {"ok": False, "error": "昵称不能为空"}, 400
        path = self._identity_path(name)
        if not path or not os.path.isfile(path):
            return {"ok": False, "code": "not-found", "error": "电脑上没有该昵称的记录"}, 404
        try:
            with open(path, "r", encoding="utf-8") as f:
                snapshot = json.load(f)
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": "身份读取失败：%s" % exc}, 500
        return {"ok": True, "name": name, "snapshot": snapshot}, 200

    def finish_file(self, file_id, device_id):
        with self.condition:
            item = self.files.get(file_id)
            if not item or item["to"] != device_id:
                return {"ok": False, "error": "文件不存在或无权操作"}, 404
            self._drop_file(file_id)
            return {"ok": True}, 200

    def _drop_file(self, file_id):
        item = self.files.pop(file_id, None)
        if item:
            try:
                os.remove(item["path"])
            except OSError:
                pass

    def poll(self, device_id, timeout):
        timeout = max(0, min(int(timeout or 20), 25))
        deadline = self._now() + timeout
        with self.condition:
            self._cleanup()
            if device_id not in self.devices:
                return {"ok": False, "error": "设备尚未注册"}, 401
            self.devices[device_id]["lastSeen"] = self._now()
            while not self.events.get(device_id):
                remaining = deadline - self._now()
                if remaining <= 0:
                    break
                self.condition.wait(remaining)
            events = self.events.setdefault(device_id, [])[:MAX_EVENTS]
            del self.events[device_id][:len(events)]
            return {"ok": True, "events": events}, 200

    def _known(self, source_id, target_id):
        return (source_id in self.devices and target_id in self.devices
                and source_id != target_id)

    def _push(self, device_id, kind, payload):
        queue = self.events.setdefault(device_id, [])
        queue.append({"kind": kind, "payload": payload, "at": int(self._now() * 1000)})
        del queue[:-MAX_EVENTS]


STATE = LanChatState()


def json_result(data, status=200):
    response = jsonify(data)
    response.status_code = status
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Cache-Control"] = "no-store"
    return response


def handle_lan_request(subpath, request):
    """Dispatch a request below ``lan/`` and return a Flask response."""
    if request.method == "OPTIONS":
        return json_result({"ok": True})
    if subpath == "register" and request.method == "POST":
        return json_result(*STATE.register(request.get_json(silent=True) or {}))
    if subpath == "pair/create" and request.method == "POST":
        body = request.get_json(silent=True) or {}
        return json_result(*STATE.create_pair(str(body.get("deviceId") or "").strip()))
    if subpath == "pair/join" and request.method == "POST":
        body = request.get_json(silent=True) or {}
        return json_result(*STATE.join_pair(
            str(body.get("deviceId") or "").strip(), str(body.get("code") or "").strip()))
    if subpath == "signal" and request.method == "POST":
        body = request.get_json(silent=True) or {}
        return json_result(*STATE.signal(
            str(body.get("from") or "").strip(), str(body.get("to") or "").strip(), body.get("message")))
    if subpath == "relay" and request.method == "POST":
        body = request.get_json(silent=True) or {}
        return json_result(*STATE.relay(
            str(body.get("from") or "").strip(), str(body.get("to") or "").strip(), body.get("message")))
    if subpath == "group" and request.method == "POST":
        body = request.get_json(silent=True) or {}
        return json_result(*STATE.group_message(
            str(body.get("from") or "").strip(), body.get("message")))
    if subpath == "file/upload" and request.method == "POST":
        try:
            return json_result(*STATE.upload_file(
                request.args.get("from", "").strip(), request.args.get("to", "").strip(),
                request.args.get("name", ""), request.headers.get("Content-Type", ""),
                request.stream, request.content_length))
        except ValueError as exc:
            return json_result({"ok": False, "error": str(exc)}, 413)
        except Exception as exc:
            return json_result({"ok": False, "error": "文件暂存失败：%s" % exc}, 500)
    if subpath.startswith("file/download/") and request.method in ("GET", "HEAD"):
        item = STATE.get_file(subpath[len("file/download/"):], request.args.get("deviceId", "").strip())
        if not item:
            return json_result({"ok": False, "error": "文件不存在、已过期或无权下载"}, 404)
        response = send_file(item["path"], mimetype=item["mime"], as_attachment=True,
                             download_name=item["name"], conditional=True)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Access-Control-Allow-Origin"] = "*"
        return response
    if subpath.startswith("file/finish/") and request.method == "POST":
        body = request.get_json(silent=True) or {}
        return json_result(*STATE.finish_file(
            subpath[len("file/finish/"):], str(body.get("deviceId") or "").strip()))
    if subpath == "poll" and request.method == "GET":
        return json_result(*STATE.poll(request.args.get("deviceId", "").strip(), request.args.get("timeout", "20")))
    if subpath == "identity/save" and request.method == "POST":
        return json_result(*STATE.save_identity(request.get_json(silent=True) or {}))
    if subpath == "identity/load" and request.method in ("GET", "POST"):
        body = request.get_json(silent=True) or {}
        name = body.get("name") if isinstance(body, dict) and body.get("name") else request.args.get("name", "")
        return json_result(*STATE.load_identity(name))
    return json_result({"ok": False, "error": "LAN Chat 接口不存在"}, 404)
