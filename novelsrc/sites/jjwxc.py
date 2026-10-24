# -*- coding: utf-8 -*-
"""晋江文学城手机版（m.jjwxc.com）—— 由 dl_jjwxc.ps1 改写为书源。

站点响应多为 GB18030，个别页面（含搜索页）会 UTF-8 / GB18030 混排，
所以按标签切段、逐段嗅探编码后再解析。
取书方式：贴 m.jjwxc.com/book2/<书号> 链接（站内搜索已强制登入，故不对聚合搜索开放）。

注意：本模块纯 Python 3.8 兼容。
Loshop & Cpt
"""
import base64
import binascii
import re
from html import unescape
from urllib.parse import urlsplit

from ..base import Site, apply_chapter_range, dedup_chapters, register
from ..htmlutil import (absolutize, attr, clean_text, find_by_id, find_first,
                        node_text, parse_html, strip_fragment)
from ..http import decode_bytes

BASE = "https://m.jjwxc.com"
MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/604.1")

BOOK_PATH_RE = re.compile(r"^/book2/(\d+)/?$")
CHAPTER_PATH_RE = re.compile(r"^/book2/(\d+)/(\d+)/?$")

TITLE_RE = re.compile(r"<title>\s*《([^》]+)》([^_<]+)_", re.S)
CATALOG_ITEM_RE = re.compile(r'href="/book2/(\d+)/(\d+)"[^>]*>(.*?)</a>', re.S)
TAG_RE = re.compile(r"<[^>]+>")
MD5_RE = re.compile(r"^[0-9a-f]{32}$")


# ---------------------------------------------------------------------------
# 编码：逐段 utf-8 / gb18030
# ---------------------------------------------------------------------------
_TAG_BYTES_RE = re.compile(rb"<[^>]*>")


_GBK_ONLY_RE = re.compile("[\u3000-\u303f\uff00-\uffef\u2010-\u201f\u00b7\u2014\u2026]")


def _decode_span(span):
    try:
        text = span.decode("utf-8")
    except UnicodeDecodeError:
        return span.decode("gb18030", "replace")
    if any(u"\u4e00" <= ch <= u"\u9fff" for ch in text):
        # 「鍒诲湪」这类错码也能被 UTF-8 解开，但解出的字全是 GBK 双字节不存在的
        # 全角/标点字符（「诲」属 CJK 扩展区），说明这段其实是 GB18030。
        if _GBK_ONLY_RE.search(text):
            return span.decode("gb18030", "replace")
        return text
    return span.decode("gb18030", "replace")


def decode_mixed(raw):
    """按标签切段解码：标签外的文本段先试 UTF-8（含汉字即采用），否则 GB18030。"""
    out = []
    pos = 0
    for match in _TAG_BYTES_RE.finditer(raw):
        out.append(_decode_span(raw[pos:match.start()]))
        out.append(match.group(0).decode("utf-8", "replace"))
        pos = match.end()
    out.append(_decode_span(raw[pos:]))
    return "".join(out)


def _fetch_mixed(site, url):
    """按站点规则解码：目录/正文用响应头 charset，搜索页用混合解码。"""
    resp = site.http.request("GET", url, headers={"User-Agent": MOBILE_UA})
    raw = resp.content
    content_type = resp.headers.get("Content-Type", "")
    charset = ""
    if "charset" in content_type.lower():
        charset = content_type.lower().split("charset=")[-1].split(";")[0].strip()
    if charset in ("gb18030", "gbk", "gb2312"):
        return raw.decode("gb18030", "replace")
    text = decode_mixed(raw)
    if "/search" in url:
        return text
    return decode_bytes(raw, content_type)


# ---------------------------------------------------------------------------
# 正文：script 里的 base64 片段 + 可见文本
# ---------------------------------------------------------------------------
B64_SCRIPT_RE = re.compile(
    r"""<script[^>]*>\s*(?:document\.writeln\s*\(\s*)?(?:qsbs\.bb|_bb)\s*\(\s*['"]([A-Za-z0-9+/=_{}]+)['"]""",
    re.I)
BASE64_CHARS_RE = re.compile(r"[^A-Za-z0-9+/=]")


def _decode_b64(value):
    value = BASE64_CHARS_RE.sub("", value or "")
    if not value:
        return ""
    value += "=" * ((4 - len(value) % 4) % 4)
    try:
        return base64.b64decode(value).decode("utf-8", "replace")
    except (binascii.Error, ValueError):
        return ""


def _html_to_paragraphs(html):
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    html = re.sub(r"(?i)</(p|div|li)>", "\n", html)
    html = TAG_RE.sub("", html)
    html = unescape(html).replace("\xa0", " ").replace("\u3000", " ")
    lines = []
    for line in html.split("\n"):
        line = clean_text(line)
        if line:
            lines.append(line)
    return lines


def _extract_paragraphs(markup):
    """正文按 GB18030 解码；若给了 base64 片段（JS 渲染站点），则用片段解码后的 HTML。"""
    b64_hits = [m.group(1) for m in B64_SCRIPT_RE.finditer(markup)]
    paragraphs = []
    for chunk in b64_hits:
        paragraphs.extend(_html_to_paragraphs(_decode_b64(chunk)))
    if paragraphs:
        return paragraphs

    start = markup.find('class="content_ul"')
    if start < 0:
        return []
    start = markup.find(">", start)
    if start < 0:
        return []
    end = markup.find("</ul>", start)
    if end < 0:
        end = len(markup)
    return _html_to_paragraphs(markup[start + 1:end])


def _extract_note(markup):
    start = markup.find('id="note_str"')
    if start < 0:
        return ""
    start = markup.find(">", start)
    end = markup.find("</script>", start)
    if start < 0 or end < 0:
        return ""
    return "\n".join(_html_to_paragraphs(markup[start + 1:end]))


@register
class JjwxcSite(Site):
    key = "jjwxc"
    display_name = "晋江文学城"
    tags = ["简体中文", "言情", "原创"]
    hosts = ["jjwxc.com", "m.jjwxc.com", "www.jjwxc.net"]
    timeout = 30.0
    # 搜索结果页反复要求登入（只给登入页），站内搜索暂不可用；
    # 仍可用「贴书籍链接 / 直接下载」的方式取书。
    searchable = False

    # ------------------------------------------------------------------
    def resolve_url(self, raw_url):
        raw_url = (raw_url or "").strip()
        if not raw_url:
            return None
        if not raw_url.startswith("http://") and not raw_url.startswith("https://"):
            raw_url = "https://" + raw_url
        parts = urlsplit(raw_url)
        host = (parts.hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        if host not in ("jjwxc.com", "jjwxc.net"):
            return None
        path = parts.path or ""
        m = CHAPTER_PATH_RE.match(path)
        if m:
            return {"site": self.key, "book_id": m.group(1), "chapter_id": m.group(2),
                    "canonical": BASE + "/book2/%s/%s" % (m.group(1), m.group(2))}
        m = BOOK_PATH_RE.match(path)
        if m:
            return {"site": self.key, "book_id": m.group(1),
                    "canonical": BASE + "/book2/" + m.group(1)}
        return None

    # ------------------------------------------------------------------
    def download_plan(self, book_id):
        book_id = (book_id or "").strip()
        if not book_id:
            raise ValueError("book id is required")
        markup = _fetch_mixed(self, BASE + "/book2/" + book_id + "?more=0&whole=1")
        page_url = BASE + "/book2/" + book_id

        title = ""
        author = ""
        m = TITLE_RE.search(markup)
        if m:
            title = clean_text(m.group(1))
            author = clean_text(m.group(2))

        chapters = []
        box_at = markup.find('id="chapter_list_box"')
        region = markup[box_at:] if box_at >= 0 else markup
        seen = set()
        for match in CATALOG_ITEM_RE.finditer(region):
            if match.group(1) != book_id:
                continue
            cid = match.group(2)
            if cid in seen:
                continue
            seen.add(cid)
            chapters.append({
                "id": cid,
                "title": _clean_chapter_title(match.group(3)),
                "url": BASE + "/book2/" + book_id + "/" + cid,
                "order": len(chapters) + 1,
            })
        chapters = apply_chapter_range(dedup_chapters(chapters))
        if not chapters:
            raise ValueError("jjwxc chapter list not found")

        doc = parse_html(markup)
        description = _meta_content(doc, "og:description") or _meta_content(doc, "description")
        cover = _meta_content(doc, "og:image")
        return {
            "site": self.key, "id": book_id, "title": title or ("晋江作品 " + book_id),
            "author": author, "description": description,
            "source_url": page_url, "cover_url": absolutize(BASE, cover),
            "chapters": chapters,
        }

    # ------------------------------------------------------------------
    def fetch_chapter(self, book_id, chapter):
        book_id = str(book_id or "").strip()
        chapter_id = str((chapter or {}).get("id") or "").strip()
        if not book_id or not chapter_id:
            raise ValueError("jjwxc chapter id is required")
        url = BASE + "/book2/" + book_id + "/" + chapter_id
        markup = _fetch_mixed(self, url)
        result = dict(chapter)

        m = re.search(r"<h2[^>]*>(.*?)</h2>", markup, re.S)
        if m:
            heading = clean_text(TAG_RE.sub("", m.group(1)))
            if heading:
                result["title"] = heading.split(" ...")[0].strip()

        paragraphs = _extract_paragraphs(markup)
        if not paragraphs:
            if "VIP" in markup or "购买" in markup:
                raise ValueError("jjwxc vip chapter is not accessible")
            raise ValueError("jjwxc chapter content not found")
        note = _extract_note(markup)
        if note:
            paragraphs.append("")
            paragraphs.append("【作者有话说】")
            paragraphs.extend(note.split("\n"))
        result["content"] = "\n".join(paragraphs)
        result["downloaded"] = True
        return result

    # ------------------------------------------------------------------
    def search(self, keyword, limit=0):
        """搜索页目前强制跳登入页，站内搜索不可用（见类属性 searchable）。

        这里返回空列表是刻意的：聚合搜索会把该源当「无结果」，不会报错；
        取书请用 m.jjwxc.com/book2/<书号> 链接直接下载。
        """
        return []

    def _get(self, url):
        return _fetch_mixed(self, url)


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _meta_content(doc, name):
    for meta in doc.find_all("meta"):
        key = attr(meta, "property") or attr(meta, "name")
        if key == name:
            return attr(meta, "content").strip()
    return ""


def _clean_chapter_title(raw):
    text = unescape(TAG_RE.sub("", raw or "")).replace("\xa0", " ")
    text = re.sub(r"\[[^\]]*更新\]", "", text)
    text = re.sub(r"\s*\.{2,}\s*$", "", text)
    return clean_text(text)
