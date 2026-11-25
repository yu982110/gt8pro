#!/usr/bin/env python3
"""patch-kernel-jetsam.py -- 找不到可杀进程时，memorystatus_jetsam_thread 不再 panic：打印，然后等下一次内存压力。

现场（artifacts/logs/gpu182-live.log、gpu178-live.log，搜 memorystatus_jetsam_thread）：

    memorystatus: Looking for highwatermark kills.
    memorystatus: killing process 22 [launchd_cache_loader] in high band IMPORTANT (180) ...
    memorystatus: Looking for highwatermark kills.
    panic(cpu 0 ...): memorystatus_jetsam_thread: no victim! available pages:33018 @kern_memorystatus.c:4147

bsd/kern/kern_memorystatus.c 的 memorystatus_thread_internal()：TOP_PROCESS/AGGRESSIVE 动作一个进程也没杀掉、
可用页仍在 critical 以下时，release XNU 就 panic()。这时 launchd 的 boot task 链正走在半路，panic 等于
整机重启。这里把它换成一条日志 + break：本次扫描结束，memorystatus_thread() 接着在 sched_cond_wait 里
睡到下一次内存压力唤醒，链子继续走。

参考 refs/data-a-live/xnu/patches/0315-jetsam.patch：**不能直接套，也用不着**。
  * 它改 config/MASTER.arm64.pandora 打开 jetsam；我们树里没有这个机器配置文件（我们编的是
    config/MASTER.arm64.bcm2837 + 生成的 config/MASTER.hakios），而且 BCM2837 的 VM_BASE 本来就有
    jetsam。证据：日志里的 "memorystatus: Looking for highwatermark kills." 只存在于 #if CONFIG_JETSAM
    里；而 memorystatus_avail_pages_below_critical() 在 jetsam 关掉时恒为 false，这条 panic 根本到不了。
  * 它的第二个 hunk 给 vm_compressor_backing_store.c 的 macOS 分支补 app swap 常量，只在
    XNU_TARGET_OS_OSX=1 时有意义；我们按 xnu-build.sh（SDKROOT=iphoneos）编的是 XNU_TARGET_OS_IOS，
    常量在 !XNU_TARGET_OS_OSX 分支里本来就有（hakios 内核带 CONFIG_JETSAM 一直编得过），没有依赖链。

    python3 ci/patch-kernel-jetsam.py <xnu 源码树>
"""
import pathlib
import sys

MARK = "HAKIOS-JETSAM"

OLD = (
    '\t\t\tif (!jetsam_thread->limit_to_low_bands && memorystatus_avail_pages_below_critical()) {\n'
    '\t\t\t\t/*\n'
    '\t\t\t\t * Still under pressure and unable to kill a process - panic\n'
    '\t\t\t\t */\n'
    '\t\t\t\tpanic("memorystatus_jetsam_thread: no victim! available pages:%llu", (uint64_t)MEMORYSTATUS_LOG_AVAILABLE_PAGES);\n'
    '\t\t\t}\n'
)

# 兜底：if 的注释块被别的补丁改过、整块匹配不上时，只换掉这条 panic 语句
OLD_PANIC = (
    '\t\t\t\tpanic("memorystatus_jetsam_thread: no victim! available pages:%llu", '
    '(uint64_t)MEMORYSTATUS_LOG_AVAILABLE_PAGES);\n'
)

WAIT = (
    '\t\t\t\tprintf("HAKIOS-JETSAM: memorystatus_jetsam_thread: no victim! available pages:%llu"\n'
    '\t\t\t\t    " - not panicking, waiting for the next wakeup\\n",\n'
    '\t\t\t\t    (uint64_t)MEMORYSTATUS_LOG_AVAILABLE_PAGES);\n'
    '\t\t\t\tbreak;\n'
)

NEW = (
    '\t\t\tif (!jetsam_thread->limit_to_low_bands && memorystatus_avail_pages_below_critical()) {\n'
    '\t\t\t\t/*\n'
    '\t\t\t\t * HAKIOS-JETSAM (ci/patch-kernel-jetsam.py): still under pressure and no\n'
    '\t\t\t\t * process left to kill. Release xnu panics here; this kernel is in the\n'
    '\t\t\t\t * middle of launchd\'s boot task chain, so panic would reboot the phone.\n'
    '\t\t\t\t * Say what happened, end this scan, and wait for the next pressure\n'
    '\t\t\t\t * wakeup (memorystatus_thread() blocks in sched_cond_wait after return).\n'
    '\t\t\t\t */\n' + WAIT +
    '\t\t\t}\n'
)


def main(root):
    root = pathlib.Path(root)
    src = root / "bsd" / "kern" / "kern_memorystatus.c"
    if not src.exists():
        print("patch-jetsam: MISS: %s not found" % src)
        return 1
    text = src.read_text(encoding="utf-8", errors="surrogateescape")

    if MARK in text:
        print("patch-jetsam: already applied")
        return 0

    if OLD in text:
        text = text.replace(OLD, NEW, 1)
        how = "no victim -> printf + wait"
    elif OLD_PANIC in text:
        # 兜底：保留原来的注释块，只把 panic 语句换掉
        text = text.replace(OLD_PANIC, WAIT, 1)
        how = "no victim -> printf + wait (fallback: panic statement only)"
    else:
        print("patch-jetsam: MISS: the no-victim panic is not in %s" % src)
        print("patch-jetsam:   lines that mention the panic (for the next port):")
        shown = 0
        for i, line in enumerate(text.splitlines(), 1):
            if "memorystatus_jetsam_thread" in line or "limit_to_low_bands" in line:
                print("patch-jetsam:   %5d: %s" % (i, line.strip()[:160]))
                shown += 1
                if shown >= 8:
                    break
        return 1

    src.write_text(text, encoding="utf-8", errors="surrogateescape")
    print("patch-jetsam: bsd/kern/kern_memorystatus.c: %s" % how)
    print("patch-jetsam:   expected log line: \"HAKIOS-JETSAM: memorystatus_jetsam_thread: no victim!"
          " available pages:... - not panicking, waiting for the next wakeup\"")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
