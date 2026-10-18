/* ============================================================
 * Tzy OS · 智能搜索（对话式，走后端联网搜索 + 可选云端 AI）
 * 平板只调用同源 api/search，搜索与 AI 请求全部由电脑完成。
 * 回答简短、有来源；AI 未配置或超额时后端自动回退本地摘要。
 * 设置（Base URL / API Key / 模型 / token 与每日次数限额）存电脑 config.json。
 * Loshop & Cpt
 * ============================================================ */
(function () {
  "use strict";

  OS.registerApp({
    id: "search",
    name: "智能搜索",
    icon: "🔎",
    tone: "tone-blue",
    version: "1.0.0",
    maximize: true,
    open: function (root, OS) { build(root, OS); }
  });

  var HISTORY_KEY = "nova2.search.history";
  var HISTORY_MAX = 40;

  function esc(s) { return String(s == null ? "" : s); }

  function build(root, OS) {
    var wrap = OS.h("div", "nv-search");
    wrap.style.cssText = "position:absolute;top:0;left:0;right:0;bottom:0;" +
      "display:flex;flex-direction:column;background:#f7f4ec;color:#24252a;";

    /* ---------- 顶栏 ---------- */
    var bar = OS.h("div", "nv-search-bar");
    bar.style.cssText = "flex:0 0 auto;display:flex;align-items:center;gap:8px;" +
      "padding:10px 12px;background:#23262e;color:#f2eee5;";
    var title = OS.h("div", "", "🔎 智能搜索");
    title.style.cssText = "font-weight:600;margin-right:auto;";
    var status = OS.h("span", "", "就绪");
    status.style.cssText = "font-size:11px;color:rgba(242,238,229,.65);";
    var btnConfig = OS.h("button", "nv-btn", "设置");
    var btnClear = OS.h("button", "nv-btn", "清空");
    bar.appendChild(title); bar.appendChild(status);
    bar.appendChild(btnConfig); bar.appendChild(btnClear);
    wrap.appendChild(bar);

    /* ---------- 消息区 ---------- */
    var list = OS.h("div", "nv-search-list");
    list.style.cssText = "flex:1;min-height:0;overflow:auto;padding:16px 14px;";
    wrap.appendChild(list);

    var empty = OS.h("div", "", "输入问题，例如：华为平板怎么截屏");
    empty.style.cssText = "color:#9a9079;font-size:14px;text-align:center;margin-top:40px;";
    list.appendChild(empty);

    /* ---------- 输入区 ---------- */
    var composer = OS.h("div", "nv-search-composer");
    composer.style.cssText = "flex:0 0 auto;display:flex;gap:8px;padding:10px;" +
      "border-top:1px solid #ddd6c7;background:#eee8dc;";
    var input = document.createElement("textarea");
    input.placeholder = "输入问题…（Enter 发送，Shift+Enter 换行）";
    input.rows = 1;
    input.style.cssText = "flex:1;min-width:0;resize:none;border:1px solid #cfc6b5;" +
      "border-radius:6px;padding:9px;font:14px sans-serif;background:#fff;";
    var btnSend = OS.h("button", "nv-btn primary", "搜索");
    composer.appendChild(input); composer.appendChild(btnSend);
    wrap.appendChild(composer);
    root.appendChild(wrap);

    var busy = false;

    /* ---------- 历史 ---------- */
    function loadHistory() {
      var h = OS.store.get(HISTORY_KEY, []);
      return Array.isArray(h) ? h : [];
    }
    function saveHistory(h) {
      OS.store.set(HISTORY_KEY, h.slice(-HISTORY_MAX));
    }

    /* ---------- 渲染 ---------- */
    function bubble(kind, text) {
      var el = OS.h("div", "nv-search-msg");
      var mine = kind === "user";
      el.style.cssText = "max-width:86%;margin:8px 0;padding:10px 12px;" +
        "border-radius:" + (mine ? "12px 4px 12px 12px" : "4px 12px 12px 12px") + ";" +
        "background:" + (mine ? "#c03f2d" : "#fff") + ";" +
        "color:" + (mine ? "#fff" : "#24252a") + ";" +
        "box-shadow:0 1px 2px rgba(45,35,20,.12);white-space:pre-wrap;" +
        "word-break:break-word;line-height:1.5;" +
        (mine ? "margin-left:auto;" : "");
      el.textContent = esc(text);
      return el;
    }

    function sourcesBlock(sources) {
      var box = document.createElement("div");
      box.style.cssText = "margin:2px 0 10px;font-size:12.5px;color:#6f6857;";
      if (!sources || !sources.length) { box.textContent = "（无来源）"; return box; }
      var head = document.createElement("div");
      head.textContent = "参考来源：";
      head.style.cssText = "margin-bottom:4px;color:#8a8271;";
      box.appendChild(head);
      sources.forEach(function (s, i) {
        var row = document.createElement("div");
        row.style.cssText = "margin:3px 0;line-height:1.4;";
        var a = document.createElement("a");
        a.textContent = (i + 1) + ". " + (s.title || s.domain || s.url);
        a.href = s.url || "#";
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        a.style.cssText = "color:#2f5d8a;text-decoration:none;";
        row.appendChild(a);
        if (s.domain) {
          var dom = document.createElement("span");
          dom.textContent = "  " + s.domain;
          dom.style.cssText = "color:#a59c89;";
          row.appendChild(dom);
        }
        box.appendChild(row);
      });
      return box;
    }

    function showEmpty(show) {
      empty.style.display = show ? "" : "none";
    }

    function renderHistory() {
      OS.clear(list);
      var hist = loadHistory();
      if (!hist.length) { list.appendChild(empty); showEmpty(true); return; }
      showEmpty(false);
      hist.forEach(function (item) {
        if (item.q) list.appendChild(bubble("user", item.q));
        if (item.a) list.appendChild(bubble("ai", item.a));
        if (item.sources && item.sources.length) list.appendChild(sourcesBlock(item.sources));
      });
      list.scrollTop = list.scrollHeight;
    }

    /* ---------- 发送 ---------- */
    function ask(query) {
      if (busy || !query) return;
      busy = true;
      btnSend.disabled = true;
      showEmpty(false);
      list.appendChild(bubble("user", query));
      var thinking = bubble("ai", "正在搜索…");
      list.appendChild(thinking);
      list.scrollTop = list.scrollHeight;
      status.textContent = "搜索中…";

      OS.api.search(query, { limit: 8 }).then(function (r) {
        busy = false;
        btnSend.disabled = false;
        if (!r || !r.ok) {
          thinking.textContent = "搜索失败：" + ((r && r.error) || "电脑服务不可用");
          status.textContent = "失败";
          return;
        }
        thinking.textContent = r.answer || "（无回答）";
        if (r.sources && r.sources.length) {
          list.appendChild(sourcesBlock(r.sources));
        }
        var via = r.usedAI ? "云端 AI" : "本地摘要";
        status.textContent = "完成 · " + via + (r.engine ? " · " + r.engine : "");
        if (r.usage && r.usage.dailyCalls) {
          status.textContent += " · 今日 " + r.usage.used + "/" + r.usage.dailyCalls;
        }
        var hist = loadHistory();
        hist.push({ q: query, a: r.answer || "", sources: r.sources || [], at: Date.now() });
        saveHistory(hist);
        list.scrollTop = list.scrollHeight;
      }, function (err) {
        busy = false;
        btnSend.disabled = false;
        thinking.textContent = "搜索失败：" + ((err && err.message) || "网络错误");
        status.textContent = "失败";
      });
    }

    function submit() {
      var q = input.value.trim();
      if (!q) return;
      input.value = "";
      ask(q);
    }

    btnSend.addEventListener("click", submit);
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); }
    });
    btnClear.addEventListener("click", function () {
      OS.dlg.confirm("清空全部搜索记录？", "清空历史").then(function (yes) {
        if (!yes) return;
        saveHistory([]);
        renderHistory();
        status.textContent = "已清空";
      });
    });

    /* ---------- 设置 ---------- */
    btnConfig.addEventListener("click", function () {
      OS.api.searchStatus().then(function (st) {
        st = st || {};
        var msg =
          "云端 AI：" + (st.ready ? "已就绪" : (st.enabled ? "已启用但配置不全" : "未启用")) + "\n" +
          "模型：" + (st.model || "（未填写）") + "\n" +
          "接口地址：" + (st.url || "（未填写）") + "\n" +
          "API Key：" + (st.keySet ? "已设置" : "未设置") + "\n" +
          "今日用量：" + ((st.usage && st.usage.used) || 0) + "/" +
          ((st.usage && st.usage.dailyCalls) || "?") + " 次" +
          "，累计 " + ((st.usage && st.usage.tokens) || 0) + " tokens\n" +
          "每次回答上限：" + ((st.limits && st.limits.max_tokens) || "?") + " tokens\n\n" +
          "配置项保存在电脑端 config.json，可在本机管理界面或此处修改。";
        OS.dlg.alert(msg, "AI 设置状态").then(function () {
          return OS.dlg.prompt("API Key（留空保持不变，输入 - 表示清除）", "", "设置 API Key");
        }).then(function (key) {
          if (key === null) return null;
          var body = {};
          if (key === "-") body.search_ai_key = "";
          else if (key.trim()) body.search_ai_key = key.trim();
          return OS.dlg.prompt("接口地址，如 https://api.openai.com/v1", st.url || "", "AI 接口地址")
            .then(function (url) {
              if (url === null) return null;
              if (url.trim()) body.search_ai_url = url.trim();
              return OS.dlg.prompt("模型名，如 gpt-4o-mini", st.model || "", "AI 模型");
            }).then(function (model) {
              if (model === null) return null;
              if (model.trim()) body.search_ai_model = model.trim();
              return OS.dlg.prompt("每次回答 token 上限（默认 400）",
                String((st.limits && st.limits.max_tokens) || 400), "Token 限额")
                .then(function (mt) {
                  if (mt === null) return null;
                  var n = parseInt(mt, 10);
                  if (!isNaN(n) && n > 0) body.search_ai_max_tokens = Math.min(4000, n);
                  return OS.dlg.prompt("每日 AI 调用次数上限（默认 50）",
                    String((st.limits && st.limits.dailyCalls) || 50), "每日限额")
                    .then(function (dc) {
                      if (dc === null) return null;
                      var d = parseInt(dc, 10);
                      if (!isNaN(d) && d > 0) body.search_ai_daily_calls = Math.min(1000, d);
                      return OS.dlg.confirm("启用云端 AI？\n禁用时只用本地摘要，不产生费用。", "启用 AI")
                        .then(function (enable) {
                          body.search_ai_enable = !!enable;
                          return body;
                        });
                    });
                });
            });
        }).then(function (body) {
          if (!body) return null;
          return OS.api.searchConfig(body).then(function (r) {
            OS.toast(r && r.ok ? "设置已保存到电脑" : ((r && r.error) || "保存失败"));
          });
        });
      }, function (err) {
        OS.toast("读取设置失败：" + ((err && err.message) || "电脑服务不可用"));
      });
    });

    renderHistory();
    status.textContent = OS.net.online() ? "就绪" : "离线";
  }
})();
