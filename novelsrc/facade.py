# -*- coding: utf-8 -*-
"""novelsrc.facade —— 小说后端的 HTTP 门面。

提供一个 Flask 应用所需的 /novel/api/* 路由（接口形态沿用 go-novel-dl，
供 OS「小说下载」应用直接使用），聚合 novelsrc 移植的全部中文书源。
纯 Python 3.8（Windows 7 可用）。
"""
import io
import os
import re
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FutureTimeout

from flask import Response, jsonify, request, send_file

from .base import get_site, site_keys

API_VERSION = "py-1.0.0"

# 聚合搜索的总时限（秒）：超时即返回已到结果，避免个别站点不可达拖死整次请求
SEARCH_DEADLINE = 15.0

_TITLE_WS = re.compile(r"[\s\u3000·・:：,，.。!！?？\-—_()（）\[\]【】]+")

_EXPORT_DIR = None
_TASKS = {}
_TASKS_LOCK = threading.Lock()


def export_dir():
    global _EXPORT_DIR
    if _EXPORT_DIR is None:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _EXPORT_DIR = os.environ.get("NOVEL_EXPORT_DIR") or os.path.join(root, "novel_exports")
        os.makedirs(_EXPORT_DIR, exist_ok=True)
    return _EXPORT_DIR


def norm_title(value):
    return _TITLE_WS.sub("", (value or "").strip().lower())


# ---------------------------------------------------------------------------
# 书源清单
# ---------------------------------------------------------------------------
def default_sources():
    items = []
    for key in site_keys():
        site = get_site(key)
        if site is None or not site.searchable or not site.downloadable:
            continue
        items.append(site.descriptor())
    return items


# ---------------------------------------------------------------------------
# 聚合搜索
# ---------------------------------------------------------------------------
# 单站搜索结果缓存：多数站点有频控（同一关键词短时间内重复请求返回空），
# 缓存既能避免把自己打限流，也能让“翻页/重试/多端同时搜”秒回。
_SEARCH_CACHE = {}
_SEARCH_CACHE_TTL = 600          # 10 分钟
_SEARCH_CACHE_MAX = 400
_search_cache_lock = threading.Lock()


def _cache_key(site_key_, keyword):
    return (site_key_, norm_title(keyword) or keyword.strip().lower())


def _search_cache_get(site_key_, keyword):
    k = _cache_key(site_key_, keyword)
    now = time.time()
    with _search_cache_lock:
        hit = _SEARCH_CACHE.get(k)
        if hit and hit[0] > now:
            return list(hit[1])
        if hit:
            _SEARCH_CACHE.pop(k, None)
    return None


def _search_cache_put(site_key_, keyword, items):
    k = _cache_key(site_key_, keyword)
    with _search_cache_lock:
        if len(_SEARCH_CACHE) >= _SEARCH_CACHE_MAX:
            _SEARCH_CACHE.clear()
        _SEARCH_CACHE[k] = (time.time() + _SEARCH_CACHE_TTL, list(items))


def _search_one(site_key_, keyword, limit):
    site = get_site(site_key_)
    if site is None:
        return site_key_, []
    cached = _search_cache_get(site_key_, keyword)
    if cached is not None:
        return site_key_, cached
    try:
        items = site.search(keyword, limit=limit)
    except Exception:
        return site_key_, []
    # 只有拿到结果才缓存；空结果可能是被频控，不能缓存成“没这本书”
    if items:
        _search_cache_put(site_key_, keyword, items)
    return site_key_, items


def _group_rows(rows):
    """按规范化标题归并，同书多源收进 variants，保持首次出现顺序。"""
    groups = {}
    order = []
    for row in rows:
        nk = norm_title(row.get("title"))
        if not nk:
            continue
        if nk not in groups:
            groups[nk] = []
            order.append(nk)
        groups[nk].append(row)
    return groups, order


def _build_results(groups, order):
    """把归并结果整理成前端结构（含 variants 与汇总字段）。"""
    results = []
    for nk in order:
        variants = groups[nk]
        primary = variants[0]
        author = ""
        latest = ""
        desc = ""
        for v in variants:
            if not author and v.get("author"):
                author = v["author"]
            if not latest and v.get("latest_chapter"):
                latest = v["latest_chapter"]
            if not desc and v.get("description"):
                desc = v["description"]
        results.append({
            "title": primary.get("title", ""),
            "author": author,
            "description": desc,
            "latest_chapter": latest,
            "source_count": len(variants),
            "primary": primary,
            "variants": variants,
        })
    return results


def _rank_results(results, keyword, overall_limit):
    """按关键词匹配度排序并截断。"""
    kw_norm = norm_title(keyword)

    def score(item):
        t = norm_title(item.get("title"))
        a = norm_title(item.get("author"))
        if not kw_norm:
            return (0, 0, 0)
        if t == kw_norm:
            base = 100
        elif t.startswith(kw_norm):
            base = 80
        elif kw_norm in t:
            base = 60
        elif a == kw_norm:
            base = 40
        elif kw_norm in a:
            base = 30
        else:
            hit = sum(1 for ch in set(kw_norm) if ch in t)
            base = int(20 * hit / max(1, len(set(kw_norm))))
        return (base, item.get("source_count", 0), -len(item.get("title", "")))

    if kw_norm:
        results.sort(key=score, reverse=True)
        results = [r for r in results if score(r)[0] >= 20]
    else:
        results.sort(key=lambda r: (-r.get("source_count", 0), len(r.get("title", ""))))
    if overall_limit > 0:
        results = results[:overall_limit]
    return results


def hybrid_search(keyword, keys, per_site_limit, overall_limit):
    rows = []
    hit_keys = []
    if keys:
        pool = ThreadPoolExecutor(max_workers=min(8, max(1, len(keys))))
        try:
            futures = {pool.submit(_search_one, k, keyword, per_site_limit): k for k in keys}
            # 硬性总时限：个别站点长时间不可达时不拖死整次搜索，先返回已到的结果
            try:
                for fut in as_completed(futures, timeout=SEARCH_DEADLINE):
                    try:
                        key, items = fut.result()
                    except Exception:
                        continue
                    if items:
                        hit_keys.append(key)
                        rows.extend(items)
            except FutureTimeout:
                pass
        finally:
            pool.shutdown(wait=False)

    groups, order = _group_rows(rows)
    results = _rank_results(_build_results(groups, order), keyword, overall_limit)
    # 为每个可下载源补充章节数（并发、限时），供前端“选择下载源”展示。
    _enrich_variants(results)
    return results, sorted(set(hit_keys))


def _enrich_variants(results, max_books=12, budget=6.0):
    """给结果里的每个源补 chapter_count / latest_chapter。

    只处理前 max_books 个结果，总耗时限 budget 秒，避免拖慢搜索。
    """
    targets = []
    for item in results[:max_books]:
        for v in item.get("variants") or []:
            if v.get("chapter_count") is None:
                targets.append(v)
    if not targets:
        return

    def one(v):
        try:
            site = get_site(v.get("site"))
            if site is None or not site.downloadable:
                v["chapter_count"] = 0
                return
            plan = site.download_plan(v.get("book_id"))
            chs = plan.get("chapters") or []
            v["chapter_count"] = len(chs)
            if chs and not v.get("latest_chapter"):
                v["latest_chapter"] = chs[-1].get("title") or ""
        except Exception:
            v["chapter_count"] = -1

    pool = ThreadPoolExecutor(max_workers=6)
    deadline = time.time() + budget
    try:
        futs = [pool.submit(one, v) for v in targets]
        for f in futs:
            remain = deadline - time.time()
            if remain <= 0:
                break
            try:
                f.result(timeout=remain)
            except Exception:
                # 超时或失败：标记未知，不阻塞整体搜索
                pass
    except Exception:
        pass
    finally:
        # wait=False 不等待卡住的线程；它们完成后会自行退出
        pool.shutdown(wait=False)


def stream_search(keyword, keys, per_site_limit, overall_limit):
    """流式聚合搜索：每个站点返回时立即算一次快照并推送。

    前端可先展示部分结果，不必等到最慢的站点（最长 SEARCH_DEADLINE）。
    """
    import json as _json
    rows, hit_keys = [], []
    seen_titles = set()

    def frame(obj):
        return "data: %s\n\n" % _json.dumps(obj, ensure_ascii=False)

    if not keys:
        yield frame({"type": "done", "total": 0})
        return
    pool = ThreadPoolExecutor(max_workers=min(8, max(1, len(keys))))
    try:
        futures = {pool.submit(_search_one, k, keyword, per_site_limit): k for k in keys}
        try:
            for fut in as_completed(futures, timeout=SEARCH_DEADLINE):
                try:
                    key, items = fut.result()
                except Exception:
                    continue
                if not items:
                    continue
                hit_keys.append(key)
                rows.extend(items)
                groups, order = _group_rows(rows)
                partial = _rank_results(_build_results(groups, order), keyword, overall_limit)
                # 只推增量：新出现的标题（按规范化标题去重）
                fresh = []
                for r in partial:
                    nk = norm_title(r.get("title"))
                    if nk and nk not in seen_titles:
                        seen_titles.add(nk)
                        fresh.append(r)
                yield frame({"type": "partial", "site": key,
                             "items": fresh, "total": len(partial),
                             "hitSites": sorted(set(hit_keys))})
        except FutureTimeout:
            pass
    finally:
        pool.shutdown(wait=False)
    groups, order = _group_rows(rows)
    final = _rank_results(_build_results(groups, order), keyword, overall_limit)
    _enrich_variants(final)
    yield frame({"type": "done", "items": final, "total": len(final),
                 "hitSites": sorted(set(hit_keys))})


def paginate(results, page, page_size):
    total = len(results)
    start = max(0, (page - 1) * page_size)
    return {
        "results": results[start:start + page_size],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_exact": True,
        "has_prev": page > 1,
        "has_next": start + page_size < total,
    }


# ---------------------------------------------------------------------------
# 导出任务
# ---------------------------------------------------------------------------
def _new_task(site_key_, book_id, title, formats):
    task = {
        "id": uuid.uuid4().hex[:12],
        "site": site_key_,
        "book_id": book_id,
        "title": title or book_id,
        "status": "queued",
        "formats": formats or ["txt"],
        "total_chapters": 0,
        "completed_chapters": 0,
        "phase": "",
        "current_chapter": "",
        "eta": "",
        "speed": "",
        "messages": [],
        "exported": [],
    }
    with _TASKS_LOCK:
        _TASKS[task["id"]] = task
    return task


def _update(task_id, **fields):
    with _TASKS_LOCK:
        task = _TASKS.get(task_id)
        if task:
            task.update(fields)


def _append_msg(task_id, message):
    with _TASKS_LOCK:
        task = _TASKS.get(task_id)
        if task:
            task.setdefault("messages", []).append(message)


def _safe_name(name):
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name or "novel").strip(" .")
    return name[:80] or "novel"


_CH_TITLE_RE = re.compile(
    r"^\s*(?:第\s*)?(\d+)\s*(?:章|节|回|话)?\s*[:：.、\-—]?\s*(.*)$")


def normalize_chapter_title(title, index):
    """把各种站点的章节标题统一成「第N章 标题」的形式。

    很多站点的标题是「123」纯数字、「第123章」、「正文 第123章 xxx」
    甚至空白。统一后阅读器和 TXT 目录可读性一致。
    """
    raw = re.sub(r"\s+", " ", str(title or "")).strip()
    if not raw:
        return "第%d章" % index
    # 去掉常见前缀噪音
    raw = re.sub(r"^(?:正文|章节目录|目录)[\s:：]*", "", raw).strip()
    m = _CH_TITLE_RE.match(raw)
    if m:
        num = int(m.group(1))
        rest = (m.group(2) or "").strip(" ：:.-、—")
        return ("第%d章 %s" % (num, rest)).strip() if rest else "第%d章" % num
    # 「第十章 / 第二十七章」：把中文数字转成阿拉伯数字并统一分隔符，
    # 否则各站标题混用「第一章，」「第1章 」难以检索与排序。
    m2 = re.match(r"^第([0-9一二三四五六七八九十百千零两]+)[章节回]\s*[，,、:：.。\-—]?\s*(.*)$", raw)
    if m2:
        num = _cn_to_int(m2.group(1))
        rest = (m2.group(2) or "").strip(" ，,、:：.。-—")
        if num > 0:
            return ("第%d章 %s" % (num, rest)).strip() if rest else "第%d章" % num
        return raw
    return "第%d章 %s" % (index, raw)


_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}


def _cn_to_int(text):
    """中文数字 -> int（支持 1-9999）。非中文数字返回 -1。"""
    text = str(text or "").strip()
    if not text:
        return -1
    if text.isdigit():
        try:
            return int(text)
        except ValueError:
            return -1
    total, section, number = 0, 0, 0
    for ch in text:
        if ch in _CN_DIGITS:
            number = _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            unit = _CN_UNITS[ch]
            if number == 0:
                number = 1
            section += number * unit
            number = 0
        else:
            return -1
    return total + section + number


def _build_txt(book):
    out = io.StringIO()
    out.write(book.get("title") or "")
    out.write("\n")
    if book.get("author"):
        out.write("作者：%s\n" % book["author"])
    if book.get("source_url"):
        out.write("来源：%s\n" % book["source_url"])
    out.write("\n")
    for i, ch in enumerate(book.get("chapters") or []):
        title = normalize_chapter_title(ch.get("title"), i + 1)
        out.write("\n%s\n\n" % title)
        out.write((ch.get("content") or "") + "\n")
    return out.getvalue()


def _build_epub(book):
    from ebooklib import epub  # noqa: WPS433
    import html as _html

    eb = epub.EpubBook()
    eb.set_identifier(book.get("source_url") or book.get("id") or "novel")
    eb.set_title(book.get("title") or "Novel")
    eb.set_language("zh")
    if book.get("author"):
        eb.add_author(book["author"])
    chapters = book.get("chapters") or []
    spine = ["nav"]
    toc = []
    for i, ch in enumerate(chapters):
        item = epub.EpubHtml(title=normalize_chapter_title(ch.get("title"), i + 1),
                             file_name="chap_%04d.xhtml" % i, lang="zh")
        body = "".join("<p>%s</p>" % _html.escape(line)
                       for line in (ch.get("content") or "").split("\n") if line.strip())
        item.content = "<h2>%s</h2>%s" % (
            _html.escape(normalize_chapter_title(ch.get("title"), i + 1)),
            body or "<p></p>")
        eb.add_item(item)
        spine.append(item)
        toc.append(item)
    eb.toc = toc
    eb.spine = spine
    eb.add_item(epub.EpubNcx())
    eb.add_item(epub.EpubNav())
    buf = io.BytesIO()
    epub.write_epub(buf, eb, {})
    return buf.getvalue()


def _run_export(task_id, site_key_, book_id, formats, title_hint=""):
    _update(task_id, status="running", phase="获取书目")
    try:
        site = get_site(site_key_)
        if site is None:
            raise ValueError("未知书源：%s" % site_key_)
        book = site.download_plan(book_id)
        chapters = book.get("chapters") or []
        if title_hint and not book.get("title"):
            book["title"] = title_hint
        _update(task_id, title=book.get("title") or book.get("id") or book_id,
                total_chapters=len(chapters), phase="抓取正文")

        # 并发抓正文：按章节分片多线程拉取，失败章节重试 2 轮，
        # 最后做完整性校验（空正文/过短章节），仍失败才标记缺失。
        def is_permanent(msg):
            """VIP/登录/无权限类错误：重试也不会成功，必须快速放弃，
            否则整本下载会被几十个必然失败的章节拖到几十分钟。"""
            m = str(msg or "").lower()
            return any(k in m for k in ("vip", "login", "requires", "not found",
                                        "no permission", "订阅", "付费"))

        def fetch_one(idx_ch):
            idx, ch = idx_ch
            last = None
            for attempt in range(3):
                try:
                    loaded = site.fetch_chapter(book_id, ch)
                    content = (loaded.get("content") or "").strip()
                    if len(content) >= 30:
                        return idx, loaded, None
                    last = "正文过短(%d字)" % len(content)
                except Exception as exc:
                    last = str(exc)[:120]
                    if is_permanent(last):
                        return idx, None, last
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
            return idx, None, last

        workers = int(os.environ.get("NOVEL_WORKERS") or 4)
        workers = max(1, min(8, workers))
        done = 0
        failed = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(fetch_one, (i, ch)) for i, ch in enumerate(chapters)]
            for fut in as_completed(futures):
                try:
                    idx, loaded, err = fut.result()
                except Exception as exc:
                    continue
                done += 1
                if loaded is not None:
                    loaded["order"] = idx + 1
                    loaded.setdefault("title", chapters[idx].get("title"))
                    chapters[idx] = loaded
                else:
                    failed.append((idx, err))
                    # 保留目录信息但标记失败，导出时写入占位说明
                    chapters[idx] = dict(chapters[idx])
                    chapters[idx]["content"] = chapters[idx].get("content") or ""
                    chapters[idx]["failed"] = err or "抓取失败"
                _update(task_id, completed_chapters=done,
                        current_chapter=(loaded or {}).get("title")
                        or chapters[idx].get("title") or "")
        # 排序回目录顺序并标记缺失
        chapters.sort(key=lambda c: c.get("order") or 0)
        missing = [i for i, c in enumerate(chapters) if c.get("failed")]
        if missing:
            _append_msg(task_id, "有 %d 章内容缺失（已重试）：%s" % (
                len(missing), ", ".join("第%d章" % (i + 1) for i in missing[:10])))
        book["chapters"] = chapters

        _update(task_id, phase="写入文件")
        exported = []
        base = _safe_name(book.get("title") or book_id)
        for fmt in (formats or ["txt"]):
            fmt = (fmt or "").lower()
            if fmt == "epub":
                path = os.path.join(export_dir(), "%s.epub" % base)
                with open(path, "wb") as fp:
                    fp.write(_build_epub(book))
            else:
                path = os.path.join(export_dir(), "%s.txt" % base)
                with open(path, "w", encoding="utf-8") as fp:
                    fp.write(_build_txt(book))
            exported.append(path)
        _update(task_id, status="completed", phase="完成", exported=exported,
                completed_chapters=len(chapters))
    except Exception as exc:
        _append_msg(task_id, str(exc))
        _update(task_id, status="failed", phase="失败")
        traceback.print_exc()


# ---------------------------------------------------------------------------
# 路由注册
# ---------------------------------------------------------------------------
def register_routes(app):
    @app.route("/novel/api/healthz")
    def _novel_healthz():
        return jsonify({"status": "ok"})

    @app.route("/novel/api/meta")
    def _novel_meta():
        try:
            sources = default_sources()
        except Exception:
            sources = []
        return jsonify({
            "default_sources": sources,
            "all_sources": sources,
            "site_warnings": [],
            "site_stats": [],
            "general_config": {},
            "version": {"current": API_VERSION},
        })

    @app.route("/novel/api/version")
    def _novel_version():
        return jsonify({"current": API_VERSION})

    @app.route("/novel/api/search", methods=["POST"])
    def _novel_search():
        body = request.get_json(silent=True) or {}
        keyword = (body.get("keyword") or "").strip()
        if not keyword:
            return jsonify({"error": "keyword is required"}), 400
        page = int(body.get("page") or 1)
        page_size = int(body.get("page_size") or 20)
        limit = int(body.get("limit") or 0)
        site_limit = int(body.get("site_limit") or 0)
        keys = [k for k in (body.get("sites") or []) if k]
        if not keys:
            keys = [s["key"] for s in default_sources()]
        fetch_limit = page * page_size + 1
        per_site = site_limit if site_limit >= fetch_limit else fetch_limit
        overall = limit if limit > fetch_limit else fetch_limit
        try:
            results, hits = hybrid_search(keyword, keys, per_site, overall)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400
        payload = paginate(results, page, page_size)
        payload["sites"] = hits
        return jsonify(payload)

    @app.route("/novel/api/search-stream", methods=["POST"])
    def _novel_search_stream():
        """流式搜索（SSE）：结果边到边推，前端可先渲染。"""
        body = request.get_json(silent=True) or {}
        keyword = (body.get("keyword") or "").strip()
        if not keyword:
            return jsonify({"error": "keyword is required"}), 400
        keys = [k for k in (body.get("sites") or []) if k]
        if not keys:
            keys = [s["key"] for s in default_sources()]
        per_site = int(body.get("site_limit") or 8)
        overall = int(body.get("limit") or 20)
        gen = stream_search(keyword, keys, per_site, overall)
        resp = Response(gen, mimetype="text/event-stream")
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Accel-Buffering"] = "no"
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp

    @app.route("/novel/api/books/detail")
    def _novel_detail():
        site_key_ = (request.args.get("site") or "").strip()
        book_id = (request.args.get("book_id") or "").strip()
        if not site_key_ or not book_id:
            return jsonify({"error": "site and book_id are required"}), 400
        site = get_site(site_key_)
        if site is None:
            return jsonify({"error": "unknown site: %s" % site_key_}), 400
        try:
            book = site.download_plan(book_id)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

        chapters = book.get("chapters") or []
        try:
            page = int(request.args.get("chapter_page") or 1)
            page_size = int(request.args.get("chapter_page_size") or 200)
        except ValueError:
            page, page_size = 1, 200
        page = max(1, page)
        page_size = max(1, min(page_size, 1000))
        start = (page - 1) * page_size
        total = len(chapters)
        slim = [{"id": c.get("id"), "title": c.get("title"), "url": c.get("url", ""),
                 "volume": c.get("volume", ""), "order": c.get("order", i + 1)}
                for i, c in enumerate(chapters[start:start + page_size])]
        out = dict(book)
        out["chapters"] = slim
        return jsonify({
            "book": out,
            "chapter_page": {
                "page": page, "page_size": page_size, "total": total,
                "has_prev": page > 1, "has_next": start + page_size < total,
            },
        })

    @app.route("/novel/api/chapter-content")
    def _novel_chapter():
        site_key_ = (request.args.get("site") or "").strip()
        book_id = (request.args.get("book_id") or "").strip()
        chapter_id = (request.args.get("chapter_id") or "").strip()
        title = (request.args.get("title") or "").strip()
        url = (request.args.get("url") or "").strip()
        if not site_key_ or not book_id or not (chapter_id or title or url):
            return jsonify({"error": "site, book_id, and chapter identity are required"}), 400
        site = get_site(site_key_)
        if site is None:
            return jsonify({"error": "unknown site: %s" % site_key_}), 400
        chapter = {"id": chapter_id, "title": title, "url": url or chapter_id}
        try:
            got = site.fetch_chapter(book_id, chapter)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"chapter": got})

    @app.route("/novel/api/download-tasks", methods=["POST"])
    def _novel_task_create():
        body = request.get_json(silent=True) or {}
        site_key_ = (body.get("site") or "").strip()
        book_id = (body.get("book_id") or "").strip()
        if not site_key_ or not book_id:
            return jsonify({"error": "site and book_id are required"}), 400
        formats = body.get("formats") or ["txt"]
        task = _new_task(site_key_, book_id, body.get("title") or "", formats)
        thread = threading.Thread(
            target=_run_export,
            args=(task["id"], site_key_, book_id, formats, body.get("title") or ""),
            daemon=True)
        thread.start()
        return jsonify({"task": task}), 202

    @app.route("/novel/api/download-tasks", methods=["GET"])
    def _novel_task_list():
        with _TASKS_LOCK:
            tasks = list(_TASKS.values())
        return jsonify({"tasks": tasks})

    @app.route("/novel/api/download-tasks/<task_id>", methods=["GET"])
    def _novel_task_get(task_id):
        with _TASKS_LOCK:
            task = _TASKS.get(task_id)
        if not task:
            return jsonify({"error": "task not found"}), 404
        return jsonify({"task": task})

    @app.route("/novel/api/download-tasks/<task_id>", methods=["DELETE"])
    def _novel_task_delete(task_id):
        with _TASKS_LOCK:
            existed = _TASKS.pop(task_id, None)
        if not existed:
            return jsonify({"error": "task not found"}), 404
        return jsonify({"deleted": task_id})

    # ---- 书源可用性验证 ----
    @app.route("/novel/api/source-verify", methods=["GET", "POST"])
    def _novel_source_verify():
        """返回验证结果；?force=1 触发后台刷新（不阻塞）。"""
        force = str(request.args.get("force") or "").lower() in ("1", "true", "yes")
        try:
            from .verify import get_results
            return jsonify(get_results(force=force))
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    @app.route("/novel/api/source-usable")
    def _novel_source_usable():
        try:
            from .verify import usable_keys
            return jsonify({"ok": True, "keys": usable_keys()})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    @app.route("/novel/api/source-catalog")
    def _novel_source_catalog():
        """本地书源整理清单（含可用性状态）。"""
        try:
            from .discover import local_catalog
            return jsonify(local_catalog())
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    @app.route("/novel/api/source-discover", methods=["GET", "POST"])
    def _novel_source_discover():
        """公开书源索引比对（网络不可达时返回 error，不阻塞）。"""
        try:
            from .discover import get_index
            force = str(request.args.get("force") or "").lower() in ("1", "true")
            return jsonify(get_index(force=force))
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    @app.route("/novel/api/download-file")
    def _novel_download_file():
        path = (request.args.get("path") or "").strip()
        if not path:
            return jsonify({"error": "path is required"}), 400
        abs_path = os.path.abspath(path)
        root = os.path.abspath(export_dir())
        if not abs_path.startswith(root):
            return jsonify({"error": "path is not allowed"}), 403
        if not os.path.isfile(abs_path):
            return jsonify({"error": "file not found"}), 404
        return send_file(abs_path, as_attachment=True,
                         download_name=os.path.basename(abs_path))

    return app