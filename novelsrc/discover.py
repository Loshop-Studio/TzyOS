# -*- coding: utf-8 -*-
"""公开书源发现：从公开索引拉取候选书源站，与本地已移植书源比对，
并对候选站点做基础可达性/搜索探测，帮助发现可以新增的书源。

设计原则：
- 只使用公开、无需登录的索引接口；
- 拉取失败静默降级（不阻塞 UI），返回已本地书源清单；
- 任何候选站点都必须经过 verify 模块验证后才建议收录；
- 不自动写代码，只输出「建议新增 / 已存在 / 不可用」三类结果供人工合并。
"""
import json
import os
import re
import threading
import time
from urllib.parse import urlparse

import requests

from novelsrc.base import get_site, site_keys

INDEX_TTL = 12 * 3600
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(HERE, "novelsrc", "source_index.json")

# 公开书源索引（无需登录）。按顺序尝试，任一成功即用。
INDEX_SOURCES = [
    # yiove 公开书源索引（社区维护，返回站点列表 JSON）
    "https://shuyuan-api.yiove.com/api/v1/book-sources",
    # 备用：同项目的站点健康列表
    "https://shuyuan-api.yiove.com/api/v1/book-sources?health=1",
]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

_lock = threading.Lock()
_running = {"on": False, "at": 0, "error": "", "found": 0}


def _local_hosts():
    hosts = {}
    for key in site_keys():
        site = get_site(key)
        for h in (site.hosts or []):
            hosts[str(h).lower()] = key
    return hosts


def _host_of(url):
    try:
        h = (urlparse(url).hostname or "").lower()
        return h[4:] if h.startswith("www.") else h
    except Exception:
        return ""


def _norm_host(h):
    h = str(h or "").lower()
    return h[4:] if h.startswith("www.") else h


def _fetch_index():
    """返回 (items, error)。items 为 [{name,url,host}]。"""
    last = ""
    for url in INDEX_SOURCES:
        try:
            r = requests.get(url, timeout=15, verify=False,
                             headers={"User-Agent": UA, "Accept": "application/json"})
            if r.status_code >= 400:
                last = "HTTP %d" % r.status_code
                continue
            data = r.json()
        except Exception as exc:
            last = str(exc)[:120]
            continue
        items = []
        # 兼容多种返回结构
        rows = data
        if isinstance(data, dict):
            for k in ("data", "sources", "items", "list"):
                if isinstance(data.get(k), list):
                    rows = data[k]
                    break
        if isinstance(rows, list):
            for it in rows:
                if not isinstance(it, dict):
                    continue
                name = it.get("name") or it.get("bookSourceName") or it.get("title") or ""
                u = it.get("url") or it.get("bookSourceUrl") or it.get("home") or ""
                host = it.get("host") or _host_of(u)
                if not host:
                    continue
                items.append({"name": str(name)[:60], "url": str(u)[:300],
                              "host": _norm_host(host)})
        if items:
            return items, ""
        last = "索引返回空列表"
    return [], last or "无可用索引源"


def _probe(host, timeout=8):
    """探测站点首页是否可达（200/3xx）。"""
    for scheme in ("https", "http"):
        try:
            r = requests.get("%s://%s/" % (scheme, host), timeout=timeout,
                             verify=False, allow_redirects=True,
                             headers={"User-Agent": UA})
            if r.status_code < 400 and len(r.content) > 500:
                return True, r.status_code
        except Exception:
            continue
    return False, 0


def _load():
    try:
        with open(CACHE, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and "at" in d:
            return d
    except (OSError, ValueError):
        pass
    return {"at": 0, "items": [], "error": ""}


def _save(items, error=""):
    d = {"at": int(time.time()), "items": items, "error": error}
    tmp = CACHE + ".tmp"
    try:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, CACHE)
    except OSError:
        pass
    return d


def status():
    with _lock:
        return dict(_running)


def get_index(force=False):
    """返回索引比对结果；force=True 时后台刷新。"""
    d = _load()
    fresh = (time.time() - int(d.get("at", 0))) < INDEX_TTL
    if force or not fresh:
        start_refresh()
    return {"ok": True, "at": d.get("at", 0), "fresh": fresh,
            "error": d.get("error", ""), "items": d.get("items", []),
            "running": status()}


def start_refresh():
    with _lock:
        if _running["on"]:
            return False
        _running.update(on=True, at=int(time.time()), error="", found=0)
    threading.Thread(target=_refresh, daemon=True).start()
    return True


def _refresh():
    local = _local_hosts()
    items, err = _fetch_index()
    out = []
    seen = set()
    for it in items:
        host = it["host"]
        if not host or host in seen:
            continue
        seen.add(host)
        known = local.get(host, "")
        rec = {"name": it["name"], "url": it["url"], "host": host,
               "localKey": known, "status": "existing" if known else "candidate"}
        if not known:
            ok, code = _probe(host)
            rec["status"] = "candidate-ok" if ok else "candidate-dead"
            rec["http"] = code
        out.append(rec)
    _save(out, err)
    with _lock:
        _running.update(on=False, error=err, found=len(out))


def suggestions():
    """建议新增（可达且本地没有的候选站点）。"""
    d = _load()
    return [x for x in d.get("items", []) if x.get("status") == "candidate-ok"]


# ---------------------------------------------------------------------------
# 本地书源清单整理（公开索引不可达时的权威来源）
# ---------------------------------------------------------------------------
def local_catalog():
    """返回本地全部书源的整理清单：按可用性验证结果标注状态。"""
    from novelsrc.verify import _load_cache as _vload
    vitems = (_vload().get("items") or {})
    rows = []
    for key in site_keys():
        site = get_site(key)
        v = vitems.get(key) or {}
        if not v:
            state, note = "unknown", "尚未验证"
        elif v.get("ok"):
            state, note = "ok", v.get("detail") or "可用"
        else:
            state = v.get("reason") or "unusable"
            note = v.get("detail") or ""
        rows.append({
            "key": key,
            "name": site.display_name or key,
            "hosts": list(site.hosts or []),
            "tags": list(site.tags or []),
            "searchable": bool(site.searchable),
            "downloadable": bool(site.downloadable),
            "loginRequired": bool(site.login_required),
            "status": state,
            "detail": note,
            "chapterCount": v.get("chapterCount", 0),
            "checkedAt": v.get("checkedAt", 0),
        })
    order = {"ok": 0, "unknown": 1}
    rows.sort(key=lambda r: (order.get(r["status"], 2), r["key"]))
    return {
        "ok": True,
        "total": len(rows),
        "usable": sum(1 for r in rows if r["status"] == "ok"),
        "items": rows,
    }
