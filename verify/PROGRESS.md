# Tzy OS 真实浏览器验证 · 进度存档

> 本文件由人工维护的说明页；**机器可读的权威进度**在
> `verify/state/progress.jsonl`（每行一条，追加写入，断电只丢最后一条）。
> 随时用 `python3 verify/runner.py --status` 查看。

## 1. 环境

| 项 | 值 |
| --- | --- |
| 主机 | iZbp1efmn5fanmq042fflpZ |
| 浏览器 | Chromium 155（`/snap/bin/chromium`，需 `--no-sandbox`） |
| 自动化 | playwright-core + Node v20（`/tmp/node-v20.18.1-linux-x64`） |
| 后端 | `verify/server.py` → 监听 `:8097`，复用 novacore 真实代码 |
| 宿主导航映射 | `--host-resolver-rules=MAP web-alicdn.zyai.cc 127.0.0.1:8097` |

> 注意：`/tmp` 下的 Node 与 playwright 在机器重启后**可能丢失**。
> runner 启动时会自检，缺依赖直接报错退出，不会静默跑空。

## 2. 调度

```bash
# 已安装（systemd timer，每天 09:00 触发，开机后补跑错过的）
systemctl list-timers tzy-verify.timer
systemctl status tzy-verify.service

# 手动
python3 verify/runner.py            # 续跑所有未 PASS 的用例
python3 verify/runner.py --status   # 摘要
python3 verify/runner.py --list     # 列表
python3 verify/runner.py --force    # 忽略 PASS 重跑
```

幂等：已 PASS 的用例默认跳过，所以 09:00 的定时任务只会跑剩下的，
不会重复消耗时间。21:20 关机时进程被杀，`progress.jsonl` 已逐条 fsync，
次日从第一个未 PASS 的用例继续。

## 3. 当前进度（2026-10-23 10:07，全量通过）

**16 / 16 全部通过**

| 用例 | 覆盖点 |
| --- | --- |
| T01_boot | 真实浏览器启动，9 应用、7 图标、无 JS 错误 |
| T02_inject | 完整注入链 stub→loader→iframe→booted |
| T03_devterm_ui | 维护终端 UI，10 个快捷按钮 |
| T04_devterm_cmds | 终端 10 条命令 |
| T05_offline_boot | 停服后从磁盘缓存重开仍能启动 |
| T06_backend_down | 断劫持回落 snapshot |
| T07_cache_headers | 入口 60s；phi 可校验；snapshot 一年 |
| T08_etag_304 | ETag 304 零正文 |
| T09_appreset_self | appreset 不自毁 |
| T10_hostpage_freeze | freeze/thaw 生效 |
| T11_corrupt_os_js | os.js 损坏时看门狗 3 轮自愈 |
| T12_hash_mismatch | 单应用 404 不影响 OS |
| T13_storage_corrupt | 存储损坏仍启动 |
| T14_slow_network | 弱网 1.2s 延迟仍启动 |
| T15_hang_recovery | 主线程卡死 6s 后仍可交互 |
| T16_phi_white | phi 版本号==mtime，渲染正常 |

## 4. T11 已解决（2026-10-23）

根因：`loader.js` 的 `nva-handshake` 分支里**仍有一行 `clearTimeout(watchdog)`**。
握手只代表 HTML 解析到 `<head>`，os.js 坏掉时握手照样成功，看门狗被停掉后
再无任何重建机制 —— 实测 `hasWatchdog: false`，`reloads` 永远是 0。

修复：删除握手分支里的 `clearTimeout`，看门狗只在 `nva-ready`
（`OS.__booted === true`）时才停。修复后实测完整执行 3 轮重建
（`?r=1` / `?r=2` / `?r=3`），随后停止，符合设计上限。

## 4. 本次会话已修复的缺陷（均已写入代码，未提交）

| 位置 | 问题 | 修复 |
| --- | --- | --- |
| `novacore/proxy_app.py` | 入口页 `no-cache, max-age=60`：`no-cache` 优先级更高，`max-age` 失效，断网必然失败 | 改为 `public, max-age=60`（60s 内读磁盘副本，过期回源） |
| `novacore/proxy_app.py` | `lib/` 应用入口页一年不回源，phi 内嵌写死 import 版本号导致白屏 | 入口改可校验；phi 目录 `max-age=86400` + 真条件请求 |
| `loader.js` | bridge 在 `<head>` 就握手，握手即停看门狗 → os.js 坏掉时永久死锁屏 | 新增 `nva-ready`（OS.__booted 才确认）；看门狗判据从 `handshaked` 改 `osReady` |
| `bridge.js` | 无“OS 真正就绪”上报 | 轮询 `OS.__booted`，真后 `postMessage({t:'nva-ready'})` |
| `novaos2/lib/phigros/script-1.5.9.2.js` | 写死 `interact.js?v=20261012-...` | 改为文件 mtime |

## 5. 已知设计取舍

1. **`__boot__.snapshot.js` 预热延迟 2.5s**：用户在打开页面后 2.5s 内断网，
   快照还没写入缓存，此时断劫持无兜底。属设计取舍，暂未改。
2. **入口 HTML `max-age=60` 过期后离线不可用**：这是“更新即时生效”的代价，
   真正的离线兜底依赖 snapshot + loader 看门狗，不依赖入口页缓存。
   需要用户手动清缓存或用维护终端 `forcerefresh`。

## 6. 产物位置

```
verify/
├── runner.py          可续跑执行器（断电安全）
├── server.py          验证用后端（:8097）
├── lib.js             用例公共库
├── cases.json         用例清单
├── cases/*.js         16 个用例脚本
├── state/progress.jsonl   权威进度（追加 + fsync）
├── state/summary.json     摘要
├── logs/runner.log        执行日志
└── logs/shots/*.png       每个用例的截图
```
