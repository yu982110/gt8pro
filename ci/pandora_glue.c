/*
 * PANDORA glue for this port: what the reference port's pandora_gpu.c expects from its own tree,
 * implemented against what this kernel has.
 *
 *   pandora_fw_read(off, dst, len)   the firmware pack (PANDFW01) written into userdata at
 *                                    400 GiB (tools/fwpack-deploy.ps1), read through
 *                                    drivers/storage/hakios.ufs.c (the same block service the
 *                                    modem's file systems use)
 *   pandora_scm_call(...)            raw "smc #0"; level 3 proved the conduit works from here
 *   pandora_pas_load_auth(...)       the PIL loader, ported from the reference's
 *                                    pandora_adsp.c (pas_load/pas_auth): 32-bit ELF program
 *                                    headers, metadata (ELF header + hash segment) in
 *                                    contiguous kernel memory, PAS init_image, mem_setup when
 *                                    relocatable, segments into the carveout, auth_and_reset.
 *                                    Needed by level 11 (the zap shader that leaves secure mode)
 *   pandora_gpu_release_ref(...)     level 14 user client, not ported: no-op
 *   sweet_gic_counts(...)            level 12 diagnostics read the reference's GIC: zeros
 */
#include <sys/param.h>
#include <sys/systm.h>
#include <sys/errno.h>
#include <kern/kalloc.h>
#include <kern/clock.h>
#include <machine/machine_routines.h>
#include <pexpert/pexpert.h>
#include <vm/pmap.h>
#include <vm/vm_kern.h>
#include <arm/caches_internal.h>

#define FW_PACK_DEFAULT_MIB  409600u    /* 400 GiB into userdata */

extern int hakios_ufs_read_partition(const char *name, uint64_t offset, uint8_t *dst,
    uint64_t len, uint64_t *bytes);
extern vm_offset_t ml_io_map_unmappable(vm_offset_t, vm_size_t, uint32_t);
extern void ml_io_unmap(vm_offset_t, vm_size_t);

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

/* ---- SCM ------------------------------------------------------------------------------- */

#define SCM_RW            2u
#define SCM_FN(svc, cmd)  ((1ULL << 30) | (2ULL << 24) | (((uint64_t)(svc) & 0xffu) << 8) | ((cmd) & 0xffu))
#define SCM_ARGS(n)       ((uint64_t)(n) & 0xfu)

/*
 * SMCCC yielding call: x0 == 1 does NOT mean failure, it means "the call was interrupted, call
 * again with x0 = 1 and the x6 this call returned" (Linux qcom_scm-smc.c does the same loop).
 * PAS init_image is long enough to be interrupted, which is exactly what happened the first time
 * this ran: a0 = 1 read as an error and level 11 stopped.
 */
static uint64_t
pandora_scm6(uint64_t fn, uint64_t arginfo, uint64_t x2, uint64_t x3, uint64_t x4, uint64_t x5,
    uint64_t *a0, uint64_t *a1)
{
	uint64_t x0 = fn, x6 = 0;

	for (int tries = 0; tries < 1000; tries++) {
		register uint64_t r0 __asm__("x0") = x0;
		register uint64_t r1 __asm__("x1") = arginfo;
		register uint64_t r2 __asm__("x2") = x2;
		register uint64_t r3 __asm__("x3") = x3;
		register uint64_t r4 __asm__("x4") = x4;
		register uint64_t r5 __asm__("x5") = x5;
		register uint64_t r6 __asm__("x6") = x6;

		__asm__ volatile ("smc #0"
		    : "+r" (r0), "+r" (r1), "+r" (r2), "+r" (r3), "+r" (r4), "+r" (r5), "+r" (r6)
		    : : "x7", "x8", "x9", "x10", "x11", "x12", "x13", "x14", "x15", "x16", "x17",
		        "memory");
		x6 = r6;
		if (r0 != 1) {
			if (a0 != NULL) {
				*a0 = r0;
			}
			if (a1 != NULL) {
				*a1 = r1;
			}
			return r0;
		}
		x0 = 1;
	}
	if (a0 != NULL) {
		*a0 = 1;
	}
	if (a1 != NULL) {
		*a1 = 0;
	}
	return 1;
}

void
pandora_scm_call(uint64_t fn, uint64_t arginfo, uint64_t x2, uint64_t x3, uint64_t *a0, uint64_t *a1)
{
	(void)pandora_scm6(fn, arginfo, x2, x3, 0, 0, a0, a1);
}

/* ---- the firmware pack's directory ------------------------------------------------------- */

struct pack_ent {
	char name[48];
	uint64_t off, size;
};

struct fw_file {
	uint64_t size, off;                 /* into the pack */
};

static struct pack_ent pack_dir[63];
static int pack_nfiles = -1;            /* -1: not read yet */

static int
fw_dir(void)
{
	static uint8_t dir[4096];

	if (pack_nfiles >= 0) {
		return 0;
	}
	if (pandora_fw_read(0, dir, sizeof(dir)) != 0 || memcmp(dir, "PANDFW01", 8) != 0) {
		kprintf("PANDORA FW: no firmware pack (no PANDFW01)\n");
		return ENOENT;
	}
	uint32_t n = 0;

	memcpy(&n, dir + 8, 4);
	if (n > 63) {
		n = 63;
	}
	for (uint32_t i = 0; i < n; i++) {
		memcpy(&pack_dir[i], dir + 16 + 64 * i, sizeof(pack_dir[i]));
		pack_dir[i].name[47] = 0;
	}
	pack_nfiles = (int)n;
	kprintf("PANDORA FW: firmware pack has %d files\n", pack_nfiles);
	return 0;
}

static int
fw_open(const char *name, struct fw_file *f)
{
	int err = fw_dir();

	if (err) {
		return err;
	}
	for (int i = 0; i < pack_nfiles; i++) {
		if (strncmp(pack_dir[i].name, name, sizeof(pack_dir[i].name)) == 0) {
			f->size = pack_dir[i].size;
			f->off = pack_dir[i].off;
			return 0;
		}
	}
	return ENOENT;
}

static int
fw_pread(const struct fw_file *f, uint64_t off, void *dst, uint64_t len)
{
	if (off + len < off || off + len > f->size) {
		return EINVAL;
	}
	return pandora_fw_read(f->off + off, dst, len);
}

/* ---- PAS: the PIL loader, from the reference's pandora_adsp.c ---------------------------- */

struct elf32_phdr {
	uint32_t p_type, p_offset, p_vaddr, p_paddr, p_filesz, p_memsz, p_flags, p_align;
};

#define MDT_TYPE_MASK    (7u << 24)
#define MDT_TYPE_HASH    (2u << 24)
#define MDT_RELOCATABLE  (1u << 27)

static bool
mdt_loadable(const struct elf32_phdr *p)
{
	return p->p_type == 1 && (p->p_flags & MDT_TYPE_MASK) != MDT_TYPE_HASH && p->p_memsz;
}

static int
pas_load(const char *base, unsigned pas, uint64_t mem_pa, uint64_t mem_size)
{
	struct fw_file mdt;
	static uint8_t hdr[0x10000];

	if (fw_open(base, &mdt) || mdt.size < 52 || mdt.size > sizeof(hdr)) {
		kprintf("PANDORA PAS: %s missing in the pack\n", base);
		return ENOENT;
	}
	int err = fw_pread(&mdt, 0, hdr, mdt.size);

	if (err) {
		return err;
	}
	uint32_t phoff;
	uint32_t phnum;

	memcpy(&phoff, hdr + 28, 4);
	phnum = hdr[44] | (hdr[45] << 8);
	if (memcmp(hdr, "\177ELF", 4) || hdr[4] != 1 || phnum < 2 || phnum > 128 ||
	    phoff + phnum * sizeof(struct elf32_phdr) > mdt.size) {
		kprintf("PANDORA PAS: %s is not a 32-bit ELF\n", base);
		return EINVAL;
	}
	const struct elf32_phdr *ph = (const struct elf32_phdr *)(hdr + phoff);
	unsigned hash = 0;
	uint64_t min = ~0ull, max = 0;
	bool reloc = false;

	for (unsigned i = 0; i < phnum; i++) {
		if (!hash && i && (ph[i].p_flags & MDT_TYPE_MASK) == MDT_TYPE_HASH) {
			hash = i;
		}
		if (!mdt_loadable(&ph[i])) {
			continue;
		}
		reloc |= (ph[i].p_flags & MDT_RELOCATABLE) != 0;
		min = MIN(min, (uint64_t)ph[i].p_paddr);
		max = MAX(max, ((uint64_t)ph[i].p_paddr + ph[i].p_memsz + 0xfff) & ~0xfffull);
	}
	if (!hash || ph[0].p_type == 1 || min == ~0ull) {
		kprintf("PANDORA PAS: %s: no hash segment\n", base);
		return EINVAL;
	}
	uint32_t ehdr_size = ph[0].p_filesz, hash_size = ph[hash].p_filesz;
	uint64_t md_size = ((uint64_t)ehdr_size + hash_size + 0xfff) & ~0xfffull;

	kprintf("PANDORA PAS: %s: %u segments, hash %u (%u bytes), load %08x%08x-%08x%08x%s\n", base,
	    phnum, hash, hash_size, (uint32_t)(min >> 32), (uint32_t)min, (uint32_t)(max >> 32),
	    (uint32_t)max, reloc ? " relocatable" : "");
	if (max - min > mem_size) {
		kprintf("PANDORA PAS: %s does not fit its carveout (%u bytes needed)\n", base,
		    (uint32_t)(max - min));
		return EINVAL;
	}
	vm_offset_t md = 0;

	if (kmem_alloc_contig(kernel_map, &md, (vm_size_t)md_size, PAGE_MASK, 0, 0,
	    KMA_DATA | KMA_ZERO | KMA_PERMANENT, VM_KERN_MEMORY_IOKIT) != KERN_SUCCESS) {
		return ENOMEM;
	}
	uint64_t md_pa = ptoa_64(pmap_find_phys(kernel_pmap, (addr64_t)md));

	memcpy((void *)md, hdr, ehdr_size);
	if (ehdr_size + hash_size == mdt.size) {
		memcpy((void *)(md + ehdr_size), hdr + ehdr_size, hash_size);
	} else if ((uint64_t)ph[hash].p_offset + hash_size <= mdt.size) {
		memcpy((void *)(md + ehdr_size), hdr + ph[hash].p_offset, hash_size);
	} else {
		kprintf("PANDORA PAS: %s: hash segment outside the file\n", base);
		return EINVAL;
	}
	flush_dcache64((addr64_t)md_pa, (unsigned)md_size, TRUE);

	/* No SHM bridge of our own: the hypervisor bridges PIL metadata itself (the reference
	 * found that a buffer already in a bridge makes init_image fail with -3145738). */
	uint64_t a0 = 0, a1 = 0;

	(void)pandora_scm6(SCM_FN(2, 1), SCM_ARGS(2) | (SCM_RW << 6), pas, md_pa, 0, 0, &a0, &a1);
	kprintf("PANDORA PAS: pas %u init_image (%u bytes) -> a0 %08x%08x (%d) a1 %08x%08x\n", pas,
	    ehdr_size + hash_size, (uint32_t)(a0 >> 32), (uint32_t)a0, (int32_t)(uint32_t)a0,
	    (uint32_t)(a1 >> 32), (uint32_t)a1);
	if (a0 != 0 || a1 != 0) {
		return EIO;
	}
	if (reloc) {
		(void)pandora_scm6(SCM_FN(2, 2), SCM_ARGS(3), pas, mem_pa, max - min, 0, &a0, &a1);
		kprintf("PANDORA PAS: pas %u mem_setup %08x+%08x -> a0 %08x%08x a1 %08x%08x\n", pas,
		    (uint32_t)mem_pa, (uint32_t)(max - min), (uint32_t)(a0 >> 32), (uint32_t)a0,
		    (uint32_t)(a1 >> 32), (uint32_t)a1);
		if (a0 != 0 || a1 != 0) {
			return EIO;
		}
	}
	/* The carveout, mapped write-combined for the copy only (ml_io_map_unmappable also
	 * supports the unmap, which ml_io_map's 8 MB submap cannot). */
	vm_offset_t mem = ml_io_map_unmappable((vm_offset_t)mem_pa,
	    (vm_size_t)((mem_size + 0xfff) & ~0xfffull), VM_WIMG_WCOMB);

	if (!mem) {
		kprintf("PANDORA PAS: %s: carveout %08x+%08x not mapped\n", base, (uint32_t)mem_pa,
		    (uint32_t)mem_size);
		return ENOMEM;
	}
	uint64_t reloc_base = reloc ? min : mem_pa, total = 0;

	err = 0;
	for (unsigned i = 0; i < phnum && !err; i++) {
		if (!mdt_loadable(&ph[i])) {
			continue;
		}
		uint64_t off = (uint64_t)ph[i].p_paddr - reloc_base;

		if ((int64_t)off < 0 || off + ph[i].p_memsz > mem_size ||
		    ph[i].p_filesz > ph[i].p_memsz) {
			kprintf("PANDORA PAS: %s segment %u outside the carveout\n", base, i);
			err = EINVAL;
			break;
		}
		if (ph[i].p_filesz && (err = fw_pread(&mdt, ph[i].p_offset, (void *)(mem + off),
		    ph[i].p_filesz))) {
			break;
		}
		if (ph[i].p_memsz > ph[i].p_filesz) {
			bzero((void *)(mem + off + ph[i].p_filesz), ph[i].p_memsz - ph[i].p_filesz);
		}
		total += ph[i].p_filesz;
	}
	__asm__ volatile ("dsb sy" ::: "memory");
	ml_io_unmap(mem, (vm_size_t)((mem_size + 0xfff) & ~0xfffull));
	if (!err) {
		kprintf("PANDORA PAS: %s: %u bytes loaded at %08x\n", base, (uint32_t)total,
		    (uint32_t)mem_pa);
	}
	return err;
}

static int
pas_auth(unsigned pas)
{
	uint64_t a0 = 0, a1 = 0, t0 = mach_absolute_time(), ns;

	(void)pandora_scm6(SCM_FN(2, 5), SCM_ARGS(1), pas, 0, 0, 0, &a0, &a1);
	absolutetime_to_nanoseconds(mach_absolute_time() - t0, &ns);
	kprintf("PANDORA PAS: pas %u auth_and_reset -> a0 %08x%08x a1 %08x%08x (%u ms)\n", pas,
	    (uint32_t)(a0 >> 32), (uint32_t)a0, (uint32_t)(a1 >> 32), (uint32_t)a1,
	    (uint32_t)(ns / 1000000));
	return (a0 != 0 || a1 != 0) ? EIO : 0;
}

int
pandora_pas_load_auth(const char *file, unsigned pas, uint64_t mem_pa, uint64_t mem_size)
{
	int err = fw_dir();

	if (!err) {
		err = pas_load(file, pas, mem_pa, mem_size);
	}
	if (!err) {
		err = pas_auth(pas);
	}
	return err;
}

/* ---- stubs for the levels this port has not taken on yet --------------------------------- */

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

/*
 * The reference's user client asks its SweetGIC this before wiring a filter interrupt source; this
 * port has no SweetGIC (no such symbol in the kernel), so the answer is no and the client falls
 * back to polling the fence, which is exactly the path it takes when the interrupt is missing
 * ("PANDORA GPU UC: no interrupt ... fence waits poll"). Interrupts can be wired later.
 */
int
sweet_gic_ready(void)
{
	return 0;
}
