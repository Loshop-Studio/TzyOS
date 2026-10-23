/*
 * Tzy OS in-frame bridge（由代理内联注入 Tzy OS 的 index.html）
 * 职责：
 *   1. 封禁 Service Worker 注册，杜绝 SW 越出挂载路径影响原平台；
 *   2. 在 Tzy OS 内部提供与外层一致的隐蔽手势，向父页面发 postMessage 关闭/切换。
 * 令牌不经过 URL（保持 URL 干净以便命中长期缓存），通过 postMessage 握手获取。
 */
(function () {
    "use strict";
    try {
        // 最原始打点：iframe 内 JS 只要执行到这一行就回传（同源相对地址，不经父页面）
        try { var _bb = new Image(); _bb.src = "__err__.gif?p=j&z=" + Date.now(); } catch (e0) { }
        var CFG = window.__BRIDGE_CFG__ || {};

        /* =====================================================================
         * 设备诊断探针：老内核/弱网平板启动失败时，把 iframe 内部真实状态
         * （JS 报错、资源加载失败、实际拿到的脚本、启动阶段）回传劫持服务端，
         * 电脑端 logs/access.log 中以 [DIAG] 记录。不做任何其他事。
         * ===================================================================== */
        function diag(phase, extra) {
            try {
                var d = {
                    t: "nva-diag", ph: phase, rs: document.readyState,
                    ua: (navigator.userAgent || "").slice(0, 130), ts: Date.now()
                };
                if (extra) { for (var k in extra) d[k] = extra[k]; }
                window.parent.postMessage(d, "*");
            } catch (e) { }
        }
        diag("boot");
        window.addEventListener("error", function (e) {
            var tgt = e.target;
            if (tgt && tgt !== window && (tgt.src || tgt.href)) {
                diag("res-error", { url: String(tgt.src || tgt.href).split("/__nova__/")[1] || String(tgt.src || tgt.href), tag: tgt.tagName || "" });
            } else {
                diag("js-error", {
                    msg: String(e.message || ""),
                    file: String(e.filename || "").split("/").pop(),
                    line: e.lineno || 0, col: e.colno || 0,
                    stack: (e.error && e.error.stack) ? String(e.error.stack).slice(0, 1400) : ""
                });
            }
        }, true);
        window.addEventListener("unhandledrejection", function (e) {
            var r = e.reason;
            diag("promise", { reason: String(r && (r.stack || r.message) || r).slice(0, 1400) });
        });
        function diagBeat(phase) {
            try {
                var ents = performance.getEntriesByType("resource")
                    .map(function (x) {
                        var n = x.name.split("/__nova__/")[1] || x.name;
                        return x.responseEnd ? n : n + "(pending)";
                    })
                    .filter(function (n) { return /\.(js|html|css|json|woff2)(\?|$)/.test(n) || n.indexOf("(pending)") > -1; });
                diag(phase, {
                    f1: typeof window.launchbios,        // script.js 执行后应为 function
                    f2: typeof window.openn,
                    f3: typeof window.setandinitnewuser,
                    res: ents.slice(0, 60)
                });
            } catch (e) { diag(phase, { probeErr: String(e) }); }
        }
        document.addEventListener("DOMContentLoaded", function () { diagBeat("dcl"); });
        window.addEventListener("load", function () {
            diagBeat("load");
            setTimeout(function () { diagBeat("t3"); }, 3000);
            setTimeout(function () { diagBeat("t8"); }, 8000);
        });

        var TOKEN = "";
        var PARENT = "*";

        /* ---- 与父页面握手获取一次性令牌 ---- */
        // 同时把 os.js 解析出的电脑服务基址（同源时为空串，IP 直连时为
        // "http://192.168.x.x/__nova__/"）回传给父页面保存。息屏恢复时
        // loader 必须用这个地址重建 iframe，否则会回到打不开的劫持域名。
        // 未 boot 时 window.OS 还没有 net，此时不带 b，避免把父页面已保存的
        // 直连地址清成同源；os.js 探测完成后会主动调用 nva-netbase 补发。
        function handshake() {
            try {
                var msg = { t: "nva-handshake" };
                try {
                    if (window.OS && window.OS.net && window.OS.net.base) {
                        msg.b = String(window.OS.net.base() || "");
                    }
                } catch (e) { }
                window.parent.postMessage(msg, "*");
            } catch (e) { }
        }
        window.addEventListener("message", function (e) {
            var d = e.data;
            if (!d || typeof d !== "object" || d.t !== "nva-auth") return;
            TOKEN = String(d.k || "");
            PARENT = e.origin || "*";
            try { window.__NVA_TOKEN__ = TOKEN; } catch (e) { }
        });
        // os.js 每次探测成功/失败都会广播 net-change，这里转成基址同步消息。
        // bridge 在 <head> 执行，此时 window.OS 还未定义，故等 os.js 加载完再绑定。
        function bindNetBase() {
            try {
                if (!window.OS || !window.OS.on) return false;
                window.OS.on("net-change", function () {
                    try {
                        window.parent.postMessage({
                            t: "nva-netbase",
                            b: String((window.OS.net && window.OS.net.base()) || "")
                        }, "*");
                    } catch (e) { }
                });
                return true;
            } catch (e) { return false; }
        }
        if (!bindNetBase()) {
            document.addEventListener("DOMContentLoaded", bindNetBase);
            var bindTries = 0;
            var bindTimer = setInterval(function () {
                if (bindNetBase() || ++bindTries > 40) clearInterval(bindTimer);
            }, 50);
        }
        handshake();
        setTimeout(handshake, 400);
        setTimeout(handshake, 1200);

        /* ---- OS 真正就绪上报（nva-ready） ----
         * handshake 只证明「HTML 解析到 <head>」，而 os.js 可能 404、被缓存
         * 截断、或返回非 JS 内容（CDN 错误页）。这种情况下 OS 永远不会 boot，
         * 但 loader 已经认为握手成功、停掉看门狗，用户看到的就是死锁屏。
         * 这里等 window.OS.__booted 变真后再上报一次，loader 据此才认为
         * 「真的起来了」；超时未上报则让看门狗继续重建。 */
        (function readyWatch() {
            var tries = 0;
            var timer = setInterval(function () {
                tries++;
                var ok = false;
                try { ok = !!(window.OS && window.OS.__booted); } catch (e) { }
                if (ok) {
                    clearInterval(timer);
                    try { window.parent.postMessage({ t: "nva-ready" }, "*"); } catch (e) { }
                    return;
                }
                // 约 12 秒仍未 boot：停止上报，交给 loader 看门狗重建
                if (tries > 240) clearInterval(timer);
            }, 50);
        })();

        /* ---- SW 封禁：Tzy OS 仅在手动开启离线模式时注册 SW，这里直接拦截 ---- */
        try {
            if (navigator.serviceWorker && navigator.serviceWorker.getRegistrations) {
                navigator.serviceWorker.getRegistrations().then(function (regs) {
                    regs.forEach(function (r) { try { r.unregister(); } catch (e) { } });
                }).catch(function () { });
            }
            if (navigator.serviceWorker) {
                navigator.serviceWorker.register = function () {
                    return Promise.reject(new DOMException("blocked", "SecurityError"));
                };
            }
        } catch (e) { }

        function send(t) {
            if (!TOKEN) return; // 尚未握手，丢弃
            try { window.parent.postMessage({ t: t, k: TOKEN }, PARENT); } catch (e) { }
        }

        /* ---- 后台静默预取（必须在本 iframe 同源分区执行：Chrome 双键缓存按
              (顶级站, 当前帧站) 分区，父页面预取对本 iframe 无效）---- */
        var PF_KEY = "__nova_pfv__";
        function bridgePrefetch() {
            try {
                // 预取是为了「离线可用」把 OS 静态资源写入磁盘缓存，与在线开关
                // 无关：开关关闭时反而更需要预取，否则断网后无缓存可用。
                // 仅在浏览器明确离线时跳过（此时也取不到）。
                if (navigator.onLine === false) return;
                fetch("__filelist__.json", { cache: "no-cache" })
                    .then(function (r) { return r.json(); })
                    .then(function (man) {
                        if (!man || !Array.isArray(man.files)) return;
                        var done = "";
                        try { done = localStorage.getItem(PF_KEY) || ""; } catch (e) { }
                        if (done === String(man.version)) return;
                        var i = 0, BATCH = 6, failed = [];
                        (function pump() {
                            if (navigator.onLine === false) return;
                            if (i >= man.files.length) {
                                // 只有全部成功才记录版本；否则保留旧版本，
                                // 下次打开继续补抓缺失项（弱网中断可自愈）。
                                if (!failed.length) {
                                    try { localStorage.setItem(PF_KEY, String(man.version)); } catch (e) { }
                                } else {
                                    try { console.warn("[nova] prefetch incomplete:", failed.length, failed.slice(0, 5)); } catch (e) { }
                                }
                                return;
                            }
                            var part = man.files.slice(i, i + BATCH);
                            i += BATCH;
                            Promise.all(part.map(function (f) {
                                // 用 reload 强制写入磁盘缓存并校验状态码，
                                // 404/失败都记为未完成，不能静默当成功。
                                // 用 default：已在缓存中则直接命中，缺失才回源；
                                // reload 会强制回源，弱网下更易失败。
                                return fetch(f, { cache: "default" }).then(function (r) {
                                    if (!r || !r.ok) failed.push(f);
                                }, function () { failed.push(f); });
                            })).then(function () { setTimeout(pump, 60); });
                        })();
                    }).catch(function () { });
            } catch (e) { }
        }
        // 预取尽早启动（1.2s）：离线缓存是产品根本，不能等太久才开始，
        // 否则用户打开看一眼就关闭时缓存永远是空的。分批小包 + 轻间隔，
        // 尽量避免与主界面首屏渲染争抢带宽。
        setTimeout(function () {
            var ric = window.requestIdleCallback || function (cb) { return setTimeout(cb, 1); };
            ric(bridgePrefetch, { timeout: 5000 });
        }, 1200);

        var hotOn = !!CFG.trigger_hotkey_enable;
        var wantKey = (CFG.trigger_hotkey_key || "").toLowerCase();
        var nCtrl = !!CFG.trigger_hotkey_ctrl, nShift = !!CFG.trigger_hotkey_shift, nAlt = !!CFG.trigger_hotkey_alt;

        var tapOn = !!CFG.trigger_tap_enable;
        var need = Math.max(1, Math.min(9, CFG.trigger_tap_count | 0 || 5));
        var radius = Math.max(8, Math.min(64, CFG.trigger_tap_radius | 0 || 20));
        var winMs = Math.max(300, Math.min(5000, CFG.trigger_tap_interval_ms | 0 || 1500));
        var corner = CFG.trigger_tap_corner || "top-left";

        function bind(doc) {
            if (!doc || doc.__nvaBridge) return;
            doc.__nvaBridge = true;

            if (hotOn && wantKey) {
                doc.addEventListener("keydown", function (e) {
                    var k = (e.key || "").toLowerCase();
                    if (k !== wantKey) return;
                    if (!!e.ctrlKey !== nCtrl || !!e.shiftKey !== nShift || !!e.altKey !== nAlt) return;
                    e.preventDefault();
                    e.stopImmediatePropagation();
                    send("nva-toggle");
                }, true);
            }

            if (tapOn) {
                var taps = 0, firstAt = 0;
                doc.addEventListener("pointerdown", function (e) {
                    var x = e.clientX, y = e.clientY, w = window.innerWidth, h = window.innerHeight, hit = false;
                    if (corner === "top-left") hit = x <= radius && y <= radius;
                    else if (corner === "top-right") hit = (w - x) <= radius && y <= radius;
                    else if (corner === "bottom-left") hit = x <= radius && (h - y) <= radius;
                    else if (corner === "bottom-right") hit = (w - x) <= radius && (h - y) <= radius;
                    if (!hit) return;
                    var now = Date.now();
                    if (!taps || now - firstAt > winMs) { taps = 1; firstAt = now; }
                    else taps++;
                    if (taps >= need) { taps = 0; send("nva-hide"); }
                }, true);
            }
        }

        function arm() {
            bind(document);
            // Tzy OS 应用可能在新子 iframe 中打开，同源子帧内的手势需要各自绑定
            document.addEventListener("load", function (e) {
                var t = e.target;
                if (t && t.tagName === "IFRAME" && t.contentDocument) {
                    try { bind(t.contentDocument); } catch (e) { }
                }
            }, true);
        }

        if (document.readyState === "loading") {
            document.addEventListener("DOMContentLoaded", arm, { once: true });
        } else {
            arm();
        }
    } catch (e) { /* 桥接失败不影响 Tzy OS 本体 */ }
})();
