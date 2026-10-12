# -*- coding: utf-8 -*-
"""Small in-memory rendezvous/relay service for the LAN Chat app.

WebRTC carries normal chat traffic when possible.  This module only handles
registration, pairing, signaling and the fallback message relay.
"""
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


class LanChatState(object):
    def __init__(self):
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.devices = {}
        self.pair_codes = {}
        self.events = {}
        self.files = {}

    def _now(self):
        return time.time()

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
        name = str(payload.get("name") or "设备")[:80]
        fingerprint = str(payload.get("fingerprint") or "")[:128]
        with self.condition:
            self._cleanup()
            self.devices[device_id] = {
                "deviceId": device_id, "name": name,
                "fingerprint": fingerprint, "lastSeen": self._now(),
                "capabilities": payload.get("capabilities") or {},
            }
            self.events.setdefault(device_id, [])
            return {"ok": True, "device": self.devices[device_id]}, 200

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
            self._push(target_id, "message", {"from": source_id, "message": message})
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
    return json_result({"ok": False, "error": "LAN Chat 接口不存在"}, 404)
