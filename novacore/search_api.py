# -*- coding: utf-8 -*-
"""对话式搜索助手后端：联网搜索 + 可选云端 AI 整理。

设计要点：
- 搜索由电脑执行（平板只调同源 api/search），复用 requests 与既有网络能力；
- 搜索源按顺序故障切换：DuckDuckGo HTML / Bing HTML / 百度 HTML；
- 云端 AI 兼容 OpenAI Chat Completions 协议，默认关闭，需在设置里填
  Base URL、API Key、模型名；回答用简短系统提示 + max_tokens 限额；
- 每天调用次数与每次 token 都有上限，避免误用烧钱；
- AI 不可用时回退本地规则摘要，保证功能仍可用。
"""
import html
import json
import re
import threading
import time
from urllib.parse import quote, urlparse, unquote

import requests

from novacore.configutil import cfg

# ----------------------------- 限额与常量 -----------------------------
MAX_QUERY_LEN = 400
MAX_SOURCES = 8
AI_TIMEOUT = 45
SEARCH_TIMEOUT = (8, 15)
DEFAULT_AI_MAX_TOKENS = 400

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 进程内每日计数器：{YYYYMMDD: {used: n, tokens: n}}
_usage_lock = threading.Lock()
_usage = {}


def _today():
    return time.strftime("%Y%m%d")


def _usage_snapshot():
    with _usage_lock:
        day = _today()
        item = _usage.get(day) or {"used": 0, "tokens": 0}
        return day, dict(item)


def _bump_usage(tokens):
    with _usage_lock:
        day = _today()
        item = _usage.setdefault(day, {"used": 0, "tokens": 0})
        item["used"] += 1
        item["tokens"] += int(tokens or 0)
        # 只保留当天，避免长期运行无限增长
        for k in list(_usage.keys()):
            if k != day:
                _usage.pop(k, None)


def _limits():
    return {
        "daily_calls": int(cfg("search_ai_daily_calls", 50) or 50),
        "max_tokens": int(cfg("search_ai_max_tokens", DEFAULT_AI_MAX_TOKENS)
                          or DEFAULT_AI_MAX_TOKENS),
        "answer_chars": int(cfg("search_answer_chars", 800) or 800),
    }


# ----------------------------- 搜索源 -----------------------------
def _clean_text(s):
    s = re.sub(r"<[^>]+>", "", s or "")
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _domain(url):
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def _norm_url(url):
    url = html.unescape(url or "").strip()
    # DuckDuckGo 的跳转包装
    m = re.search(r"[?&]uddg=([^&]+)", url)
    if m:
        url = unquote(m.group(1))
    if url.startswith("//"):
        url = "https:" + url
    return url


def _search_ddg(query, limit):
    r = requests.post(
        "https://html.duckduckgo.com/html/",
        data={"q": query, "kl": "cn-zh"},
        headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"},
        timeout=SEARCH_TIMEOUT, verify=False)
    r.raise_for_status()
    text = r.text
    out = []
    # 结果块：result__a 标题链接 + result__snippet 摘要
    for block in re.findall(r'<div class="result[^"]*"[\s\S]*?</div>\s*</div>',
                            text)[:limit * 3]:
        m = re.search(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>([\s\S]*?)</a>',
                      block)
        if not m:
            continue
        url = _norm_url(m.group(1))
        title = _clean_text(m.group(2))
        sn = re.search(r'class="result__snippet"[^>]*>([\s\S]*?)</a>', block)
        snippet = _clean_text(sn.group(1)) if sn else ""
        if title and re.match(r"^https?://", url):
            out.append({"title": title, "url": url, "snippet": snippet,
                        "domain": _domain(url), "source": "duckduckgo"})
    return out


def _search_bing(query, limit):
    r = requests.get(
        "https://www.bing.com/search",
        params={"q": query, "setlang": "zh-CN", "count": max(limit, 10)},
        headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"},
        timeout=SEARCH_TIMEOUT, verify=False)
    r.raise_for_status()
    text = r.text
    out = []
    for block in re.findall(r'<li class="b_algo"[\s\S]*?</li>', text)[:limit * 3]:
        m = re.search(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>([\s\S]*?)</a>', block)
        if not m:
            continue
        url = _norm_url(m.group(1))
        title = _clean_text(m.group(2))
        sn = re.search(r'<p[^>]*>([\s\S]*?)</p>', block)
        snippet = _clean_text(sn.group(1)) if sn else ""
        if title and re.match(r"^https?://", url):
            out.append({"title": title, "url": url, "snippet": snippet,
                        "domain": _domain(url), "source": "bing"})
    return out


def _search_baidu(query, limit):
    r = requests.get(
        "https://www.baidu.com/s",
        params={"wd": query, "rn": max(limit, 10)},
        headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"},
        timeout=SEARCH_TIMEOUT, verify=False)
    r.raise_for_status()
    text = r.text
    out = []
    for block in re.findall(r'<div[^>]+class="result[^"]*"[\s\S]*?</div>\s*</div>',
                            text)[:limit * 3]:
        m = re.search(r'<h3[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>([\s\S]*?)</a>', block)
        if not m:
            continue
        url = _norm_url(m.group(1))
        title = _clean_text(m.group(2))
        sn = re.search(r'class="[^"]*content-right[^"]*"[^>]*>([\s\S]*?)</span>', block)
        snippet = _clean_text(sn.group(1)) if sn else ""
        if title and re.match(r"^https?://", url):
            out.append({"title": title, "url": url, "snippet": snippet,
                        "domain": _domain(url), "source": "baidu"})
    return out


_ENGINES = (
    ("duckduckgo", _search_ddg),
    ("bing", _search_bing),
    ("baidu", _search_baidu),
)


def _dedupe(items, limit):
    seen_url, seen_title, out = set(), set(), []
    for it in items:
        u = it.get("url", "")
        t = it.get("title", "")
        key = re.sub(r"^https?://(www\.)?", "", u).rstrip("/")
        tk = re.sub(r"\s+", "", t)
        if not key or key in seen_url or tk in seen_title:
            continue
        seen_url.add(key)
        seen_title.add(tk)
        out.append(it)
        if len(out) >= limit:
            break
    return out


def search_web(query, limit=MAX_SOURCES):
    """按顺序尝试搜索源，返回 (sources, engine, errors)。"""
    errors = []
    for name, fn in _ENGINES:
        try:
            items = fn(query, limit)
            items = _dedupe(items, limit)
            if items:
                return items, name, errors
            errors.append("%s: 无结果" % name)
        except Exception as exc:
            errors.append("%s: %s" % (name, exc))
    return [], "", errors


def local_answer(query, sources):
    """无 AI 时的规则摘要：拼接排名靠前的标题与摘要，明确标注来源。"""
    if not sources:
        return "暂时没有找到足够可靠的结果，可能是搜索源不可用或网络受限。"
    lines = []
    for i, s in enumerate(sources[:5], 1):
        brief = s.get("snippet") or ""
        if len(brief) > 120:
            brief = brief[:120] + "…"
        lines.append("%d. %s%s" % (i, s.get("title", ""),
                                   ("：" + brief) if brief else ""))
    return "根据搜索结果：" + "\n".join(lines)


# ----------------------------- 云端 AI -----------------------------
def _ai_config():
    return {
        "enabled": bool(cfg("search_ai_enable", False)),
        "url": str(cfg("search_ai_url", "") or "").strip(),
        "key": str(cfg("search_ai_key", "") or "").strip(),
        "model": str(cfg("search_ai_model", "") or "").strip(),
    }


def ai_status():
    conf = _ai_config()
    day, used = _usage_snapshot()
    limits = _limits()
    ready = bool(conf["enabled"] and conf["url"] and conf["key"] and conf["model"])
    return {
        "enabled": conf["enabled"], "ready": ready,
        "url": conf["url"], "model": conf["model"],
        # key 不回传明文，只告诉是否已设置
        "keySet": bool(conf["key"]),
        "usage": {"day": day, "used": used["used"], "tokens": used["tokens"],
                  "dailyCalls": limits["daily_calls"]},
        "limits": limits,
    }


def _chat_url(base):
    base = base.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if re.search(r"/v\d+$", base):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def ai_summarize(query, sources):
    """调用云端 AI 输出简短回答；返回 (answer, used_tokens, error)。

    失败时返回 error，调用方回退本地摘要。
    """
    conf = _ai_config()
    if not (conf["enabled"] and conf["url"] and conf["key"] and conf["model"]):
        return None, 0, "AI 未启用或配置不完整"
    limits = _limits()
    day, usage = _usage_snapshot()
    if usage["used"] >= limits["daily_calls"]:
        return None, 0, "今日 AI 次数已用完（%d 次）" % limits["daily_calls"]

    # 只发送搜索摘要，控制上下文体积与隐私暴露范围
    ctx = []
    for i, s in enumerate(sources[:6], 1):
        brief = (s.get("snippet") or "")[:300]
        ctx.append("%d. %s\n   %s\n   %s" % (i, s.get("title", ""), brief,
                                             s.get("url", "")))
    messages = [
        {"role": "system", "content":
            "你是简洁的搜索助手。根据给出的搜索结果回答用户，用中文，"
            "总字数不超过 %d 字。只陈述结果中有依据的内容，不要编造；"
            "不确定就直说。不要列举网址，结尾不要重复来源列表。" %
            max(120, limits["answer_chars"] // 2)},
        {"role": "user", "content":
            "问题：%s\n\n搜索结果：\n%s" % (query, "\n".join(ctx))},
    ]
    payload = {
        "model": conf["model"],
        "messages": messages,
        "max_tokens": limits["max_tokens"],
        "temperature": 0.3,
        "stream": False,
    }
    try:
        r = requests.post(
            _chat_url(conf["url"]),
            headers={"Authorization": "Bearer " + conf["key"],
                     "Content-Type": "application/json"},
            data=json.dumps(payload).encode("utf-8"),
            timeout=AI_TIMEOUT, verify=False)
    except Exception as exc:
        return None, 0, "AI 请求失败：%s" % exc
    if r.status_code >= 400:
        detail = ""
        try:
            detail = (r.json().get("error") or {}).get("message", "") or r.text[:200]
        except Exception:
            detail = r.text[:200]
        return None, 0, "AI 返回 HTTP %d：%s" % (r.status_code, detail)
    try:
        data = r.json()
        answer = data["choices"][0]["message"]["content"].strip()
        used = int((data.get("usage") or {}).get("total_tokens") or 0)
    except Exception:
        return None, 0, "AI 响应格式无法解析"
    if not answer:
        return None, 0, "AI 返回空回答"
    _bump_usage(used)
    return answer, used, ""


# ----------------------------- 对外入口 -----------------------------
def search_api(op="", method="POST"):
    """供 sources.serve_nova_api 调用的统一入口。

    op:  ""(默认搜索) / "status" / "config"
    """
    from flask import request
    from novacore.sources import _api_json

    if op == "status":
        return _api_json({"ok": True, **ai_status()})

    if op == "config":
        # 读取/保存 AI 配置（管理端本机写，平板只读状态）
        if method == "POST":
            body = request.get_json(force=True, silent=True) or {}
            from novacore.configutil import CONFIG, save_config
            # save_config 会整体替换 CONFIG，必须在副本上改后一次性保存，
            # 否则会把其它配置键清空。
            merged = dict(CONFIG)
            for key in ("search_ai_enable", "search_ai_url", "search_ai_model",
                        "search_ai_max_tokens", "search_ai_daily_calls",
                        "search_answer_chars"):
                if key in body:
                    merged[key] = body[key]
            # key 单独处理：空字符串表示清除
            if "search_ai_key" in body:
                merged["search_ai_key"] = str(body.get("search_ai_key") or "").strip()
            try:
                save_config(merged)
            except Exception as exc:
                return _api_json({"ok": False, "error": "保存配置失败：%s" % exc}, 500)
            return _api_json({"ok": True, **ai_status()})
        return _api_json({"ok": True, **ai_status()})

    body = request.get_json(force=True, silent=True) or {}
    query = re.sub(r"\s+", " ", str(body.get("query") or "")).strip()
    if not query:
        return _api_json({"ok": False, "error": "问题不能为空"}, 400)
    if len(query) > MAX_QUERY_LEN:
        query = query[:MAX_QUERY_LEN]
    try:
        limit = int(body.get("limit") or MAX_SOURCES)
    except (TypeError, ValueError):
        limit = MAX_SOURCES
    limit = max(1, min(MAX_SOURCES, limit))

    sources, engine, errors = search_web(query, limit)
    answer, used, ai_error = ai_summarize(query, sources) if sources else (None, 0, "")
    if not answer:
        answer = local_answer(query, sources)

    limits = _limits()
    if len(answer) > limits["answer_chars"]:
        answer = answer[:limits["answer_chars"]] + "…"

    return _api_json({
        "ok": True,
        "query": query,
        "answer": answer,
        "sources": sources,
        "engine": engine,
        "usedAI": bool(used) or False,
        "aiError": ai_error,
        "searchErrors": errors,
        "searchedAt": int(time.time()),
        "usage": ai_status()["usage"],
    })
