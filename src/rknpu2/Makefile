# Modified 2026-11 by Konstantin Severov: builds rknpu 0.9.8 as the out-of-tree module rknpu2.ko for the Unitree Go2 (see 0001-go2-module-port.patch).
obj-m := rknpu2.o
# keep build-machine paths out of __FILE__ (warning messages)
ccflags-y += -fmacro-prefix-map=$(src)/=
ccflags-y += -I$(src)/include -DMONITOR_TYPE_DEV=MONITOR_TPYE_DEV -include $(src)/rknpu_compat.h -DCONFIG_ROCKCHIP_RKNPU=1 -DCONFIG_ROCKCHIP_RKNPU_DRM_GEM=1 -DCONFIG_ROCKCHIP_RKNPU_DEBUG_FS=1
rknpu2-y += rknpu_drv.o rknpu_reset.o rknpu_job.o rknpu_debugger.o rknpu_iommu.o rknpu_devfreq.o rknpu_gem.o
