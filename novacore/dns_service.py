# -*- coding: utf-8 -*-
"""UDP 53 劫持服务（热点网卡精确绑定，压过 Windows ICS 通配占用）。"""
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from novacore.configutil import cfg
from novacore.netutil import hotspot_ip, answer_ip

DNS_WORKERS = 24
DNS_CACHE_TTL = 30

# ----------------------------- DNS 服务 -----------------------------
class DNSQuery:
    def __init__(self, data):
        self.data = data
        self.domain = ""
        if len(data) < 13 or (data[2] >> 3) & 15 != 0:
            return
        ini = 12
        labels = []
        while ini < len(data):
            lon = data[ini]
            if lon == 0:
                break
            if lon > 63 or ini + 1 + lon > len(data):
                self.domain = ""
                return
            labels.append(data[ini + 1:ini + 1 + lon].decode("ascii", errors="ignore"))
            ini += lon + 1
        self.domain = ".".join(labels)

    def build_response(self, ip):
        pkt = self.data[:2] + b"\x85\x80"
        pkt += self.data[4:6] + self.data[4:6] + b"\x00\x00\x00\x00" + self.data[12:]
        pkt += b"\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x05\x00\x04"
        pkt += bytes(int(x) for x in ip.split("."))
        return pkt


class DNSThread(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.running = False
        self.sock = None
        self.pool = ThreadPoolExecutor(max_workers=DNS_WORKERS)
        self.send_lock = threading.Lock()
        self.cache = {}
        self.cache_lock = threading.Lock()

    def _send(self, data, addr):
        with self.send_lock:
            if self.sock:
                self.sock.sendto(data, addr)

    def _handle_query(self, data, addr):
        try:
            q = DNSQuery(data)
            domain = q.domain.strip(".").lower()
            if not domain:
                return
            hijacked = {str(d).strip().lower() for d in cfg("hijack_domains", [])}
            if domain in hijacked:
                self._send(q.build_response(answer_ip()), addr)
                return

            key = data[2:] if len(data) >= 12 else data
            now = time.time()
            with self.cache_lock:
                hit = self.cache.get(key)
                if hit and hit[0] > now:
                    self._send(data[:2] + hit[1], addr)
                    return
                if hit:
                    self.cache.pop(key, None)

            for up in cfg("dns_upstreams", ["223.5.5.5"]):
                f = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    f.settimeout(1.2)
                    f.sendto(data, (up, 53))
                    resp, _ = f.recvfrom(4096)
                    if len(resp) >= 2:
                        with self.cache_lock:
                            if len(self.cache) >= 2048:
                                self.cache.clear()
                            self.cache[key] = (now + DNS_CACHE_TTL, resp[2:])
                    self._send(resp, addr)
                    return
                except Exception:
                    continue
                finally:
                    f.close()
        except Exception as e:
            print("[DNS] 处理异常：%s" % e)

    def run(self):
        # 热点模式：必须精确绑定热点网卡 IP。
        # Windows 移动热点的 ICS 服务会占住 0.0.0.0:53，
        # 只有更具体的地址（192.168.137.1）才能优先截获设备发来的查询。
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        except OSError:
            pass
        bound_ip = None
        lan = bool(cfg("lan_mode", False))
        if lan:
            # 局域网模式：平板 Wi-Fi 的 DNS 手动指向本机，直接通配绑定即可，
            # 不必等热点网卡（模式 2 下没有热点），让服务秒级就绪。
            try:
                sock.bind(("0.0.0.0", 53))
                bound_ip = "0.0.0.0"
            except OSError:
                pass
        # 热点模式：热点开启后网卡 IP（192.168.137.1）要几秒才就绪。
        # 先等它并精确绑定（唯一能稳定压过 ICS 通配绑定的方式）；
        # 12 秒内热点网卡没出现（/nohotspot 或纯局域网模式）再退回通配。
        for _ in range(0 if lan else 6):
            hip = hotspot_ip()
            if hip:
                try:
                    sock.bind((hip, 53))
                    bound_ip = hip
                    break
                except OSError:
                    break
            time.sleep(2)
        if not bound_ip:
            try:
                sock.bind(("0.0.0.0", 53))
                bound_ip = "0.0.0.0"
            except OSError:
                pass
        if not bound_ip:
            print("[DNS] 绑定 53 端口失败（被占用或无可用网卡，需管理员权限）")
            sock.close()
            return
        self.sock = sock
        self.sock.settimeout(2)
        self.running = True
        print("[DNS] 已启动 %s:53，劫持域名 -> %s" % (bound_ip, answer_ip()))
        if lan:
            print("[DNS] 局域网模式：把平板的 Wi-Fi DNS 手动设为 %s" % answer_ip())
        while self.running:
            try:
                data, addr = self.sock.recvfrom(1024)
            except socket.timeout:
                continue
            except OSError:
                break
            if len(data) < 12:
                continue
            self.pool.submit(self._handle_query, data, addr)

    def stop(self):
        self.running = False
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass
        self.pool.shutdown(wait=False)
