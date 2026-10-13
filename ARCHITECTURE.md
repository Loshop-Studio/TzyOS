# NovaOS-Stealth 项目框架

## 1. 运行对象与入口

项目根目录下的 `Tzy-Box/ezyRawContent.html` 是被劫持的原始教学平台页面，属于宿主页面，不是 NovaOS 的启动页。NovaOS 的实际页面入口是：

```text
NovaOS-Stealth/novaos2/nova.html
```

浏览器或 WebView 打开原始页面后，电脑端服务把注入脚本插入页面；脚本再创建隔离 iframe，iframe 内运行 NovaOS。这样原始页面的 DOM、Cookie 和业务脚本与 NovaOS 分开。

## 2. 后端服务链

```text
novaosd.py / server.py
        |
        +-- novacore.service
              +-- dns_service.py       DNS 劫持与上游转发（可关闭）
              +-- proxy_app.py         HTTP 透明代理、注入、/__/nova 挂载点、系统 API
              +-- web_proxy.py         /wp/ 同源网页代理与资源 URL 重写
              +-- proxy_engine.py      HTTP/HTTPS 出站代理（HTTP 与 CONNECT）
              +-- admin_app.py         127.0.0.1:8899 管理接口
              +-- devmode.py           开发模式命令通道
              +-- sources.py           本地文件、FTP、SMB、外网取回 API
              +-- novel_dl.py           小说服务按需启动与转发
```

`service.py` 负责线程编排。常规端口是 DNS `53`、HTTP `80`、管理端 `8899`，网页应用的出站代理默认监听 `18087`。`config.json` 控制挂载域名、挂载路径、DNS、劫持域名、缓存策略、触发方式和本地文件根目录。

## 3. 注入与启动链

```text
原始页面（例如 ezyRawContent.html）
  -> htmlkit / proxy_app 注入 boot-stub
  -> boot-stub.js 请求 /__nova__/__boot__.js
       在线：加载最新 loader.js 与浏览器配置
       失败：加载长期缓存的 __boot__.snapshot.js
  -> loader.js 创建闭合 ShadowRoot + iframe
  -> iframe 请求 /__nova__/nova.html
  -> proxy_app.transform_nova_html 注入 bridge.js 配置
  -> bridge.js 握手、拦截隐藏触发、禁止 Service Worker、可选预取
  -> nova.html 加载本地依赖、os.js、novaos2/apps/*.js
  -> window.OS.boot() 创建桌面
```

`loader.js` 只负责宿主页面侧的显示、隐藏、握手和有限重试；`bridge.js` 只负责 iframe 与父页面之间的消息桥接。二者不应直接依赖原始页面的业务框架。

## 4. NovaOS 前端分层

| 层 | 位置 | 职责与依赖 |
| --- | --- | --- |
| 页面壳 | `novaos2/nova.html` | 锁屏、桌面、状态栏和脚本加载顺序 |
| 核心运行时 | `novaos2/os.js` | 网络探测、API/VFS、应用注册、WinBox 窗口、锁屏、桌面和启动流程 |
| 内置应用 | `novaos2/apps/*.js` | 文件管理、编辑器、设置、媒体、应用商店、预览、开发终端等 |
| 本地库 | `novaos2/lib/` | WinBox、JSZip、pdf.js、Mammoth 等；启动不依赖外网 |
| 应用仓库 | `apps_repo/*.tzyp` | 预装或用户安装的应用包，依赖挂载点 `sys/app-*` API |
| 离线空间 | `os.js` 内 VFS | IndexedDB 优先，localStorage 降级；保存应用文件和配置 |

内置应用通过 `window.OS` 调用统一接口：

```text
OS.net       探测电脑服务、解析动态基址、带重探的 fetch
OS.api       api/fs、api/ftp、api/smb、api/web、sys/*
OS.vfs       本地离线空间
OS.pkg       应用目录、下载和安装
OS.openApp   WinBox 窗口与应用生命周期
```

## 5. 网络与离线边界

静态 OS 页面、内置库、已缓存脚本和 VFS 可以在断网后继续工作；电脑服务 API、外网网页代理、FTP/SMB 和应用包下载必须在线或能访问电脑服务。

`os.js` 的网络状态现在有明确的三项状态：

```text
NET.online   是否有可用电脑服务
NET.base     同源为空字符串，直连时为电脑服务基址
NET.probed   是否已有一次完整探测结果
```

探测失败会清空 `NET.base` 和 `NET.info`，进入短暂离线冷却；冷却期间 API 直接返回离线结果，不再反复请求旧地址。冷却结束后普通调用仍可重新探测，即使 WebView 没有派发 `online` 事件。

## 6. 本次断网修复

- `novaos2/os.js`
  - 修复同源成功时因 `base === ""` 被重复探测的问题。
  - 探测失败清除陈旧地址和服务信息，网络灯与事件改用 `NET.online` 判断。
  - 离线 API 调用短路，避免空基址请求和递归重试；恢复后允许再次探测。
  - 增加 `offline`/`online` 事件同步和 `booting` 启动防重入，避免重复事件、定时器和应用初始化。
- `bridge.js`
  - WebView 明确离线时跳过文件清单和批量资源预取；预取过程中断网也会停止 pump。
- `loader.js`
  - WebView 明确离线时停止 iframe 看门狗的 cache-busting 重建；网络恢复后会重新挂起看门狗。
  - 原有最多三次重试限制保留，避免在线故障被无限重建。

## 7. 顶层灾备恢复按钮

`loader.js` 现在始终在宿主页面右上角创建一个透明恢复按钮，尺寸复用 `config.json` 的 `trigger_tap_radius`（当前为 60px）。按钮位于 OS iframe 之外，不依赖 `os.js`、`bridge.js` 或 OS 是否已经握手。

点击恢复按钮后：

1. 停止旧 iframe 看门狗并移除旧 OS iframe。
2. 以 `nova.html?recovery=<timestamp>` 创建新的全屏 OS iframe，覆盖并冻结后方宿主页面。
3. 重新启动握手和有限重试流程；不增加全屏透明拦截层，避免旧 WebView 吞掉 OS 触摸事件。

按钮有 DOM 守护，宿主页面重绘或清空 `body` 后会自动重新挂载。该机制可以恢复 iframe 内的死循环、加载失败和启动异常；如果宿主页面本身占满浏览器主线程，浏览器无法处理任何点击事件，这种情况仍需系统级刷新或重新打开页面。

## 8. 验证与发布注意事项

## 8.1 首次激活流程

注入系统默认处于未激活状态。未激活时，`loader.js` 只接受配置中的
`trigger_search_keyword` 作为第一步暗号：输入命中后会自动清空输入框，
但不会创建或显示 NovaOS，也不会响应左上角连点、热键或 URL 暗参。

随后在搜索框输入当天的四位日期 `MMDD`（例如 10 月 2 日输入 `1002`）。
日期正确后输入框再次自动清空，loader 将激活标记写入当前宿主页面的
`localStorage`，并首次显示 NovaOS。激活标记按 `serve_origin + mount` 隔离；
写入失败时不会放行。之后所有原有唤起方式恢复正常。

已验证：

```text
node --check novaos2/os.js
node --check bridge.js
node --check loader.js
node --check boot-stub.js
$files = @("novaosd.py", "server.py") + (Get-ChildItem novacore -Filter *.py | ForEach-Object { $_.FullName })
& runtime\python\python.exe -m py_compile $files
```

还使用 Flask 内存测试客户端验证了带正确 Host 的：

```text
GET  /__nova__/nova.html  -> 200
POST /__nova__/api/ping  -> 200, ok=true
```

修改 `novaos2`、注入脚本或 `config.json` 后，按项目缓存策略执行更新流程；不要手工修改生成的 `dist`，除非准备重新执行构建发布。
