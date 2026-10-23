#!/bin/bash
# 安装每天 09:00 自动续跑的 systemd timer（需要 root）
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$(command -v python3)"
NODE="/tmp/node-v20.18.1-linux-x64/bin"

cat > /etc/systemd/system/tzy-verify.service <<SVC
[Unit]
Description=Tzy OS browser verification (resumable)
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$ROOT
Environment=PATH=$NODE:/usr/local/bin:/usr/bin:/bin
ExecStart=$PY $ROOT/verify/runner.py --max-seconds 36000
StandardOutput=append:$ROOT/verify/logs/service.log
StandardError=append:$ROOT/verify/logs/service.log
TimeoutStartSec=infinity
SVC

cat > /etc/systemd/system/tzy-verify.timer <<'TMR'
[Unit]
Description=Run Tzy OS verification daily at 09:00

[Timer]
OnCalendar=*-*-* 09:00:00
Persistent=true
Unit=tzy-verify.service

[Install]
WantedBy=timers.target
TMR

systemctl daemon-reload
systemctl enable tzy-verify.timer
systemctl start tzy-verify.timer
systemctl list-timers tzy-verify.timer --no-pager
