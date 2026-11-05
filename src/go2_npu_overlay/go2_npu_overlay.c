// SPDX-License-Identifier: GPL-2.0
/*
 * go2_npu_overlay: apply a device-tree overlay file at runtime on the Unitree Go2
 * (5.10.176-rt86+, which has OF_OVERLAY but no OF_CONFIGFS).
 *
 *   insmod go2_npu_overlay.ko path=/opt/go2-npu/overlays/npu-dvfs.dtbo
 *
 * There is deliberately NO module_exit: removing the overlay tears down the NPU and
 * IOMMU devices underneath their users and hangs the board, so this module cannot be
 * unloaded. A reboot undoes everything.
 *
 * WARNING: on this 5.10 kernel an overlay that is rejected during init (for example one
 * that carries a /__symbols__ node) is freed twice by of_overlay_fdt_apply() and panics
 * the kernel. Only load the overlays shipped with this package.
 */
#include <linux/module.h>
#include <linux/of.h>
#include <linux/vmalloc.h>
#include <linux/kernel_read_file.h>

static char *path;
module_param(path, charp, 0444);
MODULE_PARM_DESC(path, "absolute path of the .dtbo to apply");

static int ovcs_id;

static int __init go2_npu_overlay_init(void)
{
	void *fdt = NULL;
	size_t size;
	int ret;

	if (!path)
		return -EINVAL;
	ret = kernel_read_file_from_path(path, 0, &fdt, INT_MAX, &size, READING_UNKNOWN);
	if (ret < 0) {
		pr_err("go2_npu_overlay: read %s failed: %d\n", path, ret);
		return ret;
	}
	ret = of_overlay_fdt_apply(fdt, size, &ovcs_id);
	if (ret) {
		pr_err("go2_npu_overlay: apply %s failed: %d\n", path, ret);
		return ret;	/* fdt ownership is unclear after a failed apply on 5.10; leak it */
	}
	/* the applied overlay keeps referencing fdt: never free it */
	pr_info("go2_npu_overlay: %s applied (changeset %d)\n", path, ovcs_id);
	return 0;
}

module_init(go2_npu_overlay_init);
MODULE_DESCRIPTION("Apply the Go2 NPU device-tree overlay (non-removable)");
MODULE_LICENSE("GPL");
