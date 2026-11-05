#!/bin/sh
# Install go2-npu on a Unitree Go2 (run on the robot as root, from the unpacked package).
#   ./install.sh          install + enable at boot (the NPU comes up on the next boot)
#   ./install.sh --now    also enable the NPU right away if that is safe in this boot
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
PREFIX=/opt/go2-npu
NOW=0; [ "${1:-}" = "--now" ] && NOW=1

[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }
[ "$(uname -m)" = aarch64 ] || { echo "this is not the robot (not aarch64)"; exit 1; }

# A previous experimental install (go2-npu-enable.service + npu_enable_dvfs.ko) enables the NPU
# with the old built-in 0.8.2 driver. It must go, or the two would fight over the NPU.
if [ -e /etc/systemd/system/go2-npu-enable.service ] || [ -e /lib/systemd/system/go2-npu-enable.service ]; then
	echo "removing the old go2-npu-enable.service (built-in 0.8.2 driver path)"
	systemctl disable --now go2-npu-enable.service 2>/dev/null || true
	rm -f /etc/systemd/system/go2-npu-enable.service /lib/systemd/system/go2-npu-enable.service
fi
if [ -d "$PREFIX" ] && [ ! -x "$PREFIX/go2-npu" ]; then
	B="$PREFIX.old-$(date +%Y%m%d-%H%M%S)"
	echo "moving the old $PREFIX to $B"
	mv "$PREFIX" "$B"
fi

echo "installing to $PREFIX"
mkdir -p "$PREFIX/modules" "$PREFIX/overlays"
install -m 0644 "$HERE"/modules/rknpu2.ko "$HERE"/modules/go2_npu_overlay.ko "$PREFIX/modules/"
install -m 0644 "$HERE"/overlays/npu-dvfs.dtbo "$HERE"/overlays/npu-fixed.dtbo "$PREFIX/overlays/"
install -m 0755 "$HERE/go2-npu" "$PREFIX/go2-npu"
install -m 0644 "$HERE/README.md" "$PREFIX/README.md"
install -m 0644 "$HERE/systemd/go2-npu.service" /etc/systemd/system/go2-npu.service
ln -sf "$PREFIX/go2-npu" /usr/local/bin/go2-npu
rm -f "$PREFIX/DISABLE"
systemctl daemon-reload
systemctl enable go2-npu.service

echo
"$PREFIX/go2-npu" detect || true
echo
if [ $NOW = 1 ]; then
	if "$PREFIX/go2-npu" detect >/dev/null 2>&1; then
		systemctl start go2-npu.service && "$PREFIX/go2-npu" status
	else
		echo "not starting now (see the checks above); reboot and it will start at boot"
	fi
else
	echo "installed. Reboot, or run: systemctl start go2-npu"
fi
