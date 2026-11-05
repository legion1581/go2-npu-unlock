#!/bin/sh
# Rebuild modules/rknpu2.ko and modules/go2_npu_overlay.ko for the Go2 kernel 5.10.176-rt86+.
#   build/build.sh <linux-stable-rt checkout at v5.10.176-rt86> [cross-compile prefix]
# The checkout must be a git tree: the kernel then reports itself as "5.10.176-rt86+" (the "+" marks a
# modified tree), which is exactly the Go2's release string the modules must carry.
# Toolchain used for the release: Arm GNU Toolchain 10.3-2021.07 (aarch64-none-linux-gnu-).
set -eu
LINUX=$(readlink -f "$1")
CROSS=${2:-aarch64-none-linux-gnu-}
HERE=$(cd "$(dirname "$0")/.." && pwd)
OUT=${OUT:-$HERE/build/out}
[ "$(git -C "$LINUX" describe --tags 2>/dev/null)" = v5.10.176-rt86 ] || { echo "$LINUX is not a git checkout of v5.10.176-rt86"; exit 1; }
if git -C "$LINUX" diff --quiet; then
	echo "applying build/kernel-recon.patch.xz (Rockchip drivers the Go2 kernel carries)"
	xzcat "$HERE/build/kernel-recon.patch.xz" | git -C "$LINUX" apply
fi
mkdir -p "$OUT/kbuild"
cp "$HERE/build/kbuild.config" "$OUT/kbuild/.config"
make -s -C "$LINUX" O="$OUT/kbuild" ARCH=arm64 CROSS_COMPILE="$CROSS" olddefconfig modules_prepare
# symbol CRCs of the robot's own kernel, so every import matches it
cp "$HERE/build/Module.symvers" "$OUT/kbuild/Module.symvers"
for m in rknpu2 go2_npu_overlay; do
	rm -rf "$OUT/$m"; cp -r "$HERE/src/$m" "$OUT/$m"; rm -f "$OUT/$m"/*.patch
	make -s -C "$LINUX" O="$OUT/kbuild" ARCH=arm64 CROSS_COMPILE="$CROSS" M="$OUT/$m" modules
	# the released modules carry no debug info (it would only hold build-machine paths)
	"${CROSS}strip" --strip-debug "$OUT/$m/$m.ko"
done
modinfo "$OUT/rknpu2/rknpu2.ko" | grep vermagic
echo "built: $OUT/rknpu2/rknpu2.ko $OUT/go2_npu_overlay/go2_npu_overlay.ko"
