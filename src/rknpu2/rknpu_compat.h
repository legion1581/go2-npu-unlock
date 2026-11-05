/* SPDX-License-Identifier: GPL-2.0 */
/* Go2 port of rknpu 0.9.8 as a loadable module on the stock 5.10.176-rt86+ kernel.
 * Three symbols the stock kernel does not export are reached through addresses
 * handed over as module parameters (read from /proc/kallsyms by the loader);
 * when a parameter is 0 the corresponding feature is a no-op. */
#ifndef RKNPU_COMPAT_H
#define RKNPU_COMPAT_H
#include <linux/types.h>
#include <linux/mm.h>
#include <linux/device.h>
extern unsigned long rknpu_sym_dma_map_area, rknpu_sym_dma_unmap_area, rknpu_sym_iommu_setup_dma_ops;
static inline void rknpu_compat_dma_map_area(const void *start, size_t size, int dir)
{ void (*f)(const void *, size_t, int) = (void *)rknpu_sym_dma_map_area; if (f) f(start, size, dir); }
static inline void rknpu_compat_dma_unmap_area(const void *start, size_t size, int dir)
{ void (*f)(const void *, size_t, int) = (void *)rknpu_sym_dma_unmap_area; if (f) f(start, size, dir); }
static inline void rknpu_compat_iommu_setup_dma_ops(struct device *dev, u64 base, u64 limit)
{ void (*f)(struct device *, u64, u64) = (void *)rknpu_sym_iommu_setup_dma_ops; if (f) f(dev, base, limit); else dev_warn(dev, "rknpu: iommu_setup_dma_ops address not provided; extra iommu domains unsupported\n"); }

#endif
