#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在非 Windows 主机上重现 build_all.bat 的打包语义。

build_all.bat 依赖 PowerShell/.NET，Linux 跑不了；本脚本按同样的规则
组装 dist/TzyOS 并压缩为 dist/TzyOS.zip：
  * 目录白名单：novacore novelsrc novaos2 admin runtime tools
  * apps_repo 每个 id 只留版本最高的 .tzyp（等价 build_select_apps.ps1）
  * 必带文件清单；缺失必需文件直接失败
  * zip 条目用正斜杠、不带顶层目录、Optimal 压缩
"""
import json, os, re, shutil, sys, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, 'dist', 'TzyOS')


def read_version():
    """从 novaos2/os.js 读取系统版本，作为发布包文件名。

    版本号只有一处定义（OS_VERSION / OS_BUILD），打包时自动取，
    避免脚本里再抄一份导致漂移。"""
    src = open(os.path.join(ROOT, 'novaos2', 'os.js'), encoding='utf-8').read()
    m = re.search(r'var OS_VERSION = "([^"]+)"', src)
    b = re.search(r'var OS_BUILD = "([^"]+)"', src)
    if not m:
        raise SystemExit('未能从 novaos2/os.js 读取 OS_VERSION')
    return m.group(1), (b.group(1) if b else '')


VERSION, BUILD = read_version()
ZIP = os.path.join(ROOT, 'dist', 'TzyOS-v%s.zip' % VERSION)
SKIP_DIRS = {'__pycache__', '.git'}
SKIP_EXT = {'.pyc', '.log'}
DIRS = ('novacore', 'novelsrc', 'novaos2', 'admin', 'runtime', 'tools')
FILES = ('config.json', 'loader.js', 'boot-stub.js', 'bridge.js', 'novaosd.py',
         'server.py', 'novel_server.py', 'requirements.txt', 'README.md',
         'start.bat', 'install.bat', 'stop-hotspot.bat', 'enable-hotspot.ps1',
         'disable-hotspot.ps1', 'set-hotspot-credentials.ps1')
REQUIRED = ('config.json', 'loader.js', 'boot-stub.js', 'bridge.js',
            'novaosd.py', 'novaos2/nova.html')


def copytree(src, dst):
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        rel = os.path.relpath(dirpath, src)
        out = dst if rel == '.' else os.path.join(dst, rel)
        os.makedirs(out, exist_ok=True)
        for f in filenames:
            if os.path.splitext(f)[1] in SKIP_EXT:
                continue
            shutil.copy2(os.path.join(dirpath, f), os.path.join(out, f))


def main():
    if os.path.isdir(PKG):
        shutil.rmtree(PKG)
    os.makedirs(PKG)

    for d in DIRS:
        src = os.path.join(ROOT, d)
        if os.path.isdir(src):
            copytree(src, os.path.join(PKG, d))
            print('  + ' + d)
        else:
            print('  [i] 跳过不存在的目录 ' + d)

    repo_src = os.path.join(ROOT, 'apps_repo')
    repo_dst = os.path.join(PKG, 'apps_repo')
    os.makedirs(repo_dst, exist_ok=True)
    best = {}
    for name in os.listdir(repo_src):
        if not name.endswith('.tzyp'):
            continue
        fp = os.path.join(repo_src, name)
        try:
            with zipfile.ZipFile(fp) as z:
                meta = json.loads(z.read('app.json').decode('utf-8'))
        except Exception as e:
            print('  ! 跳过无效包 %s: %s' % (name, e))
            continue
        app_id = str(meta.get('id') or '')
        if not app_id:
            continue
        ver = tuple(int(x) if str(x).isdigit() else 0
                    for x in str(meta.get('version', '0')).split('.'))
        if app_id not in best or ver > best[app_id][0]:
            best[app_id] = (ver, name, fp)
    for app_id, (ver, name, fp) in sorted(best.items()):
        shutil.copy2(fp, os.path.join(repo_dst, name))
    print('  apps_repo: %d 个（每应用取最高版本）' % len(best))

    for f in FILES:
        src = os.path.join(ROOT, f)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(PKG, f))
        else:
            print('  [i] 缺少 ' + f)

    missing = [r for r in REQUIRED if not os.path.isfile(os.path.join(PKG, r))]
    if missing:
        print('!! 缺少必需文件: %s' % missing)
        return 1

    # 清掉同前缀的旧包，避免 dist 里堆积多个版本
    for old in os.listdir(os.path.dirname(ZIP)):
        if old.startswith('TzyOS-v') and old.endswith('.zip'):
            try:
                os.remove(os.path.join(os.path.dirname(ZIP), old))
            except OSError:
                pass
    n = 0
    with zipfile.ZipFile(ZIP, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for dirpath, dirnames, filenames in os.walk(PKG):
            dirnames.sort()
            for f in sorted(filenames):
                full = os.path.join(dirpath, f)
                z.write(full, os.path.relpath(full, PKG).replace(os.sep, '/'))
                n += 1
    print('[*] 版本 %s%s' % (VERSION, (' (build ' + BUILD + ')') if BUILD else ''))
    print('[*] %s  (%d 文件, %.1f MB)' % (ZIP, n, os.path.getsize(ZIP) / 1048576))

    # 写一份版本信息，供发布脚本与后续核对使用
    info = os.path.join(os.path.dirname(ZIP), 'latest.json')
    with open(info, 'w', encoding='utf-8') as f:
        json.dump({'version': VERSION, 'build': BUILD,
                   'zip': os.path.basename(ZIP),
                   'size': os.path.getsize(ZIP)}, f, ensure_ascii=False, indent=1)
    print('[*] %s' % info)
    return 0


if __name__ == '__main__':
    sys.exit(main())
