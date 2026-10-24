# -*- coding: utf-8 -*-
"""千千小说网（www.qqxcw.com）—— 由 fetch_novel.ps1 改写为书源。

站点特点：
  * 书籍页 /book/<id>/ 提供 og:novel:* 元信息与 id="list" 章节列表；
  * 搜索是 POST /search.html（字段 s），GET 方式只返回表单页；
  * 正文被切成若干 <script>document.writeln(qsbs.bb('<base64>'))</script>，
    单章还可能分页为 <id>_1.html、<id>_2.html …。

注意：本模块纯 Python 3.8 兼容。
Loshop & Cpt
"""
import base64
import binascii
import re
from urllib.parse import urlsplit

from bs4 import NavigableString, Tag

from ..base import Site, apply_chapter_range, dedup_chapters, register
from ..htmlutil import absolutize, attr, clean_text, find_by_id, normalize_path, parse_html
from ._biquge_common import meta_property

BASE = "http://www.qqxcw.com"

BOOK_RE = re.compile(r"^/book/(\d+)/?$")
CHAPTER_RE = re.compile(r"^/book/(\d+)/(\d+)(?:_(\d+))?\.html$")
B64_RE = re.compile(r"""qsbs\.bb\(\s*['"]([A-Za-z0-9+/=]+)['"]\s*\)""")


def _decode_chunk(value):
    value += "=" * ((4 - len(value) % 4) % 4)
    try:
        return base64.b64decode(value).decode("utf-8", "replace")
    except (binascii.Error, ValueError):
        return ""


def _script_paragraphs(container):
    """script 里 base64 解码后是 <p>…</p>，逐段取出。"""
    parts = []
    for script in container.find_all("script"):
        match = B64_RE.search(script.get_text() or str(script))
        if not match:
            continue
        html = _decode_chunk(match.group(1))
        for piece in re.split(r"(?i)</?p[^>]*>", html):
            piece = clean_text(re.sub(r"<[^>]+>", "", piece))
            if piece:
                parts.append(piece)
    return parts


def _plain_paragraphs(container):
    parts = []
    for child in container.children:
        if isinstance(child, NavigableString):
            text = clean_text(str(child))
            if text:
                parts.append(text)
            continue
        if not isinstance(child, Tag):
            continue
        if child.name in ("script", "style"):
            continue
        if child.name == "br":
            continue
        text = clean_text(child.get_text("\n", strip=True))
        for line in text.split("\n"):
            line = line.strip()
            if line:
                parts.append(line)
    return parts


@register
class QqxcwSite(Site):
    key = "qqxcw"
    display_name = "千千小说网"
    tags = ["简体中文", "转载站"]
    hosts = ["qqxcw.com", "www.qqxcw.com"]

    # ------------------------------------------------------------------
    def resolve_url(self, raw_url):
        raw_url = (raw_url or "").strip()
        if not raw_url:
            return None
        if not raw_url.startswith("http://") and not raw_url.startswith("https://"):
            raw_url = "http://" + raw_url
        parts = urlsplit(raw_url)
        host = (parts.hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        if host != "qqxcw.com":
            return None
        path = parts.path or ""
        m = CHAPTER_RE.match(path)
        if m:
            return {"site": self.key, "book_id": m.group(1), "chapter_id": m.group(2),
                    "canonical": BASE + "/book/%s/%s.html" % (m.group(1), m.group(2))}
        m = BOOK_RE.match(path)
        if m:
            return {"site": self.key, "book_id": m.group(1),
                    "canonical": BASE + "/book/" + m.group(1) + "/"}
        return None

    # ------------------------------------------------------------------
    def download_plan(self, book_id):
        book_id = (book_id or "").strip()
        if not book_id:
            raise ValueError("book id is required")
        url = BASE + "/book/" + book_id + "/"
        doc = parse_html(self._get(url))

        chapters = []
        seen = set()
        box = find_by_id(doc, "list")
        region = box if box is not None else doc
        for a in region.find_all("a"):
            m = CHAPTER_RE.match(normalize_path(attr(a, "href").strip()))
            if not m or m.group(1) != book_id:
                continue
            cid = m.group(2)
            if cid in seen:
                continue
            title = clean_text(a.get_text())
            if not title:
                continue
            seen.add(cid)
            chapters.append({
                "id": cid, "title": title,
                "url": BASE + "/book/" + book_id + "/" + cid + ".html",
                "order": len(chapters) + 1,
            })
        chapters = apply_chapter_range(dedup_chapters(chapters))
        if not chapters:
            raise ValueError("qqxcw chapter list not found")

        title = meta_property(doc, "og:novel:book_name") or meta_property(doc, "og:title")
        return {
            "site": self.key, "id": book_id,
            "title": title or ("千千小说 " + book_id),
            "author": meta_property(doc, "og:novel:author"),
            "description": meta_property(doc, "og:description"),
            "source_url": url,
            "cover_url": absolutize(BASE, meta_property(doc, "og:image")),
            "chapters": chapters,
        }

    # ------------------------------------------------------------------
    def fetch_chapter(self, book_id, chapter):
        book_id = str(book_id or "").strip()
        chapter_id = str((chapter or {}).get("id") or "").strip()
        if not book_id or not chapter_id:
            raise ValueError("qqxcw chapter id is required")
        result = dict(chapter)
        paragraphs = []
        page = 0
        while page < 20:
            if page == 0:
                url = "%s/book/%s/%s.html" % (BASE, book_id, chapter_id)
            else:
                url = "%s/book/%s/%s_%d.html" % (BASE, book_id, chapter_id, page)
            try:
                markup = self._get(url)
            except Exception:
                if page == 0:
                    raise
                break
            doc = parse_html(markup)
            container = find_by_id(doc, "content")
            if container is None:
                if page == 0:
                    raise ValueError("qqxcw chapter content not found")
                break
            block = _script_paragraphs(container)
            if not block:
                block = _plain_paragraphs(container)
            paragraphs.extend(block)
            next_url = _next_url(doc, chapter_id, page)
            if not next_url:
                break
            page += 1
        if not paragraphs:
            raise ValueError("qqxcw chapter content not found")
        result["content"] = "\n".join(paragraphs)
        result["downloaded"] = True
        return result

    # ------------------------------------------------------------------
    def search(self, keyword, limit=0):
        keyword = (keyword or "").strip()
        if not keyword:
            return []
        markup = self.http.post_form(
            BASE + "/search.html", {"s": keyword.encode("utf-8")},
            headers={"Referer": BASE + "/search.html"})
        if "搜索间隔" in markup:
            # 站点限流：20 秒内只允许一次搜索，期间返回「请于 hh:mm:ss 后再进行搜索」。
            # 不在这里休眠等待（聚合搜索有总时限），直接按无结果处理即可。
            return []
        return _parse_search(markup, self.key, limit)

    def _get(self, url):
        return self.http.get(url, headers={"Referer": BASE + "/"})


def _next_url(doc, chapter_id, page):
    """站点把「下一页」也写成 class=next；仍指向本章 <id>_N.html 才继续翻页。"""
    for a in doc.find_all("a", class_="next"):
        href = normalize_path(attr(a, "href").strip())
        m = CHAPTER_RE.match(href)
        if m and m.group(2) == chapter_id:
            return href
    return ""


def _parse_search(markup, site_key, limit=0):
    doc = parse_html(markup)
    results = []
    seen = set()
    for a in doc.find_all("a"):
        m = BOOK_RE.match(normalize_path(attr(a, "href").strip()))
        if not m:
            continue
        book_id = m.group(1)
        if book_id in seen:
            continue
        title = clean_text(a.get_text())
        if not title:
            continue
        seen.add(book_id)
        results.append({
            "site": site_key, "book_id": book_id, "title": title,
            "url": BASE + "/book/" + book_id + "/",
        })
        if limit > 0 and len(results) >= limit:
            break
    return results
