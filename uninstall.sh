#!/bin/sh
# Remove go2-npu. The driver cannot be unloaded while running: reboot afterwards.
set -eu
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }
systemctl disable go2-npu.service 2>/dev/null || true
rm -f /etc/systemd/system/go2-npu.service /usr/local/bin/go2-npu
systemctl daemon-reload
rm -rf /opt/go2-npu
echo "removed. Reboot to switch the NPU off again (the stock state)."
