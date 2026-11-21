/*
 * PANDORA glue for this port: the few symbols the reference port's pandora_gpu.c expects from
 * its own tree, implemented against what this kernel actually has.  Keeping this in a separate
 * file lets pandora_gpu.c stay verbatim (bar printf -> kprintf), so its levels keep the exact
 * semantics the reference measured on the same SoC.
 *
 *   pandora_fw_read(off, dst, len)  the firmware pack (PANDFW01, tools/mkfwpack.py) written
 *                                   into userdata at 400 GiB (tools/fwpack-deploy.ps1); read
 *                                   through drivers/storage/hakios.ufs.c, which this kernel
 *                                   already exports for the modem's file systems
 *   pandora_scm_call(...)           raw "smc #0": this kernel has no SCM helper (checked), and
 *                                   level 3 proved the conduit works from here
 *   pandora_pas_load_auth(...)      level 11 (zap shader) is not ported yet: refuses cleanly
 *   pandora_gpu_release_ref(...)    level 14 user client, not ported: no-op
 *   sweet_gic_counts(...)           level 12 diagnostics read the reference's GIC: report zeros
 */
#include <sys/param.h>
#include <sys/systm.h>
#include <sys/errno.h>
#include <pexpert/pexpert.h>

#define FW_PACK_DEFAULT_MIB  409600u    /* 400 GiB into userdata */

extern int hakios_ufs_read_partition(const char *name, uint64_t offset, uint8_t *dst,
    uint64_t len, uint64_t *bytes);

int
pandora_fw_read(uint64_t off, void *dst, uint64_t len)
{
	static uint64_t base;

	if (base == 0) {
		uint32_t mib = FW_PACK_DEFAULT_MIB;

		(void)PE_parse_boot_argn("fwpack_mib", &mib, sizeof(mib));
		if (mib == 0) {
			mib = FW_PACK_DEFAULT_MIB;
		}
		base = (uint64_t)mib << 20;
	}
	return hakios_ufs_read_partition("userdata", base + off, (uint8_t *)dst, len, NULL);
}

void
pandora_scm_call(uint64_t fn, uint64_t arginfo, uint64_t x2, uint64_t x3, uint64_t *a0,
    uint64_t *a1)
{
	register uint64_t r0 __asm__("x0") = fn;
	register uint64_t r1 __asm__("x1") = arginfo;
	register uint64_t r2 __asm__("x2") = x2;
	register uint64_t r3 __asm__("x3") = x3;

	__asm__ volatile ("smc #0"
	    : "+r" (r0), "+r" (r1), "+r" (r2), "+r" (r3)
	    : : "x4", "x5", "x6", "x7", "x8", "x9", "x10", "x11", "x12", "x13", "x14", "x15",
	        "x16", "x17", "memory");
	if (a0 != NULL) {
		*a0 = r0;
	}
	if (a1 != NULL) {
		*a1 = r1;
	}
}

int
pandora_pas_load_auth(const char *file, unsigned pas, uint64_t mem_pa, uint64_t mem_size)
{
	(void)file;
	(void)pas;
	(void)mem_pa;
	(void)mem_size;
	return ENOENT;
}

void
pandora_gpu_release_ref(void *ref)
{
	(void)ref;
}

void
sweet_gic_counts(uint64_t *fired, uint64_t *unregistered, uint64_t *hard_disables)
{
	if (fired != NULL) {
		*fired = 0;
	}
	if (unregistered != NULL) {
		*unregistered = 0;
	}
	if (hard_disables != NULL) {
		*hard_disables = 0;
	}
}
