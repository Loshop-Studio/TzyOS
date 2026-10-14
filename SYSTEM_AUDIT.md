# NovaOS 前后端通讯与系统逻辑审查报告

审查日期：2026-10-14  
范围：`novaos2` 前端核心、内置应用、墨卷/QuickDraw/Phi 桥接，`novacore` HTTP 路由、文件/FTP/SMB/API、系统更新、备份和 DevLink。

## 结论摘要

本次完成了静态调用链审查、接口路由核对、JavaScript/Python 语法检查和构建前后的文件校验。已确认 4 项需要修复的问题：

| 优先级 | 数量 | 结论 |
|---|---:|---|
| P1 高 | 1 | 网络 POST 失败重试可能重复执行写操作 |
| P2 中 | 2 | 受限文件根目录可被符号链接绕出；VFS 重命名会静默覆盖目标路径 |
| P3/风险 | 3 | postMessage 未校验来源、DevLink 管理接口依赖本机边界、外网代理需限制目标网络 |

## 通讯链路核对

前端 `novaos2/os.js` 的 `netProbe -> pingAt -> netCall/netFetch` 负责同源、上次成功地址和局域网地址探测；后端 `novacore/proxy_app.py` 按 `/api`、`/sys`、`/novel`、`/lan` 分发。业务 API 主要由 `novacore/sources.py` 提供，系统更新/备份/应用仓库由 `novacore/toolkit.py` 提供。

已核对的正常点：API JSON 响应默认 `no-store`；`/sys/update` 只刷新 HTML 本地资源引用版本号；VFS 使用 IndexedDB 并保留 localStorage 降级；备份上传路径有 `_safe_rel` 校验；应用包清单和压缩包内部路径有基本校验；目标同步目录关键文件 SHA-256 一致。

## 已确认问题

### P1-1：所有 POST 失败都会自动重试，写操作可能重复执行

位置：`novaos2/os.js:358-405` 的 `rawCall/netCall`。

`netCall` 对 `fs.put/del/mkdir/rename`、FTP/SMB 写操作以及 DevLink 等 POST，在响应丢失、网关返回非 JSON 或连接中断时会重新探测并再次提交。后端没有通用 request-id/幂等键，文件写入、删除、目录创建、重命名可能已经在服务端成功后才丢响应，重试会造成重复副作用或覆盖。建议只对明确幂等的探测/读取请求自动重试；写操作使用请求 ID、条件版本或由调用方提示重试。

### P2-1：`fs_root` 限制未解析符号链接，可绕出配置根目录

位置：`novacore/sources.py:_safe_fs_path`。

该函数使用 `abspath(join(root, rel))` 做字符串前缀校验，但没有对根目录和目标路径调用 `realpath`。当 `fs_root` 内存在指向外部目录的符号链接时，`list/get/put/del/mkdir/rename` 可通过该链接访问根目录外文件。若产品允许用户配置或写入 `fs_root`，这是实际的越界风险。应使用 `realpath` 后再做 `commonpath` 校验，并对写入目标的父目录做同样检查；Windows 还应覆盖 junction/reparse point 场景。

### P2-2：VFS 重命名目标存在时静默覆盖元数据和 Blob

位置：`novaos2/os.js` 的 IndexedDB `rename` 和 localStorage `legacyVfs.rename`。

重命名文件或目录时没有检查 `newPath` 及其子树是否已存在，直接写入 `m.files[newPath]`、`bs.put(..., newPath)`。目标冲突时旧目标内容会被替换，目录移动还可能只保留部分来源树，界面没有覆盖确认。应在事务内先检测目标及冲突子项，默认返回“目标已存在”，由前端显式确认覆盖；覆盖模式也应先删除完整目标子树并保证 Blob/元数据一致。

## 中低优先级风险与设计缺陷

### P3-1：桥接消息使用 `*` 且未校验 source/origin

位置：`novaos2/lib/quickdraw/index.html:178-205`、`novaos2/lib/phigros/index.html:248`、`novaos2/os.js:3034` 及多个 `postMessage` 调用。

QuickDraw 的回复监听只按 `__qd` 和操作名匹配，Phi 的关闭消息只检查 payload；没有验证 `event.source`、允许的 `event.origin` 或每次请求的随机 ID。当前应用通常运行在同源 iframe，功能上可工作，但恶意或错误 iframe 可伪造回复、触发关闭/释放或干扰桥接状态。应保存宿主窗口引用、使用明确 origin，并加入 request ID。

### P3-2：DevLink 是高权限执行桥，安全性依赖“仅本机管理端”边界

位置：`novacore/admin_app.py:/api/dev/exec`、`novacore/devmode.py`。

管理端命令可在平板上执行 `eval`、VFS/localStorage 操作、reload 等高权限动作。代码没有额外认证，安全假设是 admin 只绑定 127.0.0.1；该绑定和防火墙配置应在部署检查中强制验证。若管理端被 LAN 暴露，等同于远程控制平板。

### P3-3：外网代理允许任意 HTTP/HTTPS URL

位置：`novacore/sources.py:api_web`。

这是产品功能的一部分，但目前只限制协议和大小，没有目标网段黑名单。部署在可访问内网的电脑上时，应阻止 localhost、链路本地、RFC1918、云元数据地址及重定向后的私网地址，避免 SSRF。

## 需要现场验证的项目

1. IndexedDB 被其他页面占用导致 `onblocked` 时，前端会整体降级到 localStorage；需在目标 WebView 验证用户是否能看到明确的降级提示，以及旧数据是否仍可读。
2. 系统更新在版本号刷新成功、应用仓库更新失败时会返回 `ok=true` 并带 `appErrors`；当前设置页面会提示“完成”并允许 reload，需要产品确认是否应把部分失败显示为警告而非成功。
3. `netProbe` 并行接受第一个 `{ok:true}` 的局域网响应；在不可信 Wi-Fi 上应考虑服务签名或更强的身份校验。

## 已执行验证

- `node --check`：核心及应用 JavaScript 语法通过。
- Python 编译/导入检查：通过。
- `git diff --check`：通过。
- `build_all.bat /nort`：通过，生成 `dist/TzyOS.zip`。
- 本地与 `\\10.10.28.10\Temp\TZY\NovaOS-Stealth` 的关键文件 SHA-256 已核对一致。

本报告是静态审查和受控构建验证结果；未在真实平板、FTP/SMB 服务器和不可信 Wi-Fi 中进行破坏性操作复现。

## 建议修复顺序

1. 先修复写请求重试策略，增加幂等性测试。
2. 修复 `realpath/commonpath` 文件边界检查，并补充 Windows junction 测试。
3. 给 VFS rename 增加目标冲突检测、显式覆盖选项和事务测试。
4. 收紧 postMessage、DevLink 和外网代理的部署安全边界。
