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

## 2026-10-12 文件管理器 3.4.0

- VFS 文件和文件夹的操作按钮已统一改为“复制到…”，打开独立目标选择窗口，不再要求输入目标序号。
- 目标窗口可切换电脑、FTP、SMB，并读取对应源的目录列表、逐级进入文件夹、通过面包屑或“上一级”返回，最后选择“复制到此目录”。
- FTP/SMB 使用文件管理器中已保存的连接信息并从已配置的起始目录打开；未配置或目录读取失败时在窗口内明确提示并禁止确认。
- 支持 VFS 文件和文件夹递归复制到电脑、FTP、SMB；目标目录创建或文件写入失败会终止并显示原因。
- 文件管理器版本提升至 3.4.0，`nova.html` 的加载版本号已更新，避免平板继续使用旧脚本。
- `files.js` 和 `os.js` 均通过 `node --check`，关键路径静态检查与 `git diff --check` 通过；构建后的 `files.js`、`nova.html` 与源码哈希一致。
- 默认 `build_all.bat` 成功，包内运行时冒烟测试输出 `package import ok`，`dist/TzyOS/apps_repo` 仍只有 9 个最新版应用包。
- 本次 `dist/TzyOS.zip` 大小为 20,761,701 字节，SHA-256 为 `DD07C53540F6CDA352F9D2C59787B359AC2B3B7EBED6B6A1756EBB1678A7FA2F`。
- 按要求仅同步到 SMB 目标 `\\10.10.28.10\Temp\TZY\NovaOS-Stealth`，未访问已拔出的 U 盘；目标中的 `files.js`、`nova.html`、`dist/TzyOS.zip` 哈希均与源目录一致，发布目录为 9 个最新版应用包，完整项目和发布树 dry-run 均返回 0。

## 2026-10-12 文件管理器 3.4.1 与 FTP 目录修复

- VFS“复制到…”目标窗口新增 VFS 页签，现在可在电脑、VFS、FTP、SMB 四类目标中逐级选择目录；复制 VFS 文件夹到自身或其子目录会被禁止。
- FTP 连接窗口新增“文件名编码”，默认自动识别；中文乱码时可明确填写 `cp936`，UTF-8 服务也可填写 `utf-8`。
- FTP 服务端会根据 `FEAT` 自动选择 UTF-8 或 CP936；自动模式遇到解码错误时会以 CP936 重试目录列表。
- 修复不支持 `MLSD` 的 FTP 服务：`NLST` 回退现在会逐项判断目录，不再把子目录全部标为文件，因此子目录可以进入。
- 文件管理器版本提升至 3.4.1，并更新 `nova.html` 加载版本号；`files.js` 通过 `node --check`，`sources.py` 通过 `py_compile`，模拟 FTP 中文目录与文件识别测试通过。
- 默认 `build_all.bat` 成功，包内冒烟测试输出 `package import ok`，源码与发布目录的相关文件哈希一致，发布目录仍只有 9 个最新版应用包。
- 本次 `dist/TzyOS.zip` 大小为 20,762,897 字节，SHA-256 为 `9DA58210E9E5C9655862E12A0BBFB001A78F11467A8E32C6D75A3748161B90DA`。
- 本版仅同步到 SMB 目标 `\\10.10.28.10\Temp\TZY\NovaOS-Stealth`；目标中的文件管理器、入口、FTP 后端和 ZIP 哈希均与源一致，`dist/TzyOS/apps_repo` 为 9 个最新版包，完整项目与发布树 dry-run 均返回 0。

## 2026-10-12 NovelDL TXT 文本兼容修复

- 已定位文本编辑器提示“不是文本文件”的原因：NovelDL 1.0.1 将 TXT 转为 Base64 后通过 VFS 的 `data` 字段保存，VFS 因而把节点记为二进制 `kind: "bin"`；电脑直接复制的 TXT 使用文本路径，所以可正常打开。
- 新增 `noveldl-1.0.2.tzyp`：TXT、Markdown、日志、CSV、JSON 等文本导出改用 Blob 文本解码并通过 VFS `text` 字段保存；EPUB 等非文本格式仍使用 Base64 二进制路径。
- 系统 VFS 增加历史缓存兼容：除 `kind: "text"` 外，还会依据 `text/*` MIME 和文本扩展名读取为文本，因此 NovelDL 1.0.1 已缓存的 `.txt` 无需重新下载即可由文本编辑器打开。
- `os.js` 与 NovelDL 1.0.2 `main.js` 均通过 `node --check`；旧二进制 TXT、`text/plain` MIME 和 EPUB 反例的兼容逻辑测试通过；`git diff --check` 通过。
- `build_all.bat` 构建成功，包内运行时 smoke test 输出 `package import ok`；源与发布树的 `os.js` 及 NovelDL 1.0.2 包哈希一致。
- `dist/TzyOS/apps_repo` 仅保留按应用 ID 选择的 9 个最新包，其中 NovelDL 为 1.0.2；纳入构建时已稳定的并发工作树修改后，最终 `dist/TzyOS.zip` 大小 20,762,824 字节，SHA-256 为 `7E6FC59B6C4992B1CA6BC5ACE1C8CE61803178581D3658CE4475A9B1713138FD`。
- 本次按要求只同步到 SMB `\\10.10.28.10\Temp\TZY\NovaOS-Stealth`，不访问已拔出的 U 盘。
