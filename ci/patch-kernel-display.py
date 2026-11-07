#!/usr/bin/env python3
"""HAKIOS-LAFA: 把 SWEET 的 IOSurface + IOMobileFramebuffer 装进这个内核。

素材来自 data-a-live（小米 17 Pro，同 SoC：SM8850 / 骁龙 8 Elite Gen 5，iOS 16，
xnu-8796）的 `0046-sweet-iosurface-iomfb.patch`。本脚本把那三个文件写进 xnu 树，
面板参数改成 lafa 的 1440x3136 / stride 5760 / PA 0xFC800000，在
`iokit/conf/files.arm64` 和 `iokit/KernelConfigTables.cpp` 里注册，并把 hakios 树里
同名的两个 Mini 显示驱动（`MiniIOSurface.cpp` / `MiniIOMobileFramebuffer.cpp`）从
构建里摘掉——两套实现定义的是同一批 IOKit 类（`IOCoreSurfaceRoot`、
`IOSurfaceRootUserClient`、`IOMobileFramebuffer`、`IOMobileFramebufferUserClient`），
不能同时链接。

为什么必须摘掉 Mini 的显示器驱动（而不是留着当备份）：它从 `vinfo.v_baseaddr`
（视频控制台的 `struct vc_info`）取面板地址，在本移植上那个值是 PE_state.video 的
**物理地址** 0xFC800000（`initialize_screen()` 只把 bit0 置位的 v_baseAddr 当物理地址），
当指针用会在第一帧 memcpy 时 data abort；而 SweetFramebuffer 用
`IOMemoryDescriptor::withPhysicalAddress(0xFC800000)`（失败时退到
`ml_io_map_unmappable()`，也就是 `ci/patch-kernel-fb.py` 已经在这块硬件上验证过的做法）
自己建映射。

用法:
    python3 ci/patch-kernel-display.py <xnu 源码树> [选项]

选项:
    --check                 只报告会改什么，不写文件
    --mini auto|disable|none
                            auto（默认）：树里有 Mini 显示驱动就从构建里摘掉；
                            disable：强制摘；none：不碰（vanilla xnu 树用）
    --force                 即使 GPU 驱动（CONFIG_HAKIOS_ADRENO）开着也继续
                            （那种配置下 hakios.gpu.cpp / hakios.adreno.c 会引用
                            MiniIOSurface* 和 MiniIOMobileFramebufferShow，链接会失败）

幂等：同一棵树跑第二次是 no-op（文件内容相同不重写，注册项按标记查重）。退出码：
0 = 已是最新或改好了，1 = 树不对/前置条件不满足，2 = 用法错误。
"""
import re
import sys
from pathlib import Path

MARK = "HAKIOS-LAFA-DISPLAY"

SURFACE_H = r"""/*
 * Built-in IOSurface for this port (see SweetSurface.cpp).
 *
 * Shared with SweetFramebuffer.cpp: the framebuffer looks surfaces up by
 * id when a swap names them and publishes the panel memory itself as the
 * default framebuffer surface.
 *
 * Ported from the SWEET project (data-a-live,
 * xnu/patches/0046-sweet-iosurface-iomfb.patch, worked out against iOS 16
 * on the same SoC, SM8850).  What changed for the HakiOS lafa port:
 *
 *   - the "SWEET" build option guard is gone.  This tree has no such option
 *     (config/MASTER.arm64.bcm2837 does not define SWEET), so with the guard
 *     in place both C++ files would compile to nothing and the display
 *     services would silently not exist;
 *   - the panel geometry lives in SweetFramebuffer.cpp (1440x3136,
 *     stride 5760, PA 0xFC800000 -- see ci/patch-kernel-fb.py);
 *   - the 16 KiB page size of this build is accounted for, see the note at
 *     the top of SweetSurface.cpp.
 */
#ifndef _SWEET_SURFACE_H
#define _SWEET_SURFACE_H

#include <pexpert/arm64/board_config.h>

#include <IOKit/IOMemoryDescriptor.h>
#include <IOKit/IOBufferMemoryDescriptor.h>
#include <libkern/c++/OSObject.h>
#include <mach/mach_types.h>

#define SWEET_SURFACE_MAX_PLANES        20

struct sweet_surface_plane {
	uint32_t width, height, bytesPerRow, offset, size;
	uint16_t bytesPerElement;
	uint8_t  elementWidth, elementHeight;
};

class SweetSurface : public OSObject {
	OSDeclareDefaultStructors(SweetSurface);

public:
	uint32_t id;
	uint32_t width, height, bytesPerRow, offset, pixelFormat, allocSize;
	uint16_t bytesPerElement;
	uint8_t  elementWidth, elementHeight;
	uint32_t cacheMode, planeCount, parentID;
	struct sweet_surface_plane planes[SWEET_SURFACE_MAX_PLANES];

	IOMemoryDescriptor       *memory;       /* pixels (buffer or physical) */
	IOOptionBits              mapCache;     /* cache mode for user mappings */
	IOBufferMemoryDescriptor *state;        /* shared state page, read-only for users */
	IOMemoryMap              *kernelMap;    /* lazily created for kernel copies */

	bool init() APPLE_KEXT_OVERRIDE { return OSObject::init(); }
	void free() APPLE_KEXT_OVERRIDE;

	/* Kernel address of the pixels (mapped on first use), or 0. */
	uintptr_t kernelAddress();
};

/* Retained surface for an id, or NULL. */
SweetSurface *sweet_surface_copy(uint32_t id);

/*
 * Publish physical memory (the panel framebuffer) as a surface. Returns a
 * retained surface that stays in the id table until released by the caller
 * and every user.
 */
SweetSurface *sweet_surface_create_physical(uint64_t phys, uint32_t width,
    uint32_t height, uint32_t bytesPerRow, uint32_t pixelFormat);

/* Send right for the surface in task (IOSurfaceLookupFromMachPort). */
mach_port_name_t sweet_surface_port_in_task(SweetSurface *surface, task_t task);

#endif /* _SWEET_SURFACE_H */
"""

SURFACE_CPP = r"""/*
 * Built-in IOSurface for SWEET.
 *
 * Apple's IOSurface kext is closed. IOSurface.framework (iOS 16.7) finds
 * IOServiceNameMatching("IOCoreSurfaceRoot"), opens it with type 0 and
 * talks to the user client below:
 *
 *   sel 13  limits: struct out 0x18, u16 at +0x16 must be 1 (ABI version),
 *           +0 offset alignment mask, +4 bytes-per-row alignment mask
 *   sel 0   create: scalar [parent id], struct in = serialized property
 *           dictionary (binary or XML), struct out = lock result (0xa68)
 *   sel 4   lookup by id: scalar [id], struct out = lock result
 *   sel 34  lookup by mach port: scalar [port name], struct out = lock result
 *   sel 35  create mach port: scalars [id, options], scalar out [port name]
 *   traps   0/1 use count +/- (id, category), 2 lock / 3 unlock
 *           (id, options, &seed), 4/5 release (id)
 *
 * The lock result tells the library where the surface lives in the
 * caller's address space: +0 pixels, +8 shared state page (registry id,
 * seed at +0xc), +0x10 timestamps, then id, sizes and geometry (layout in
 * sweet_surface_fill_result). Every surface is wired kernel memory mapped
 * into each task that creates or looks it up; mappings are counted per
 * client and dropped on release or close.
 *
 * Values, notifications, accelerators and shared events are accepted and
 * ignored (logged once per selector).
 *
 * ---------------------------------------------------------------------------
 * Ported to the HakiOS lafa port (realme GT8 Pro, SM8850) from data-a-live's
 * 0046-sweet-iosurface-iomfb.patch.  The protocol below was worked out against
 * iOS 16's IOSurface.framework and is left as it was; what differs in this
 * tree:
 *
 *   1. there is no "SWEET" build option, so the guards are gone and the file
 *      is built whenever iokit/conf/files.arm64 lists it;
 *   2. the kernel has 16 KiB pages, not 4 KiB.  Nothing here hard-codes a page
 *      size -- round_page(), page_size and IOMemoryDescriptor's alignment all
 *      follow the build -- but the consequences are worth knowing:
 *	- a surface's allocation is rounded to 16 KiB (the panel's
 *	  1440*3136*4 = 18,063,360 bytes become 18,071,552);
 *	- IOSurfacePlaneOffset defaults advance 16 KiB at a time, so a
 *	  multi-plane surface is packed page-aligned in a slightly different way
 *	  than the 4 KiB reference produced;
 *	- the shared state page a client maps is 16 KiB instead of 4 KiB: the
 *	  offsets inside it (registry id at +0, seed at +0xc, timestamps at
 *	  +0x800) are unchanged and still fit, checked at compile time below;
 *	- every client mapping of a surface is 16 KiB-granular, which is what
 *	  IOSurface.framework asks for anyway (one mapping of the whole
 *	  allocation).
 * ---------------------------------------------------------------------------
 */
#include "SweetSurface.h"

#include <IOKit/IOService.h>
#include <IOKit/IOUserClient.h>
#include <IOKit/IOKitKeys.h>
#include <IOKit/IOLib.h>
#include <IOKit/IOLocks.h>
#include <IOKit/system.h>
#include <libkern/c++/OSArray.h>
#include <libkern/c++/OSDictionary.h>
#include <libkern/c++/OSNumber.h>
#include <libkern/c++/OSUnserialize.h>
#include <sys/proc.h>
#include <sys/systm.h>          /* bzero(), copyout() */
#include <mach/vm_param.h>      /* page_size, round_page() */

enum {
	kSweetSurfaceCreate             = 0,
	kSweetSurfaceLookup             = 4,
	kSweetSurfaceLimits             = 13,
	kSweetSurfaceLookupFromPort     = 34,
	kSweetSurfaceCreatePort         = 35,
};

enum {
	kSweetSurfaceTrapIncrementUse   = 0,
	kSweetSurfaceTrapDecrementUse   = 1,
	kSweetSurfaceTrapLock           = 2,
	kSweetSurfaceTrapUnlock         = 3,
	kSweetSurfaceTrapRelease        = 4,
	kSweetSurfaceTrapReleaseFailed  = 5,
};

#define SWEET_SURFACE_RESULT_SIZE       0xa68
#define SWEET_SURFACE_MAX_ID            4096
#define SWEET_SURFACE_MAX_DIM           16384
#define SWEET_SURFACE_MAX_ALLOC         (256u << 20)
#define SWEET_SURFACE_MAX_PROPS         (64u << 10)
#define SWEET_SURFACE_ROW_ALIGN         64
#define SWEET_SURFACE_TIMESTAMPS        0x800   /* offset in the state page */
#define SWEET_SURFACE_STATE_SEED        0xc     /* u32 in the state page */
#define kIOSurfaceLockReadOnly          0x1

/*
 * lafa: 16 KiB pages (config/MASTER.arm64.bcm2837, ARM_16K_PG); the reference
 * was built with 4 KiB.  Every rounding in this file goes through round_page()
 * or page_size, so the code follows the build; the one thing that cannot
 * follow is the layout of the shared state page, whose fixed offsets have to
 * stay inside a single page for any page size this port might use.
 */
#if SWEET_SURFACE_TIMESTAMPS + 16 > 4096
#error "SweetSurface: the shared-page offsets no longer fit in one page"
#endif

static IOLock       *sweet_surface_lock;
static SweetSurface *sweet_surface_table[SWEET_SURFACE_MAX_ID];
static uint32_t      sweet_surface_users[SWEET_SURFACE_MAX_ID];
static uint32_t      sweet_surface_next_id = 1;
static uint32_t      sweet_surface_creates_logged;
static uint64_t      sweet_surface_wired;       /* bytes of buffer surfaces */
#define SWEET_SURFACE_WIRED_MAX         (1536ull << 20)

OSDefineMetaClassAndStructors(SweetSurface, OSObject);

void
SweetSurface::free()
{
	if (OSDynamicCast(IOBufferMemoryDescriptor, memory) != NULL) {
		OSAddAtomic64(-(SInt64)allocSize, (volatile SInt64 *)&sweet_surface_wired);
	}
	OSSafeReleaseNULL(kernelMap);
	OSSafeReleaseNULL(memory);
	OSSafeReleaseNULL(state);
	OSObject::free();
}

uintptr_t
SweetSurface::kernelAddress()
{
	IOBufferMemoryDescriptor *buffer = OSDynamicCast(IOBufferMemoryDescriptor, memory);
	if (buffer != NULL) {
		return (uintptr_t)buffer->getBytesNoCopy();
	}
	IOLockLock(sweet_surface_lock);
	if (kernelMap == NULL && memory != NULL) {
		kernelMap = memory->map(kIOMapWriteCombineCache);
	}
	IOLockUnlock(sweet_surface_lock);
	return kernelMap != NULL ? (uintptr_t)kernelMap->getVirtualAddress() : 0;
}

static uint32_t *
sweet_surface_seed(SweetSurface *s)
{
	return (uint32_t *)((uint8_t *)s->state->getBytesNoCopy() + SWEET_SURFACE_STATE_SEED);
}

/*
 * Give the surface an id and a table slot; the table holds one retain and a
 * user count (creators, client mappings, kernel holders). Lock held.
 */
static bool
sweet_surface_insert_locked(SweetSurface *s)
{
	for (uint32_t n = 0; n < SWEET_SURFACE_MAX_ID - 1; n++) {
		uint32_t id = sweet_surface_next_id;
		sweet_surface_next_id = id + 1 < SWEET_SURFACE_MAX_ID ? id + 1 : 1;
		if (sweet_surface_table[id] == NULL) {
			s->id = id;
			s->retain();
			sweet_surface_table[id] = s;
			sweet_surface_users[id] = 1;
			*(uint64_t *)s->state->getBytesNoCopy() = id;   /* registry id */
			return true;
		}
	}
	return false;
}

/* Add a user to a surface that may have left the table (port lookups). */
static bool
sweet_surface_get(SweetSurface *s)
{
	bool ok = true;
	IOLockLock(sweet_surface_lock);
	if (sweet_surface_table[s->id] == s) {
		sweet_surface_users[s->id]++;
	} else if (sweet_surface_table[s->id] == NULL) {
		s->retain();
		sweet_surface_table[s->id] = s;
		sweet_surface_users[s->id] = 1;
	} else {
		ok = false;
	}
	IOLockUnlock(sweet_surface_lock);
	return ok;
}

/* Drop a user; the last one removes the surface from the table. */
static void
sweet_surface_put(SweetSurface *s)
{
	bool drop = false;
	IOLockLock(sweet_surface_lock);
	if (sweet_surface_table[s->id] == s && --sweet_surface_users[s->id] == 0) {
		sweet_surface_table[s->id] = NULL;
		drop = true;
	}
	IOLockUnlock(sweet_surface_lock);
	if (drop) {
		s->release();
	}
}

SweetSurface *
sweet_surface_copy(uint32_t id)
{
	SweetSurface *s = NULL;
	if (id == 0 || id >= SWEET_SURFACE_MAX_ID || sweet_surface_lock == NULL) {
		return NULL;
	}
	IOLockLock(sweet_surface_lock);
	s = sweet_surface_table[id];
	if (s != NULL) {
		s->retain();
	}
	IOLockUnlock(sweet_surface_lock);
	return s;
}

/* The table lock; the framebuffer may publish a surface before the root starts. */
static void
sweet_surface_init_lock(void)
{
	if (sweet_surface_lock == NULL) {
		IOLock *lock = IOLockAlloc();
		if (!OSCompareAndSwapPtr(NULL, lock, &sweet_surface_lock)) {
			IOLockFree(lock);
		}
	}
}

static SweetSurface *
sweet_surface_alloc(void)
{
	sweet_surface_init_lock();
	SweetSurface *s = OSTypeAlloc(SweetSurface);
	if (s == NULL || !s->init()) {
		OSSafeReleaseNULL(s);
		return NULL;
	}
	s->state = IOBufferMemoryDescriptor::inTaskWithOptions(kernel_task,
	    kIODirectionInOut | kIOMemoryKernelUserShared, page_size, page_size);
	if (s->state == NULL) {
		s->release();
		return NULL;
	}
	bzero(s->state->getBytesNoCopy(), page_size);
	return s;
}

static bool
sweet_surface_publish(SweetSurface *s)
{
	bool ok;
	IOLockLock(sweet_surface_lock);
	ok = sweet_surface_insert_locked(s);
	IOLockUnlock(sweet_surface_lock);
	return ok;
}

SweetSurface *
sweet_surface_create_physical(uint64_t phys, uint32_t width, uint32_t height,
    uint32_t bytesPerRow, uint32_t pixelFormat)
{
	SweetSurface *s = sweet_surface_alloc();
	if (s == NULL) {
		return NULL;
	}
	s->width = width;
	s->height = height;
	s->bytesPerRow = bytesPerRow;
	s->bytesPerElement = 4;
	s->elementWidth = s->elementHeight = 1;
	s->pixelFormat = pixelFormat;
	s->allocSize = (uint32_t)round_page(bytesPerRow * height);
	s->memory = IOMemoryDescriptor::withPhysicalAddress(phys, s->allocSize, kIODirectionInOut);
	s->mapCache = kIOMapWriteCombineCache;
	if (s->memory == NULL || !sweet_surface_publish(s)) {
		s->release();
		return NULL;
	}
	return s;
}

mach_port_name_t
sweet_surface_port_in_task(SweetSurface *s, task_t task)
{
	mach_port_name_t name = 0;
	IOUserClient::copyPortNameForObjectInTask(task, s, &name);
	return name;
}

static uint32_t
sweet_dict_u32(OSDictionary *dict, const char *key, uint32_t def)
{
	OSNumber *n = OSDynamicCast(OSNumber, dict->getObject(key));
	return n != NULL ? n->unsigned32BitValue() : def;
}

static uint32_t
sweet_div_up(uint32_t a, uint32_t b)
{
	return b > 1 ? (a + b - 1) / b : a;
}

static uint32_t
sweet_row_bytes(uint32_t width, uint32_t ew, uint32_t bpe)
{
	uint64_t row = (uint64_t)sweet_div_up(width, ew) * bpe;
	return (uint32_t)((row + SWEET_SURFACE_ROW_ALIGN - 1) & ~(uint64_t)(SWEET_SURFACE_ROW_ALIGN - 1));
}

/* Geometry from the creation properties (IOSurface.h keys). */
static IOReturn
sweet_surface_layout(SweetSurface *s, OSDictionary *props)
{
	uint64_t end;

	s->width = sweet_dict_u32(props, "IOSurfaceWidth", 0);
	s->height = sweet_dict_u32(props, "IOSurfaceHeight", 0);
	s->bytesPerElement = (uint16_t)sweet_dict_u32(props, "IOSurfaceBytesPerElement", 4);
	s->elementWidth = (uint8_t)sweet_dict_u32(props, "IOSurfaceElementWidth", 1);
	s->elementHeight = (uint8_t)sweet_dict_u32(props, "IOSurfaceElementHeight", 1);
	s->pixelFormat = sweet_dict_u32(props, "IOSurfacePixelFormat", 0);
	s->cacheMode = sweet_dict_u32(props, "IOSurfaceCacheMode", 0);
	s->offset = sweet_dict_u32(props, "IOSurfaceOffset", 0);
	if (s->width > SWEET_SURFACE_MAX_DIM || s->height > SWEET_SURFACE_MAX_DIM ||
	    s->bytesPerElement > 64 || s->elementWidth == 0 || s->elementHeight == 0) {
		return kIOReturnBadArgument;
	}
	s->bytesPerRow = sweet_dict_u32(props, "IOSurfaceBytesPerRow",
	    sweet_row_bytes(s->width, s->elementWidth, s->bytesPerElement));
	if (s->bytesPerRow < (uint64_t)sweet_div_up(s->width, s->elementWidth) * s->bytesPerElement) {
		return kIOReturnBadArgument;
	}
	end = (uint64_t)s->offset + (uint64_t)s->bytesPerRow * sweet_div_up(s->height, s->elementHeight);

	OSArray *planes = OSDynamicCast(OSArray, props->getObject("IOSurfacePlaneInfo"));
	s->planeCount = 0;
	if (planes != NULL) {
		uint64_t next = s->offset;
		if (planes->getCount() > SWEET_SURFACE_MAX_PLANES) {
			return kIOReturnBadArgument;
		}
		for (unsigned int i = 0; i < planes->getCount(); i++) {
			OSDictionary *pd = OSDynamicCast(OSDictionary, planes->getObject(i));
			struct sweet_surface_plane *p = &s->planes[i];
			if (pd == NULL) {
				return kIOReturnBadArgument;
			}
			p->width = sweet_dict_u32(pd, "IOSurfacePlaneWidth", s->width);
			p->height = sweet_dict_u32(pd, "IOSurfacePlaneHeight", s->height);
			p->bytesPerElement = (uint16_t)sweet_dict_u32(pd, "IOSurfacePlaneBytesPerElement", 1);
			p->elementWidth = (uint8_t)sweet_dict_u32(pd, "IOSurfacePlaneElementWidth", 1);
			p->elementHeight = (uint8_t)sweet_dict_u32(pd, "IOSurfacePlaneElementHeight", 1);
			if (p->width > SWEET_SURFACE_MAX_DIM || p->height > SWEET_SURFACE_MAX_DIM ||
			    p->bytesPerElement > 64 || p->elementWidth == 0 || p->elementHeight == 0) {
				return kIOReturnBadArgument;
			}
			p->bytesPerRow = sweet_dict_u32(pd, "IOSurfacePlaneBytesPerRow",
			    sweet_row_bytes(p->width, p->elementWidth, p->bytesPerElement));
			p->offset = sweet_dict_u32(pd, "IOSurfacePlaneOffset", (uint32_t)next);
			p->size = sweet_dict_u32(pd, "IOSurfacePlaneSize",
			    p->bytesPerRow * sweet_div_up(p->height, p->elementHeight));
			next = round_page((uint64_t)p->offset + p->size);
			if ((uint64_t)p->offset + p->size > end) {
				end = (uint64_t)p->offset + p->size;
			}
		}
		s->planeCount = planes->getCount();
	}

	uint64_t alloc = sweet_dict_u32(props, "IOSurfaceAllocSize", 0);
	if (alloc < end) {
		alloc = end;
	}
	alloc = round_page(alloc);
	if (alloc == 0 || alloc > SWEET_SURFACE_MAX_ALLOC) {
		return kIOReturnNoMemory;
	}
	s->allocSize = (uint32_t)alloc;
	return kIOReturnSuccess;
}

/* Per-client mapping of one surface. */
class SweetSurfaceRef : public OSObject {
	OSDeclareDefaultStructors(SweetSurfaceRef);

public:
	SweetSurface *surface;
	IOMemoryMap  *data;
	IOMemoryMap  *state;
	uint32_t      count;

	bool init() APPLE_KEXT_OVERRIDE { return OSObject::init(); }
	void free() APPLE_KEXT_OVERRIDE;
};

OSDefineMetaClassAndStructors(SweetSurfaceRef, OSObject);

void
SweetSurfaceRef::free()
{
	OSSafeReleaseNULL(data);
	OSSafeReleaseNULL(state);
	if (surface != NULL) {
		sweet_surface_put(surface);
		surface->release();
		surface = NULL;
	}
	OSObject::free();
}

class IOCoreSurfaceRoot : public IOService {
	OSDeclareDefaultStructors(IOCoreSurfaceRoot);

public:
	bool start(IOService *provider) APPLE_KEXT_OVERRIDE;
};

OSDefineMetaClassAndStructors(IOCoreSurfaceRoot, IOService);

bool
IOCoreSurfaceRoot::start(IOService *provider)
{
	if (!IOService::start(provider)) {
		return false;
	}
	sweet_surface_init_lock();
	/*
	 * lafa: IOSurface.framework finds this service with
	 * IOServiceNameMatching("IOCoreSurfaceRoot"), so the registry name is set here
	 * explicitly.  The reference relied on whatever name the personality dictionary
	 * had given the instance, which is not a contract.  (hakios'
	 * MiniIOSurface.cpp sets it the same way.)
	 */
	setName("IOCoreSurfaceRoot");
	setProperty(kIOUserClientClassKey, "IOSurfaceRootUserClient");
	registerService();
	IOLog("SWEET SURF: IOCoreSurfaceRoot registered\n");
	return true;
}

class IOSurfaceRootUserClient : public IOUserClient {
	OSDeclareDefaultStructors(IOSurfaceRootUserClient);

	task_t   fTask;
	IOLock  *fLock;
	OSArray *fRefs;

	SweetSurfaceRef *findRef(uint32_t id, unsigned int *index);
	IOReturn addRef(SweetSurface *s, SweetSurfaceRef **refOut);
	IOReturn returnSurface(SweetSurface *s, IOExternalMethodArguments *args);
	IOReturn create(IOExternalMethodArguments *args);

public:
	bool initWithTask(task_t owningTask, void *securityID, UInt32 type) APPLE_KEXT_OVERRIDE;
	void free() APPLE_KEXT_OVERRIDE;
	IOReturn clientClose(void) APPLE_KEXT_OVERRIDE;
	IOReturn externalMethod(uint32_t selector, IOExternalMethodArguments *args,
	    IOExternalMethodDispatch *dispatch, OSObject *target,
	    void *reference) APPLE_KEXT_OVERRIDE;
	IOExternalTrap *getTargetAndTrapForIndex(IOService **targetP, UInt32 index) APPLE_KEXT_OVERRIDE;

	IOReturn trapUseCount(void *id, void *category, void *, void *, void *, void *);
	IOReturn trapLock(void *id, void *options, void *seedOut, void *, void *, void *);
	IOReturn trapUnlock(void *id, void *options, void *seedOut, void *, void *, void *);
	IOReturn trapRelease(void *id, void *, void *, void *, void *, void *);
};

OSDefineMetaClassAndStructors(IOSurfaceRootUserClient, IOUserClient);

static uint64_t sweet_surf_logged;      /* selectors / traps already logged (< 64) */

static void
sweet_surf_log_once(const char *what, uint32_t n)
{
	uint32_t bit = (what[0] == 't' ? 48 : 0) + n;
	if (bit >= 64 || (sweet_surf_logged & (1ULL << bit))) {
		return;
	}
	sweet_surf_logged |= 1ULL << bit;
	char name[32];
	proc_selfname(name, sizeof(name));
	IOLog("SWEET SURF: %s %s %u\n", name, what, n);
}

bool
IOSurfaceRootUserClient::initWithTask(task_t owningTask, void *securityID, UInt32 type)
{
	if (!IOUserClient::initWithTask(owningTask, securityID, type)) {
		return false;
	}
	fTask = owningTask;
	fLock = IOLockAlloc();
	fRefs = OSArray::withCapacity(16);
	return fLock != NULL && fRefs != NULL;
}

void
IOSurfaceRootUserClient::free()
{
	OSSafeReleaseNULL(fRefs);
	if (fLock != NULL) {
		IOLockFree(fLock);
		fLock = NULL;
	}
	IOUserClient::free();
}

IOReturn
IOSurfaceRootUserClient::clientClose(void)
{
	IOLockLock(fLock);
	fRefs->flushCollection();
	IOLockUnlock(fLock);
	terminate();
	return kIOReturnSuccess;
}

/* Lock held. */
SweetSurfaceRef *
IOSurfaceRootUserClient::findRef(uint32_t id, unsigned int *index)
{
	for (unsigned int i = 0; i < fRefs->getCount(); i++) {
		SweetSurfaceRef *ref = (SweetSurfaceRef *)fRefs->getObject(i);
		if (ref->surface->id == id) {
			if (index != NULL) {
				*index = i;
			}
			return ref;
		}
	}
	return NULL;
}

/*
 * Count one more create/lookup of s by this client, mapping it into the
 * task the first time. Takes over the caller's user count on s.
 */
IOReturn
IOSurfaceRootUserClient::addRef(SweetSurface *s, SweetSurfaceRef **refOut)
{
	IOLockLock(fLock);
	SweetSurfaceRef *ref = findRef(s->id, NULL);
	if (ref != NULL && ref->surface == s) {
		ref->count++;
		IOLockUnlock(fLock);
		sweet_surface_put(s);
		*refOut = ref;
		return kIOReturnSuccess;
	}
	IOLockUnlock(fLock);

	ref = OSTypeAlloc(SweetSurfaceRef);
	if (ref == NULL || !ref->init()) {
		OSSafeReleaseNULL(ref);
		sweet_surface_put(s);
		return kIOReturnNoMemory;
	}
	s->retain();
	ref->surface = s;
	ref->count = 1;
	ref->data = s->memory->createMappingInTask(fTask, 0, kIOMapAnywhere | s->mapCache);
	ref->state = s->state->createMappingInTask(fTask, 0, kIOMapAnywhere | kIOMapReadOnly);
	if (ref->data == NULL || ref->state == NULL) {
		ref->release();
		return kIOReturnVMError;
	}
	IOLockLock(fLock);
	SweetSurfaceRef *raced = findRef(s->id, NULL);
	if (raced != NULL && raced->surface == s) {
		/* Another thread mapped it meanwhile: count there, drop ours. */
		raced->count++;
		IOLockUnlock(fLock);
		ref->release();
		*refOut = raced;
		return kIOReturnSuccess;
	}
	fRefs->setObject(ref);
	IOLockUnlock(fLock);
	ref->release();         /* fRefs holds it */
	*refOut = ref;
	return kIOReturnSuccess;
}

static void
put32(uint8_t *p, uint32_t off, uint32_t v)
{
	memcpy(p + off, &v, 4);
}

static void
put64(uint8_t *p, uint32_t off, uint64_t v)
{
	memcpy(p + off, &v, 8);
}

/* IOSurface.framework's lock result (copied to IOSurfaceClient + 0x68). */
static void
sweet_surface_fill_result(SweetSurface *s, SweetSurfaceRef *ref, uint8_t *out)
{
	bzero(out, SWEET_SURFACE_RESULT_SIZE);
	put64(out, 0x00, ref->data->getAddress());
	put64(out, 0x08, ref->state->getAddress());
	put64(out, 0x10, ref->state->getAddress() + SWEET_SURFACE_TIMESTAMPS);
	put32(out, 0x18, s->id);
	put32(out, 0x1c, s->allocSize);
	put32(out, 0x20, s->width);
	put32(out, 0x24, s->height);
	put32(out, 0x28, s->bytesPerRow);
	put32(out, 0x2c, s->offset);
	put32(out, 0x30, s->pixelFormat);
	put32(out, 0x34, s->allocSize);
	put32(out, 0x38, s->planeCount);
	memcpy(out + 0x40, &s->bytesPerElement, 2);
	out[0x42] = s->elementWidth;
	out[0x43] = s->elementHeight;
	put32(out, 0x44, s->cacheMode);
	put32(out, 0x50, s->parentID);
	put32(out, 0x54, 1);
	for (uint32_t i = 0; i < s->planeCount; i++) {
		uint8_t *p = out + 0x68 + i * 0x80;
		const struct sweet_surface_plane *pl = &s->planes[i];
		put32(p, 0x00, pl->width);
		put32(p, 0x04, pl->height);
		put32(p, 0x08, pl->bytesPerRow);
		put32(p, 0x0c, pl->offset);
		put32(p, 0x14, pl->size);
		memcpy(p + 0x18, &pl->bytesPerElement, 2);
		p[0x1a] = pl->elementWidth;
		p[0x1b] = pl->elementHeight;
	}
}

static IOReturn
sweet_struct_out(IOExternalMethodArguments *args, const void *buf, uint32_t len)
{
	if (args->structureOutputDescriptor != NULL) {
		if (len > args->structureOutputDescriptor->getLength()) {
			return kIOReturnNoSpace;
		}
		args->structureOutputDescriptor->writeBytes(0, buf, len);
		args->structureOutputDescriptorSize = len;
		return kIOReturnSuccess;
	}
	if (args->structureOutput == NULL || len > args->structureOutputSize) {
		return kIOReturnNoSpace;
	}
	memcpy(args->structureOutput, buf, len);
	args->structureOutputSize = len;
	return kIOReturnSuccess;
}

/* Map s for this client and answer with its lock result. Takes s's user count. */
IOReturn
IOSurfaceRootUserClient::returnSurface(SweetSurface *s, IOExternalMethodArguments *args)
{
	SweetSurfaceRef *ref = NULL;
	IOReturn ret = addRef(s, &ref);
	if (ret != kIOReturnSuccess) {
		return ret;
	}
	uint8_t *out = (uint8_t *)IOMallocZeroData(SWEET_SURFACE_RESULT_SIZE);
	if (out == NULL) {
		ret = kIOReturnNoMemory;
	} else {
		sweet_surface_fill_result(s, ref, out);
		ret = sweet_struct_out(args, out, SWEET_SURFACE_RESULT_SIZE);
		IOFreeData(out, SWEET_SURFACE_RESULT_SIZE);
	}
	if (ret != kIOReturnSuccess) {
		/* The caller never learns the id: undo this create/lookup. */
		trapRelease((void *)(uintptr_t)s->id, NULL, NULL, NULL, NULL, NULL);
	}
	return ret;
}

IOReturn
IOSurfaceRootUserClient::create(IOExternalMethodArguments *args)
{
	const char *buf = (const char *)args->structureInput;
	uint32_t len = args->structureInputSize;
	char *copy = NULL;
	IOReturn ret;

	if (args->structureInputDescriptor != NULL) {
		len = (uint32_t)args->structureInputDescriptor->getLength();
		if (len == 0 || len > SWEET_SURFACE_MAX_PROPS) {
			return kIOReturnBadArgument;
		}
		copy = (char *)IOMallocData(len);
		if (copy == NULL) {
			return kIOReturnNoMemory;
		}
		args->structureInputDescriptor->readBytes(0, copy, len);
		buf = copy;
	}
	OSObject *obj = (buf != NULL && len > 0) ? OSUnserializeXML(buf, len) : NULL;
	if (copy != NULL) {
		IOFreeData(copy, len);
	}
	OSDictionary *props = OSDynamicCast(OSDictionary, obj);
	if (props == NULL) {
		OSSafeReleaseNULL(obj);
		IOLog("SWEET SURF: create: bad property dictionary (%u bytes)\n", len);
		return kIOReturnBadArgument;
	}

	SweetSurface *s = sweet_surface_alloc();
	if (s == NULL) {
		props->release();
		return kIOReturnNoMemory;
	}
	s->parentID = args->scalarInputCount > 0 ? (uint32_t)args->scalarInput[0] : 0;
	ret = sweet_surface_layout(s, props);
	if (ret == kIOReturnSuccess) {
		IOBufferMemoryDescriptor *mem = NULL;
		if ((uint64_t)OSAddAtomic64(s->allocSize, (volatile SInt64 *)&sweet_surface_wired) +
		    s->allocSize <= SWEET_SURFACE_WIRED_MAX) {
			mem = IOBufferMemoryDescriptor::inTaskWithOptions(kernel_task,
			    kIODirectionInOut | kIOMemoryKernelUserShared, s->allocSize, page_size);
		}
		if (mem == NULL) {
			OSAddAtomic64(-(SInt64)s->allocSize, (volatile SInt64 *)&sweet_surface_wired);
			ret = kIOReturnNoMemory;
		} else {
			bzero(mem->getBytesNoCopy(), s->allocSize);
			s->memory = mem;
			s->mapCache = kIOMapDefaultCache;
			if (!sweet_surface_publish(s)) {
				ret = kIOReturnNoResources;
			}
		}
	}
	if (ret != kIOReturnSuccess || sweet_surface_creates_logged < 64) {
		char name[32];
		proc_selfname(name, sizeof(name));
		sweet_surface_creates_logged++;
		IOLog("SWEET SURF: %s create id %u %ux%u fmt 0x%x bpe %u bpr %u planes %u size %u keys %u -> 0x%x\n",
		    name, s->id, s->width, s->height, s->pixelFormat, s->bytesPerElement,
		    s->bytesPerRow, s->planeCount, s->allocSize, props->getCount(), ret);
	}
	props->release();
	if (ret != kIOReturnSuccess) {
		s->release();
		return ret;
	}
	ret = returnSurface(s, args);   /* takes the creator's user count */
	s->release();
	return ret;
}

IOReturn
IOSurfaceRootUserClient::externalMethod(uint32_t selector, IOExternalMethodArguments *args,
    __unused IOExternalMethodDispatch *dispatch, __unused OSObject *target,
    __unused void *reference)
{
	uint64_t arg0 = args->scalarInputCount > 0 ? args->scalarInput[0] : 0;

	switch (selector) {
	case kSweetSurfaceLimits: {
		uint8_t limits[0x18] = { 0 };
		put32(limits, 0x0, SWEET_SURFACE_ROW_ALIGN - 1);
		put32(limits, 0x4, SWEET_SURFACE_ROW_ALIGN - 1);
		limits[0x16] = 1;
		sweet_surf_log_once("sel", selector);
		return sweet_struct_out(args, limits, sizeof(limits));
	}
	case kSweetSurfaceCreate:
		return create(args);
	case kSweetSurfaceLookup: {
		SweetSurface *s = sweet_surface_copy((uint32_t)arg0);
		IOReturn ret = kIOReturnNotFound;
		if (s != NULL) {
			ret = sweet_surface_get(s) ? returnSurface(s, args) : kIOReturnNotFound;
			s->release();
		}
		sweet_surf_log_once("sel", selector);
		return ret;
	}
	case kSweetSurfaceLookupFromPort: {
		OSObject *obj = NULL;
		IOReturn ret = IOUserClient::copyObjectForPortNameInTask(fTask, (mach_port_name_t)arg0, &obj);
		SweetSurface *s = OSDynamicCast(SweetSurface, obj);
		if (ret == kIOReturnSuccess) {
			ret = (s != NULL && sweet_surface_get(s)) ? returnSurface(s, args) : kIOReturnBadArgument;
		}
		OSSafeReleaseNULL(obj);
		sweet_surf_log_once("sel", selector);
		return ret;
	}
	case kSweetSurfaceCreatePort: {
		SweetSurface *s = sweet_surface_copy((uint32_t)arg0);
		if (s == NULL || args->scalarOutputCount < 1) {
			OSSafeReleaseNULL(s);
			return kIOReturnNotFound;
		}
		args->scalarOutput[0] = sweet_surface_port_in_task(s, fTask);
		args->scalarOutputCount = 1;
		s->release();
		sweet_surf_log_once("sel", selector);
		return kIOReturnSuccess;
	}
	default:
		/* Values, notifications, ownership, timestamps...: accept and ignore. */
		for (uint32_t i = 0; i < args->scalarOutputCount; i++) {
			args->scalarOutput[i] = 0;
		}
		if (args->structureOutput != NULL) {
			bzero(args->structureOutput, args->structureOutputSize);
		}
		sweet_surf_log_once("sel", selector);
		return kIOReturnSuccess;
	}
}

IOReturn
IOSurfaceRootUserClient::trapUseCount(__unused void *id, __unused void *category,
    void *, void *, void *, void *)
{
	return kIOReturnSuccess;
}

static IOReturn
sweet_surface_seed_out(SweetSurface *s, void *seedOut, bool bump)
{
	uint32_t seed = bump ? OSIncrementAtomic((volatile SInt32 *)sweet_surface_seed(s)) + 1 :
	    *sweet_surface_seed(s);
	if (seedOut != NULL && copyout(&seed, (user_addr_t)seedOut, sizeof(seed)) != 0) {
		return kIOReturnBadArgument;
	}
	return kIOReturnSuccess;
}

/* Retained surface this client created or looked up, or NULL. */
static SweetSurface *
sweet_surface_held(IOLock *lock, OSArray *refs, uint32_t id)
{
	SweetSurface *s = NULL;
	IOLockLock(lock);
	for (unsigned int i = 0; i < refs->getCount(); i++) {
		SweetSurfaceRef *ref = (SweetSurfaceRef *)refs->getObject(i);
		if (ref->surface->id == id) {
			s = ref->surface;
			s->retain();
			break;
		}
	}
	IOLockUnlock(lock);
	return s;
}

IOReturn
IOSurfaceRootUserClient::trapLock(void *id, __unused void *options, void *seedOut,
    void *, void *, void *)
{
	SweetSurface *s = sweet_surface_held(fLock, fRefs, (uint32_t)(uintptr_t)id);
	if (s == NULL) {
		return kIOReturnNotFound;
	}
	IOReturn ret = sweet_surface_seed_out(s, seedOut, false);
	s->release();
	return ret;
}

IOReturn
IOSurfaceRootUserClient::trapUnlock(void *id, void *options, void *seedOut,
    void *, void *, void *)
{
	SweetSurface *s = sweet_surface_held(fLock, fRefs, (uint32_t)(uintptr_t)id);
	if (s == NULL) {
		return kIOReturnNotFound;
	}
	IOReturn ret = sweet_surface_seed_out(s, seedOut,
	    !((uintptr_t)options & kIOSurfaceLockReadOnly));
	s->release();
	return ret;
}

IOReturn
IOSurfaceRootUserClient::trapRelease(void *id, void *, void *, void *, void *, void *)
{
	unsigned int index;
	IOLockLock(fLock);
	SweetSurfaceRef *ref = findRef((uint32_t)(uintptr_t)id, &index);
	if (ref != NULL && --ref->count == 0) {
		fRefs->removeObject(index);     /* unmaps and drops the user count */
	}
	IOLockUnlock(fLock);
	return ref != NULL ? kIOReturnSuccess : kIOReturnNotFound;
}

static IOExternalTrap sweet_surface_traps[] = {
	[kSweetSurfaceTrapIncrementUse]  = { NULL, (IOTrap) & IOSurfaceRootUserClient::trapUseCount },
	[kSweetSurfaceTrapDecrementUse]  = { NULL, (IOTrap) & IOSurfaceRootUserClient::trapUseCount },
	[kSweetSurfaceTrapLock]          = { NULL, (IOTrap) & IOSurfaceRootUserClient::trapLock },
	[kSweetSurfaceTrapUnlock]        = { NULL, (IOTrap) & IOSurfaceRootUserClient::trapUnlock },
	[kSweetSurfaceTrapRelease]       = { NULL, (IOTrap) & IOSurfaceRootUserClient::trapRelease },
	[kSweetSurfaceTrapReleaseFailed] = { NULL, (IOTrap) & IOSurfaceRootUserClient::trapRelease },
};

IOExternalTrap *
IOSurfaceRootUserClient::getTargetAndTrapForIndex(IOService **targetP, UInt32 index)
{
	if (index >= sizeof(sweet_surface_traps) / sizeof(sweet_surface_traps[0])) {
		sweet_surf_log_once("trap", index);
		return NULL;
	}
	sweet_surf_log_once("trap", index);
	*targetP = this;
	return &sweet_surface_traps[index];
}
"""

FRAMEBUFFER_CPP = r"""/*
 * Built-in IOMobileFramebuffer for this port: the ABL's continuous-splash
 * framebuffer (1440x3136 a8r8g8b8 in RAM, the panel scans it out by
 * itself) as the main display, driven by CoreAnimation's CPU renderer.
 *
 * Ported to the HakiOS lafa port (realme GT8 Pro, SM8850) from data-a-live's
 * 0046-sweet-iosurface-iomfb.patch, which ran the same code on the same SoC
 * (Xiaomi 17 Pro: 1220x2656, the same PA 0xFC800000).  The panel parameters
 * are the difference in this file, and they are in SWEET_FB_PANEL_* below.
 *
 * IOMobileFramebuffer.framework counts /arm-io children with device_type
 * "display-subsystem", waits for that many services matching
 * IOServiceMatching("IOMobileFramebuffer"), opens each with type 0 and reads
 * canvas-width/canvas-height from the service's provider.  The reference got
 * that node from its loader and a nub from its platform expert; this port has
 * neither, so sweet_fb_publish_display_node() publishes the node and the
 * service itself is attached to IOResources through gIOKernelConfigTables
 * (open-source xnu has no IOPlatformDevice).
 *
 * User client selectors used by CoreAnimation:
 *   3  GetLayerDefaultSurface [layer, ?] -> [IOSurface port name]
 *   4  SwapBegin -> [swap id]
 *   5  SwapEnd, struct 0x46c: +0x50 swap id, +0x54 IOSurface id per layer
 *      (4 layers), +0x64 rect a / +0xc4 rect b per layer (int x,y,w,h),
 *      +0x104 enabled layer mask
 *   6  SwapWait, 7 GetID, 8 GetDisplaySize -> [w, h], 18 IsMainDisplay,
 *   28 GetDotPitch, 72 Enable/DisableNotifications [func, refcon, type, 0]
 * Everything else is accepted and logged once.
 *
 * SwapEnd copies the enabled layers' surfaces into the panel memory (no
 * scaling, CPU copy). Vsync (notification type 5) is a 60 Hz timer that
 * sends (count, mach_absolute_time, 0) as 32-bit halves to clients that
 * enabled it, through the port they registered (IOConnectSetNotificationPort).
 *
 * The panel memory itself is published as an IOSurface and returned as the
 * default framebuffer surface.  The kernel's text console is paused on the
 * first swap (boot-arg sweetfb_console=1 keeps it).  The reference left that
 * to sweetfb_noconsole=1 because its console could draw on the panel; this
 * port's console cannot (see the comment in swap()), so paused is the
 * default here.
 */
#include "SweetSurface.h"

#include <IOKit/IOService.h>
#include <IOKit/IOUserClient.h>
#include <IOKit/IOKitKeys.h>
#include <IOKit/IOLib.h>
#include <IOKit/IOLocks.h>
#include <IOKit/IOWorkLoop.h>
#include <IOKit/IOTimerEventSource.h>
#include <IOKit/system.h>
#include <IOKit/IODeviceTreeSupport.h>  /* gIODTPlane, for /arm-io/disp0 */
#include <libkern/c++/OSArray.h>
#include <libkern/c++/OSData.h>
#include <libkern/c++/OSDictionary.h>
#include <pexpert/pexpert.h>
#include <kern/clock.h>
#include <mach/vm_param.h>      /* round_page() */
#include <sys/proc.h>
#include <sys/systm.h>          /* bzero() */

enum {
	kSweetFBGetLayerDefaultSurface  = 3,
	kSweetFBSwapBegin               = 4,
	kSweetFBSwapEnd                 = 5,
	kSweetFBSwapWait                = 6,
	kSweetFBGetID                   = 7,
	kSweetFBGetDisplaySize          = 8,
	kSweetFBIsMainDisplay           = 18,
	kSweetFBGetDotPitch             = 28,
	kSweetFBNotifications           = 72,
};

#define SWEET_FB_SWAP_REC_SIZE          0x46c
#define SWEET_FB_LAYERS                 4
#define SWEET_FB_NOTIFY_TYPES           8
#define SWEET_FB_NOTIFY_VSYNC           5
#define SWEET_FB_VSYNC_US               16667
/*
 * ppi.  This is what the system says it is -- the iPhone X identity the
 * userland was built for (5.8", 1125x2436, 458 ppi), which is also what
 * hakios' MiniIOMobileFramebuffer.cpp reports.  The panel itself is 6.79"
 * 1440x3136 (~508 ppi) and the reference reported its real panel (395 ppi for
 * a 6.67" 1080x2400); only userland metrics read this, it does not change the
 * picture.
 */
#define SWEET_FB_DOT_PITCH              458
#define SWEET_FB_FORMAT_BGRA            0x42475241

/*
 * The panel on this board, from the phone's own device tree (docs/lafa-dt.md)
 * and from ci/patch-kernel-fb.py, which already wrote into this buffer on the
 * hardware:
 *
 *	1440 x 3136, stride 5760 bytes (1440 pixels), 32 bpp => 18,063,360 bytes
 *	at PA 0xFC800000 -- the ABL's cont_splash_region (size 0x2B00000)
 *
 * The reference had the same PA with 1220x2656 / stride 4880, so these three
 * numbers are the whole difference here.
 */
#define SWEET_FB_PANEL_PHYS             0xfc800000ULL
#define SWEET_FB_PANEL_WIDTH            1440u
#define SWEET_FB_PANEL_HEIGHT           3136u
#define SWEET_FB_PANEL_ROWBYTES         5760u   /* 1440 * 4: nothing padded */

/*
 * The panel memory is outside this port's physmap (boot_args physBase
 * 0x8C0000000, ~3.4 GB), so the kernel mapping has to be made explicitly.
 * IOMemoryDescriptor::map() is tried first (it enters pages into kernel_map
 * through pmap, not through the physmap); if that fails, ml_io_map_unmappable()
 * is the call ci/patch-kernel-fb.py already proved on this buffer.  ml_io_map()
 * cannot be used for 18 MB: it only allocates from the 8 MB io submap.  Both the
 * prototype and the cache flag are behind MACH_KERNEL_PRIVATE in the kernel's
 * headers, so they are spelled out here.
 */
extern "C" vm_offset_t ml_io_map_unmappable(vm_offset_t phys_addr, vm_size_t size,
    unsigned int flags);
#ifndef VM_WIMG_WCOMB
#define VM_WIMG_WCOMB                   0x01    /* osfmk/arm/pmap/pmap.h: write-combining */
#endif

class IOMobileFramebuffer : public IOService {
	OSDeclareDefaultStructors(IOMobileFramebuffer);
};

OSDefineMetaClassAndStructors(IOMobileFramebuffer, IOService);

class IOMobileFramebufferUserClient;

class AppleCLCD : public IOMobileFramebuffer {
	OSDeclareDefaultStructors(AppleCLCD);

	IOWorkLoop         *fWorkLoop;
	IOTimerEventSource *fVsync;
	uint64_t            fVsyncCount;

	static void vsyncFired(OSObject *owner, IOTimerEventSource *sender);

public:
	uint64_t      fPhys;
	uint32_t      fWidth, fHeight, fRowBytes;
	uintptr_t     fPixels;          /* kernel mapping of the panel memory */
	IOMemoryMap  *fMap;
	SweetSurface *fSurface;         /* the panel memory as an IOSurface */
	IOLock       *fLock;            /* swaps and client list */
	OSArray      *fClients;
	uint32_t      fSwapID;
	bool          fConsolePaused;

	bool start(IOService *provider) APPLE_KEXT_OVERRIDE;
	void addClient(IOMobileFramebufferUserClient *client);
	void removeClient(IOMobileFramebufferUserClient *client);
	void swap(const uint8_t *rec);
};

OSDefineMetaClassAndStructors(AppleCLCD, IOMobileFramebuffer);

class IOMobileFramebufferUserClient : public IOUserClient {
	OSDeclareDefaultStructors(IOMobileFramebufferUserClient);

public:
	AppleCLCD          *fFB;
	task_t              fTask;
	mach_port_t         fPort;
	io_user_reference_t fFunc[SWEET_FB_NOTIFY_TYPES];
	io_user_reference_t fRefcon[SWEET_FB_NOTIFY_TYPES];

	bool initWithTask(task_t owningTask, void *securityID, UInt32 type) APPLE_KEXT_OVERRIDE;
	bool start(IOService *provider) APPLE_KEXT_OVERRIDE;
	IOReturn clientClose(void) APPLE_KEXT_OVERRIDE;
	IOReturn registerNotificationPort(mach_port_t port, UInt32 type,
	    io_user_reference_t refCon) APPLE_KEXT_OVERRIDE;
	IOReturn externalMethod(uint32_t selector, IOExternalMethodArguments *args,
	    IOExternalMethodDispatch *dispatch, OSObject *target,
	    void *reference) APPLE_KEXT_OVERRIDE;
	void notify(uint32_t type, const uint64_t *values, uint32_t count);
};

OSDefineMetaClassAndStructors(IOMobileFramebufferUserClient, IOUserClient);

/*
 * IOMobileFramebuffer.framework decides how many displays to wait for by
 * counting the children of /arm-io in the device tree whose device_type is
 * "display-subsystem", and then waits for that many services matching
 * IOServiceMatching("IOMobileFramebuffer").  The reference got that node from
 * its loader (it matched 'sweet,display' there); this port's loader builds
 * /arm-io with only isp, gic and uart0 (xnuloader/loader.c), so the node is
 * published here.  (hakios' MiniIOMobileFramebuffer.cpp does the same in its
 * publishDisplayNode().)
 */
static void
sweet_fb_publish_display_node(void)
{
	IORegistryEntry *armio;
	IORegistryEntry *node;
	OSDictionary    *props;
	OSData          *data;
	bool             ok = false;

	node = IORegistryEntry::fromPath("/arm-io/disp0", gIODTPlane);
	if (node != NULL) {
		node->release();
		return;                 /* the loader, or an earlier start, published it */
	}
	armio = IORegistryEntry::fromPath("/arm-io", gIODTPlane);
	if (armio == NULL) {
		IOLog("SWEET FB: no /arm-io in the device tree: the framework will not "
		    "wait for this display\n");
		return;
	}
	node = OSTypeAlloc(IORegistryEntry);
	props = OSDictionary::withCapacity(4);
	if (node != NULL && props != NULL) {
		data = OSData::withBytes("display-subsystem", sizeof("display-subsystem"));
		props->setObject("device_type", data);
		OSSafeReleaseNULL(data);
		data = OSData::withBytes("sweet,display", sizeof("sweet,display"));
		props->setObject("compatible", data);
		OSSafeReleaseNULL(data);
		data = OSData::withBytes("disp0", sizeof("disp0"));
		props->setObject("name", data);
		OSSafeReleaseNULL(data);
		if (node->init(props)) {
			node->setName("disp0");
			ok = node->attachToParent(armio, gIODTPlane);
		}
	}
	if (ok) {
		IOLog("SWEET FB: published /arm-io/disp0 (device_type display-subsystem)\n");
	} else {
		IOLog("SWEET FB: could not publish /arm-io/disp0\n");
	}
	OSSafeReleaseNULL(props);
	OSSafeReleaseNULL(node);
	armio->release();
}

bool
AppleCLCD::start(IOService *provider)
{
	PE_Video video;
	uint32_t autoVideo = 0;
	vm_size_t panelBytes;
	vm_offset_t kva;

	if (!IOService::start(provider)) {
		return false;
	}
	/*
	 * Take the geometry from this board's constants.  PE_state.video is read and
	 * logged -- this port's loader does fill it from the GOP mode -- but it is not
	 * trusted: the panel memory is outside the physmap and the video console that
	 * would normally own it is not used here, so a wrong value in there is a blank
	 * screen with no clue.  boot-arg sweetfb_auto=1 takes PE's numbers when they
	 * look sane (the reference's behaviour).
	 */
	bzero(&video, sizeof(video));
	PE_current_console(&video);
	fPhys = SWEET_FB_PANEL_PHYS;
	fWidth = SWEET_FB_PANEL_WIDTH;
	fHeight = SWEET_FB_PANEL_HEIGHT;
	fRowBytes = SWEET_FB_PANEL_ROWBYTES;
	if (PE_parse_boot_argn("sweetfb_auto", &autoVideo, sizeof(autoVideo)) && autoVideo != 0 &&
	    video.v_baseAddr != 0 && video.v_width != 0 && video.v_height != 0 &&
	    video.v_rowBytes >= video.v_width * 4) {
		fPhys = video.v_baseAddr;
		fWidth = (uint32_t)video.v_width;
		fHeight = (uint32_t)video.v_height;
		fRowBytes = (uint32_t)video.v_rowBytes;
	}
	IOLog("SWEET FB: panel %ux%u stride %u at 0x%llx (PE video: %ux%u stride %u at 0x%llx)\n",
	    fWidth, fHeight, fRowBytes, fPhys, (uint32_t)video.v_width, (uint32_t)video.v_height,
	    (uint32_t)video.v_rowBytes, (uint64_t)video.v_baseAddr);
	if (fPhys == 0 || fWidth == 0 || fHeight == 0 || fRowBytes < fWidth * 4) {
		IOLog("SWEET FB: no plausible panel geometry\n");
		return false;
	}
	fLock = IOLockAlloc();
	fClients = OSArray::withCapacity(4);
	fWorkLoop = IOWorkLoop::workLoop();
	if (fLock == NULL || fClients == NULL || fWorkLoop == NULL) {
		IOLog("SWEET FB: setup failed\n");
		OSSafeReleaseNULL(fWorkLoop);
		return false;
	}
	panelBytes = round_page((vm_size_t)fRowBytes * (vm_size_t)fHeight);
	fSurface = sweet_surface_create_physical(fPhys, fWidth, fHeight, fRowBytes,
	    SWEET_FB_FORMAT_BGRA);
	fPixels = fSurface != NULL ? fSurface->kernelAddress() : 0;
	if (fPixels == 0) {
		kva = ml_io_map_unmappable((vm_offset_t)fPhys, panelBytes, VM_WIMG_WCOMB);
		IOLog("SWEET FB: no kernel mapping from the surface descriptor%s; "
		    "ml_io_map_unmappable(0x%llx, %lu) -> 0x%lx\n",
		    fSurface != NULL ? "" : " (no surface either)", fPhys,
		    (unsigned long)panelBytes, (unsigned long)kva);
		fPixels = (uintptr_t)kva;
	}
	if (fSurface == NULL || fPixels == 0) {
		IOLog("SWEET FB: cannot map the panel\n");
		OSSafeReleaseNULL(fSurface);    /* stays published: also held by the table */
		OSSafeReleaseNULL(fWorkLoop);
		return false;
	}
	fVsync = IOTimerEventSource::timerEventSource(this, &AppleCLCD::vsyncFired);
	if (fVsync == NULL || fWorkLoop->addEventSource(fVsync) != kIOReturnSuccess) {
		IOLog("SWEET FB: no vsync timer\n");
		return false;
	}
	fVsync->setTimeoutUS(SWEET_FB_VSYNC_US);

	setProperty(kIOUserClientClassKey, "IOMobileFramebufferUserClient");
	setProperty("external", false);
	setProperty("DisplayPipePlaneBaseAlignment", 64ULL, 32);
	/*
	 * IOMobileFramebuffer.framework reads canvas-width/canvas-height off the
	 * service's provider.  The reference's provider was a nub of its own platform
	 * expert (IOPlatformDevice); open-source xnu has no IOPlatformDevice, so this
	 * service hangs off IOResources and the canvas size goes there.
	 */
	if (provider != NULL) {
		provider->setProperty("canvas-width", (unsigned long long)fWidth, 32);
		provider->setProperty("canvas-height", (unsigned long long)fHeight, 32);
	}
	sweet_fb_publish_display_node();
	registerService();
	IOLog("SWEET FB: AppleCLCD %ux%u stride %u at 0x%llx, default surface %u\n",
	    fWidth, fHeight, fRowBytes, fPhys, fSurface->id);
	return true;
}

void
AppleCLCD::vsyncFired(OSObject *owner, IOTimerEventSource *sender)
{
	AppleCLCD *fb = (AppleCLCD *)owner;
	uint64_t now = mach_absolute_time();
	uint64_t count = ++fb->fVsyncCount;
	uint64_t values[6] = {
		(uint32_t)count, (uint32_t)(count >> 32),
		(uint32_t)now, (uint32_t)(now >> 32),
		0, 0,
	};

	IOLockLock(fb->fLock);
	for (unsigned int i = 0; i < fb->fClients->getCount(); i++) {
		IOMobileFramebufferUserClient *client =
		    (IOMobileFramebufferUserClient *)fb->fClients->getObject(i);
		client->notify(SWEET_FB_NOTIFY_VSYNC, values, 6);
	}
	IOLockUnlock(fb->fLock);
	sender->setTimeoutUS(SWEET_FB_VSYNC_US);
}

void
AppleCLCD::addClient(IOMobileFramebufferUserClient *client)
{
	IOLockLock(fLock);
	fClients->setObject(client);
	IOLockUnlock(fLock);
}

void
AppleCLCD::removeClient(IOMobileFramebufferUserClient *client)
{
	IOLockLock(fLock);
	unsigned int i = fClients->getNextIndexOfObject(client, 0);
	if (i != (unsigned int)-1) {
		fClients->removeObject(i);
	}
	IOLockUnlock(fLock);
}

static uint32_t
get32(const uint8_t *p, uint32_t off)
{
	uint32_t v;
	memcpy(&v, p + off, 4);
	return v;
}

static uint32_t sweet_fb_swaps_logged;

/* Copy each enabled layer's surface into the panel memory (no scaling). */
void
AppleCLCD::swap(const uint8_t *rec)
{
	uint32_t mask = get32(rec, 0x104);

	if (!fConsolePaused) {
		uint32_t keep = 0;
		fConsolePaused = true;
		/*
		 * lafa: the kernel's own text console must not draw on this panel.  The
		 * console maps the framebuffer out of PE_state.video, and on this port that
		 * holds the physical address 0xFC800000 (outside the physmap), so its writes
		 * would go to a bogus kernel address; the loader already drains the kernel
		 * log to the screen.  Paused by default here, therefore; the reference's
		 * sweetfb_noconsole=1 means the same thing, and sweetfb_console=1 keeps the
		 * console for a debugging session.
		 */
		if (PE_parse_boot_argn("sweetfb_console", &keep, sizeof(keep)) && keep != 0) {
			IOLog("SWEET FB: sweetfb_console=1: the text console keeps the panel\n");
		} else {
			PE_Video video;
			PE_current_console(&video);
			PE_initialize_console(&video, kPEDisableScreen);
			IOLog("SWEET FB: text console paused (sweetfb_console=1 keeps it)\n");
		}
	}

	for (uint32_t layer = 0; layer < SWEET_FB_LAYERS; layer++) {
		uint32_t id = get32(rec, 0x54 + 4 * layer);
		const uint8_t *a = rec + 0x64 + 16 * layer;
		const uint8_t *b = rec + 0xc4 + 16 * layer;
		int32_t sx = (int32_t)get32(a, 0), sy = (int32_t)get32(a, 4);
		int32_t dx = (int32_t)get32(b, 0), dy = (int32_t)get32(b, 4);
		int32_t w = (int32_t)get32(b, 8), h = (int32_t)get32(b, 12);

		if (!(mask & (1u << layer)) || id == 0) {
			continue;
		}
		SweetSurface *s = sweet_surface_copy(id);
		if (sweet_fb_swaps_logged < 16) {
			sweet_fb_swaps_logged++;
			IOLog("SWEET FB: swap %u layer %u surface %u %ux%u fmt 0x%x bpr %u a %d,%d %ux%u b %d,%d %dx%d\n",
			    get32(rec, 0x50), layer, id, s ? s->width : 0, s ? s->height : 0,
			    s ? s->pixelFormat : 0, s ? s->bytesPerRow : 0, sx, sy, get32(a, 8),
			    get32(a, 12), dx, dy, w, h);
		}
		if (s == NULL || s == fSurface) {
			OSSafeReleaseNULL(s);
			continue;
		}
		uintptr_t src = s->kernelAddress();
		uint32_t bpe = s->bytesPerElement ? s->bytesPerElement : 4;
		if (w <= 0 || h <= 0) {
			w = (int32_t)s->width;
			h = (int32_t)s->height;
		}
		if (sx < 0 || sy < 0) {
			sx = sy = 0;
		}
		if (dx < 0 || dy < 0) {
			dx = dy = 0;
		}
		if (w > (int32_t)s->width - sx) {
			w = (int32_t)s->width - sx;
		}
		if (h > (int32_t)s->height - sy) {
			h = (int32_t)s->height - sy;
		}
		if (w > (int32_t)fWidth - dx) {
			w = (int32_t)fWidth - dx;
		}
		if (h > (int32_t)fHeight - dy) {
			h = (int32_t)fHeight - dy;
		}
		uint64_t last = (uint64_t)s->offset + (uint64_t)(sy + h - 1) * s->bytesPerRow +
		    (uint64_t)(sx + w) * 4;
		if (w <= 0 || h <= 0 || s->elementWidth != 1 || s->elementHeight != 1 ||
		    s->memory == NULL || last > s->memory->getLength()) {
			src = 0;
		}
		if (src != 0 && bpe == 4) {
			for (int32_t y = 0; y < h; y++) {
				memcpy((void *)(fPixels + (uint64_t)(dy + y) * fRowBytes + (uint64_t)dx * 4),
				    (const void *)(src + s->offset + (uint64_t)(sy + y) * s->bytesPerRow + (uint64_t)sx * 4),
				    (size_t)w * 4);
			}
		}
		s->release();
	}
}

bool
IOMobileFramebufferUserClient::initWithTask(task_t owningTask, void *securityID, UInt32 type)
{
	if (!IOUserClient::initWithTask(owningTask, securityID, type)) {
		return false;
	}
	fTask = owningTask;
	return true;
}

bool
IOMobileFramebufferUserClient::start(IOService *provider)
{
	fFB = OSDynamicCast(AppleCLCD, provider);
	if (fFB == NULL || !IOUserClient::start(provider)) {
		return false;
	}
	fFB->addClient(this);
	return true;
}

IOReturn
IOMobileFramebufferUserClient::clientClose(void)
{
	if (fFB != NULL) {
		fFB->removeClient(this);
		IOLockLock(fFB->fLock);
		mach_port_t old = fPort;
		fPort = MACH_PORT_NULL;
		IOLockUnlock(fFB->fLock);
		if (old != MACH_PORT_NULL) {
			releaseNotificationPort(old);
		}
	}
	terminate();
	return kIOReturnSuccess;
}

IOReturn
IOMobileFramebufferUserClient::registerNotificationPort(mach_port_t port, __unused UInt32 type,
    __unused io_user_reference_t refCon)
{
	mach_port_t old;
	IOLockLock(fFB->fLock);
	old = fPort;
	fPort = port;
	IOLockUnlock(fFB->fLock);
	if (old != MACH_PORT_NULL) {
		releaseNotificationPort(old);
	}
	return kIOReturnSuccess;
}

/* Called with the framebuffer lock held. */
void
IOMobileFramebufferUserClient::notify(uint32_t type, const uint64_t *values, uint32_t count)
{
	OSAsyncReference64 ref;

	if (type >= SWEET_FB_NOTIFY_TYPES || fFunc[type] == 0 || fPort == MACH_PORT_NULL) {
		return;
	}
	bzero(ref, sizeof(ref));
	setAsyncReference64(ref, fPort, (mach_vm_address_t)fFunc[type], fRefcon[type], fTask);
	sendAsyncResult64WithOptions(ref, kIOReturnSuccess, (io_user_reference_t *)values, count,
	    kIOUserNotifyOptionCanDrop);
}

static uint64_t sweet_fb_logged;        /* selectors already logged (< 64) */

IOReturn
IOMobileFramebufferUserClient::externalMethod(uint32_t selector, IOExternalMethodArguments *args,
    __unused IOExternalMethodDispatch *dispatch, __unused OSObject *target,
    __unused void *reference)
{
	IOReturn ret = kIOReturnSuccess;
	uint64_t out[2] = { 0, 0 };
	uint32_t outCount = 0;

	switch (selector) {
	case kSweetFBGetLayerDefaultSurface:
		out[0] = sweet_surface_port_in_task(fFB->fSurface, fTask);
		outCount = 1;
		break;
	case kSweetFBSwapBegin:
		IOLockLock(fFB->fLock);
		out[0] = ++fFB->fSwapID;
		IOLockUnlock(fFB->fLock);
		outCount = 1;
		break;
	case kSweetFBSwapEnd: {
		uint8_t rec[SWEET_FB_SWAP_REC_SIZE];
		if (args->structureInputDescriptor != NULL ||
		    args->structureInputSize < SWEET_FB_SWAP_REC_SIZE) {
			ret = kIOReturnBadArgument;
			break;
		}
		memcpy(rec, args->structureInput, sizeof(rec));
		fFB->swap(rec);
		break;
	}
	case kSweetFBGetID:
		out[0] = 0;
		outCount = 1;
		break;
	case kSweetFBGetDisplaySize:
		out[0] = fFB->fWidth;
		out[1] = fFB->fHeight;
		outCount = 2;
		break;
	case kSweetFBIsMainDisplay:
		out[0] = 1;
		outCount = 1;
		break;
	case kSweetFBGetDotPitch:
		out[0] = SWEET_FB_DOT_PITCH;
		outCount = 1;
		break;
	case kSweetFBNotifications:
		if (args->scalarInputCount >= 3 && args->scalarInput[2] < SWEET_FB_NOTIFY_TYPES) {
			uint32_t type = (uint32_t)args->scalarInput[2];
			IOLockLock(fFB->fLock);
			fFunc[type] = args->scalarInput[0];
			fRefcon[type] = args->scalarInput[1];
			IOLockUnlock(fFB->fLock);
			char name[32];
			proc_selfname(name, sizeof(name));
			IOLog("SWEET FB: %s notifications type %u %s\n", name, type,
			    args->scalarInput[0] ? "on" : "off");
		}
		break;
	case kSweetFBSwapWait:
	default:
		break;
	}

	for (uint32_t i = 0; i < args->scalarOutputCount; i++) {
		args->scalarOutput[i] = i < outCount ? out[i] : 0;
	}
	if (args->structureOutputDescriptor == NULL && args->structureOutput != NULL) {
		bzero(args->structureOutput, args->structureOutputSize);
	}
	if (selector >= 64 || !(sweet_fb_logged & (1ULL << selector))) {
		if (selector < 64) {
			sweet_fb_logged |= 1ULL << selector;
		}
		char name[32];
		proc_selfname(name, sizeof(name));
		IOLog("SWEET FB: %s sel %u in %u/%u out %u/%u -> 0x%x\n", name, selector,
		    args->scalarInputCount, args->structureInputSize,
		    args->scalarOutputCount, args->structureOutputSize, ret);
	}
	return ret;
}
"""

SOURCES = {
    "iokit/Kernel/SweetSurface.h": SURFACE_H,
    "iokit/Kernel/SweetSurface.cpp": SURFACE_CPP,
    "iokit/Kernel/SweetFramebuffer.cpp": FRAMEBUFFER_CPP,
}

FILES_ARM64_BLOCK = (
    "# " + MARK + ": built-in IOSurface + IOMobileFramebuffer (the display services)\n"
    "iokit/Kernel/SweetSurface.cpp\t\t\t\toptional iokitcpp\n"
    "iokit/Kernel/SweetFramebuffer.cpp\t\t\toptional iokitcpp\n"
)

# 加在 gIOKernelConfigTables 末尾（每项以逗号开头，接在最后一个 "   }" 后面对任何顺序都成立）。
CONFIG_TABLE_BLOCK = (
    "#if defined(__arm64__)\n"
    "    /* " + MARK + ": the display services (iokit/Kernel/SweetFramebuffer.cpp).  The\n"
    "     * hakios Mini drivers define the same IOKit classes, so only one set can be in\n"
    "     * the build. */\n"
    '    "   ,{"\n'
    "    \"     'IOClass'         = IOCoreSurfaceRoot;\"\n"
    "    \"     'IOProviderClass' = IOResources;\"\n"
    "    \"     'IOMatchCategory' = IOCoreSurfaceRoot;\"\n"
    '    "   }"\n'
    '    "   ,{"\n'
    "    \"     'IOClass'         = AppleCLCD;\"\n"
    "    \"     'IOProviderClass' = IOResources;\"\n"
    "    \"     'IOMatchCategory' = AppleCLCD;\"\n"
    '    "   }"\n'
    "#endif\n"
)

MINI_SOURCES = (
    "iokit/Kernel/MiniIOSurface.cpp",
    "iokit/Kernel/MiniIOMobileFramebuffer.cpp",
)

MINI_START_CALLS = (
    "MiniIOSurfaceStart(this);",
    "MiniIOMobileFramebufferStart(this);",
)


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


def install_sources(root: Path, check: bool) -> list:
    """写那三个 C/C++ 文件。返回报告行。"""
    report = []
    for rel, text in SOURCES.items():
        path = root / rel
        if path.exists() and read(path) == text:
            report.append("  = %s（已是最新）" % rel)
            continue
        action = "改写" if path.exists() else "新建"
        report.append("  + %s（%s，%d 行）" % (rel, action, text.count("\n")))
        if not check:
            write(path, text)
    return report


def register_files_arm64(root: Path, check: bool) -> list:
    """在 iokit/conf/files.arm64 里登记两个 .cpp。"""
    path = root / "iokit" / "conf" / "files.arm64"
    text = read(path)
    have_surface = "iokit/Kernel/SweetSurface.cpp" in text
    have_fb = "iokit/Kernel/SweetFramebuffer.cpp" in text
    if have_surface and have_fb:
        return ["  = iokit/conf/files.arm64（已登记）"]
    if have_surface or have_fb:
        raise Failure(
            "iokit/conf/files.arm64 里只有一半的登记项，先手工清干净（或换一棵干净树）"
        )
    if not text.endswith("\n"):
        text += "\n"
    text = text + "\n" + FILES_ARM64_BLOCK
    if not check:
        write(path, text)
    return ["  + iokit/conf/files.arm64（+2 行：SweetSurface.cpp / SweetFramebuffer.cpp）"]


def register_config_table(root: Path, check: bool) -> list:
    """在 iokit/KernelConfigTables.cpp 的 gIOKernelConfigTables 末尾加两项。"""
    path = root / "iokit" / "KernelConfigTables.cpp"
    text = read(path)
    if MARK in text:
        return ["  = iokit/KernelConfigTables.cpp（已登记）"]
    if "gIOKernelConfigTables" not in text:
        raise Failure("iokit/KernelConfigTables.cpp 里没有 gIOKernelConfigTables")
    # 收尾是单独一行的 \")"; ，把新项插在它前面（逗号开头的写法对任何前一项都成立）。
    matches = list(re.finditer(r'^[ \t]*"\)";[ \t]*$', text, re.MULTILINE))
    if not matches:
        raise Failure('iokit/KernelConfigTables.cpp 里找不到收尾的 ")";')
    at = matches[-1].start()
    if not check:
        write(path, text[:at] + CONFIG_TABLE_BLOCK + text[at:])
    return [
        "  + iokit/KernelConfigTables.cpp（gIOKernelConfigTables 加 IOCoreSurfaceRoot、"
        "AppleCLCD 两项，provider 都是 IOResources）"
    ]


def disable_mini(root: Path, check: bool) -> list:
    """把 hakios 树里的两个 Mini 显示驱动从构建里摘掉。"""
    report = []
    files_path = root / "iokit" / "conf" / "files"
    if files_path.is_file():
        text = read(files_path)
        changed = False
        for rel in MINI_SOURCES:
            active = re.search(r"^[ \t]*" + re.escape(rel) + r"(?=[ \t])", text, re.MULTILINE)
            if not active:
                continue
            line = active.group(0)
            start = text.rfind("\n", 0, active.start()) + 1
            end = text.find("\n", active.start())
            original = text[start:end]
            replacement = (
                "# " + MARK + ": replaced by SweetSurface.cpp / SweetFramebuffer.cpp "
                "(same IOKit classes, one implementation only)\n"
                "#" + original
            )
            text = text[:start] + replacement + text[end:]
            changed = True
            report.append("  ~ iokit/conf/files：注释掉 %s" % rel)
        if changed and not check:
            write(files_path, text)
        if not changed:
            report.append("  = iokit/conf/files（Mini 显示驱动已不在构建里）")
    else:
        report.append("  - iokit/conf/files 不存在（不是 hakios 树？跳过 Mini 摘除）")

    pe_path = root / "iokit" / "Kernel" / "GenericARMPE.cpp"
    if pe_path.is_file():
        text = read(pe_path)
        changed = False
        for call in MINI_START_CALLS:
            if ("/* " + call + " */") in text:
                continue
            pattern = re.compile(r"^([ \t]*)" + re.escape(call) + r"[ \t]*$", re.MULTILINE)
            match = pattern.search(text)
            if not match:
                continue
            indent = match.group(1)
            comment = (
                "%s/* %s: the display services are SweetSurface / SweetFramebuffer now */\n"
                "%s/* %s */" % (indent, MARK, indent, call)
            )
            text = text[:match.start()] + comment + text[match.end():]
            changed = True
            report.append("  ~ iokit/Kernel/GenericARMPE.cpp：注释掉 %s" % call)
        if changed and not check:
            write(pe_path, text)
        if not changed:
            report.append("  = iokit/Kernel/GenericARMPE.cpp（调用已注释）")
    else:
        report.append("  - iokit/Kernel/GenericARMPE.cpp 不存在（不是 hakios 树？跳过）")
    return report


def mini_sources_present(root: Path) -> bool:
    """树里还有 Mini 显示驱动在构建里（或者已经被本脚本摘掉了）吗？"""
    path = root / "iokit" / "conf" / "files"
    if not path.is_file():
        return False
    text = read(path)
    if MARK in text:
        return True             # 已经摘过了：再跑一次要报"已是最新"，不是"树里没有"
    return any(
        re.search(r"^[ \t]*" + re.escape(rel) + r"(?=[ \t])", text, re.MULTILINE)
        for rel in MINI_SOURCES
    )


def gpu_driver_enabled(root: Path) -> bool:
    """hakios 的 GPU 驱动开着吗？（它引用 MiniIOSurface* / MiniIOMobileFramebufferShow）"""
    conf = root / "config" / "MASTER.hakios"
    if conf.is_file():
        for line in read(conf).splitlines():
            if "hakios_adreno" in line and not line.lstrip().startswith("#"):
                return True
        return False
    # 没有生成的配置就退一步看构建表：optional hakios_adreno 只在那里出现
    files_path = root / "iokit" / "conf" / "files"
    if files_path.is_file():
        for line in read(files_path).splitlines():
            if "hakios.gpu.cpp" in line and "hakios_adreno" in line:
                return None  # 有驱动文件，但看不出开关
    return False


USAGE = ("usage: patch-kernel-display.py <xnu source tree> [--check] "
         "[--mini auto|disable|none] [--force]")


def parse_args(argv):
    """-> (tree Path, check, force, mini)。用法错误抛 UsageError。"""
    tree = None
    check = force = False
    mini = "auto"
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg in ("--check", "-n"):
            check = True
        elif arg == "--force":
            force = True
        elif arg == "--mini":
            i += 1
            if i >= len(argv):
                raise UsageError("--mini 需要 auto / disable / none")
            mini = argv[i]
            if mini not in ("auto", "disable", "none"):
                raise UsageError("--mini 只能是 auto / disable / none（收到 %r）" % mini)
        elif arg in ("-h", "--help"):
            raise UsageError(USAGE)
        elif arg.startswith("-"):
            raise UsageError("不认识的选项 %r\n%s" % (arg, USAGE))
        elif tree is None:
            tree = arg
        else:
            raise UsageError("只能给一个 xnu 源码树（多给了 %r）\n%s" % (arg, USAGE))
        i += 1
    if tree is None:
        raise UsageError(USAGE)
    return Path(tree), check, force, mini


def main(argv: list) -> int:
    try:
        root, check, force, mini = parse_args(argv)
    except UsageError as exc:
        print("patch-display: %s" % exc, file=sys.stderr)
        return 2
    for required in ("iokit/conf/files.arm64", "iokit/KernelConfigTables.cpp"):
        if not (root / required).is_file():
            print("patch-display: %s 不存在（%s 是 xnu 源码树吗？）" % (required, root),
                  file=sys.stderr)
            return 1

    gpu = gpu_driver_enabled(root)
    take_over = mini == "disable" or (mini == "auto" and mini_sources_present(root))

    if take_over and gpu and mini != "none" and not force:
        print(
            "patch-display: config/MASTER.hakios 里 CONFIG_HAKIOS_ADRENO 开着。\n"
            "  hakios.gpu.cpp / hakios.adreno.c 引用 MiniIOSurface*() 和\n"
            "  MiniIOMobileFramebufferShow()，摘掉 Mini 显示驱动后链接会失败。\n"
            "  先把 GPU 驱动关掉（devices/lafa/configs/lafa.config 里现在就是关的），\n"
            "  做桥接，或者加 --force 接受这次链接失败。",
            file=sys.stderr,
        )
        return 1
    if take_over and gpu is None:
        print("patch-display: 警告：看不出 GPU 驱动是否开着；如果开着，链接会缺 "
              "MiniIOSurface* / MiniIOMobileFramebufferShow，请自行确认。", file=sys.stderr)

    print("patch-display: %s%s" % (root, "（--check，不写文件）" if check else ""))
    lines = []
    try:
        lines += install_sources(root, check)
        lines += register_files_arm64(root, check)
        lines += register_config_table(root, check)
        if take_over:
            lines += disable_mini(root, check)
        elif mini == "none":
            lines.append("  - 按 --mini none：不碰 Mini 驱动（vanilla 树）")
        else:
            lines.append("  - 树里没有 Mini 显示驱动：只装 Sweet 这一套")
    except Failure as exc:
        print("patch-display: %s" % exc, file=sys.stderr)
        return 1
    for line in lines:
        print(line)

    print(
        "patch-display: 面板 1440x3136 / stride 5760 / PA 0xFC800000，16 KiB 页；"
        "开机看这几行日志：\n"
        "  SWEET FB: panel 1440x3136 stride 5760 at 0xfc800000\n"
        "  SWEET FB: published /arm-io/disp0 (device_type display-subsystem)\n"
        "  SWEET SURF: IOCoreSurfaceRoot registered\n"
        "  SWEET FB: AppleCLCD 1440x3136 stride 5760 at 0xfc800000, default surface N"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
