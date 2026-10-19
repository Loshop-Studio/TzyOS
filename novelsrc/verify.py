# -*- coding: utf-8 -*-
"""书源可用性验证：搜索 → 目录 → 首章正文，判定是否能正常免费阅读。

判据（任一命中即视为不可用/降级）：
  - 站点不可达或搜索无结果            → unreachable / empty
  - 搜索结果打不开详情或目录为空      → broken
  - 首章正文过短或含 VIP/订阅/登录提示 → vip / login / incomplete
  - 正文与目录章节数明显不符（抽样）    → incomplete

只在服务端批量跑一次即可，结果缓存到磁盘供 UI 展示与 novelsrc 整理使用。
"""
import json
import os
import re
import threading
import time

from novelsrc.base import get_site, site_keys

VERIFY_TTL = 24 * 3600
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_PATH = os.path.join(HERE, "novelsrc", "source_verify.json")

# 章节正文里常见的付费/拦截提示
BLOCK_PATTERNS = [
    (re.compile(r"VIP章节|VIP专享|开通VIP|升级VIP|VIP会员"), "vip"),
    (re.compile(r"订阅本章|本章为订阅|订阅后才能阅读|付费章节|购买本章"), "subscription"),
    (re.compile(r"登录后阅读|请先登录|登录才能|注册后阅读|扫码登录"), "login"),
    (re.compile(r"章节内容正在更新|内容加载失败|请稍后再试"), "incomplete"),
]
MIN_CONTENT_LEN = 200
MIN_PLAUSIBLE_RATIO = 0.5

_lock = threading.Lock()
_running = {"on": False, "started": 0, "total": 0, "done": 0, "current": ""}


def _load_cache():
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "at" in data:
            return data
    except (OSError, ValueError):
        pass
    return {"at": 0, "items": {}}


def _save_cache(items):
    payload = {"at": int(time.time()), "items": items}
    tmp = CACHE_PATH + ".tmp"
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        os.replace(tmp, CACHE_PATH)
    except OSError:
        pass
    return payload


def _content_verdict(text):
    text = (text or "").strip()
    if len(text) < MIN_CONTENT_LEN:
        for pat, reason in BLOCK_PATTERNS:
            if pat.search(text):
                return reason, "正文命中拦截提示（%d 字）" % len(text)
        return "incomplete", "正文过短（%d 字）" % len(text)
    for pat, reason in BLOCK_PATTERNS:
        if pat.search(text[:1200]):
            return reason, "正文开头出现付费/登录提示"
    return "", ""


def verify_site(key, keyword="斗破苍穹", timeout_note=""):
    """返回单个书源的验证结果 dict。"""
    from novelsrc.base import SITES
    cls = SITES.get(key)
    if not cls:
        return {"key": key, "ok": False, "reason": "missing", "detail": "书源不存在"}
    site = get_site(key)
    out = {"key": key, "name": site.display_name or key,
           "ok": False, "reason": "", "detail": "", "checkedAt": int(time.time()),
           "searchable": bool(site.searchable), "downloadable": bool(site.downloadable),
           "loginRequired": bool(site.login_required)}
    if not site.searchable:
        out.update(reason="no-search", detail="不支持搜索")
        return out
    # 1) 搜索
    try:
        results = site.search(keyword, limit=3) or []
    except Exception as exc:
        out.update(reason="unreachable", detail="搜索失败：%s" % str(exc)[:120])
        return out
    if not results:
        out.update(reason="empty", detail="搜索无结果")
        return out
    book = results[0]
    out["sample"] = {"title": book.get("title") or book.get("name") or "",
                     "bookId": str(book.get("book_id") or book.get("id")
                                   or book.get("bookId") or book.get("bid") or "")}
    # 各书源字段不一：book_id / id / bookId 都可能出现
    book_id = (book.get("book_id") or book.get("id") or book.get("bookId")
               or book.get("bid") or "")
    if not book_id:
        out.update(reason="broken", detail="搜索结果缺少 book id")
        return out
    if not site.downloadable:
        out.update(ok=True, reason="", detail="仅搜索可用")
        return out
    # 2) 目录
    try:
        plan = site.download_plan(book_id) or {}
    except Exception as exc:
        out.update(reason="broken", detail="获取目录失败：%s" % str(exc)[:120])
        return out
    chapters = plan.get("chapters") or []
    if not chapters:
        out.update(reason="empty-book", detail="章节目录为空")
        return out
    out["chapterCount"] = len(chapters)
    # 3) 抽样：首章常是作者说明/插图页（字数很少），因此首章过短时
    # 继续看后续几章；只要有一章正文正常就认为是可用的。
    sample_idx = [0]
    for extra in (1, 2, len(chapters) // 2):
        if 0 < extra < len(chapters) and extra not in sample_idx:
            sample_idx.append(extra)
    blocked = None          # 付费/订阅/登录等硬拦截
    fetched = 0
    good = 0
    short_detail = ""
    for idx in sample_idx:
        ch = chapters[idx]
        try:
            loaded = site.fetch_chapter(book_id, ch) or {}
        except Exception as exc:
            if fetched == 0:
                blocked = ("broken", "抓取第 %d 章失败：%s" % (idx + 1, str(exc)[:100]))
                break
            continue
        content = loaded.get("content") or loaded.get("text") or ""
        if not content and isinstance(loaded.get("paragraphs"), list):
            content = "\n".join(loaded["paragraphs"])
        fetched += 1
        reason, detail = _content_verdict(content)
        if not reason:
            good += 1
            break
        if reason in ("vip", "subscription", "login"):
            blocked = (reason, "第 %d 章：%s" % (idx + 1, detail))
            break
        if not short_detail:
            short_detail = "第 %d 章：%s" % (idx + 1, detail)
    if blocked:
        out.update(reason=blocked[0], detail=blocked[1])
        return out
    if good == 0:
        out.update(reason="incomplete",
                   detail=short_detail or "抽样章节均无有效正文")
        return out
    # 4) 站点声明需要登录：即便当前匿名可读，也标记为需登录，按需求排除
    if site.login_required:
        out.update(reason="login", detail="站点要求登录后阅读")
        return out
    out.update(ok=True, detail="搜索/目录/正文可用（目录 %d 章）" % len(chapters))
    return out


def status():
    with _lock:
        return dict(_running)


def get_results(force=False):
    """返回缓存结果；force=True 时后台刷新（不阻塞请求）。"""
    cache = _load_cache()
    fresh = (time.time() - int(cache.get("at", 0))) < VERIFY_TTL
    if force or not fresh:
        start_verify_async()
    return {"ok": True, "at": cache.get("at", 0),
            "fresh": fresh, "items": cache.get("items", {}),
            "running": status()}


def start_verify_async(keyword="斗破苍穹"):
    with _lock:
        if _running["on"]:
            return False
        _running.update(on=True, started=int(time.time()), total=0, done=0, current="")
    t = threading.Thread(target=_verify_all, args=(keyword,), daemon=True)
    t.start()
    return True


def _verify_all(keyword):
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from concurrent.futures import TimeoutError as FutureTimeout
    keys = site_keys()
    with _lock:
        _running["total"] = len(keys)
    items = dict(_load_cache().get("items", {}))
    pool = ThreadPoolExecutor(max_workers=6)
    futures = {}
    for key in keys:
        futures[pool.submit(_verify_one_safe, key, keyword)] = key
    try:
        for fut in as_completed(futures, timeout=25 * 60):
            key = futures[fut]
            try:
                items[key] = fut.result(timeout=1)
            except Exception as exc:
                items[key] = {"key": key, "ok": False, "reason": "error",
                              "detail": str(exc)[:160], "checkedAt": int(time.time())}
            with _lock:
                _running["done"] += 1
                _running["current"] = key
            _save_cache(items)
    except FutureTimeout:
        pass
    finally:
        # 未在总时限内完成的按超时记录
        for fut, key in futures.items():
            if key in items:
                continue
            items[key] = {"key": key, "ok": False, "reason": "timeout",
                          "detail": "验证超时", "checkedAt": int(time.time())}
        pool.shutdown(wait=False)
    _save_cache(items)
    with _lock:
        _running.update(on=False, current="")


SITE_BUDGET = 75   # 单个书源总预算（秒）


def _verify_one_safe(key, keyword):
    """带硬超时的单源验证：避免某个站点 socket 卡住拖垮整批。"""
    import threading as _th
    box = {}

    def run():
        try:
            box["r"] = verify_site(key, keyword=keyword)
        except Exception as exc:  # noqa: BLE001
            box["r"] = {"key": key, "ok": False, "reason": "error",
                        "detail": str(exc)[:160], "checkedAt": int(time.time())}

    t = _th.Thread(target=run, daemon=True)
    t.start()
    t.join(SITE_BUDGET)
    if "r" not in box:
        return {"key": key, "ok": False, "reason": "timeout",
                "detail": "单源验证超时（%ds）" % SITE_BUDGET,
                "checkedAt": int(time.time())}
    return box["r"]


def usable_keys(min_chapters=1):
    """可用的书源 key 列表（供 novelsrc 整理/过滤）。"""
    items = _load_cache().get("items", {})
    ok = []
    for key, item in items.items():
        if not item.get("ok"):
            continue
        if item.get("chapterCount", 0) < min_chapters:
            continue
        if item.get("loginRequired"):
            continue
        if item.get("reason") in ("vip", "subscription", "login", "incomplete"):
            continue
        ok.append(key)
    return sorted(ok)
