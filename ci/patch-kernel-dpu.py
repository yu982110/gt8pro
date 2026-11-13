#!/usr/bin/env python3
"""patch-kernel-dpu.py -- 把参考项目 0217 的 DPU / scanout 显示驱动移植到我们的内核。

参考：refs/data-a-live/xnu/patches/0215-pandora-idle-dpu-probe.patch（只读探针）
      refs/data-a-live/xnu/patches/0217-pandora-scanout.patch（显示控制器直接扫出 iOS 的帧）

背景：ci/patch-kernel-display.py 只有 framebuffer/Surface 层——内核把每一帧 CPU 拷贝进
ABL 的面板显存（PA 0xFC800000），而参考项目在 0217 里让 DPU 的扫描输出管线（VIG/CTL/
LM/INTF）每个 vsync 自己读 GPU 写好的 IOSurface，CPU 不再拷贝。我们没有这段，所以
SpringBoard 活着、屏却是黑的/不刷新。

本脚本做四件事（全部幂等、带 HAKIOS-LAFA-DPU 标记、锚点校验失败返回 1）：
  1. bsd/conf/files：登记 bsd/dev/hakios_dpu.c（新的 C 文件，0215 只读探针 + 0217 scanout）
  2. 写 bsd/dev/hakios_dpu.c：DPU 寄存器表、lafa 面板参数、SMMU bank2 的 4K stage-1
     页表（自带实现，不依赖参考项目 GPU 补丁里的 ArmSmmu4K* 库）、show/panel/current、
     定时采样日志；一切失败都退回原来的 CPU 拷贝
  3. iokit/Kernel/SweetFramebuffer.cpp：在 lazyMap() 后做一次只读 DPU 探针；swap() 里
     单层整屏 surface 走 scanout；vsync 里 scanTick() 回收 hold；free() 里归位
  4. iokit/Kernel/SweetSurface.{h,cpp}：0217 的 use count（显示 hold 住的 surface 对
     IOSurfaceIsInUse 可见），traps 0/1 改成真正的 +1/-1

用法：
    python3 ci/patch-kernel-dpu.py <xnu 源码树> [--check]

    <xnu 源码树>：apply.sh 之后、且已经跑过 ci/patch-kernel-display.py 的
                  hakios/xnu-src/xnu-8796.121.2
    --check     只报告会改什么，不写文件

CI 里（Lead 集成；放在 patch-kernel-display.py 之后、build kernel 之前）：
    python3 ci/patch-kernel-dpu.py hakios/xnu-src/xnu-8796.121.2

输出码：0 = 已就位或本来就是最新，1 = 树不对/锚点找不到/前置条件不满足，2 = 用法错误。

预期日志（第一次真机启动，串口）：
    HAKIOS DPU: MDSS hw ..., DPU top ...
    HAKIOS DPU: ctl0: ... intf ... fetch ... pipe ...
    HAKIOS DPU: vig0 rec0: ... addr fc800000 stride 1680 ... current fc800000
    HAKIOS DPU: t0 / t0+100ms: intf1 frame N -> N+...（帧计数必须前进）
    HAKIOS DPU: SMMU SMR i ... (id 0x800 mask 0x2) S2CR ... (translate, bank 2)
    HAKIOS DPU: bank 2: sctlr ...（translation off，ABL 留下的状态）
    SWEET FB: scanout on: ... frames scanned out ... SMMU faults 0

失败判据（见 docs/DPU-SCANOUT-PORT.md）：帧计数不前进 = 面板根本没在刷新（DSI/显示
电源/loader 交接问题，不在本补丁范围）；"main screen not as expected" = ABL 的 pipe
布局和参考项目不同，把上面 ctl0 行发回来；scanout 后 SMMU fault 非 0 = 页表/ACTLR 错，
日志会打 fault fsr/far，并且自动退回 CPU 拷贝。
"""
import re
import sys
from pathlib import Path

MARK = "HAKIOS-LAFA-DPU"

# ==========================================================================
# 1) bsd/dev/hakios_dpu.c -- 新的 DPU / scanout 驱动
# ==========================================================================
DPU_C = r"""/* HAKIOS-LAFA-DPU: bsd/dev/hakios_dpu.c, written by ci/patch-kernel-dpu.py.
 * The marker in this line is what makes the patch idempotent; leave it. */
/*
 * HAKIOS DPU: the display controller of lafa (realme GT8 Pro, SM8850) -- the
 * part of the same-SoC reference's patch 0217 that lets the display controller
 * scan out iOS's frames itself instead of a CPU copy of every frame into the
 * ABL's panel memory (2.4 ms a frame on the reference).
 *
 * Ported from refs/data-a-live/xnu/patches/0215-pandora-idle-dpu-probe.patch
 * (read-only probe) and 0217-pandora-scanout.patch (SMMU + pipes).  What is the
 * same, because it is the same SoC (SM8850, DPU 13.0, apps SMMU / MMU-500):
 *
 *   - MDSS at 0x09800000 (0x1c8800 bytes), apps SMMU at 0x15000000;
 *   - DPU v13 catalogue offsets: CTL 0x1f000 + 0x1000*n, LM 0x103000 + 0x8000*n,
 *     INTF 0x18d000 + 0x1000*n, a source pipe's registers in its REC0 block at
 *     pipe base + 0x1000 (not at the pipe base), QSEED3 at pipe base + 0x2000;
 *   - the display stream is SID 0x800 mask 0x2, through apps-SMMU bank 2, which
 *     the ABL leaves with stage-1 translation off: the pipes fetch 32-bit
 *     physical addresses (the continuous-splash panel memory at 0xfc800000);
 *   - the bank then gets ArmSmmu4K-format 4K stage-1 tables, and its translation
 *     is turned on with the panel memory mapped at its own address; the stream
 *     route (SMR/S2CR) is not touched.  Mainline Linux keeps the DPU behind its
 *     SMMU with 32-bit IOVAs the same way.
 *
 * What is different for lafa:
 *
 *   - the panel is 1440x3136 (reference 1220x2656), and the number of pipes and
 *     the mixer split are the ABL's: the geometry comes from the framebuffer
 *     (hakios_dpu_scanout_geometry) and the pipes are found in CTL0, instead of
 *     being compiled in;
 *   - this tree has no ArmSmmu4K* library (data-a-live adds it with its GPU
 *     patches), so the register programming is reimplemented here on the
 *     SMMUv2 register map, with the same encoded values the reference validated
 *     by readback on this SoC;
 *   - no pandora_log_sync() (this port has no log-file ring; the serial console
 *     is the log) and no DPU power / DSI / backlight control: this file only
 *     reads what the ABL left running and points those pipes elsewhere.
 *
 * Everything is best effort: every check before the first write is read-only,
 * and any failure after a write puts the registers back the way they were.  A
 * failure returns to the caller and the framebuffer keeps the CPU copy, which
 * is what it did before this file existed.
 */
#include <sys/param.h>
#include <sys/systm.h>
#include <sys/errno.h>
#include <mach/kern_return.h>
#include <mach/vm_param.h>              /* PAGE_MASK, ptoa_64 */
#include <kern/clock.h>                 /* delay() */
#include <pexpert/pexpert.h>            /* PE_parse_boot_argn() */
#include <arm/machine_routines.h>       /* ml_io_map*; we declare the private one */
#include <vm/pmap.h>                    /* kernel_pmap */
#include <vm/vm_kern.h>                 /* kernel_map, kmem_alloc_contig, KMA_* */
#include <arm/caches_internal.h>        /* clean_dcache64() */

#if defined(__arm64__)

/* Both live behind MACH_KERNEL_PRIVATE / PEXPERT_KERNEL_PRIVATE in the headers;
 * bsd/kern/bsd_init.c solves it the same way (see ci/patch-kernel-fb.py). */
extern vm_offset_t ml_io_map_unmappable(vm_offset_t phys_addr, vm_size_t size,
    uint32_t flags);
extern void IOSleep(unsigned int milliseconds);
extern ppnum_t pmap_find_phys(pmap_t pmap, addr64_t va);

#ifndef VM_WIMG_IO
#define VM_WIMG_IO 0x7                  /* <arm/memory_types.h>: coherent|not-cacheable|guarded */
#endif
#ifndef VM_KERN_MEMORY_IOKIT
#define VM_KERN_MEMORY_IOKIT 3          /* <mach/vm_statistics.h> */
#endif

/*
 * Mappings come from ml_io_map_unmappable (kernel_map), not ml_io_map: the
 * latter takes permanent space in the small 8 MB io_submap (the reference ran
 * it out at the first frame and panicked in kmem_alloc).
 */
#define dpu_map(pa, len) ml_io_map_unmappable((vm_offset_t)(pa), (vm_size_t)(len), \
	VM_WIMG_IO)
#define dpu_dsb() __asm__ volatile ("dsb sy" ::: "memory")

/* -------------------------------------------------------------------------- */
/* DPU registers: SM8850 MDSS, DPU 13.0 (mainline dpu_13_0_kaanapali.h and      */
/* dpu_hw_sspp_v13.c names, the reference's 0215 probe offsets).                */
/* -------------------------------------------------------------------------- */
#define DPU_MDSS_PA   0x09800000u
#define DPU_MDSS_LEN  0x001c8800u
#define DPU_MDP       0x1000u                  /* catalogue counts from MDP */
#define DPU_CTL(n)    (DPU_MDP + 0x1f000u + 0x1000u * (n))
#define DPU_LM(n)     (DPU_MDP + 0x103000u + 0x8000u * (n))
#define DPU_INTF(n)   (DPU_MDP + 0x18d000u + 0x1000u * (n))
#define DPU_TEARI(n)  (DPU_MDP + 0x18d800u + 0x1000u * (n))
#define DPU_REC0      0x1000u
#define DPU_REC1      0x3000u
#define DPU_SMMU_PA   0x15000000u
#define DPU_SID       0x800u

/* CTL registers, DPU v13 (dpu_hw_ctl.c) */
#define DPU_CTL_FLUSH        0x18u
#define DPU_CTL_INTF_ACTIVE  0xf4u
#define DPU_CTL_FETCH_ACTIVE 0xfcu
#define DPU_CTL_PIPE_ACTIVE  0x12cu

/* INTF registers (dpu_hw_intf.c) */
#define DPU_INTF_FRAME_COUNT 0xacu
#define DPU_INTF_LINE_COUNT  0xb0u
#define DPU_INTF_TEAR_LINE   0x2b0u
#define DPU_INTF_STATUS      0x26cu
#define DPU_INTF_INTR        0x1c4u
#define DPU_INTF_INTR_EN     0x1c0u
#define DPU_INTF_AUTOREFRESH 0x2b4u

static const struct {
	const char *name;
	uint32_t base;
} dpu_sspp[] = {
	{ "vig0", 0x2b000 }, { "vig1", 0x34000 }, { "vig2", 0x3d000 }, { "vig3", 0x46000 },
	{ "dma0", 0x97000 }, { "dma1", 0xa0000 }, { "dma2", 0xa9000 }, { "dma3", 0xb2000 },
	{ "dma4", 0xbb000 }, { "dma5", 0xc4000 },
};
#define DPU_SSPP_COUNT (sizeof(dpu_sspp) / sizeof(dpu_sspp[0]))

static vm_offset_t dpu_va;

static inline uint32_t
dpu_rd(uint32_t off)
{
	return *(volatile uint32_t *)(uintptr_t)(dpu_va + off);
}

static inline void
dpu_wr(uint32_t off, uint32_t v)
{
	*(volatile uint32_t *)(uintptr_t)(dpu_va + off) = v;
}

/* -------------------------------------------------------------------------- */
/* 0215: read-only probe.  Everything this prints is what the decision below    */
/* needs; it writes no register at all.                                        */
/* -------------------------------------------------------------------------- */

/* What changes in 100 ms: frame/line counters, CTL flush, each pipe's address. */
static void
dpu_sample(const char *when, uint32_t pipes)
{
	printf("HAKIOS DPU: %s: intf1 frame %u line %u tear-line %u status %08x intr %08x\n", when,
	    dpu_rd(DPU_INTF(1) + DPU_INTF_FRAME_COUNT), dpu_rd(DPU_INTF(1) + DPU_INTF_LINE_COUNT),
	    dpu_rd(DPU_INTF(1) + DPU_INTF_TEAR_LINE), dpu_rd(DPU_INTF(1) + DPU_INTF_STATUS),
	    dpu_rd(DPU_INTF(1) + DPU_INTF_INTR));
	printf("HAKIOS DPU: %s: intf2 frame %u line %u | ctl0 flush %08x ctl1 flush %08x\n", when,
	    dpu_rd(DPU_INTF(2) + DPU_INTF_FRAME_COUNT), dpu_rd(DPU_INTF(2) + DPU_INTF_LINE_COUNT),
	    dpu_rd(DPU_CTL(0) + DPU_CTL_FLUSH), dpu_rd(DPU_CTL(1) + DPU_CTL_FLUSH));
	for (unsigned p = 0; p < DPU_SSPP_COUNT; p++) {
		if (pipes & (1u << p)) {
			const uint32_t b = DPU_MDP + dpu_sspp[p].base;
			printf("HAKIOS DPU: %s: %s current %08x\n", when, dpu_sspp[p].name,
			    dpu_rd(b + DPU_REC0 + 0x64));
		}
	}
}

/* The apps-SMMU route of the display stream, read only (0215's dpu_smmu()). */
static void
dpu_smmu_probe(void)
{
	vm_offset_t base = dpu_map(DPU_SMMU_PA, 0x1000);
	if (!base) {
		printf("HAKIOS DPU: apps SMMU not mapped\n");
		return;
	}
	const uint32_t id0 = *(volatile uint32_t *)(uintptr_t)(base + 0x20);
	const uint32_t id1 = *(volatile uint32_t *)(uintptr_t)(base + 0x24);
	const uint32_t id2 = *(volatile uint32_t *)(uintptr_t)(base + 0x28);
	const uint32_t nsmr = id0 & 0xff;
	const uint64_t page = (id1 & (1u << 31)) ? 0x10000 : 0x1000;
	const uint64_t numpage = 1ull << (((id1 >> 28) & 7) + 1);
	printf("HAKIOS DPU: SMMU ID %08x %08x %08x: %u stream matches, %u banks, %lluK granule, %llu bank pages\n",
	    id0, id1, id2, nsmr, id1 & 0xff, (unsigned long long)(page >> 10),
	    (unsigned long long)numpage);
	for (unsigned i = 0; i < nsmr; i++) {
		const uint32_t smr = *(volatile uint32_t *)(uintptr_t)(base + 0x800 + 4 * i);
		const uint32_t s2cr = *(volatile uint32_t *)(uintptr_t)(base + 0xc00 + 4 * i);
		if (!(smr & 0x80000000u)) {
			continue;
		}
		if ((smr & 0x7fff) != DPU_SID || ((smr >> 16) & 0x7fff) != 0x2) {
			continue;
		}
		const uint32_t type = (s2cr >> 16) & 3, bank = s2cr & 0xff;
		printf("HAKIOS DPU: SMMU SMR %u %08x (id %#x mask %#x) S2CR %08x (%s, bank %u)\n", i, smr,
		    smr & 0x7fff, (smr >> 16) & 0x7fff, s2cr,
		    type == 0 ? "translate" : type == 1 ? "bypass" : "fault", bank);
		if (type != 0 || bank > 255) {
			continue;
		}
		vm_offset_t gr1 = dpu_map(DPU_SMMU_PA + page, 0x1000);
		vm_offset_t cbv = dpu_map(DPU_SMMU_PA + numpage * page + bank * page, 0x1000);
		if (!gr1 || !cbv) {
			printf("HAKIOS DPU: SMMU bank %u not mapped\n", bank);
			continue;
		}
		const uint32_t cbar = *(volatile uint32_t *)(uintptr_t)(gr1 + 4 * bank);
		const uint32_t cba2r = *(volatile uint32_t *)(uintptr_t)(gr1 + 0x800 + 4 * bank);
		const uint32_t sctlr = *(volatile uint32_t *)(uintptr_t)(cbv + 0x0);
		const uint64_t ttbr0 = *(volatile uint64_t *)(uintptr_t)(cbv + 0x20);
		printf("HAKIOS DPU: SMMU bank %u: cbar %08x cba2r %08x sctlr %08x (translation %s) "
		    "actlr %08x tcr %08x tcr2 %08x ttbr0 %#llx mair %08x/%08x fsr %08x far %08x%08x\n",
		    bank, cbar, cba2r, sctlr, (sctlr & 1) ? "ON" : "off",
		    *(volatile uint32_t *)(uintptr_t)(cbv + 0x4), *(volatile uint32_t *)(uintptr_t)(cbv + 0x30),
		    *(volatile uint32_t *)(uintptr_t)(cbv + 0x10), ttbr0,
		    *(volatile uint32_t *)(uintptr_t)(cbv + 0x38), *(volatile uint32_t *)(uintptr_t)(cbv + 0x3c),
		    *(volatile uint32_t *)(uintptr_t)(cbv + 0x58),
		    *(volatile uint32_t *)(uintptr_t)(cbv + 0x64), *(volatile uint32_t *)(uintptr_t)(cbv + 0x60));
	}
}

void hakios_dpu_probe(uint64_t fb);
void
hakios_dpu_probe(uint64_t fb)
{
	int on = 1;

	PE_parse_boot_argn("hakios_dpu_probe", &on, sizeof(on));
	if (!on) {
		printf("HAKIOS DPU: probe off (hakios_dpu_probe=0)\n");
		return;
	}
	if (!dpu_va) {
		dpu_va = dpu_map(DPU_MDSS_PA, DPU_MDSS_LEN);
	}
	if (!dpu_va) {
		printf("HAKIOS DPU: MDSS not mapped\n");
		return;
	}
	printf("HAKIOS DPU: MDSS hw %08x, DPU top %08x %08x %08x %08x, boot framebuffer %#llx\n",
	    dpu_rd(0), dpu_rd(DPU_MDP), dpu_rd(DPU_MDP + 4), dpu_rd(DPU_MDP + 8),
	    dpu_rd(DPU_MDP + 0xc), (unsigned long long)fb);

	for (unsigned n = 0; n < 6; n++) {
		const uint32_t c = DPU_CTL(n);
		const uint32_t intf = dpu_rd(c + DPU_CTL_INTF_ACTIVE);
		const uint32_t fetch = dpu_rd(c + DPU_CTL_FETCH_ACTIVE);
		const uint32_t pipe = dpu_rd(c + DPU_CTL_PIPE_ACTIVE);
		if (intf == 0 && fetch == 0 && pipe == 0) {
			continue;
		}
		printf("HAKIOS DPU: ctl%u: top %08x flush %08x start %08x prepare %08x intf %08x fetch %08x "
		    "pipe %08x layer0 %08x\n", n, dpu_rd(c + 0x14), dpu_rd(c + DPU_CTL_FLUSH),
		    dpu_rd(c + 0x1c), dpu_rd(c + 0xd0), intf, fetch, pipe, dpu_rd(c + 0x0));
	}

	uint32_t pipes = 0;
	for (unsigned p = 0; p < DPU_SSPP_COUNT; p++) {
		const uint32_t b = DPU_MDP + dpu_sspp[p].base;
		for (unsigned r = 0; r < 2; r++) {
			const uint32_t q = b + (r ? DPU_REC1 : DPU_REC0);
			const uint32_t size = dpu_rd(q + 0x14), addr = dpu_rd(q + 0x4c);
			const uint32_t cur = dpu_rd(q + 0x64);
			if (size == 0 && addr == 0 && cur == 0) {
				continue;
			}
			pipes |= 1u << p;
			printf("HAKIOS DPU: %s rec%u: format %08x op %08x src %08x xy %08x out %08x out-xy %08x "
			    "addr %08x stride %08x current %08x\n", dpu_sspp[p].name, r, dpu_rd(q + 0x0),
			    dpu_rd(q + 0x8), size, dpu_rd(q + 0x18), dpu_rd(q + 0x1c), dpu_rd(q + 0x20),
			    addr, dpu_rd(q + 0x5c), cur);
		}
	}
	if (!pipes) {
		printf("HAKIOS DPU: no source pipe has an address or size\n");
	}
	for (unsigned n = 0; n < 4; n++) {
		const uint32_t m = DPU_LM(n);
		printf("HAKIOS DPU: lm%u: op %08x out %08x bg-sel %08x stage0 op %08x fg-sel %08x alpha %08x\n",
		    n, dpu_rd(m + 0x0), dpu_rd(m + 0x4), dpu_rd(m + 0x14), dpu_rd(m + 0x40),
		    dpu_rd(m + 0x44), dpu_rd(m + 0x48));
	}
	for (unsigned n = 1; n <= 2; n++) {
		const uint32_t i = DPU_INTF(n);
		printf("HAKIOS DPU: intf%u (dsi%u): engine %08x config %08x cmd-trigger %08x status %08x "
		    "frame %u line %u tear-line %u autorefresh %08x intr-en %08x\n", n, n - 1, dpu_rd(i + 0x0),
		    dpu_rd(i + 0x4), dpu_rd(i + 0x84), dpu_rd(i + DPU_INTF_STATUS), dpu_rd(i + DPU_INTF_FRAME_COUNT),
		    dpu_rd(i + DPU_INTF_LINE_COUNT), dpu_rd(i + DPU_INTF_TEAR_LINE),
		    dpu_rd(i + DPU_INTF_AUTOREFRESH), dpu_rd(i + DPU_INTF_INTR_EN));
	}
	dpu_sample("t0", pipes);
	IOSleep(100);
	dpu_sample("t0+100ms", pipes);
	dpu_smmu_probe();
	printf("HAKIOS DPU: probe done (read only)\n");
}

/* -------------------------------------------------------------------------- */
/* 0217: the scanout.  SMMU bank for the display stream, and the pipes.        */
/* -------------------------------------------------------------------------- */
#define SC_SMMU_BYTES     0x100000u     /* DT apps-smmu@15000000 reg */
#define SC_SMR_VALID      (1u << 31)
/* CB_FSR fault bits as Linux arm-smmu reads them (TF..UUT, SS, MULTI); bits 10:9
 * are FORMAT, set from boot (the reference saw 0x400 before and after with no
 * fault), not a fault. */
#define SC_FSR_FAULT      (0x1feu | (1u << 30) | (1u << 31))
#define SC_ACTLR          0x1u          /* DT qcom,actlr <0x800 0x02 0x01> */
#define SC_TABLE_PAGES    192u
#define SC_SLOT_BASE      0x80000000u
#define SC_SLOT_BYTES     (16u << 20)
#define SC_SLOTS          16u
#define SC_PIPES_MAX      2u
#define SC_PANEL_W_DEF    1440u         /* lafa: realme GT8 Pro, BOE AE084 */
#define SC_PANEL_H_DEF    3136u
#define SC_HALF_W_DEF     720u

/* REC block registers (dpu_hw_sspp_v13.c) */
#define SC_OP_MODE        0x08u
#define SC_SRC_SIZE       0x14u
#define SC_SRC_XY         0x18u
#define SC_OUT_SIZE       0x1cu
#define SC_OUT_XY         0x20u
#define SC_PIX_EXT_LR     0x24u
#define SC_PIX_EXT_TB     0x28u
#define SC_PIX_EXT_LR_ODX 0x40u
#define SC_PIX_EXT_TB_ODX 0x44u
#define SC_SRC0_ADDR      0x4cu
#define SC_YSTRIDE0       0x5cu
#define SC_CURRENT0       0x64u
/* QSEED3 at pipe + 0x2000 (dpu_hw_util.c) */
#define SC_QSEED          0x2000u
#define SC_Q_OP_MODE      0x04u
#define SC_Q_STEP_Y_H     0x10u
#define SC_Q_STEP_Y_V     0x14u
#define SC_Q_STEP_UV_H    0x18u
#define SC_Q_STEP_UV_V    0x1cu
#define SC_Q_PRELOAD      0x20u
#define SC_Q_SRC_SIZE     0x40u
#define SC_Q_SRC_SIZE_UV  0x44u
#define SC_Q_DST_SIZE     0x48u
#define SC_Q_INIT_Y_H     0x90u
#define SC_Q_INIT_Y_V     0x94u
#define SC_Q_INIT_UV_H    0x98u
#define SC_Q_INIT_UV_V    0x9cu
/* SMMUv2 context-bank and global registers */
#define SC_CB_SCTLR       0x0u
#define SC_CB_ACTLR       0x4u
#define SC_CB_TCR2        0x10u
#define SC_CB_TTBR0       0x20u
#define SC_CB_TTBR1       0x28u
#define SC_CB_TCR         0x30u
#define SC_CB_MAIR0       0x38u
#define SC_CB_MAIR1       0x3cu
#define SC_CB_FSR         0x58u
#define SC_CB_TLBIALL     0x618u
#define SC_CB_TLBSYNC     0x7f0u
#define SC_CB_TLBSTATUS   0x7f4u

static const uint32_t sc_save_regs[] = { SC_OP_MODE, SC_SRC_SIZE, SC_SRC_XY, SC_OUT_SIZE, SC_OUT_XY,
	SC_PIX_EXT_LR, SC_PIX_EXT_TB, SC_PIX_EXT_LR_ODX, SC_PIX_EXT_TB_ODX, SC_SRC0_ADDR, SC_YSTRIDE0 };
#define SC_SAVED (sizeof(sc_save_regs) / sizeof(sc_save_regs[0]))

static struct {
	bool ready;                     /* bank on, pipes' panel values saved */
	bool on_panel;                  /* the pipes show the ABL's panel memory */
	vm_offset_t smmu;
	vm_offset_t tva;                /* table pool: virtual and physical (contiguous) */
	uint64_t tpa;
	unsigned tnext;                 /* next free table page in the pool */
	unsigned input_bits, output_bits, start_level;
	unsigned bank, smr, pipes;
	uint32_t panel_s2cr, cb_off, gr1;
	uint32_t panel_w, panel_h, half_w;
	unsigned intf;                  /* the DPU interface CTL0 drives */
	uint32_t flush;                 /* CTL flush bits for these pipes */
	uint32_t rec[SC_PIPES_MAX];
	uint32_t qseed[SC_PIPES_MAX];
	uint32_t saved[SC_PIPES_MAX][SC_SAVED], saved_qop[SC_PIPES_MAX];
	uint32_t panel_addr;            /* what the pipes read before (0xfc800000) */
	bool slot_used[SC_SLOTS];
	uint64_t shows, faults;
} sc = {
	.panel_w = SC_PANEL_W_DEF, .panel_h = SC_PANEL_H_DEF, .half_w = SC_HALF_W_DEF,
};

static inline uint32_t
sc_rd(uint32_t off)
{
	return *(volatile uint32_t *)(uintptr_t)(sc.smmu + off);
}

static inline void
sc_wr(uint32_t off, uint32_t v)
{
	*(volatile uint32_t *)(uintptr_t)(sc.smmu + off) = v;
	dpu_dsb();
}

static inline uint64_t
sc_rd64(uint32_t off)
{
	return *(volatile uint64_t *)(uintptr_t)(sc.smmu + off);
}

static inline void
sc_wr64(uint32_t off, uint64_t v)
{
	*(volatile uint64_t *)(uintptr_t)(sc.smmu + off) = v;
	dpu_dsb();
}

static void sc_unmap(uint64_t iova, uint32_t npages);

/* TLBIALL for the owned bank, then the bounded TLBSYNC (SMMUv2 0x7f0/0x7f4). */
static bool
sc_tlb_sync(void)
{
	sc_wr(sc.cb_off + SC_CB_TLBIALL, 0);
	sc_wr(sc.cb_off + SC_CB_TLBSYNC, 0xffffffffu);
	for (unsigned i = 0; i < 1000; i++) {
		if ((sc_rd(sc.cb_off + SC_CB_TLBSTATUS) & 1) == 0) {
			dpu_dsb();
			return true;
		}
		delay(10);
	}
	return false;
}

/* Descriptor for a display read of a normal, non-cacheable (GPU-written) page;
 * the same bits the reference's ArmSmmu4KBuild produced (AttrIdx 0 = MAIR0 0x44). */
#define SC_LEAF_ATTR ((1ull << 11) | (1ull << 10) | (2u << 8) | (1u << 7) | (1u << 6) | 3u | (3ull << 53))

static void
sc_clean(vm_offset_t entry, unsigned bytes)
{
	/* The pool is physically contiguous; clean by physical address for the SMMU walker. */
	clean_dcache64((addr64_t)(sc.tpa + (uint64_t)(entry - sc.tva)), bytes, TRUE);
}

/* Level-3 table for IOVA, creating the tables above it as needed; NULL when the
 * pool is full.  The level-1 root is indexed by the top input_bits-30 bits, so a
 * 32-bit input uses its first four entries (the panel memory at 0xfc800000 sits
 * in entry 3). */
static uint64_t *
sc_l3(uint64_t iova)
{
	uint64_t *t = (uint64_t *)(uintptr_t)sc.tva;
	for (unsigned level = sc.start_level; level < 3; level++) {
		const unsigned shift = 39 - 9 * level;
		const unsigned idx = (unsigned)((iova >> shift) & 0x1ff);
		if (!t[idx]) {
			if (sc.tnext >= SC_TABLE_PAGES) {
				return NULL;
			}
			t[idx] = (sc.tpa + 0x1000ull * sc.tnext++) | 3;
			sc_clean((vm_offset_t)&t[idx], sizeof(t[idx]));
		}
		t = (uint64_t *)(uintptr_t)(sc.tva + ((t[idx] & 0xfffffffff000ull) - sc.tpa));
	}
	return t;
}

/* Map PAGES (4K physical pages) at IOVA in the bank's tables, read-only for the
 * display; then TLBIALL.  pages == NULL maps PA itself (identity). */
static bool
sc_map(uint64_t iova, const uint64_t *pages, uint64_t pa, uint32_t npages)
{
	for (uint32_t i = 0; i < npages; i++) {
		const uint64_t va = iova + 0x1000ull * i;
		const uint64_t page = pages ? pages[i] : pa + 0x1000ull * i;
		uint64_t *leaf = sc_l3(va);
		if (leaf == NULL) {
			printf("HAKIOS SCANOUT: table pool full at iova %#llx\n",
			    (unsigned long long)va);
			return false;
		}
		leaf[(va >> 12) & 511] = (page & 0xfffffffff000ull) | SC_LEAF_ATTR;
		sc_clean((vm_offset_t)&leaf[(va >> 12) & 511], sizeof(uint64_t));
	}
	dpu_dsb();
	return sc_tlb_sync();
}

static void
sc_unmap(uint64_t iova, uint32_t npages)
{
	for (uint32_t i = 0; i < npages; i++) {
		const uint64_t va = iova + 0x1000ull * i;
		uint64_t *t = (uint64_t *)(uintptr_t)sc.tva;
		unsigned level;
		for (level = sc.start_level; level < 3 && t; level++) {
			const unsigned shift = 39 - 9 * level;
			const uint64_t e = t[(va >> shift) & 0x1ff];
			t = e ? (uint64_t *)(uintptr_t)(sc.tva + ((e & 0xfffffffff000ull) - sc.tpa)) : NULL;
		}
		if (t != NULL) {
			t[(va >> 12) & 511] = 0;
			sc_clean((vm_offset_t)&t[(va >> 12) & 511], sizeof(uint64_t));
		}
	}
	dpu_dsb();
	(void)sc_tlb_sync();
}

/* Until CTL0 has taken the last flush (it clears its bits at the frame that
 * latches them), at most 25 ms. */
static bool
sc_flush_idle(void)
{
	for (unsigned i = 0; i < 250; i++) {
		if ((dpu_rd(DPU_CTL(0) + DPU_CTL_FLUSH) & sc.flush) == 0) {
			return true;
		}
		delay(100);
	}
	return false;
}

static const uint8_t sc_oas_bits[6] = { 32, 36, 40, 42, 44, 48 };

static unsigned
sc_width_code(unsigned bits)
{
	switch (bits) {
	case 32: return 0;
	case 36: return 1;
	case 40: return 2;
	case 42: return 3;
	case 44: return 4;
	case 48: return 5;
	default: return 0xffffffffu;
	}
}

void hakios_dpu_scanout_geometry(uint32_t width, uint32_t height);
void
hakios_dpu_scanout_geometry(uint32_t width, uint32_t height)
{
	if (width >= 2 && height >= 2 && width <= 7680 && height <= 7680) {
		sc.panel_w = width;
		sc.panel_h = height;
		sc.half_w = width / 2;
	}
}

int hakios_dpu_scanout_init(void);
int
hakios_dpu_scanout_init(void)
{
	int on = 1;

	PE_parse_boot_argn("hakios_scanout", &on, sizeof(on));
	if (!on) {
		printf("HAKIOS SCANOUT: off (hakios_scanout=0): CPU copy\n");
		return ENOTSUP;
	}
	if (sc.ready) {
		return 0;
	}
	if (!dpu_va) {
		dpu_va = dpu_map(DPU_MDSS_PA, DPU_MDSS_LEN);
	}
	sc.smmu = dpu_map(DPU_SMMU_PA, SC_SMMU_BYTES);
	if (!dpu_va || !sc.smmu) {
		printf("HAKIOS SCANOUT: MDSS %s, SMMU %s: CPU copy\n",
		    dpu_va ? "mapped" : "not mapped", sc.smmu ? "mapped" : "not mapped");
		return ENOMEM;
	}

	/*
	 * The pipes CTL0 fetches: the ABL's own split.  Reference assumed VIG0+VIG1
	 * (fetch bits 16/17); here the layout is read out, one or two pipes, and
	 * both must read the same panel address.
	 */
	const uint32_t fetch = dpu_rd(DPU_CTL(0) + DPU_CTL_FETCH_ACTIVE);
	const uint32_t intf = dpu_rd(DPU_CTL(0) + DPU_CTL_INTF_ACTIVE);
	unsigned found = 0;
	uint32_t addr0 = 0;
	for (unsigned p = 0; p < DPU_SSPP_COUNT && found < SC_PIPES_MAX; p++) {
		if (!(fetch & (0x10000u << p))) {
			continue;
		}
		const uint32_t base = DPU_MDP + dpu_sspp[p].base;
		const uint32_t a = dpu_rd(base + DPU_REC0 + SC_SRC0_ADDR);
		if (a == 0) {
			continue;
		}
		if (found == 0) {
			addr0 = a;
		}
		if (a != addr0) {
			printf("HAKIOS SCANOUT: ctl0 fetches pipes with different addresses (%08x vs %08x): "
			    "CPU copy\n", addr0, a);
			return ENODEV;
		}
		sc.rec[found] = base + DPU_REC0;
		sc.qseed[found] = p < 4 ? base + SC_QSEED : 0;   /* only VIG has QSEED3 */
		sc.flush |= 1u << p;
		found++;
	}
	if (found == 0) {
		printf("HAKIOS SCANOUT: ctl0 fetches no source pipe (host %08x, intf %08x, fetch %08x): "
		    "CPU copy\n", dpu_rd(DPU_CTL(0) + 0x14), intf, fetch);
		return ENODEV;
	}
	if (sc.qseed[0] == 0) {
		printf("HAKIOS SCANOUT: the fetched pipe is a DMA pipe (no scaler): CPU copy\n");
		return ENODEV;
	}
	sc.pipes = found;
	sc.flush |= 1u << 17;           /* CTL-level flush, as the reference's r2c set */
	if (intf == 0) {
		printf("HAKIOS SCANOUT: ctl0 drives no interface (fetch %08x): CPU copy\n", fetch);
		return ENODEV;
	}
	for (unsigned i = 0; i < 31; i++) {
		if (intf & (1u << i)) {
			sc.intf = i;
			break;
		}
	}
	if (sc.intf < 1 || sc.intf > 2) {
		printf("HAKIOS SCANOUT: ctl0 drives interface %u (intf %08x): CPU copy\n", sc.intf, intf);
		return ENODEV;
	}

	/* The ABL's split: source/out rectangles, and the panel memory it reads. */
	uint64_t lo = ~0ull, hi = 0;
	uint32_t halves = 0;
	for (unsigned p = 0; p < sc.pipes; p++) {
		const uint32_t r = sc.rec[p];
		const uint32_t src = dpu_rd(r + SC_SRC_SIZE), out = dpu_rd(r + SC_OUT_SIZE);
		const uint32_t addr = dpu_rd(r + SC_SRC0_ADDR), stride = dpu_rd(r + SC_YSTRIDE0) & 0xffff;
		const uint32_t outw = out & 0xffff, h = src >> 16;
		const uint64_t end = (uint64_t)addr + (uint64_t)stride * (uint64_t)h;
		if (addr == 0 || stride == 0 || h == 0 || outw == 0) {
			printf("HAKIOS SCANOUT: pipe %u has no rectangle: CPU copy\n", p);
			return ENODEV;
		}
		lo = MIN(lo, (uint64_t)addr);
		hi = MAX(hi, end);
		if (p == 0) {
			sc.half_w = outw;
		}
		halves += outw;
	}
	if (sc.pipes == 1) {
		sc.half_w = sc.panel_w;
	}
	/* The mixers split the panel at its middle (the ABL may round the first half
	 * either way for an odd width). */
	const uint32_t half_def = sc.panel_w / 2;
	const bool split_ok = sc.pipes == 1 ? halves == sc.panel_w :
	    (halves == sc.panel_w && (sc.half_w == half_def || sc.half_w == sc.panel_w - half_def));
	if (!split_ok) {
		printf("HAKIOS SCANOUT: the ABL's pipes cover %u px (%u panel) with widths %u/%u: "
		    "CPU copy\n", halves, sc.panel_w, sc.half_w, halves - sc.half_w);
		return ENODEV;
	}
	lo &= ~0xfffull;
	hi = (hi + 0xfff) & ~0xfffull;
	if (hi > SC_SLOT_BASE && lo < (uint64_t)SC_SLOT_BASE + SC_SLOTS * SC_SLOT_BYTES) {
		printf("HAKIOS SCANOUT: panel memory %#llx-%#llx is inside the surface slots: CPU copy\n",
		    (unsigned long long)lo, (unsigned long long)hi);
		return ENODEV;
	}
	sc.panel_addr = addr0;
	printf("HAKIOS SCANOUT: init at the first frame: %u pipe(s) (flush %08x, interface %u), "
	    "panel %ux%u split %u, panel memory %#llx-%#llx at %08x\n", sc.pipes, sc.flush, sc.intf,
	    sc.panel_w, sc.panel_h, sc.half_w, (unsigned long long)lo, (unsigned long long)hi, addr0);

	/*
	 * The stream's route: its stream match and the bank its S2CR names (bank 2
	 * from the ABL, stage-1 off, so the pipes read physical addresses).  No
	 * other bank is taken: the display's own bank gets the tables and its
	 * translation is turned on, the route stays as it is.
	 */
	const uint32_t id0 = sc_rd(0x20), id1 = sc_rd(0x24), id2 = sc_rd(0x28);
	const uint32_t nsmr = id0 & 0xff;
	const uint64_t page = (id1 & (1u << 31)) ? 0x10000 : 0x1000;
	const uint64_t numpage = 1ull << (((id1 >> 28) & 7) + 1);
	sc.smr = 0xffffffffu;
	for (unsigned i = 0; i < nsmr; i++) {
		const uint32_t smr = sc_rd(0x800 + 4 * i), s2cr = sc_rd(0xc00 + 4 * i);
		if ((smr & SC_SMR_VALID) && (smr & 0x7fff) == DPU_SID && ((smr >> 16) & 0x7fff) == 0x2) {
			sc.smr = i;
			sc.panel_s2cr = s2cr;
		}
	}
	if (sc.smr == 0xffffffffu || ((sc.panel_s2cr >> 16) & 3) != 0) {
		printf("HAKIOS SCANOUT: no translating stream match for %#x (%u, s2cr %08x): CPU copy\n",
		    DPU_SID, sc.smr, sc.panel_s2cr);
		return ENODEV;
	}
	sc.bank = sc.panel_s2cr & 0xff;
	sc.cb_off = (uint32_t)(numpage * page + sc.bank * page);
	sc.gr1 = (uint32_t)page;
	if (sc.cb_off + 0x1000 > SC_SMMU_BYTES) {
		printf("HAKIOS SCANOUT: bank %u at %#x outside the SMMU window: CPU copy\n", sc.bank, sc.cb_off);
		return ENODEV;
	}
	const uint32_t was_sctlr = sc_rd(sc.cb_off + SC_CB_SCTLR);
	const uint32_t was_actlr = sc_rd(sc.cb_off + SC_CB_ACTLR);
	const uint32_t was_cbar = sc_rd(sc.gr1 + 4 * sc.bank);
	const uint32_t was_cba2r = sc_rd(sc.gr1 + 0x800 + 4 * sc.bank);
	if (was_sctlr & 1) {
		printf("HAKIOS SCANOUT: bank %u already translates (sctlr %08x): CPU copy\n", sc.bank, was_sctlr);
		return ENODEV;
	}

	/* Output address size: ID2's PA field, capped at 40 bits (the panel memory is
	 * below 4 GB and iOS's pages below the 40-bit window this SoC has). */
	const unsigned oas_field = (id2 >> 4) & 15;
	const unsigned oas = MIN(oas_field < 6 ? sc_oas_bits[oas_field] : 32, 40);
	if (sc_width_code(oas) == 0xffffffffu) {
		printf("HAKIOS SCANOUT: SMMU ID2 %08x gives no 4K OAS: CPU copy\n", id2);
		return ENODEV;
	}
	sc.input_bits = 32;             /* the pipes present 32-bit addresses */
	sc.output_bits = oas;
	sc.start_level = 4 - (sc.input_bits - 12 + 8) / 9;   /* 1: 32-bit input, L1 root */
	if (sc.start_level > 3) {
		return ENODEV;
	}

	/* Tables: the panel memory at its own address, then 16 MiB surface slots. */
	if (kmem_alloc_contig(kernel_map, &sc.tva, SC_TABLE_PAGES * 0x1000, PAGE_MASK, 0, 0,
	    KMA_DATA | KMA_ZERO | KMA_PERMANENT, VM_KERN_MEMORY_IOKIT) != KERN_SUCCESS) {
		printf("HAKIOS SCANOUT: no %u KiB contiguous table pool: CPU copy\n",
		    (SC_TABLE_PAGES * 0x1000) >> 10);
		return ENOMEM;
	}
	sc.tpa = ptoa_64(pmap_find_phys(kernel_pmap, (addr64_t)sc.tva));
	if (sc.tpa == 0) {
		printf("HAKIOS SCANOUT: table pool has no physical address: CPU copy\n");
		return ENOMEM;
	}
	sc.tnext = 1;                   /* page 0 is the level-1 root */
	clean_dcache64((addr64_t)sc.tpa, SC_TABLE_PAGES * 0x1000, TRUE);
	if (!sc_map(lo, NULL, lo, (uint32_t)((hi - lo) / 0x1000))) {
		printf("HAKIOS SCANOUT: identity tables for the panel memory failed: CPU copy\n");
		return EINVAL;
	}

	/* The context-bank image: the values the reference's ArmSmmu4K encoder wrote
	 * (stage-1 translate, stage-2 bypass, AArch64, 4K TG0/TG1, NC MAIR0 0xff44). */
	const uint32_t want_cbar = 0x1f300u;
	const uint32_t want_cba2r = 1u;
	const uint32_t want_tcr = (2u << 30) | 0x2000u | (64u - sc.input_bits);
	const uint32_t want_tcr2 = (7u << 15) | (1u << 4) | sc_width_code(sc.output_bits);
	const uint32_t want_mair0 = 0xff44u, want_mair1 = 0u;
	const uint32_t want_sctlr = 0x1067u;
	const uint64_t want_ttbr0 = (1ull << 48) | sc.tpa;    /* ASID 1, TTBR0 = table pool */
	/* Disabled bank only: SCTLR = 0 first, then the image, read back, then enable. */
	sc_wr(sc.cb_off + SC_CB_SCTLR, 0);
	if (sc_rd(sc.cb_off + SC_CB_SCTLR) != 0) {
		printf("HAKIOS SCANOUT: bank %u does not stay disabled: CPU copy\n", sc.bank);
		return EIO;
	}
	sc_wr(sc.cb_off + SC_CB_ACTLR, SC_ACTLR);
	sc_wr(sc.gr1 + 0x800 + 4 * sc.bank, want_cba2r);
	sc_wr(sc.gr1 + 4 * sc.bank, want_cbar);
	sc_wr(sc.cb_off + SC_CB_TCR2, want_tcr2);
	sc_wr(sc.cb_off + SC_CB_TCR, want_tcr);
	sc_wr64(sc.cb_off + SC_CB_TTBR0, want_ttbr0);
	sc_wr64(sc.cb_off + SC_CB_TTBR1, 0);
	sc_wr(sc.cb_off + SC_CB_MAIR0, want_mair0);
	sc_wr(sc.cb_off + SC_CB_MAIR1, want_mair1);
	sc_wr(sc.cb_off + SC_CB_FSR, 0xffffffffu);      /* clear any stale fault */
	if (!sc_tlb_sync()) {
		printf("HAKIOS SCANOUT: TLBSYNC timed out: CPU copy\n");
		goto rollback;
	}
	/* Read back while M is still zero; never enable a bad granule.
	 * TCR2.TBI0/1 may read 1 and SCTLR.ASIDPNE may read 0 on this IP (the
	 * reference's readback tolerates exactly those two). */
	{
		const uint32_t r_cbar = sc_rd(sc.gr1 + 4 * sc.bank);
		const uint32_t r_cba2r = sc_rd(sc.gr1 + 0x800 + 4 * sc.bank);
		const uint32_t r_tcr = sc_rd(sc.cb_off + SC_CB_TCR);
		const uint32_t r_tcr2 = sc_rd(sc.cb_off + SC_CB_TCR2);
		const uint64_t r_ttbr0 = sc_rd64(sc.cb_off + SC_CB_TTBR0);
		const uint32_t r_mair0 = sc_rd(sc.cb_off + SC_CB_MAIR0);
		const uint32_t r_mair1 = sc_rd(sc.cb_off + SC_CB_MAIR1);
		const uint32_t r_sctlr = sc_rd(sc.cb_off + SC_CB_SCTLR);
		if (r_cbar != want_cbar || r_cba2r != want_cba2r || r_tcr != want_tcr ||
		    ((r_tcr2 ^ want_tcr2) & ~0x60u) != 0 || r_mair0 != want_mair0 || r_mair1 != want_mair1 ||
		    r_ttbr0 != want_ttbr0 || r_sctlr != 0) {
			printf("HAKIOS SCANOUT: bank %u readback mismatch (cbar %08x/%08x cba2r %08x/%08x "
			    "tcr %08x/%08x tcr2 %08x/%08x ttbr0 %#llx/%#llx mair %08x/%08x sctlr %08x): "
			    "CPU copy\n", sc.bank, r_cbar, want_cbar, r_cba2r, want_cba2r, r_tcr, want_tcr,
			    r_tcr2, want_tcr2, (unsigned long long)r_ttbr0, (unsigned long long)want_ttbr0,
			    r_mair0, want_mair0, r_sctlr);
			goto rollback;
		}
	}
	const uint32_t f0 = dpu_rd(DPU_INTF(sc.intf) + DPU_INTF_FRAME_COUNT);
	sc_wr(sc.cb_off + SC_CB_SCTLR, want_sctlr);
	IOSleep(100);
	{
		const uint32_t sctlr = sc_rd(sc.cb_off + SC_CB_SCTLR);
		const uint32_t fsr = sc_rd(sc.cb_off + SC_CB_FSR);
		const uint32_t f1 = dpu_rd(DPU_INTF(sc.intf) + DPU_INTF_FRAME_COUNT);
		printf("HAKIOS SCANOUT: bank %u: sctlr %08x tcr %08x ttbr0 %#llx; 100 ms later fsr %08x "
		    "far %08x%08x, frames %u -> %u\n", sc.bank, sctlr, sc_rd(sc.cb_off + SC_CB_TCR),
		    (unsigned long long)sc_rd64(sc.cb_off + SC_CB_TTBR0), fsr,
		    sc_rd(sc.cb_off + 0x64), sc_rd(sc.cb_off + 0x60), f0, f1);
		if (sctlr != want_sctlr || (fsr & SC_FSR_FAULT) || f1 == f0) {
			if (f1 == f0) {
				printf("HAKIOS SCANOUT: the panel interface is not counting frames "
				    "(intf %u): that is a display/DSI problem, not scanout\n", sc.intf);
			}
			goto rollback;
		}
		sc_wr(sc.cb_off + SC_CB_FSR, fsr);
	}

	for (unsigned p = 0; p < sc.pipes; p++) {
		for (unsigned r = 0; r < SC_SAVED; r++) {
			sc.saved[p][r] = dpu_rd(sc.rec[p] + sc_save_regs[r]);
		}
		sc.saved_qop[p] = sc.qseed[p] ? dpu_rd(sc.qseed[p] + SC_Q_OP_MODE) : 0;
	}
	sc.on_panel = true;
	sc.ready = true;
	printf("HAKIOS SCANOUT: the display reads through bank %u's tables, no fault: frames can be "
	    "scanned out\n", sc.bank);
	return 0;

rollback:
	/* Back as the ABL left it: translation off, its attributes. */
	sc_wr(sc.cb_off + SC_CB_SCTLR, was_sctlr);
	sc_wr(sc.cb_off + SC_CB_ACTLR, was_actlr);
	sc_wr(sc.gr1 + 4 * sc.bank, was_cbar);
	sc_wr(sc.gr1 + 0x800 + 4 * sc.bank, was_cba2r);
	if (sc_rd(sc.cb_off + SC_CB_FSR)) {
		sc_wr(sc.cb_off + SC_CB_FSR, 0xffffffffu);
	}
	(void)sc_tlb_sync();
	printf("HAKIOS SCANOUT: bank %u back to translation off (sctlr %08x): CPU copy\n",
	    sc.bank, sc_rd(sc.cb_off + SC_CB_SCTLR));
	return EIO;
}

/* A slot of 16 MiB of IOVA with PAGES mapped, or 0. */
uint32_t hakios_dpu_scanout_map(const uint64_t *pages, uint32_t npages);
uint32_t
hakios_dpu_scanout_map(const uint64_t *pages, uint32_t npages)
{
	if (!sc.ready || pages == NULL || npages == 0 || npages > SC_SLOT_BYTES / 0x1000) {
		return 0;
	}
	for (unsigned i = 0; i < SC_SLOTS; i++) {
		if (!sc.slot_used[i]) {
			const uint32_t iova = SC_SLOT_BASE + i * SC_SLOT_BYTES;
			if (!sc_map(iova, pages, 0, npages)) {
				sc_unmap(iova, npages);
				return 0;
			}
			sc.slot_used[i] = true;
			return iova;
		}
	}
	return 0;
}

void hakios_dpu_scanout_unmap(uint32_t iova, uint32_t npages);
void
hakios_dpu_scanout_unmap(uint32_t iova, uint32_t npages)
{
	if (!sc.ready || iova < SC_SLOT_BASE ||
	    iova >= SC_SLOT_BASE + SC_SLOTS * SC_SLOT_BYTES) {
		return;
	}
	sc_unmap(iova, npages);
	sc.slot_used[(iova - SC_SLOT_BASE) / SC_SLOT_BYTES] = false;
}

/* Program one pipe's REC0 block and (when scaling) its QSEED3. */
static void
sc_pipe_show(unsigned p, uint32_t addr, uint32_t stride, uint32_t sx, uint32_t sw, uint32_t dx,
    uint32_t dw, uint32_t dy, uint32_t dh, uint32_t h)
{
	const uint32_t r = sc.rec[p];
	const uint32_t q = sc.qseed[p];

	dpu_wr(r + SC_SRC0_ADDR, addr);
	dpu_wr(r + SC_YSTRIDE0, stride & 0xffff);
	dpu_wr(r + SC_SRC_SIZE, (h << 16) | sw);
	dpu_wr(r + SC_SRC_XY, sx);
	dpu_wr(r + SC_OUT_SIZE, (dh << 16) | dw);
	dpu_wr(r + SC_OUT_XY, (dy << 16) | dx);
	if (dw != sw || dh != h) {
		const uint32_t sh = (uint32_t)(((uint64_t)sw << 21) / dw);
		const uint32_t sv = (uint32_t)(((uint64_t)h << 21) / dh);
		dpu_wr(r + SC_PIX_EXT_LR, 0);
		dpu_wr(r + SC_PIX_EXT_TB, 0);
		dpu_wr(r + SC_PIX_EXT_LR_ODX, 0);
		dpu_wr(r + SC_PIX_EXT_TB_ODX, 0);
		dpu_wr(r + SC_OP_MODE, sc.saved[p][0] | (1u << 31));   /* SW pixel extension override */
		if (q != 0) {
			dpu_wr(q + SC_Q_INIT_Y_H, 0);
			dpu_wr(q + SC_Q_INIT_Y_V, 0);
			dpu_wr(q + SC_Q_INIT_UV_H, 0);
			dpu_wr(q + SC_Q_INIT_UV_V, 0);
			dpu_wr(q + SC_Q_STEP_Y_H, sh & 0xffffff);
			dpu_wr(q + SC_Q_STEP_Y_V, sv & 0xffffff);
			dpu_wr(q + SC_Q_STEP_UV_H, sh & 0xffffff);
			dpu_wr(q + SC_Q_STEP_UV_V, sv & 0xffffff);
			dpu_wr(q + SC_Q_PRELOAD, 4u | (2u << 8) | (4u << 16) | (2u << 24));
			dpu_wr(q + SC_Q_SRC_SIZE, sw | (h << 16));
			dpu_wr(q + SC_Q_SRC_SIZE_UV, sw | (h << 16));
			dpu_wr(q + SC_Q_DST_SIZE, dw | (dh << 16));
			/* enable, RGB filter bilinear (3), blend, non-DX format, alpha bilinear (1) */
			dpu_wr(q + SC_Q_OP_MODE, 1u | (3u << 16) | (1u << 31) | (1u << 14) | (1u << 10) | (1u << 29));
		}
	} else {
		dpu_wr(r + SC_OP_MODE, sc.saved[p][0]);
		if (q != 0) {
			dpu_wr(q + SC_Q_OP_MODE, sc.saved_qop[p]);
		}
	}
}

/*
 * Show a W x H frame at ADDR (IOVA of its first pixel, rows of STRIDE bytes) on
 * the main screen: scaled to the whole panel with FILL, else centred at its own
 * size.  Returns false when the previous flush is still pending.
 */
bool hakios_dpu_scanout_show(uint32_t addr, uint32_t stride, uint32_t w, uint32_t h, bool fill);
bool
hakios_dpu_scanout_show(uint32_t addr, uint32_t stride, uint32_t w, uint32_t h, bool fill)
{
	if (!sc.ready || w < 2 || h == 0 || w > sc.panel_w || h > sc.panel_h || !sc_flush_idle()) {
		return false;
	}
	if (sc.pipes == 2) {
		/* Both modes split at the mixers' boundary (the ABL's own split), as the
		 * ABL's setup does: scaled, the source splits at its middle (mainline
		 * splits a plane the same way); centred, at the column that lands on the
		 * boundary. */
		const uint32_t left = fill ? 0 : (sc.panel_w - w) / 2;
		if (!fill && left >= sc.half_w) {
			return false;
		}
		const uint32_t half = fill ? w / 2 : sc.half_w - left;
		if (half == 0 || half >= w) {
			return false;
		}
		for (unsigned p = 0; p < 2; p++) {
			const uint32_t sx = p ? half : 0, sw = p ? w - half : half;
			if (fill) {
				sc_pipe_show(p, addr, stride, sx, sw, p ? sc.half_w : 0,
				    p ? sc.panel_w - sc.half_w : sc.half_w, 0, sc.panel_h, h);
			} else {
				sc_pipe_show(p, addr, stride, sx, sw, left + sx, sw,
				    (sc.panel_h - h) / 2, h, h);
			}
		}
	} else {
		const uint32_t dx = fill ? 0 : (sc.panel_w - w) / 2;
		const uint32_t dy = fill ? 0 : (sc.panel_h - h) / 2;
		sc_pipe_show(0, addr, stride, 0, w, dx, fill ? sc.panel_w : w, dy,
		    fill ? sc.panel_h : h, h);
	}
	dpu_dsb();
	dpu_wr(DPU_CTL(0) + DPU_CTL_FLUSH, sc.flush);
	sc.on_panel = false;
	sc.shows++;
	return true;
}

/* The ABL's own pipe setup again: the panel memory, unscaled. */
bool hakios_dpu_scanout_panel(void);
bool
hakios_dpu_scanout_panel(void)
{
	if (!sc.ready || sc.on_panel) {
		return true;
	}
	if (!sc_flush_idle()) {
		return false;
	}
	for (unsigned p = 0; p < sc.pipes; p++) {
		for (unsigned r = 0; r < SC_SAVED; r++) {
			dpu_wr(sc.rec[p] + sc_save_regs[r], sc.saved[p][r]);
		}
		if (sc.qseed[p] != 0) {
			dpu_wr(sc.qseed[p] + SC_Q_OP_MODE, sc.saved_qop[p]);
		}
	}
	dpu_dsb();
	dpu_wr(DPU_CTL(0) + DPU_CTL_FLUSH, sc.flush);
	sc.on_panel = true;
	return true;
}

/* Whether the pipes now read ADDR (0: the panel memory); and the bank's fault
 * status, cleared once read. */
bool hakios_dpu_scanout_current(uint32_t addr);
bool
hakios_dpu_scanout_current(uint32_t addr)
{
	if (!sc.ready) {
		return true;
	}
	const uint32_t want = addr ? addr : sc.panel_addr;
	const uint32_t fsr = sc_rd(sc.cb_off + SC_CB_FSR);
	if (fsr & SC_FSR_FAULT) {
		sc.faults++;
		if (sc.faults <= 8) {
			printf("HAKIOS SCANOUT: fault fsr %08x far %08x%08x fsynr0 %08x\n", fsr,
			    sc_rd(sc.cb_off + 0x64), sc_rd(sc.cb_off + 0x60), sc_rd(sc.cb_off + 0x68));
		}
		sc_wr(sc.cb_off + SC_CB_FSR, fsr);
	}
	bool on = true;
	for (unsigned p = 0; p < sc.pipes; p++) {
		if (dpu_rd(sc.rec[p] + SC_CURRENT0) != want) {
			on = false;
		}
	}
	return on;
}

bool hakios_dpu_scanout_ready(void);
bool
hakios_dpu_scanout_ready(void)
{
	return sc.ready;
}

void hakios_dpu_scanout_counts(uint64_t *shows, uint64_t *faults);
void
hakios_dpu_scanout_counts(uint64_t *shows, uint64_t *faults)
{
	*shows = sc.shows;
	*faults = sc.faults;
}

#endif /* __arm64__ */
"""

# ==========================================================================
# 2) iokit/Kernel/SweetFramebuffer.cpp -- call sites
# ==========================================================================
FB_EXTERN_ANCHOR = None  # located at run time (see find_extern_anchor)
# the display patch generates one of two variants of that include line; accept either.
FB_EXTERN_ANCHOR_ALT = "#include <sys/systm.h>          /* bzero(), copyout() */\n"
FB_EXTERN_BLOCK = r"""
/*
 * HAKIOS-LAFA-DPU (port of data-a-live 0215 + 0217): the display controller's
 * scanout, in bsd/dev/hakios_dpu.c.  The probe is read only and tells us what
 * the ABL left running; the scanout functions point the existing pipes at the
 * presented IOSurface when it is one whole-screen layer, and fall back to the
 * CPU copy silently otherwise.
 */
#define HAKIOS_SCAN_MAPS                6
extern "C" void hakios_dpu_probe(uint64_t fb);
extern "C" void hakios_dpu_scanout_geometry(uint32_t width, uint32_t height);
extern "C" int hakios_dpu_scanout_init(void);
extern "C" uint32_t hakios_dpu_scanout_map(const uint64_t *pages, uint32_t npages);
extern "C" void hakios_dpu_scanout_unmap(uint32_t iova, uint32_t npages);
extern "C" bool hakios_dpu_scanout_show(uint32_t addr, uint32_t stride, uint32_t w,
    uint32_t h, bool fill);
extern "C" bool hakios_dpu_scanout_panel(void);
extern "C" bool hakios_dpu_scanout_current(uint32_t addr);
extern "C" void hakios_dpu_scanout_counts(uint64_t *shows, uint64_t *faults);

/* One retain and one IOSurface use count per held surface (0217). */
static void
sweet_scan_hold(SweetSurface *s)
{
	s->retain();
	sweet_surface_use(s, 1);
}

static void
sweet_scan_drop(SweetSurface **s)
{
	if (*s != NULL) {
		sweet_surface_use(*s, -1);
		(*s)->release();
		*s = NULL;
	}
}
"""

FB_MEMBER_ANCHOR = "\tbool          fConsolePaused;\n"
FB_MEMBER_BLOCK = r"""
	/* HAKIOS-LAFA-DPU: scanout.  Each of fScanShown / fScanQueued holds one
	 * retain and one IOSurface use count. */
	bool          fScanWanted;      /* boot-arg hakios_scanout (default 1) */
	bool          fScanFill;        /* boot-arg hakios_scanout_fill (default 1) */
	bool          fScanTried;
	bool          fScanOn;
	bool          fScanToPanel;     /* pipes sent back to the panel memory; holds go when that shows */
	SweetSurface *fScanShown;       /* the surface the pipes read */
	SweetSurface *fScanQueued;      /* the surface the pipes were just given (not yet current) */
	uint32_t      fScanQueuedAddr;
	struct { SweetSurface *s; uint32_t iova, pages; uint64_t used; } fScanMaps[HAKIOS_SCAN_MAPS];
	uint64_t      fScanClock;
	uint32_t      fScanFrames, fScanCopies, fScanRefused;
"""

FB_DECL_ANCHOR = "\tvoid swap(const uint8_t *rec);\n"
FB_DECL_BLOCK = r"""	uint32_t scanIova(SweetSurface *s);
	bool scanPresent(const uint8_t *rec, uint32_t mask);
	void scanPanel(void);
	void scanTick(void);
"""

FB_LAZYMAP_ANCHOR = None  # located at run time (see find_lazymap_anchor)
FB_LAZYMAP_BLOCK = r"""
		/*
		 * HAKIOS-LAFA-DPU: read-only DPU dump (0215) and the scanout switch.
		 * Both are here, in lazyMap(), and not in start(): this port does no
		 * kernel_map/vm work before bsd_init() has finished (see the top of
		 * this file), and the reference's probe at start() was the one risky
		 * mapping in 0217.  hakios_dpu_probe=0 / hakios_scanout=0 turn them
		 * off without a rebuild.
		 */
		{
			int probe = 1, on = 1, fill = 1;
			PE_parse_boot_argn("hakios_dpu_probe", &probe, sizeof(probe));
			PE_parse_boot_argn("hakios_scanout", &on, sizeof(on));
			PE_parse_boot_argn("hakios_scanout_fill", &fill, sizeof(fill));
			if (probe != 0) {
				hakios_dpu_probe((uint64_t)fPhys);
			}
			fScanWanted = on != 0;
			fScanFill = fill != 0;
			IOLog("SWEET FB: scanout %s\n", fScanWanted ?
			    (fScanFill ? "wanted, scaled to the whole panel" :
			    "wanted, surface centred at its own size") : "off (CPU copy only)");
		}
"""

FB_VSYNC_ANCHOR = "\tuint64_t count = ++fb->fVsyncCount;\n"
FB_VSYNC_BLOCK = r"""	/* HAKIOS-LAFA-DPU: each tick, drop what the pipes no longer read.  Also the
	 * clock the reference used to notice the pipes went back to the panel. */
	fb->scanTick();
"""

FB_SWAP_ANCHOR = "\tfor (uint32_t layer = 0; layer < SWEET_FB_LAYERS; layer++) {\n"
FB_SWAP_BLOCK = r"""	/*
	 * HAKIOS-LAFA-DPU (0217): a whole screen that is one surface is put on the
	 * display pipes themselves; there is nothing to copy then.  Anything else
	 * (several layers, a scaled rectangle, the panel surface itself) keeps the
	 * CPU path, whose first act must be to put the pipes back on the panel
	 * memory -- that is where the copy below writes.
	 */
	if (mask != 0 && scanPresent(rec, mask)) {
		return;
	}
	if (fScanOn) {
		fScanCopies++;          /* scanout is on but this frame was not for the pipes */
	}
	scanPanel();

"""

FB_METHODS_ANCHOR = "/* Copy (or enlarge)"  # located at run time (see find_methods_anchor)
FB_METHODS_BLOCK = r"""#if defined(__arm64__)
/* -------------------------------------------------------------------------
 * HAKIOS-LAFA-DPU: scanout (port of data-a-live 0217's AppleCLCD methods).
 * sweet_scan_hold()/sweet_scan_drop() are with the externs at the top of the
 * file, because free() (above this point in the file) uses them too.
 * ------------------------------------------------------------------------- */

/* The IOVA of S's memory for the display (mapped on first use, kept while it
 * keeps being presented), or 0. */
uint32_t
AppleCLCD::scanIova(SweetSurface *s)
{
	unsigned slot = HAKIOS_SCAN_MAPS;
	fScanClock++;
	for (unsigned i = 0; i < HAKIOS_SCAN_MAPS; i++) {
		if (fScanMaps[i].s == s) {
			fScanMaps[i].used = fScanClock;
			return fScanMaps[i].iova;
		}
	}
	/* A slot: a free one, else the one longest unused that the pipes do not hold. */
	for (unsigned i = 0; i < HAKIOS_SCAN_MAPS; i++) {
		SweetSurface *t = fScanMaps[i].s;
		if (t == NULL) {
			slot = i;
			break;
		}
		if (t != fScanShown && t != fScanQueued &&
		    (slot == HAKIOS_SCAN_MAPS || fScanMaps[i].used < fScanMaps[slot].used)) {
			slot = i;
		}
	}
	if (slot == HAKIOS_SCAN_MAPS) {
		return 0;
	}
	if (fScanMaps[slot].s != NULL) {
		hakios_dpu_scanout_unmap(fScanMaps[slot].iova, fScanMaps[slot].pages);
		OSSafeReleaseNULL(fScanMaps[slot].s);
	}
	const uint64_t len = s->memory->getLength();
	const uint32_t n = (uint32_t)((len + 0xfff) >> 12);
	uint64_t *pages = (uint64_t *)IOMallocData((vm_size_t)n * sizeof(uint64_t));
	if (pages == NULL) {
		return 0;
	}
	bool ok = true;
	for (uint32_t i = 0; i < n && ok; i++) {
		IOByteCount seg = 0;
		const addr64_t pa = s->memory->getPhysicalSegment((IOByteCount)i << 12, &seg,
		    kIOMemoryMapperNone);
		ok = pa != 0 && (pa & 0xfff) == 0;
		pages[i] = pa;
	}
	const uint32_t iova = ok ? hakios_dpu_scanout_map(pages, n) : 0;
	IOFreeData(pages, (vm_size_t)n * sizeof(uint64_t));
	if (iova == 0) {
		return 0;
	}
	s->retain();
	fScanMaps[slot].s = s;
	fScanMaps[slot].iova = iova;
	fScanMaps[slot].pages = n;
	fScanMaps[slot].used = fScanClock;
	IOLog("SWEET FB: scanout maps surface %u (%ux%u, %u pages) at iova %#x\n", s->id,
	    s->width, s->height, n, iova);
	return iova;
}

/*
 * A frame the pipes take as it is: one layer, the whole presented display, a
 * BGRA surface, the console paused.  True when the pipes were given it.
 */
bool
AppleCLCD::scanPresent(const uint8_t *rec, uint32_t mask)
{
	if (!fScanWanted || !fConsolePaused) {
		return false;
	}
	if (!fScanOn) {
		if (fScanTried) {
			return false;
		}
		fScanTried = true;
		hakios_dpu_scanout_geometry(fWidth, fHeight);
		fScanOn = hakios_dpu_scanout_init() == 0;
		if (!fScanOn) {
			return false;
		}
	}
	uint32_t layer = SWEET_FB_LAYERS, layers = 0;
	for (uint32_t l = 0; l < SWEET_FB_LAYERS; l++) {
		if ((mask & (1u << l)) && get32(rec, 0x54 + 4 * l) != 0) {
			layer = l;
			layers++;
		}
	}
	if (layers != 1 || layer == SWEET_FB_LAYERS) {
		return false;
	}
	const uint8_t *a = rec + 0x64 + 16 * layer, *b = rec + 0xc4 + 16 * layer;
	SweetSurface *s = sweet_surface_copy(get32(rec, 0x54 + 4 * layer));
	const bool ok = s != NULL && s != fSurface && s->memory != NULL &&
	    s->pixelFormat == SWEET_FB_FORMAT_BGRA && s->bytesPerElement == 4 &&
	    s->elementWidth == 1 && s->elementHeight == 1 &&
	    s->width >= 2 && s->height >= 1 && s->width <= fWidth && s->height <= fHeight &&
	    s->bytesPerRow >= s->width * 4 && s->bytesPerRow < 0x10000 &&
	    (s->offset & 0xf) == 0 &&
	    (uint64_t)s->offset + (uint64_t)s->bytesPerRow * s->height <= s->memory->getLength() &&
	    get32(a, 0) == 0 && get32(a, 4) == 0 &&
	    get32(a, 8) == s->width && get32(a, 12) == s->height &&
	    get32(b, 0) == 0 && get32(b, 4) == 0 &&
	    get32(b, 8) == s->width && get32(b, 12) == s->height;
	uint32_t iova = 0;
	if (ok) {
		iova = scanIova(s);
	}
	if (!ok || iova == 0) {
		if (ok && fScanRefused++ < 8) {
			IOLog("SWEET FB: scanout refused surface %u (%ux%u): no IOVA\n",
			    s->id, s->width, s->height);
		}
		OSSafeReleaseNULL(s);
		return false;
	}
	const bool fill = fScanFill && (s->width != fWidth || s->height != fHeight);
	if (!hakios_dpu_scanout_show(iova + s->offset, s->bytesPerRow, s->width, s->height, fill)) {
		if (fScanRefused++ < 8) {
			IOLog("SWEET FB: scanout refused surface %u (%ux%u, map %#x)\n", s->id,
			    s->width, s->height, iova);
		}
		OSSafeReleaseNULL(s);
		return false;
	}
	/* show() waited for the earlier queued frame's flush: the pipes read that
	 * one now, the one before it no more. */
	if (fScanQueued != NULL) {
		sweet_scan_drop(&fScanShown);
		fScanShown = fScanQueued;
		fScanQueued = NULL;
	}
	sweet_scan_hold(s);
	fScanQueued = s;
	fScanQueuedAddr = iova + s->offset;
	fScanToPanel = false;
	s->release();
	fScanFrames++;
	if (fScanFrames == 1 || (fScanFrames % 600) == 0) {
		uint64_t shows = 0, faults = 0;
		hakios_dpu_scanout_counts(&shows, &faults);
		IOLog("SWEET FB: scanout on: %u frames scanned out, %u copied, %u refused; "
		    "%llu pipe updates, %llu SMMU faults (%s)\n", fScanFrames, fScanCopies,
		    fScanRefused, shows, faults, fill ? "fill" : "centred");
	}
	return true;
}

/* The pipes back on the panel memory; the surface holds go once that is on screen. */
void
AppleCLCD::scanPanel(void)
{
	if (fScanOn && !fScanToPanel && (fScanShown != NULL || fScanQueued != NULL) &&
	    hakios_dpu_scanout_panel()) {
		fScanToPanel = true;
	}
}

/* Each vsync tick, drop what the pipes no longer read. */
void
AppleCLCD::scanTick(void)
{
	if (!fScanOn) {
		return;
	}
	if (fScanToPanel) {
		if (hakios_dpu_scanout_current(0)) {
			sweet_scan_drop(&fScanShown);
			sweet_scan_drop(&fScanQueued);
			fScanToPanel = false;
		}
		return;
	}
	if (fScanQueued != NULL && hakios_dpu_scanout_current(fScanQueuedAddr)) {
		sweet_scan_drop(&fScanShown);
		fScanShown = fScanQueued;
		fScanQueued = NULL;
	}
}
#endif /* __arm64__ */

"""

FB_FREE_ANCHOR = None  # located at run time (see find_free_anchor)
FB_FREE_BLOCK = r"""
	/* HAKIOS-LAFA-DPU: put the pipes back on the ABL's panel memory before this
	 * service goes away, then let the surfaces go. */
	hakios_dpu_scanout_panel();
	sweet_scan_drop(&fScanShown);
	sweet_scan_drop(&fScanQueued);
	for (unsigned i = 0; i < HAKIOS_SCAN_MAPS; i++) {
		if (fScanMaps[i].s != NULL) {
			hakios_dpu_scanout_unmap(fScanMaps[i].iova, fScanMaps[i].pages);
			OSSafeReleaseNULL(fScanMaps[i].s);
		}
	}
"""

# ==========================================================================
# 3) SweetSurface.{h,cpp} -- 0217 use count
# ==========================================================================
SURFACE_H_ANCHOR = "SweetSurface *sweet_surface_copy(uint32_t id);\n"
SURFACE_H_BLOCK = r"""
/* HAKIOS-LAFA-DPU (0217): add DELTA to the use count IOSurfaceIsInUse reads (a
 * display holds one on a surface it shows). */
void sweet_surface_use(SweetSurface *s, int delta);
"""

SURFACE_CPP_SEED_ANCHOR = "#define SWEET_SURFACE_STATE_SEED        0xc     /* u32 in the state page */\n"
SURFACE_CPP_SEED_BLOCK = "#define SWEET_SURFACE_STATE_USE         0x18    /* u64 in the state page: IOSurfaceIsInUse = not 0 (HAKIOS-LAFA-DPU) */\n"

SURFACE_CPP_FUNC_ANCHOR = """static uint32_t *
sweet_surface_seed(SweetSurface *s)
{
	return (uint32_t *)((uint8_t *)s->state->getBytesNoCopy() + SWEET_SURFACE_STATE_SEED);
}
"""
SURFACE_CPP_FUNC_BLOCK = r"""
/* HAKIOS-LAFA-DPU (0217): the surface's use count as IOSurfaceIsInUse reads it
 * (clients' traps and kernel holders together). */
void
sweet_surface_use(SweetSurface *s, int delta)
{
	uint64_t *use = (uint64_t *)((uint8_t *)s->state->getBytesNoCopy() + SWEET_SURFACE_STATE_USE);
	if (delta > 0) {
		__atomic_add_fetch(use, (uint64_t)delta, __ATOMIC_RELEASE);
		return;
	}
	uint64_t old = __atomic_load_n(use, __ATOMIC_ACQUIRE), want;
	do {
		want = old >= (uint64_t)-delta ? old + (uint64_t)(int64_t)delta : 0;
	} while (!__atomic_compare_exchange_n(use, &old, want, true, __ATOMIC_ACQ_REL,
	    __ATOMIC_ACQUIRE));
}
"""

SURFACE_CPP_REF_ANCHOR = "\tuint32_t      count;\n"
SURFACE_CPP_REF_BLOCK = "\tuint32_t      uses;          /* HAKIOS-LAFA-DPU (0217): this client's use count +/- */\n"

SURFACE_CPP_FREE_ANCHOR = """	OSSafeReleaseNULL(data);
	OSSafeReleaseNULL(state);
	if (surface != NULL) {
"""
SURFACE_CPP_FREE_BLOCK = """	if (surface != NULL && uses != 0) {
		/* HAKIOS-LAFA-DPU (0217): the client went away with uses held. */
		sweet_surface_use(surface, -(int)uses);
		uses = 0;
	}
"""
SURFACE_CPP_DECL_ANCHOR = "\tIOReturn trapUseCount(void *id, void *category, void *, void *, void *, void *);\n"
SURFACE_CPP_DECL_BLOCK = """	IOReturn trapUnuseCount(void *id, void *category, void *, void *, void *, void *);
	IOReturn useCount(uint32_t id, int delta);
"""

SURFACE_CPP_TRAP_ANCHOR = """IOReturn
IOSurfaceRootUserClient::trapUseCount(__unused void *id, __unused void *category,
    void *, void *, void *, void *)
{
	return kIOReturnSuccess;
}
"""
SURFACE_CPP_TRAP_BLOCK = r"""/* HAKIOS-LAFA-DPU (0217): traps 0 and 1, IOSurfaceIncrementUseCount /
 * DecrementUseCount (id, category).  The count lives in the shared state page so
 * userland's IOSurfaceIsInUse sees the display's hold. */
IOReturn
IOSurfaceRootUserClient::trapUseCount(void *id, __unused void *category, void *, void *, void *, void *)
{
	return useCount((uint32_t)(uintptr_t)id, 1);
}

IOReturn
IOSurfaceRootUserClient::trapUnuseCount(void *id, __unused void *category, void *, void *, void *, void *)
{
	return useCount((uint32_t)(uintptr_t)id, -1);
}

IOReturn
IOSurfaceRootUserClient::useCount(uint32_t id, int delta)
{
	IOLockLock(fLock);
	SweetSurfaceRef *ref = findRef(id, NULL);
	if (ref == NULL) {
		IOLockUnlock(fLock);
		return kIOReturnNotFound;
	}
	if (delta < 0 && ref->uses == 0) {
		IOLockUnlock(fLock);
		return kIOReturnSuccess;   /* more decrements than increments: nothing of ours to drop */
	}
	ref->uses += (uint32_t)delta;
	sweet_surface_use(ref->surface, delta);
	IOLockUnlock(fLock);
	return kIOReturnSuccess;
}
"""

SURFACE_CPP_TRAPS_ANCHOR = "\t[kSweetSurfaceTrapDecrementUse]  = { NULL, (IOTrap) & IOSurfaceRootUserClient::trapUseCount },\n"
SURFACE_CPP_TRAPS_BLOCK = "\t[kSweetSurfaceTrapDecrementUse]  = { NULL, (IOTrap) & IOSurfaceRootUserClient::trapUnuseCount },\n"

# ==========================================================================
# edits: validate every anchor first, then write everything (a MISS writes
# nothing at all, so a half-patched tree can never happen from this script)
# ==========================================================================
USAGE = "python3 ci/patch-kernel-dpu.py <xnu source tree> [--check]"
DPU_C_REL = "bsd/dev/hakios_dpu.c"
FB_REL = "iokit/Kernel/SweetFramebuffer.cpp"
SURFACE_H_REL = "iokit/Kernel/SweetSurface.h"
SURFACE_CPP_REL = "iokit/Kernel/SweetSurface.cpp"
FILES_REL = "bsd/conf/files"
FILES_LINE = "bsd/dev/hakios_dpu.c\t\t\tstandard\n"


class Failure(Exception):
    pass


class UsageError(Exception):
    pass


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="surrogateescape")


def write(path: Path, text: str) -> bool:
    """写文件（保留原有换行风格）。返回是否真的变了。"""
    if path.exists():
        old = read(path)
        if old == text:
            return False
        if "\r\n" in old:
            text = text.replace("\n", "\r\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", errors="surrogateescape", newline="")
    return True


def find_free_anchor(text):
    """The free() hook goes at the start of the fScaleScratch teardown block."""
    for line in text.splitlines():
        if "fScaleScratch != NULL" in line:
            return line + "\n"
    return None


def find_methods_anchor(text):
    """The methods block goes right before the composer of SweetFramebuffer.

    The display patch's two variants word that comment differently, so match its start.
    """
    for line in text.splitlines():
        if line.startswith("/* Copy (or enlarge)") or "surface into the panel memory" in line:
            return line + "\n"
    return None


def find_lazymap_anchor(text):
    """The DPU hooks go right after the panel-mapped log line of lazyMap().

    Its leading whitespace differs between the display patch's two variants, so find the
    line by its content and use whatever indentation it actually has.
    """
    for line in text.splitlines():
        # the log line differs between the display patch's variants; any of these is it
        if "fPixels" in line and "fSurface" in line and "sweet_fb_scale_name" in line:
            return line + "\n"
    for line in text.splitlines():
        if "fPixels" in line and "fSurface" in line:
            return line + "\n"
    return None


def find_extern_anchor(text):
    """The externs block goes right after the sys/systm.h include of SweetFramebuffer.cpp.

    The display patch generates that line with different trailing comments depending on
    which variant it takes, so locate it by its prefix instead of hard-coding it.
    """
    for line in text.splitlines():
        if line.startswith("#include <sys/systm.h>"):
            return line + "\n"
    return None


def splice_after(text: str, anchor: str, block: str, what: str) -> str:
    """Insert `block` right after the line that matches `anchor`.

    The anchors in this file were written against one variant of the display patch's
    SweetFramebuffer.cpp; the other variant indents the same lines differently.  So when the
    exact text is not there, match the anchor's stripped form against the stripped lines and
    splice after the line that actually exists (indent-insensitive).
    """
    if anchor in text:
        idx = text.index(anchor) + len(anchor)
    else:
        want = anchor.strip()
        idx = None
        for line in text.splitlines(True):
            if line.strip() == want:
                idx = text.index(line) + len(line)
                break
        if idx is None:
            raise Failure(what + ": anchor not found: " + anchor.strip()[:60])
    return text[:idx] + block + text[idx:]


def replace_once(text: str, old: str, new: str, what: str) -> str:
    count = text.count(old)
    if count == 0:
        raise Failure("%s 里找不到要替换的块 %r" % (what, old[:60]))
    if count > 1:
        raise Failure("%s 里要替换的块出现 %d 次，不唯一" % (what, count))
    return text.replace(old, new, 1)


def splice_before(text: str, anchor: str, block: str, what: str) -> str:
    count = text.count(anchor)
    if count == 0:
        raise Failure("%s 里找不到锚点 %r（树不是 apply.sh + patch-kernel-display.py 之后的样子？）"
                      % (what, anchor[:60]))
    if count > 1:
        raise Failure("%s 里锚点 %r 出现 %d 次，不唯一" % (what, anchor[:60], count))
    return text.replace(anchor, block + anchor, 1)


def patched_framebuffer(text: str) -> str:
    anchor = find_extern_anchor(text)
    if anchor is None:
        raise Failure(FB_REL + ": no #include <sys/systm.h> to anchor the externs on")
    text = splice_after(text, anchor, FB_EXTERN_BLOCK, FB_REL + "(externs)")
    text = splice_after(text, FB_MEMBER_ANCHOR, FB_MEMBER_BLOCK, FB_REL + "(members)")
    text = splice_after(text, FB_DECL_ANCHOR, FB_DECL_BLOCK, FB_REL + "(methods decl)")
    anchor = find_lazymap_anchor(text)
    if anchor is None:
        raise Failure(FB_REL + ": no panel-mapped log line to anchor the lazyMap hook on")
    text = splice_after(text, anchor, FB_LAZYMAP_BLOCK, FB_REL + "(lazyMap)")
    text = splice_after(text, FB_VSYNC_ANCHOR, FB_VSYNC_BLOCK, FB_REL + "(vsyncFired)")
    text = splice_before(text, FB_SWAP_ANCHOR, FB_SWAP_BLOCK, FB_REL + "(swap)")
    anchor = find_methods_anchor(text)
    if anchor is None:
        raise Failure(FB_REL + ": no composer comment to anchor the methods on")
    text = splice_after(text, anchor, FB_METHODS_BLOCK, FB_REL + "(methods)")
    anchor = find_free_anchor(text)
    if anchor is None:
        # The free() hook only restores the ABL's panel memory when the service goes away;
        # it is not needed for scanning out, so a variant without that teardown simply
        # skips it instead of failing the whole patch.
        print("  ! " + FB_REL + "(free): no fScaleScratch teardown, skipping that hook")
    else:
        text = splice_after(text, anchor, FB_FREE_BLOCK, FB_REL + "(free)")
    return text


def patched_surface_h(text: str) -> str:
    return splice_after(text, SURFACE_H_ANCHOR, SURFACE_H_BLOCK, SURFACE_H_REL)


def patched_surface_cpp(text: str) -> str:
    text = splice_after(text, SURFACE_CPP_SEED_ANCHOR, SURFACE_CPP_SEED_BLOCK,
                        SURFACE_CPP_REL + "(state use count)")
    if "\tUInt32      uses;" in text or "uint32_t      uses;" in text:
        raise Failure("%s 里已经有 uses 字段但没有 %s 标记，先手工清干净" % (SURFACE_CPP_REL, MARK))
    text = splice_after(text, SURFACE_CPP_FUNC_ANCHOR, SURFACE_CPP_FUNC_BLOCK,
                        SURFACE_CPP_REL + "(sweet_surface_use)")
    text = splice_after(text, SURFACE_CPP_REF_ANCHOR, SURFACE_CPP_REF_BLOCK,
                        SURFACE_CPP_REL + "(SweetSurfaceRef.uses)")
    text = splice_after(text, SURFACE_CPP_FREE_ANCHOR, SURFACE_CPP_FREE_BLOCK,
                        SURFACE_CPP_REL + "(SweetSurfaceRef.free)")
    text = splice_after(text, SURFACE_CPP_DECL_ANCHOR, SURFACE_CPP_DECL_BLOCK,
                        SURFACE_CPP_REL + "(method decls)")
    text = replace_once(text, SURFACE_CPP_TRAP_ANCHOR, SURFACE_CPP_TRAP_BLOCK,
                        SURFACE_CPP_REL + "(trapUseCount)")
    text = replace_once(text, SURFACE_CPP_TRAPS_ANCHOR, SURFACE_CPP_TRAPS_BLOCK,
                        SURFACE_CPP_REL + "(traps table)")
    return text


def plan_one(root: Path, rel: str, patcher, markers) -> tuple:
    """返回 (path, 新内容 或 None, 报告行)。None = 已是最新/不需要写。"""
    path = root / rel
    if not path.is_file():
        raise Failure("%s 不存在（%s 是 apply.sh 之后的 xnu 源码树吗？）" % (rel, root))
    text = read(path)
    if MARK in text:
        missing = [name for name, probe in markers if probe not in text]
        if missing:
            raise Failure("%s 里有 %s 标记但缺 %s，先手工清干净" % (rel, MARK, "、".join(missing)))
        return path, None, "  = %s（已就位）" % rel
    new = patcher(text)
    return path, new, "  + %s（%d 行追加/改动）" % (rel, len(new.splitlines()) - len(text.splitlines()))


def plan_files(root: Path) -> tuple:
    path = root / FILES_REL
    if not path.is_file():
        raise Failure("%s 不存在（%s 是 apply.sh 之后的 xnu 源码树吗？）" % (FILES_REL, root))
    text = read(path)
    if "bsd/dev/hakios_dpu.c" in text:
        return path, None, "  = %s（hakios_dpu.c 已在构建里）" % FILES_REL
    m = re.search(r"^bsd/dev/sweet_ufs\.c[ \t]+standard[ \t]*$", text, re.MULTILINE)
    if not m:
        # 兜底：任何 bsd/dev/*.c 的 standard 行之后（0028 加进来的 sweet_ufs.c 不在时）
        m = re.search(r"^bsd/dev/[^\n]+\.c[ \t]+standard[ \t]*$", text, re.MULTILINE)
        if not m:
            raise Failure("%s 里找不到可以插入 bsd/dev/*.c 的 standard 行" % FILES_REL)
    new = text[:m.end()] + "\n" + FILES_LINE.rstrip("\n") + text[m.end():]
    return path, new, "  + %s（+1 行：bsd/dev/hakios_dpu.c）" % FILES_REL


FB_MARKERS = (
    ("extern 块", "hakios_dpu_scanout_init"),
    ("成员", "fScanWanted"),
    ("lazyMap 探针", "hakios_dpu_probe((uint64_t)fPhys)"),
    ("vsync scanTick", "fb->scanTick();"),
    ("swap scanout", "if (mask != 0 && scanPresent(rec, mask))"),
    ("方法块", "AppleCLCD::scanIova"),
    ("free 归位", "hakios_dpu_scanout_panel();"),
)
SURFACE_H_MARKERS = (("use count 声明", "sweet_surface_use(SweetSurface *s, int delta)"),)
SURFACE_CPP_MARKERS = (
    ("状态页常量", "SWEET_SURFACE_STATE_USE"),
    ("sweet_surface_use", "sweet_surface_use(SweetSurface *s, int delta)"),
    ("uses 字段", "uint32_t      uses;"),
    ("trap 实现", "IOSurfaceRootUserClient::trapUnuseCount"),
    ("traps 表", "trapUnuseCount"),
)


def parse_args(argv) -> tuple:
    root = None
    check = False
    for arg in argv[1:]:
        if arg in ("--check", "-n"):
            check = True
        elif arg in ("-h", "--help"):
            raise UsageError(USAGE)
        elif arg.startswith("-"):
            raise UsageError("不认识的选项 %r\n%s" % (arg, USAGE))
        elif root is None:
            root = arg
        else:
            raise UsageError("只能给一个 xnu 源码树（多给了 %r）\n%s" % (arg, USAGE))
    if root is None:
        raise UsageError(USAGE)
    return Path(root), check


def main(argv) -> int:
    try:
        root, check = parse_args(argv)
    except UsageError as exc:
        print("patch-dpu: %s" % exc, file=sys.stderr)
        return 2
    for required in (FILES_REL, FB_REL, SURFACE_H_REL, SURFACE_CPP_REL):
        if not (root / required).is_file():
            print("patch-dpu: %s 里没有 %s（不是 apply.sh 之后的 xnu 源码树？）" % (root, required),
                  file=sys.stderr)
            print("patch-dpu: 这个补丁要跑在 ci/patch-kernel-display.py 之后", file=sys.stderr)
            return 1

    print("patch-dpu: %s%s" % (root, "（--check，不写文件）" if check else ""))
    try:
        plan = []
        report = []
        dpu_path = root / DPU_C_REL
        if dpu_path.is_file():
            body = read(dpu_path)
            if MARK not in body:
                raise Failure("%s 已存在但没有 %s 标记（手写的？先备份/删除）" % (DPU_C_REL, MARK))
            if body != DPU_C:
                raise Failure("%s 是旧版/改过的（不是本脚本写的这一版），先手工清干净" % DPU_C_REL)
            report.append("  = %s（已就位）" % DPU_C_REL)
        else:
            report.append("  + %s（新建：%d 行 DPU/scanout 驱动）"
                          % (DPU_C_REL, DPU_C.count("\n")))
            plan.append((dpu_path, DPU_C))
        for rel, patcher, markers in ((FB_REL, patched_framebuffer, FB_MARKERS),
                                      (SURFACE_H_REL, patched_surface_h, SURFACE_H_MARKERS),
                                      (SURFACE_CPP_REL, patched_surface_cpp, SURFACE_CPP_MARKERS)):
            path, new, line = plan_one(root, rel, patcher, markers)
            report.append(line)
            if new is not None:
                plan.append((path, new))
        path, new, line = plan_files(root)
        report.append(line)
        if new is not None:
            plan.append((path, new))
    except Failure as exc:
        print("patch-dpu: MISS: %s" % exc, file=sys.stderr)
        print("patch-dpu: 没有写任何文件（锚点校验全部通过才落盘）", file=sys.stderr)
        return 1

    changed = 0
    if not check:
        for path, text in plan:
            if write(path, text):
                changed += 1
    for line in report:
        print(line)
    if check:
        print("patch-dpu: %d 个文件会改（--check：一个都没写）" % len(plan))
    elif changed == 0:
        print("patch-dpu: 已经是补丁后版本，什么都没写")
    else:
        print("patch-dpu: 写了 %d 个文件" % changed)

    print(
        "patch-dpu: DPU/scanout 就位。第一次真机启动看这些行（串口）：\n"
        "  HAKIOS DPU: MDSS hw ... / HAKIOS DPU: ctl0: ... intf ... fetch ...\n"
        "  HAKIOS DPU: t0 / t0+100ms: intf1 frame N -> N+...（帧计数必须前进）\n"
        "  HAKIOS DPU: SMMU SMR ... (translate, bank 2) / HAKIOS DPU: SMMU bank 2: sctlr ... off\n"
        "  HAKIOS SCANOUT: init at the first frame ... / ... frames can be scanned out\n"
        "  SWEET FB: scanout on: ... SMMU faults 0\n"
        "失败判据：帧计数不前进 = 面板没在刷新（DSI/显示电源/loader 交接，不在本补丁）；\n"
        "          \"main screen not as expected / the ABL's pipe split\" = pipe 布局不同，回传 ctl0 行；\n"
        "          SMMU fault 非 0 会自动退回 CPU 拷贝（屏仍是原来的行为）。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
