/*
 * PANDORA Adreno 840 bring-up (docs/GPU_PLAN.md, docs/GPU_PORT_MAP.md), boot-arg pandora_gpu=N.
 *
 * Level 1 (patch 0162): read only, nothing is written. A block is read only when the one it
 * hangs off is powered and clocked, because reading GPUCC, the GPU SMMU or the GMU while
 * their supply or clock is off can stall the bus and reset the phone:
 *   GCC (always on)
 *   -> GPUCC, if gcc_gpu_cfg_ahb is enabled
 *   -> GPU SMMU, if the CX GDSC is on and the SMMU vote clock runs (one SMR/S2CR at a time)
 *   -> GMU state, if the CX GDSC is on and gpu_cc_ahb and gpu_cc_cx_gmu run.
 * cmd-db (shared memory, no clock) gives the RPMh resources the GMU votes will use.
 * Before each risky read a line goes to the log and the thread waits for the log thread
 * (every 3 s) to put it on disk, so a reset still shows how far it got.
 *
 * Addresses: mainline gcc-kaanapali.c / gpucc-kaanapali.c and the phone's device tree
 * (SM8850, "canoe"); GMU registers as in Linux a6xx/a8xx (GPU base + dword offset * 4).
 * The gating order follows UniXNU's sheng GpuPowerBoard.h (BSD-2-Clause-Patent); no code
 * is copied from it.
 */
#if 1 /* HAKIOS: this port has no PANDORA macro; see ci/patch-kernel-pandora-gpu-probe.py */
#include <sys/param.h>
#include <sys/systm.h>
#include <kern/clock.h>
#include <kern/thread.h>
#include <kern/sched_prim.h>
#include <pexpert/pexpert.h>
#include <vm/vm_map.h>
#include <arm/machine_routines.h>   /* ml_io_map_* and VM_WIMG_* */

/*
 * HAKIOS-GPU-UNMAPPABLE: gpu_map() only covers the 8 MB io_submap, and GPUCC at 0x3d90000
 * is far outside it -- the map call never returns (the reference port hit the same thing and
 * switched these windows to ml_io_map_unmappable in patch 0180).  This port's own lafa-fb
 * driver already maps 0xfc800000 this way, so use the same call.
 */
extern vm_offset_t ml_io_map_unmappable(vm_offset_t phys_addr, vm_size_t size, uint32_t wimg);
#define gpu_map(pa, len) ml_io_map_unmappable((vm_offset_t)(pa), (vm_size_t)(len), VM_WIMG_IO)
/* HAKIOS-GPU-UNMAPPABLE */

extern kern_return_t thread_terminate(thread_t);
void pandora_gpu_init(void);

#define GCC_PA            0x00100000u
#define GCC_GPU_CFG_AHB   0x71004u     /* gcc_gpu_cfg_ahb_clk CBCR */
#define GCC_GEMNOC_GFX    0x71010u     /* gcc_gpu_gemnoc_gfx_clk CBCR */
#define GCC_GPLL0_VOTE    0x52000u     /* APCS PLL vote; bits 15/16: GPU votes */

#define GPUCC_PA          0x03d90000u
#define GPUCC_SIZE        0x9800u
#define GPUCC_PLL0        0x0000u
#define GPUCC_SMMU_VOTE   0x7000u      /* gpu_cc_..._smmu vote clock CBCR */
#define GPUCC_CX_GDSCR    0x9080u
#define GPUCC_CX_GDS_HW   0x9094u
#define GPUCC_AHB         0x90bcu
#define GPUCC_CX_GMU      0x90d4u
#define GPUCC_CXO         0x90e4u
#define GPUCC_HUB_CX_INT  0x90e8u
#define GPUCC_GMU_RCG     0x9318u
#define GPUCC_HUB_RCG     0x93f0u
#define GPUCC_SCAN_FIRST  0x9000u
#define GPUCC_SCAN_END    0x95ecu      /* regmap max_register 0x95e8 */

#define SMMU_PA           0x03da0000u  /* GR0 page */
#define SMMU_SCR0         0x000u
#define SMMU_ID0          0x020u
#define SMMU_ID1          0x024u
#define SMMU_ID2          0x028u
#define SMMU_SGFSR        0x048u
#define SMMU_SMR(n)       (0x800u + 4u * (n))
#define SMMU_S2CR(n)      (0xc00u + 4u * (n))

#define GPU_PA            0x03d00000u
#define GMU_PA            0x03d37000u  /* phone DT: gmu reg, 0x68000 */
#define GMU_SIZE          0x68000u
#define GMU_REG(dw)       (GPU_PA + 4u * (dw) - GMU_PA)

#define CMDDB_PA          0x81c60000u
#define CMDDB_SIZE        0x20000u

#define CBCR_CLK_OFF      (1u << 31)
#define CBCR_CLK_ENABLE   (1u << 0)
#define GDSCR_PWR_ON      (1u << 31)

static int gpu_chan;

static void
gpu_sleep(unsigned ms)
{
	uint64_t deadline;
	clock_interval_to_deadline(ms, NSEC_PER_MSEC, &deadline);
	(void)assert_wait_deadline(&gpu_chan, THREAD_UNINT, deadline);
	thread_block(THREAD_CONTINUE_NULL);
}

static uint32_t
gpu_rd(vm_offset_t b, uint32_t o)
{
	uint32_t v = *(volatile uint32_t *)(b + o);
	__asm__ volatile ("dmb oshld" ::: "memory");
	return v;
}

/* Log what comes next and let the log thread write it before the read happens. */
static void
gpu_step(const char *what)
{
	kprintf("PANDORA GPU: next: %s\n", what);
	gpu_sleep(800);
}

/* ---- cmd-db (same layout as pandora_adsp.c) ------------------------------------------- */

static vm_offset_t gpu_db;

static uint16_t
db16(uint32_t off)
{
	return (uint16_t)(gpu_rd(gpu_db, off & ~3u) >> (8u * (off & 3u)));
}

static int
cmddb_find(const char *res, uint32_t *addr, uint32_t *aux, uint32_t *auxlen)
{
	uint32_t key[2] = {0, 0};
	for (unsigned i = 0; i < 8 && res[i]; i++) {
		key[i / 4] |= (uint32_t)(uint8_t)res[i] << (8u * (i % 4));
	}
	for (unsigned i = 0; i < 8; i++) {
		uint32_t h = 8u + 16u * i;
		if (!db16(h)) {
			break;
		}
		uint32_t entries = 144 + db16(h + 2), data = 144 + db16(h + 4);
		unsigned count = db16(h + 6);
		for (unsigned j = 0; j < count && entries + 24 * (j + 1) <= CMDDB_SIZE; j++) {
			uint32_t e = entries + 24 * j;
			if (gpu_rd(gpu_db, e) == key[0] && gpu_rd(gpu_db, e + 4) == key[1]) {
				*addr = gpu_rd(gpu_db, e + 16);
				*auxlen = db16(e + 20);
				*aux = data + db16(e + 22);
				return 0;
			}
		}
	}
	return ENOENT;
}

static void
gpu_cmddb(void)
{
	static const char *const res[] = {
		"gfx.lvl", "mx.lvl", "cx.lvl", "gmxc.lvl", "mxc.lvl", "SH0", "MC0", "ACV", "CN0",
	};
	gpu_db = gpu_map(CMDDB_PA, CMDDB_SIZE);
	if (!gpu_db) {
		kprintf("PANDORA GPU: cmd-db not mapped\n");
		return;
	}
	for (unsigned i = 0; i < sizeof(res) / sizeof(res[0]); i++) {
		uint32_t addr, aux, auxlen;
		if (cmddb_find(res[i], &addr, &aux, &auxlen)) {
			kprintf("PANDORA GPU: cmd-db %s: not found\n", res[i]);
			continue;
		}
		char line[160];
		int n = snprintf(line, sizeof(line), "PANDORA GPU: cmd-db %s: addr %#x aux %u:", res[i],
		    addr, auxlen);
		for (uint32_t k = 0; k + 2 <= auxlen && k < 32 && n > 0 && (size_t)n < sizeof(line) - 6; k += 2) {
			n += snprintf(line + n, sizeof(line) - (size_t)n, " %x", db16(aux + k));
		}
		kprintf("%s\n", line);
	}
}

/* ---- level 1 ---------------------------------------------------------------------------- */

static void
gpu_dump(vm_offset_t b, uint32_t first, uint32_t end, const char *name)
{
	for (uint32_t o = first; o < end; o += 32) {
		uint32_t v[8] = {0};
		bool any = false;
		for (unsigned k = 0; k < 8 && o + 4 * k < end; k++) {
			v[k] = gpu_rd(b, o + 4 * k);
			any |= v[k] != 0;
		}
		if (any) {
			kprintf("PANDORA GPU: %s %04x: %08x %08x %08x %08x %08x %08x %08x %08x\n", name, o,
			    v[0], v[1], v[2], v[3], v[4], v[5], v[6], v[7]);
		}
	}
}


/* HAKIOS-GPU-CC-POWER: our phone has the GPU fully down when fastboot hands over, so the
 * first GPUCC read stalls the bus (the same-SoC port's level-2 patch says "fastboot leaves
 * the CX GDSC collapsed").  Power the CX side first, then read.  Written blind because the
 * read is what hangs: SW_COLLAPSE cleared, the three RETAINs set, and the reserved bits left
 * as the hardware wants them. */
static void
gpu_wr(vm_offset_t b, uint32_t o, uint32_t v)
{
	__asm__ volatile ("dmb oshst" ::: "memory");
	*(volatile uint32_t *)(b + o) = v;
	__asm__ volatile ("dsb osh" ::: "memory");
}

static uint32_t
gpu_poll(vm_offset_t b, uint32_t o, uint32_t mask, uint32_t want, unsigned us, bool *ok)
{
	uint32_t v = gpu_rd(b, o);
	for (unsigned t = 0; (v & mask) != want && t < us; t += 10) {
		delay(10);
		v = gpu_rd(b, o);
	}
	*ok = (v & mask) == want;
	return v;
}

/* Enable a branch clock (CBCR bit 0) and wait for CLK_OFF to clear. */
static bool
gpu_branch_on(vm_offset_t cc, uint32_t cbcr, const char *name)
{
	bool ok;
	uint32_t v = gpu_rd(cc, cbcr);
	if (!(v & CBCR_CLK_OFF)) {
		kprintf("PANDORA GPU: %s already running (%08x)\n", name, v);
		return true;
	}
	gpu_wr(cc, cbcr, v | CBCR_CLK_ENABLE);
	v = gpu_poll(cc, cbcr, CBCR_CLK_OFF, 0, 500, &ok);
	kprintf("PANDORA GPU: %s on: %08x %s\n", name, v, ok ? "running" : "STILL OFF");
	return ok;
}

static void
gpu_level1(void)
{
	/* GCC: always on. */
	vm_offset_t gcc71 = gpu_map(GCC_PA + GCC_GPU_CFG_AHB - 0x4u, 0x1000);
	vm_offset_t gcc52 = gpu_map(GCC_PA + GCC_GPLL0_VOTE, 0x1000);
	if (!gcc71 || !gcc52) {
		kprintf("PANDORA GPU: GCC not mapped\n");
		return;
	}
	uint32_t cfg_ahb = gpu_rd(gcc71, 4), gemnoc = gpu_rd(gcc71, GCC_GEMNOC_GFX - GCC_GPU_CFG_AHB + 4);
	uint32_t gpll0 = gpu_rd(gcc52, 0);
	kprintf("PANDORA GPU: GCC gpu_cfg_ahb %08x gemnoc_gfx %08x gpll0_vote %08x (gpu bits %u%u)\n",
	    cfg_ahb, gemnoc, gpll0, (gpll0 >> 16) & 1, (gpll0 >> 15) & 1);
	if (!(cfg_ahb & CBCR_CLK_ENABLE)) {
		kprintf("PANDORA GPU: gpu_cfg_ahb not enabled: GPUCC not read\n");
		return;
	}

	/* GPUCC: map first (marker), power the CX side, only then read. */
	gpu_step("GPUCC map");
	vm_offset_t cc = gpu_map(GPUCC_PA, GPUCC_SIZE);
	if (!cc) {
		kprintf("PANDORA GPU: GPUCC not mapped\n");
		return;
	}
	kprintf("PANDORA GPU: GPUCC mapped at %lx (pa %x size %x)\n",
	    (unsigned long)cc, (unsigned)GPUCC_PA, (unsigned)GPUCC_SIZE);
	gpu_step("CX GDSC on (blind write, SW_COLLAPSE cleared)");
	gpu_wr(cc, GPUCC_CX_GDSCR, (1u << 16) | (1u << 17) | (1u << 20));
	{
		bool pok;
		uint32_t hw = gpu_poll(cc, GPUCC_CX_GDS_HW, GDSCR_PWR_ON, GDSCR_PWR_ON, 1000, &pok);
		kprintf("PANDORA GPU: CX GDSC after write: gdscr %08x hw %08x %s\n",
		    gpu_rd(cc, GPUCC_CX_GDSCR), hw, pok ? "on" : "DID NOT TURN ON");
		if (!pok) {
			return;
		}
	}
	gpu_step("CX clocks on (gpu_cc_ahb, SMMU vote)");
	(void)gpu_branch_on(cc, GPUCC_AHB, "gpu_cc_ahb");
	(void)gpu_branch_on(cc, GPUCC_SMMU_VOTE, "SMMU vote clock");
	uint32_t gdscr = gpu_rd(cc, GPUCC_CX_GDSCR), smmu_vote = gpu_rd(cc, GPUCC_SMMU_VOTE);
	uint32_t ahb = gpu_rd(cc, GPUCC_AHB), cx_gmu = gpu_rd(cc, GPUCC_CX_GMU);
	kprintf("PANDORA GPU: GPUCC cx_gdscr %08x hw %08x ahb %08x cx_gmu %08x cxo %08x hub_cx_int %08x "
	    "smmu_vote %08x\n", gdscr, gpu_rd(cc, GPUCC_CX_GDS_HW), ahb, cx_gmu, gpu_rd(cc, GPUCC_CXO),
	    gpu_rd(cc, GPUCC_HUB_CX_INT), smmu_vote);
	kprintf("PANDORA GPU: GPUCC gmu_rcg %08x %08x hub_rcg %08x %08x\n", gpu_rd(cc, GPUCC_GMU_RCG),
	    gpu_rd(cc, GPUCC_GMU_RCG + 4), gpu_rd(cc, GPUCC_HUB_RCG), gpu_rd(cc, GPUCC_HUB_RCG + 4));
	gpu_dump(cc, GPUCC_PLL0, GPUCC_PLL0 + 0x40, "GPUCC pll0");
	gpu_dump(cc, GPUCC_SCAN_FIRST, GPUCC_SCAN_END, "GPUCC");
	bool cx_on = gdscr & GDSCR_PWR_ON;

	/* GPU SMMU, GR0 page, one stream-match entry at a time. */
	if (!cx_on || (smmu_vote & CBCR_CLK_OFF)) {
		kprintf("PANDORA GPU: CX %s, SMMU vote clock %s: SMMU not read\n", cx_on ? "on" : "off",
		    (smmu_vote & CBCR_CLK_OFF) ? "off" : "on");
	} else {
		gpu_step("GPU SMMU GR0 ID registers");
		vm_offset_t smmu = gpu_map(SMMU_PA, 0x1000);
		uint32_t id0 = smmu ? gpu_rd(smmu, SMMU_ID0) : 0;
		if (smmu) {
			kprintf("PANDORA GPU: SMMU sCR0 %08x ID0 %08x ID1 %08x ID2 %08x sGFSR %08x\n",
			    gpu_rd(smmu, SMMU_SCR0), id0, gpu_rd(smmu, SMMU_ID1), gpu_rd(smmu, SMMU_ID2),
			    gpu_rd(smmu, SMMU_SGFSR));
			unsigned nsmr = MIN(id0 & 0xffu, 128u), valid = 0;
			for (unsigned i = 0; i < nsmr; i++) {
				if (i % 16 == 0) {
					char what[48];
					snprintf(what, sizeof(what), "SMMU SMR/S2CR %u..%u of %u", i, MIN(i + 15, nsmr - 1), nsmr);
					gpu_step(what);
				}
				uint32_t smr = gpu_rd(smmu, SMMU_SMR(i));
				uint32_t s2cr = gpu_rd(smmu, SMMU_S2CR(i));
				if (smr & (1u << 31)) {
					valid++;
					kprintf("PANDORA GPU: SMMU SMR[%u] %08x (id %#x mask %#x) S2CR %08x\n", i, smr,
					    smr & 0xffffu, (smr >> 16) & 0x7fffu, s2cr);
				}
			}
			kprintf("PANDORA GPU: SMMU %u of %u stream-match entries valid\n", valid, nsmr);
		}
	}

	/* GMU state. */
	if (!cx_on || (ahb & CBCR_CLK_OFF) || (cx_gmu & CBCR_CLK_OFF)) {
		kprintf("PANDORA GPU: CX %s, gpu_cc_ahb %s, cx_gmu %s: GMU not read\n", cx_on ? "on" : "off",
		    (ahb & CBCR_CLK_OFF) ? "off" : "on", (cx_gmu & CBCR_CLK_OFF) ? "off" : "on");
		return;
	}
	gpu_step("GMU state registers");
	vm_offset_t gmu = gpu_map(GMU_PA, GMU_SIZE);
	if (!gmu) {
		kprintf("PANDORA GPU: GMU window not mapped\n");
		return;
	}
	kprintf("PANDORA GPU: GMU cm3_sysreset %08x fw_init_result %08x hfi_ctrl_status %08x "
	    "gfx_pwr_clk_status %08x rpmh_power_state %08x ao_host_irq %08x\n",
	    gpu_rd(gmu, GMU_REG(0x1f800)), gpu_rd(gmu, GMU_REG(0x1f81c)), gpu_rd(gmu, GMU_REG(0x1f980)),
	    gpu_rd(gmu, GMU_REG(0x1f7e8)), gpu_rd(gmu, GMU_REG(0x1f7e9)), gpu_rd(gmu, GMU_REG(0x23b05)));
	kprintf("PANDORA GPU: RSCC status %08x CX_MISC tcm_ret %08x\n",
	    gpu_rd(gmu, 0x03d50000u + 0x101u * 4 - GMU_PA), gpu_rd(gmu, 0x03d9e000u + 0x39u * 4 - GMU_PA));
}

/* ---- level 3: the GPLL0 votes and SCM GPU_INIT_REGS -------------------------------------- */

#define SCM_FN(svc, cmd)  ((1ULL << 30) | (2ULL << 24) | (((uint64_t)(svc) & 0xffu) << 8) | ((cmd) & 0xffu))
#define SCM_ARGS(n)       ((uint64_t)(n) & 0xfu)
#define SCM_SVC_INFO      0x06u
#define SCM_INFO_AVAIL    0x01u        /* is_call_available */
#define SCM_SVC_GPU       0x28u
#define SCM_GPU_INIT_REGS 0x01u
#define GPU_ALWAYS_EN_REQ (1u << 0)
#define GPU_TSENSE_EN_REQ (1u << 3)

/*
 * The reference port reaches the secure monitor through its adsp driver's scm_smc().  This port
 * has no SCM helper in the kernel at all (checked against the symbol table: no scm_smc, no
 * qcom_scm_*), so the call is made here the way loader.c makes its own (smc_call): raw
 * "smc #0", function in x0, argument info in x1, arguments in x2/x3, results read back from
 * x0..x3.  No pSCI/hvc fallback is tried first: if the conduit is wrong the call comes back with
 * a negative status and nothing else happens, which is exactly what this level is for.
 */
static uint64_t
gpu_scm(uint64_t fn, uint64_t arginfo, uint64_t x2, uint64_t x3, uint64_t *r1, uint64_t *r2)
{
	register uint64_t x0 __asm__("x0") = fn;
	register uint64_t x1 __asm__("x1") = arginfo;
	register uint64_t x2r __asm__("x2") = x2;
	register uint64_t x3r __asm__("x3") = x3;

	__asm__ volatile ("smc #0"
	    : "+r" (x0), "+r" (x1), "+r" (x2r), "+r" (x3r)
	    : : "x4", "x5", "x6", "x7", "x8", "x9", "x10", "x11", "x12", "x13", "x14", "x15",
	        "x16", "x17", "memory");
	if (r1 != NULL) {
		*r1 = x1;
	}
	if (r2 != NULL) {
		*r2 = x2r;
	}
	return x0;
}

static void
gpu_level3(void)
{
	vm_offset_t cc = gpu_map(GPUCC_PA, GPUCC_SIZE);
	vm_offset_t gcc52 = gpu_map(GCC_PA + GCC_GPLL0_VOTE, 0x1000);
	if (!cc || !gcc52) {
		kprintf("PANDORA GPU: level 3: a window did not map\n");
		return;
	}
	if (!(gpu_rd(cc, GPUCC_CX_GDSCR) & GDSCR_PWR_ON)) {
		kprintf("PANDORA GPU: level 3 skipped, CX is off\n");
		return;
	}

	/* Linux's gcc-kaanapali.c: the GPU's GPLL0 votes are the two bits in 0x52000. */
	gpu_step("GPLL0 votes for the GPU (GCC 0x52000 bits 15/16)");
	uint32_t v = gpu_rd(gcc52, 0);
	gpu_wr(gcc52, 0, v | (1u << 15) | (1u << 16));
	delay(50);
	kprintf("PANDORA GPU: gpll0_vote %08x -> %08x\n", v, gpu_rd(gcc52, 0));

	/* Ask the secure monitor whether the GPU call exists before making it (svc 6, cmd 1). */
	uint64_t avail = 0, extra = 0;
	uint64_t id = (2ULL << 24) | ((uint64_t)SCM_SVC_GPU << 8) | SCM_GPU_INIT_REGS;
	gpu_step("SCM is_call_available(gpu, GPU_INIT_REGS)");
	avail = gpu_scm(SCM_FN(SCM_SVC_INFO, SCM_INFO_AVAIL), SCM_ARGS(1), id, 0, &extra, NULL);
	kprintf("PANDORA GPU: SCM is_call_available -> status %08x%08x info %08x%08x %s\n",
	    (uint32_t)(avail >> 32), (uint32_t)avail, (uint32_t)(extra >> 32), (uint32_t)extra,
	    (avail == 0 && extra != 0) ? "call exists" : "not available / conduit wrong");

	/* GPU_INIT_REGS: ALWAYS_EN | TSENSE_EN, the argument the reference port passes. */
	gpu_step("SCM GPU_INIT_REGS (ALWAYS_EN | TSENSE_EN)");
	uint64_t st = 0, st1 = 0;
	st = gpu_scm(SCM_FN(SCM_SVC_GPU, SCM_GPU_INIT_REGS), SCM_ARGS(1),
	    (uint64_t)(GPU_ALWAYS_EN_REQ | GPU_TSENSE_EN_REQ), 0, &st1, NULL);
	kprintf("PANDORA GPU: SCM GPU_INIT_REGS(%#x) -> status %08x%08x info %08x%08x %s\n",
	    GPU_ALWAYS_EN_REQ | GPU_TSENSE_EN_REQ, (uint32_t)(st >> 32), (uint32_t)st,
	    (uint32_t)(st1 >> 32), (uint32_t)st1, st == 0 ? "ok" : "FAILED");

	/* Re-read what the plan says to check: GMU still held, no reset, HFI quiet, TCM_RET 0. */
	kprintf("PANDORA GPU: GPUCC after: cx_gdscr %08x hw %08x smmu_vote %08x cx_gmu %08x\n",
	    gpu_rd(cc, GPUCC_CX_GDSCR), gpu_rd(cc, GPUCC_CX_GDS_HW), gpu_rd(cc, GPUCC_SMMU_VOTE),
	    gpu_rd(cc, GPUCC_CX_GMU));
	vm_offset_t gmu = gpu_map(GMU_PA, GMU_SIZE);
	if (gmu != 0) {
		kprintf("PANDORA GPU: GMU after: cm3_sysreset %08x fw_init_result %08x "
		    "hfi_ctrl_status %08x gfx_pwr_clk_status %08x rpmh_power_state %08x "
		    "ao_host_irq %08x\n", gpu_rd(gmu, GMU_REG(0x1f800)),
		    gpu_rd(gmu, GMU_REG(0x1f81c)), gpu_rd(gmu, GMU_REG(0x1f980)),
		    gpu_rd(gmu, GMU_REG(0x1f7e8)), gpu_rd(gmu, GMU_REG(0x1f7e9)),
		    gpu_rd(gmu, GMU_REG(0x23b05)));
		kprintf("PANDORA GPU: RSCC status %08x CX_MISC tcm_ret %08x\n",
		    gpu_rd(gmu, 0x03d50000u + 0x101u * 4 - GMU_PA),
		    gpu_rd(gmu, 0x03d9e000u + 0x39u * 4 - GMU_PA));
	} else {
		kprintf("PANDORA GPU: GMU window not mapped after SCM\n");
	}
}

/* ---- level 4a: the GPU firmware pack (PANDFW01) out of userdata --------------------------- */

/*
 * The reference port pins its firmware pack in memory and reads it with pandora_fw_read() from
 * its sweet_ufs.c.  This port already has the same capability as an exported function:
 * drivers/storage/hakios.ufs.c's hakios_ufs_read_partition(name, offset, dst, len, &bytes) reads
 * bytes of a named partition through the loader's block page (it is what the modem's file systems
 * use).  So the pack is simply written into userdata, well away from the image and the log
 * regions, and read from there: no loader change and no new storage driver.
 *
 *   tools/fwpack-deploy.ps1  pushes artifacts/fwpack-gpu.img to userdata at 400 GiB
 *   boot arg fwpack_mib=<MiB> moves it (argument is in MiB so it still fits an int)
 */
#define FW_PACK_DEFAULT_MIB  409600u             /* 400 GiB into userdata */

struct pack_ent {
	char name[48];
	uint64_t off, size;
};

extern int hakios_ufs_read_partition(const char *name, uint64_t offset, uint8_t *dst,
    uint64_t len, uint64_t *bytes);

static void
gpu_level4a(void)
{
	static uint8_t dir[4096];
	static uint8_t head[64];
	uint64_t part_bytes = 0;
	/* 400 GiB into userdata; PE_parse_boot_argn cannot take a 64-bit value, so the override is
	 * given in MiB (fwpack_mib=409600) and shifted here. */
	uint32_t mib = FW_PACK_DEFAULT_MIB;
	uint64_t off = (uint64_t)mib << 20;

	if (PE_parse_boot_argn("fwpack_mib", &mib, sizeof(mib)) && mib != 0) {
		off = (uint64_t)mib << 20;
	}
	int err = hakios_ufs_read_partition("userdata", off, dir, sizeof(dir), &part_bytes);
	kprintf("PANDORA GPU: fwpack userdata+%u MiB -> read %d, partition %08x%08x bytes\n",
	    (uint32_t)(off >> 20), err, (uint32_t)(part_bytes >> 32), (uint32_t)part_bytes);
	if (err != 0) {
		return;
	}
	if (memcmp(dir, "PANDFW01", 8) != 0) {
		kprintf("PANDORA GPU: fwpack: no PANDFW01 magic, first bytes %02x %02x %02x %02x "
		    "%02x %02x %02x %02x\n", dir[0], dir[1], dir[2], dir[3], dir[4], dir[5], dir[6],
		    dir[7]);
		return;
	}
	uint32_t n = 0;
	memcpy(&n, dir + 8, 4);
	kprintf("PANDORA GPU: fwpack has %u files\n", n);
	uint64_t gmu_off = 0, gmu_size = 0;
	for (uint32_t i = 0; i < n && i < 63; i++) {
		struct pack_ent e;
		memcpy(&e, dir + 16 + 64 * i, sizeof(e));
		e.name[47] = 0;
		kprintf("PANDORA GPU: fwpack[%u] %s off %08x%08x size %08x%08x\n", i, e.name,
		    (uint32_t)(e.off >> 32), (uint32_t)e.off, (uint32_t)(e.size >> 32), (uint32_t)e.size);
		if (strncmp(e.name, "gen80200_gmu.bin", 47) == 0) {
			gmu_off = e.off;
			gmu_size = e.size;
		}
	}
	if (gmu_size == 0) {
		kprintf("PANDORA GPU: fwpack has no gen80200_gmu.bin\n");
		return;
	}
	/* the file itself: its first bytes, so a broken directory is not mistaken for a good one */
	err = hakios_ufs_read_partition("userdata", off + gmu_off, head, sizeof(head), NULL);
	kprintf("PANDORA GPU: gmu.bin read %d, size %08x%08x, first %02x %02x %02x %02x %02x %02x %02x %02x\n",
	    err, (uint32_t)(gmu_size >> 32), (uint32_t)gmu_size, head[0], head[1], head[2], head[3],
	    head[4], head[5], head[6], head[7]);
}

static void
gpu_thread(void *arg, wait_result_t result)
{
	int level = 0;
	(void)arg;
	(void)result;
	(void)PE_parse_boot_argn("pandora_gpu", &level, sizeof(level));
	kprintf("PANDORA GPU: level %d (1 = read only, 3 = + GPLL0 vote and GPU_INIT_REGS, "
	    "4 = + firmware pack)\n", level);
	gpu_cmddb();
	gpu_level1();
	if (level >= 3) {
		gpu_level3();
	}
	if (level >= 4) {
		gpu_level4a();
	}
	kprintf("PANDORA GPU: done\n");
	(void)thread_terminate(current_thread());
	thread_block(THREAD_CONTINUE_NULL);
}

void
pandora_gpu_init(void)
{
	thread_t thread;
	int level = 0;
	int parsed = PE_parse_boot_argn("pandora_gpu", &level, sizeof(level));
	const char *args = PE_boot_args();

	/*
	 * HAKIOS-GPU-DIAG: this is reached from bsdinit_task right after bsd_do_post (verified in the
	 * image: bl _bsd_do_post -> kprintf -> bl _pandora_gpu_init).  A probe that says nothing has
	 * to say why, or a whole boot cycle is wasted: how the boot argument parsed, and what the
	 * kernel's own copy of the string looks like from inside.
	 */
	kprintf("PANDORA GPU: init parsed %d level %d\n", parsed, level);
	if (args != NULL) {
		kprintf("PANDORA GPU: args <%s>\n", args);
	} else {
		kprintf("PANDORA GPU: args NULL\n");
	}
	if (level <= 0) {
		kprintf("PANDORA GPU: boot-arg pandora_gpu missing or 0, probe idle\n");
		return;
	}
	if (kernel_thread_start(gpu_thread, NULL, &thread) == KERN_SUCCESS) {
		kprintf("PANDORA GPU: probe thread started\n");
		thread_deallocate(thread);
	} else {
		kprintf("PANDORA GPU: kernel_thread_start FAILED\n");
	}
}
#endif /* PANDORA */
