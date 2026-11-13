#!/usr/bin/env python3
"""patch-kernel-keystore.py -- 把参考项目 0038 的假 AppleKeyStore 移植到我们的内核树。

参考：refs/data-a-live/xnu/patches/0038-sweet-applekeystore.patch
  · 新增 iokit/Kernel/arm/SweetKeyStore.cpp（假 AppleKeyStore kext：libaks 的
    kAKSGetSystem(14) 回 handle 0；keybagd --init 拿到 handle 就不去读
    /private/var/keybags/systembag.kb，用户会话/钥匙串才成立）
  · 在 iokit/conf/files.arm64 登记这个 .cpp
  · 在 iokit/KernelConfigTables.cpp 的 gIOKernelConfigTables 里注册
    IOClass=AppleKeyStore / IOProviderClass=IOResources / IOMatchCategory=AppleKeyStore

我们的树（xnu-8796.121.2 + xnu/hakios.patch + xnu/files/ overlay）不能直接 git apply：
  · iokit/conf/files.arm64 在 8796 里只有三条（IONVRAM / IOPMrootDomain / AppleARMSMP），
    参考补丁的上下文（SweetCPU/SweetEntitlements 行）不存在；
  · 参考补丁的 SweetKeyStore.cpp 包在 #if defined(SWEET) 里，我们的 build 没有 SWEET
    （我们的平台专家叫 GenericARMPE，machine config 是 bcm2837/HAKIOS）；
  · 我们已经有一份更长、更完整的等价物：iokit/Kernel/MiniAppleKeyStore.cpp（335 行，
    AppleKeyStore + AppleKeyStoreUserClient，kAKSGetSystem=0x0e 回 success + handle 0，
    kAKSGetLockState/GetDeviceState/WrapKey/UnwrapKey/GetBagUUID/... 都实现了），
    并且 iokit/conf/files 里已登记、由 GenericARMPE::start() 调用
    MiniAppleKeyStoreStart() 自注册。
  · 和参考项目唯一的结构性差别：**我们是自注册，不是 gIOKernelConfigTables 匹配**。
    参考项目是靠 IOKit 把它当 built-in kext 挂到 IOResources 上（这也是 0026 HFS、
    0040 IOWatchdog、0046 IOSurface 等补丁一致的做法）。真机上 keybagd 仍然退回去读
    systembag.kb，说明 libaks 没拿到这个 service，这个差别就是首要嫌疑。

所以本脚本做三件事，全部幂等、带 HAKIOS-LAFA-KEYSTORE 标记、锚点校验失败就退 1：

  1. iokit/conf/files：保证 iokit/Kernel/MiniAppleKeyStore.cpp 进构建。
     （hakios.patch 通常已经加过这一行，脚本只做校验；上游 overlay 变化时补上。）
  2. iokit/Kernel/MiniAppleKeyStore.cpp：树里没有就写入内嵌的 335 行版本（上游
     xnu/files overlay 缺文件时的兜底）；有就补三处：
       a. AppleKeyStore::start() 注册成功打一行日志（判断 service 有没有被发布）；
       b. externalMethod 每次调用打一行（selector + 参数形状 —— 看 keybagd 是不是
          真的来问 selector 14，以及下一个缺的 selector 是哪个）；
       c. 0038 里还有、我们没实现的 bag 类 selector：2/3/4/5/6/13/55（无 keybag 文件，
          直接成功；这就是 libaks 期待的 userspace-reboot 路径）。
  3. iokit/KernelConfigTables.cpp：在 gIOKernelConfigTables 末尾加 AppleKeyStore 的
     IOResources 匹配项（参考补丁的第 2/3 处改动）。

用法：
    python3 ci/patch-kernel-keystore.py <xnu 源码树> [--check]

CI 里（Lead 集成，加在 build kernel 之前、其它 patch-kernel-*.py 之间即可）：
    python3 ci/patch-kernel-keystore.py hakios/xnu-src/xnu-8796.121.2

预期日志（下一轮真机）：
    AppleKeyStore: registered (MiniAppleKeyStore stand-in: no passcode, unlocked since boot)
    AppleKeyStore: keybagd selector 0xe (...)
    然后 keybagd 不再出现 "Could not open /private/var/keybags/systembag.kb"。
"""
import re
import sys
from pathlib import Path

MARK = "HAKIOS-LAFA-KEYSTORE"

KEYSTORE_REL = "iokit/Kernel/MiniAppleKeyStore.cpp"

# 内嵌的已知可用版本（xnu/files/iokit/Kernel/MiniAppleKeyStore.cpp 原文，335 行）。
# 只在 apply.sh 之后的树里缺这个文件时用来写；文件在时只做上面那三处追加。
KEYSTORE_CPP_BASE = r"""/*
 * MiniAppleKeyStore: a stand-in for Apple's AppleKeyStore (the kernel side of the keybag,
 * which on real hardware talks to the Secure Enclave), as seen by MobileKeyBag.framework
 * and libaks.
 *
 * It describes one fixed situation: a device that has no passcode -- never locked, unlocked
 * since boot. No keys exist here. The keychain (securityd) gives every item a key of its own and
 * has it wrapped by the key of the item's protection class (kAKSWrapKey) before it stores it, and
 * unwrapped (kAKSUnwrapKey) to read the item: that is answered, so that the keychain works -- but
 * with nothing secret to wrap with, the "wrapping" is a reversible transformation and protects
 * nothing (and the keychain lives in /private/var, in memory, and is gone at the next start).
 * Requests that would need real keys or bags otherwise (creating or loading bags, keys kept in the
 * key store) are refused, logged once.
 *
 * The protocol was worked out from the user-space side of Darwin userland 16.5: selectors, and the
 * DER-encoded answers ("SET of SEQUENCE { UTF8String key, INTEGER value }").
 */

#include <IOKit/IOLib.h>
#include <IOKit/IOKitKeys.h>
#include <IOKit/IOService.h>
#include <IOKit/IOUserClient.h>
#include <IOKit/IOMemoryDescriptor.h>
#include <sys/systm.h>
#include <sys/proc.h>

#include "MiniDrivers.h"

enum {
	kAKSOpenCheck           = 0x00,
	kAKSGetLockState        = 0x07,         /* in: handle; out: state flags */
	kAKSLockDevice          = 0x08,
	kAKSUnlockDevice        = 0x09,
	kAKSWrapKey             = 0x0a,         /* in: handle, class; a key; out: the class, the key wrapped */
	kAKSUnwrapKey           = 0x0b,         /* in: handle, class; a wrapped key; out: the key */
	kAKSUnlockBag           = 0x0c,
	kAKSGetSystem           = 0x0e,         /* out: the handle of the system keybag */
	kAKSGetDeviceState      = 0x11,         /* in: handle; out: DER */
	kAKSGetBagUUID          = 0x17,         /* out: 16 bytes */
	kAKSAssertHold          = 0x1a,
	kAKSAssertDrop          = 0x1b,
	kAKSGetConfiguration    = 0x1f,         /* in: handle; out: DER (a big buffer, so out of line) */
	kAKSLockDeviceNow       = 0x20,
	kAKSGetExtendedState    = 0x23,         /* as kAKSGetDeviceState */
	kAKSGeneration          = 0x28,
	kAKSAssertPromote       = 0x64,
	kAKSAssertConsume       = 0x65,
	kAKSNotifyStateSet      = 0x66,         /* "the first-unlock notification has been posted" */
	kAKSNotifyStateIsSet    = 0x67,
	kAKSPrewarm             = 0x73,
};

/* state flags: bit 1 no passcode, bit 2 unlocked since boot */
#define kAKSStateFlags          0x6
/* lock state: 0 unlocked, 1 locked, 2 locking, 3 disabled (no passcode) */
#define kAKSLockStateDisabled   3

static uint64_t gAKSLogged[4];          /* selectors already reported */
static bool     gAKSNotifyState;

/* Append SEQUENCE { UTF8String key, INTEGER value } for a value below 128; returns the new length. */
static size_t
derPut(uint8_t * buf, size_t length, const char * key, uint8_t value)
{
	size_t keyLength = strlen(key);

	buf[length++] = 0x30;
	buf[length++] = (uint8_t) (keyLength + 5);
	buf[length++] = 0x0c;
	buf[length++] = (uint8_t) keyLength;
	memcpy(buf + length, key, keyLength);
	length += keyLength;
	buf[length++] = 0x02;
	buf[length++] = 0x01;
	buf[length++] = value;
	return length;
}

/* The answer to "device state": every field zero, except the two that say "no passcode, unlocked". */
static size_t
deviceState(uint8_t * buf)
{
	size_t length = 2;

	length = derPut(buf, length, "sb", 0);          /* backoff */
	length = derPut(buf, length, "sr", 0);
	length = derPut(buf, length, "ss", kAKSStateFlags);
	length = derPut(buf, length, "sas", 0);         /* assertion set */
	length = derPut(buf, length, "sfa", 0);         /* failed attempts */
	length = derPut(buf, length, "sgs", 0);         /* generation state */
	length = derPut(buf, length, "sls", kAKSLockStateDisabled);
	length = derPut(buf, length, "sgpe", 0);        /* grace period enabled */
	length = derPut(buf, length, "srcd", 0);        /* recovery countdown */
	buf[0] = 0x31;
	buf[1] = (uint8_t) (length - 2);
	return length;
}

static size_t
configuration(uint8_t * buf)
{
	size_t length = 2;

	length = derPut(buf, length, "bd", 0);          /* backoff delay */
	length = derPut(buf, length, "gp", 0);          /* grace period */
	length = derPut(buf, length, "mua", 10);        /* maximum unlock attempts */
	buf[0] = 0x31;
	buf[1] = (uint8_t) (length - 2);
	return length;
}

/* * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * */

class AppleKeyStore : public IOService
{
	OSDeclareDefaultStructors(AppleKeyStore);

public:
	virtual bool start(IOService * provider) APPLE_KEXT_OVERRIDE;
};

OSDefineMetaClassAndStructors(AppleKeyStore, IOService);

bool
AppleKeyStore::start(IOService * provider)
{
	if (!IOService::start(provider)) {
		return false;
	}
	setProperty(kIOUserClientClassKey, "AppleKeyStoreUserClient");
	setName("AppleKeyStore");
	registerService();
	return true;
}

class AppleKeyStoreUserClient : public IOUserClient
{
	OSDeclareDefaultStructors(AppleKeyStoreUserClient);

	static IOReturn answer(IOExternalMethodArguments * args, const uint8_t * bytes, size_t length);

public:
	virtual IOReturn clientClose(void) APPLE_KEXT_OVERRIDE;
	virtual IOReturn registerNotificationPort(mach_port_t port, UInt32 type, io_user_reference_t refCon) APPLE_KEXT_OVERRIDE;
	virtual IOReturn externalMethod(uint32_t selector, IOExternalMethodArguments * args,
	    IOExternalMethodDispatch * dispatch, OSObject * target, void * reference) APPLE_KEXT_OVERRIDE;
};

OSDefineMetaClassAndStructors(AppleKeyStoreUserClient, IOUserClient);

IOReturn
AppleKeyStoreUserClient::clientClose(void)
{
	if (!isInactive()) {
		terminate();
	}
	return kIOReturnSuccess;
}

/* keybagd asks to be told about keys to back up and bags to save; there never are any. */
IOReturn
AppleKeyStoreUserClient::registerNotificationPort(mach_port_t port, UInt32 type, io_user_reference_t refCon)
{
	releaseNotificationPort(port);
	return kIOReturnSuccess;
}

/*
 * kAKSWrapKey and kAKSUnwrapKey. A wrapped key is 8 bytes longer than the key, as with AES key
 * wrap (RFC 3394), which is what callers size their buffers for: a mark, the class, and the key
 * mixed with a fixed pattern. Not encryption: there is no secret here to encrypt with.
 */
static const uint8_t kWrapMark[4] = { 'H', 'K', 'w', 'r' };

static void
wrapMix(uint8_t * bytes, size_t length)
{
	for (size_t i = 0; i < length; i++) {
		bytes[i] ^= (uint8_t)(0x5a + 37 * i);
	}
}

/* A structure answer; a big buffer on the other side arrives as a memory descriptor. */
IOReturn
AppleKeyStoreUserClient::answer(IOExternalMethodArguments * args, const uint8_t * bytes, size_t length)
{
	if (args->structureOutputDescriptor != NULL) {
		IOMemoryDescriptor * out = args->structureOutputDescriptor;
		IOReturn ret;

		if (out->getLength() < length) {
			return kIOReturnNoSpace;
		}
		ret = out->prepare();
		if (ret == kIOReturnSuccess) {
			out->writeBytes(0, bytes, length);
			out->complete();
			args->structureOutputDescriptorSize = (uint32_t) length;
		}
		return ret;
	}
	if (args->structureOutput == NULL || args->structureOutputSize < length) {
		return kIOReturnNoSpace;
	}
	memcpy(args->structureOutput, bytes, length);
	args->structureOutputSize = (uint32_t) length;
	return kIOReturnSuccess;
}

IOReturn
AppleKeyStoreUserClient::externalMethod(uint32_t selector, IOExternalMethodArguments * args,
    IOExternalMethodDispatch * dispatch, OSObject * target, void * reference)
{
	static const uint8_t bagUUID[16] = { 0x72, 0x65, 0x64, 0x77, 0x6f, 0x6f, 0x64, 0x00, 0x6b, 0x65, 0x79, 0x62, 0x61, 0x67, 0x00, 0x01 };
	uint8_t der[128];
	uint64_t * out = args->scalarOutput;

	switch (selector) {
	case kAKSOpenCheck:
	case kAKSLockDevice:
	case kAKSLockDeviceNow:
	case kAKSUnlockDevice:
	case kAKSUnlockBag:
	case kAKSAssertHold:
	case kAKSAssertDrop:
	case kAKSAssertPromote:
	case kAKSAssertConsume:
	case kAKSPrewarm:
		return kIOReturnSuccess;

	case kAKSGetLockState:
		if (args->scalarOutputCount < 1) {
			return kIOReturnBadArgument;
		}
		out[0] = kAKSStateFlags;
		args->scalarOutputCount = 1;
		return kIOReturnSuccess;

	case kAKSGetSystem:
	case kAKSGeneration:
		if (args->scalarOutputCount >= 1) {
			out[0] = 0;
			args->scalarOutputCount = 1;
		}
		return kIOReturnSuccess;

	case kAKSGetDeviceState:
	case kAKSGetExtendedState:
		return answer(args, der, deviceState(der));

	case kAKSGetConfiguration:
		return answer(args, der, configuration(der));

	case kAKSGetBagUUID:
		return answer(args, bagUUID, sizeof(bagUUID));

	case kAKSNotifyStateSet:
		gAKSNotifyState = true;
		return kIOReturnSuccess;

	case kAKSWrapKey: {
		uint8_t wrapped[8 + 64];
		size_t length = args->structureInputSize;

		if (args->structureInput == NULL || length == 0 || length > 64 || args->scalarInputCount < 2) {
			return kIOReturnBadArgument;
		}
		memcpy(wrapped, kWrapMark, sizeof(kWrapMark));
		wrapped[4] = (uint8_t)args->scalarInput[1];
		wrapped[5] = wrapped[6] = wrapped[7] = 0;
		memcpy(wrapped + 8, args->structureInput, length);
		wrapMix(wrapped + 8, length);
		if (args->scalarOutputCount >= 1) {
			out[0] = args->scalarInput[1];
			args->scalarOutputCount = 1;
		}
		return answer(args, wrapped, 8 + length);
	}

	case kAKSUnwrapKey: {
		uint8_t key[64];
		const uint8_t * wrapped = (const uint8_t *)args->structureInput;
		size_t length = args->structureInputSize;

		if (wrapped == NULL || length <= 8 || length > 8 + sizeof(key) || memcmp(wrapped, kWrapMark, sizeof(kWrapMark)) != 0) {
			return kIOReturnBadArgument;
		}
		memcpy(key, wrapped + 8, length - 8);
		wrapMix(key, length - 8);
		return answer(args, key, length - 8);
	}

	case kAKSNotifyStateIsSet:
		if (args->scalarOutputCount < 1) {
			return kIOReturnBadArgument;
		}
		out[0] = gAKSNotifyState;
		args->scalarOutputCount = 1;
		return kIOReturnSuccess;

	default:
		break;
	}

	if (selector < 256 && !(gAKSLogged[selector / 64] & (1ULL << (selector % 64)))) {
		char procname[32] = "?";

		gAKSLogged[selector / 64] |= 1ULL << (selector % 64);
		proc_selfname(procname, sizeof(procname));
		IOLog("MiniAppleKeyStore: %s[%d] selector 0x%x not implemented (scalars %u in, %u out; structure %u in, %u out)\n",
		    procname, proc_selfpid(), selector, args->scalarInputCount, args->scalarOutputCount, args->structureInputSize,
		    args->structureOutputSize);
	}
	return kIOReturnUnsupported;
}

/* * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * */

void
MiniAppleKeyStoreStart(void)
{
	/* user space looks for it by class, and by the path IOService:/IOResources/AppleKeyStore */
	IOService * resources = IOService::getResourceService();
	AppleKeyStore * store = OSTypeAlloc(AppleKeyStore);

	if (resources == NULL || store == NULL || !store->init() || !store->attach(resources)) {
		OSSafeReleaseNULL(store);
		IOLog("MiniAppleKeyStore: cannot create the service\n");
		return;
	}
	if (!store->start(resources)) {
		store->detach(resources);
	}
	store->release();
}
"""

# --------------------------------------------------------------------------
# 1) iokit/conf/files：加这一行（没有时）
# --------------------------------------------------------------------------
FILES_BLOCK = (
    "# " + MARK + ": built-in AppleKeyStore stand-in (libaks / keybagd)\n"
    "iokit/Kernel/MiniAppleKeyStore.cpp\t\t\toptional iokitcpp\n"
)

# --------------------------------------------------------------------------
# 2) iokit/Kernel/MiniAppleKeyStore.cpp 的三处追加
# --------------------------------------------------------------------------
# 2a. AppleKeyStore::start()：注册成功打一行。
REGISTER_ANCHOR = "\tregisterService();\n"
REGISTER_BLOCK = r"""	/* HAKIOS-LAFA-KEYSTORE: one line on the console when the service is published, so the
	 * next boot log says whether libaks has anything to open. */
	IOLog("AppleKeyStore: registered (MiniAppleKeyStore stand-in: no passcode, unlocked since boot)\n");
"""

# 2b. externalMethod()：每次调用打一行（selector + 参数形状）。
TRACE_ANCHOR = "\tuint64_t * out = args->scalarOutput;\n\n"
TRACE_BLOCK = r"""	/* HAKIOS-LAFA-KEYSTORE: report every call (selector and argument shapes) so the next
	 * boot log shows what libaks/keybagd actually asks for. */
	{
		char aksProc[32] = "?";

		proc_selfname(aksProc, sizeof(aksProc));
		IOLog("AppleKeyStore: %s selector 0x%x (scalars %u in, %u out; structure %u in, %u out)\n",
		    aksProc, selector, args->scalarInputCount, args->scalarOutputCount,
		    args->structureInputSize, args->structureOutputSize);
	}

"""

# 2c. 0038 的 SweetKeyStore 还回答这些 selector（我们的 enum 里没有；无 bag 文件，
#     直接成功就是“用户空间重启”路径）。
SELECTORS_ANCHOR = "\tcase kAKSPrewarm:\n\t\treturn kIOReturnSuccess;\n"
SELECTORS_BLOCK = r"""
	/* HAKIOS-LAFA-KEYSTORE: the selectors the 0038 SweetKeyStore answers in addition to ours.
	 * There is no bag file and no Secure Enclave here, so bag bookkeeping succeeds and says
	 * nothing -- that is the userspace-reboot path libaks expects. */
	case 2:         /* kAKSCreateBag */
	case 3:         /* kAKSSaveBag */
	case 4:         /* kAKSUnloadBag */
	case 5:         /* kAKSSetSystem */
	case 6:         /* kAKSLoadBag */
	case 13:        /* kAKSLockBag */
	case 55:        /* kAKSUnloadSessionBags */
		return kIOReturnSuccess;
"""

MARKERS = (
    ("注册日志", "registered (MiniAppleKeyStore stand-in"),
    ("selector 日志", "selector 0x%x (scalars"),
    ("0038 的 bag selector", "kAKSCreateBag"),
)

# --------------------------------------------------------------------------
# 3) iokit/KernelConfigTables.cpp：在收尾的 ")"; 之前插入 AppleKeyStore 匹配项
# --------------------------------------------------------------------------
CONFIG_TABLE_BLOCK = (
    "#if defined(__arm64__) && !defined(APPLE_ARM64_ARCH_FAMILY)\n"
    "    /* " + MARK + ": AppleKeyStore for libaks / keybagd --init.  Its kAKSGetSystem\n"
    "     * (selector 14) answers with handle 0, so keybagd does not fall back to\n"
    "     * /private/var/keybags/systembag.kb.  The class is the one in\n"
    "     * iokit/Kernel/MiniAppleKeyStore.cpp (our counterpart of the reference\n"
    "     * 0038 SweetKeyStore.cpp); matching on IOResources is how a built-in kext\n"
    "     * is instantiated, exactly like the reference does it. */\n"
    '    "   ,{"\n'
    "    \"     'IOClass'         = AppleKeyStore;\"\n"
    "    \"     'IOProviderClass' = IOResources;\"\n"
    "    \"     'IOMatchCategory' = AppleKeyStore;\"\n"
    '    "   }"\n'
    "#endif\n"
)

USAGE = "python3 ci/patch-kernel-keystore.py <xnu source tree> [--check]"


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


def splice(text: str, anchor: str, block: str, what: str) -> str:
    """把 block 追加在 anchor 后面（anchor 必须唯一）。"""
    count = text.count(anchor)
    if count == 0:
        raise Failure("%s 里找不到锚点 %r（树不是预期的 xnu-8796.121.2 + hakios 吗？）" % (what, anchor))
    if count > 1:
        raise Failure("%s 里锚点 %r 出现 %d 次，不唯一" % (what, anchor, count))
    return text.replace(anchor, anchor + block, 1)


def patched_keystore(text: str) -> str:
    """给 MiniAppleKeyStore.cpp 打三处追加（返回新文本）。"""
    text = splice(text, REGISTER_ANCHOR, REGISTER_BLOCK, "MiniAppleKeyStore.cpp(start)")
    text = splice(text, TRACE_ANCHOR, TRACE_BLOCK, "MiniAppleKeyStore.cpp(externalMethod)")
    text = splice(text, SELECTORS_ANCHOR, SELECTORS_BLOCK, "MiniAppleKeyStore.cpp(selectors)")
    return text


def install_source(root: Path, check: bool) -> list:
    """保证 iokit/Kernel/MiniAppleKeyStore.cpp 存在且带三处追加。"""
    path = root / KEYSTORE_REL
    if not path.is_file():
        # 上游 xnu/files overlay 缺文件时的兜底：写入内嵌的已知可用版本。
        new = patched_keystore(KEYSTORE_CPP_BASE)
        if not check:
            write(path, new)
        return ["  + %s（新建：内嵌 %d 行 + 3 处追加）" % (KEYSTORE_REL, new.count("\n"))]
    text = read(path)
    if MARK in text:
        missing = [name for name, probe in MARKERS if probe not in text]
        if missing:
            raise Failure("%s 里有 %s 标记但缺 %s，先手工清干净" % (KEYSTORE_REL, MARK, "、".join(missing)))
        return ["  = %s（已是补丁后版本）" % KEYSTORE_REL]
    if any(probe in text for _name, probe in MARKERS):
        raise Failure("%s 里已经有一部分追加但没有 %s 标记（手改过？），先手工清干净" % (KEYSTORE_REL, MARK))
    new = patched_keystore(text)
    if not check:
        write(path, new)
    return ["  + %s（注册日志 + 每次调用的 selector 日志 + 0038 的 7 个 bag selector）" % KEYSTORE_REL]


def ensure_files_entry(root: Path, check: bool) -> list:
    """保证 iokit/conf/files 里登记了 MiniAppleKeyStore.cpp（hakios.patch 通常已加）。"""
    path = root / "iokit" / "conf" / "files"
    text = read(path)
    if "MiniAppleKeyStore.cpp" in text:
        return ["  = iokit/conf/files（MiniAppleKeyStore.cpp 已在构建里）"]
    anchor_re = re.compile(r"^[ \t]*iokit/Kernel/IOPlatformExpert\.cpp[ \t]+optional[ \t]+iokitcpp[^\n]*$", re.MULTILINE)
    match = anchor_re.search(text)
    if not match:
        raise Failure("iokit/conf/files 里找不到 iokit/Kernel/IOPlatformExpert.cpp 那一行")
    end = text.find("\n", match.start())
    if end < 0:
        raise Failure("iokit/conf/files 里 IOPlatformExpert.cpp 行没有换行结尾")
    new = text[:end + 1] + FILES_BLOCK + text[end + 1:]
    if not check:
        write(path, new)
    return ["  + iokit/conf/files（+1 行：iokit/Kernel/MiniAppleKeyStore.cpp）"]


def register_config_table(root: Path, check: bool) -> list:
    """把 AppleKeyStore 的 IOResources 匹配项加进 gIOKernelConfigTables。"""
    path = root / "iokit" / "KernelConfigTables.cpp"
    text = read(path)
    if MARK in text or "'IOClass'         = AppleKeyStore" in text:
        return ["  = iokit/KernelConfigTables.cpp（AppleKeyStore 已登记）"]
    if "gIOKernelConfigTables" not in text:
        raise Failure("iokit/KernelConfigTables.cpp 里没有 gIOKernelConfigTables")
    # 收尾是单独一行的 ")"; ，把新项插在它前面（每项以逗号开头，对任何前一项都成立）。
    matches = list(re.finditer(r'^[ \t]*"\)";[ \t]*$', text, re.MULTILINE))
    if not matches:
        raise Failure('iokit/KernelConfigTables.cpp 里找不到收尾的 ")";')
    at = matches[-1].start()
    new = text[:at] + CONFIG_TABLE_BLOCK + text[at:]
    if not check:
        write(path, new)
    return ["  + iokit/KernelConfigTables.cpp（+AppleKeyStore，IOProviderClass=IOResources）"]


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
        print("patch-keystore: %s" % exc, file=sys.stderr)
        return 2
    for required in ("iokit/conf/files", "iokit/KernelConfigTables.cpp"):
        if not (root / required).is_file():
            print("patch-keystore: %s 不存在（%s 是 apply.sh 之后的 xnu 源码树吗？）"
                  % (required, root), file=sys.stderr)
            return 1
    if not (root / "iokit" / "Kernel").is_dir():
        print("patch-keystore: %s 里没有 iokit/Kernel 目录" % root, file=sys.stderr)
        return 1

    print("patch-keystore: %s%s" % (root, "（--check，不写文件）" if check else ""))
    try:
        lines = []
        lines += install_source(root, check)
        lines += ensure_files_entry(root, check)
        lines += register_config_table(root, check)
    except Failure as exc:
        print("patch-keystore: %s" % exc, file=sys.stderr)
        return 1
    for line in lines:
        print(line)

    print(
        "patch-keystore: 假 AppleKeyStore 就位（自注册 + config-table 两条路）。"
        "下一轮真机 keybag boot task 的日志判据：\n"
        "  AppleKeyStore: registered (MiniAppleKeyStore stand-in: ...)\n"
        "  AppleKeyStore: keybagd selector 0xe (...)\n"
        "  keybagd 不再出现 \"Could not open /private/var/keybags/systembag.kb\"\n"
        "失败判据：只有 \"MiniAppleKeyStore: cannot create the service\"（config table 没匹配上）、\n"
        "         有 selector 日志但 keybagd 仍读文件（看 selector 14 的返回值/状态值）、\n"
        "         或 \"selector 0x.. not implemented\"（补那个 selector）。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
