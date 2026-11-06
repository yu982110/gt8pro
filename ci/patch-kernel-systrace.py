#!/usr/bin/env python3
"""追踪 pid 1（launchd）的系统调用：失败时打调用号和错误码。

用于定位 libignition 的 "failed to query hardware target: 22" 到底走了哪个系统调用。

    python3 ci/patch-kernel-systrace.py <xnu 源码树>
"""
import sys
from pathlib import Path

ANCHOR = "\terror = (*(callp->sy_call))(proc, &uthread->uu_arg[0], &(uthread->uu_rval[0]));\n"

INSERT = ANCHOR + """
\t/* HAKIOS-LAFA: what the first user-space program asks for, and what it gets back */
\tif (error != 0 && proc_getpid(proc) == 1) {
\t\tprintf("lafa-sys: pid 1 code %d -> %d\\n", code, error);
\t}
"""


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    src = root / "bsd" / "dev" / "arm" / "systemcalls.c"
    text = src.read_text(encoding="utf-8", errors="surrogateescape")
    if "lafa-sys" in text:
        print("patch-systrace: already patched")
        return 0
    if ANCHOR not in text:
        print("patch-systrace: anchor not found", file=sys.stderr)
        return 1
    text = text.replace(ANCHOR, INSERT, 1)
    src.write_text(text, encoding="utf-8", errors="surrogateescape")
    print("patch-systrace: pid 1 syscall trace inserted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
