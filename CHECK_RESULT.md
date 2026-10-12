# NovaOS-Stealth 当前进度与检查结果

检查日期：2026-10-12

## 当前进度

- LAN Chat 后端已接入主 HTTP 挂载点，支持设备注册、六位配对码、长轮询、WebRTC 信令和消息中继。
- LAN Chat 1.1.0 已增加文件传输：优先 WebRTC DataChannel 分块直传；不可用时由电脑节点暂存中继。中继文件限制 100 MB、保存 30 分钟，并校验目标设备身份，接收完成后删除。
- Learning Toolbox 3.5.0 已增加“新评测”菜单，支持任务列表、题目详情、考试概览、题目分析和客观题答案导出；未加入分享功能。
- Learning Toolbox 云笔记已增加源文件下载，调用 `Resources/GetByFileId` 后忽略 `resourceType=2` 缩略图，只下载源资源。
- Learning Toolbox 继续作为 `apps_repo` 中的预装应用包（`preinstall: true`），未并入 `novaos2/apps` 系统应用。
- Phigros 已包含另一工作进程完成的触控释放和 iframe 内伪全屏修复，退出/重开应用时会释放游戏输入占用。
- `apps_repo` 下全部 38 个 `.tzyp` 应用包现已纳入 Git 跟踪范围，共约 27.4 MB。

## 已完成验证

- LAN Chat 注册、配对、轮询、消息中继和 WebRTC 信令的模拟流程通过。
- LAN Chat 文件中继的上传、接收通知、目标设备鉴权、下载和下载后删除流程通过；非目标设备下载返回 404。
- `lan-chat-1.1.0.tzyp` 与 `learning-toolbox-3.5.0.tzyp` 包结构和应用仓库最高版本识别通过。
- 新增/修改的 Python 文件通过 `py_compile`。
- LAN Chat 1.1.0 和 Learning Toolbox 3.5.0 的 JavaScript 通过 `node --check`。

## 构建与同步

- 默认 `build_all.bat` 已成功完成，包内运行时 smoke test 输出 `package import ok`。
- 已生成 `dist\TzyOS\` 和 `dist\TzyOS.zip`；部署树含 1,064 个文件、总计 46,574,870 字节，ZIP 为 20,760,806 字节（约 19.8 MB）。
- 源 `apps_repo` 继续保留并由 Git 跟踪全部 38 个历史 `.tzyp`；发行目录与 ZIP 仅包含按应用 ID 选出的 9 个最新版本：Hello 1.0.1、LAN Chat 1.1.0、Learning Toolbox 3.5.0、墨卷 1.0.1、MusicSquare 1.4.1、NovelDL 1.0.1、Phi 1.5.9.6、QuickDraw 0.2.1、VBrowser 2.1.1。
- 最终同步使用的 `dist\TzyOS.zip` SHA-256：`32AE2D6DDB2F9EFAB6D0E1D7485088EEB9AEBB93688BBCE46FDF938DE77AF58F`。
- 全部 Git 跟踪 Python 文件检查：56 个，`py_compile` 失败 0 个。
- 全部 Git 跟踪 JavaScript 文件检查：71 个，`node --check` 失败 0 个。
- 已用 Robocopy `/E` 覆盖同步完整项目树到 `F:\NovaOS-Stealth` 和 `\\10.10.28.10\Temp\TZY\NovaOS-Stealth`，并单独用 `/MIR` 精确同步两个目标的 `dist\TzyOS`，清除了此前进入发行目录的历史包及多余展开目录。
- 最终核验：两个目标的项目根 `apps_repo` 均有 38 个历史 `.tzyp`，`dist\TzyOS\apps_repo` 均只有 9 个最新版；`dist\TzyOS.zip` SHA-256 均与源一致。完整项目 `/E` dry-run 和发行树 `/MIR` dry-run 对两个目标均返回 `0`，没有待同步差异。
- `build/`、`runtime/` 和 `dist/` 属于生成物，不纳入 Git；同步完整项目时按磁盘现状一并复制。

## 2026-10-12 Phi 播放修复复检

- 修复平板最新版 Phi 播放时 `TypeError: Cannot read properties of undefined (reading 'duplicate')`：入口与动态插件统一使用 `index-CfqrayXh.js` 的唯一模块 URL，避免浏览器创建两套核心模块实例和相互隔离的谱面状态。
- Phi 入口版本标记已更新为 `20261012-phi-module-fix1`，保证平板重新获取修复后的入口脚本；核心模块自身不再附加独立查询参数。
- `script-1.5.9.2.js` 已通过 `node --check`，Phi 目录与发布目录中带查询参数的核心模块引用均为 0。
- 重新运行默认 `build_all.bat` 成功，包内运行时冒烟测试输出 `package import ok`；`dist/TzyOS/apps_repo` 仍只包含 9 个应用的最新版。
- 本次 `dist/TzyOS.zip` 大小为 20,760,803 字节，SHA-256 为 `A9B3B790F8E9A2BCDB0CF2CE9764F3FD64F46753AB0E2EF929638838872FF3B8`。
- Phi 修复版已覆盖同步到 `F:\NovaOS-Stealth` 和 `\\10.10.28.10\Temp\TZY\NovaOS-Stealth`；两个目标的 Phi 入口文件与 ZIP 哈希均与源目录一致，`dist/TzyOS/apps_repo` 均为 9 个最新版包，项目与发布树 dry-run 均返回 0。
