#!/usr/bin/env python3
"""追踪 pid 1（launchd）的系统调用：失败时打调用号和错误码；sysctl 还打 MIB。

用于定位 libignition 的 "failed to query hardware target: 22" 到底查的是哪个 sysctl。

    python3 ci/patch-kernel-systrace.py <xnu 源码树>
"""
import sys
from pathlib import Path

ANCHOR = "\terror = (*(callp->sy_call))(proc, &uthread->uu_arg[0], &(uthread->uu_rval[0]));\n"

INSERT = ANCHOR + """
\t/* HAKIOS-LAFA: what the first user-space program asks for, and what it gets back */
\tif (error != 0 && proc_getpid(proc) == 1) {
\t\tif (code == 202) {              /* sysctl: say which one */
\t\t\tint mib[4] = { -1, -1, -1, -1 };
\t\t\tu_int n = (u_int)uthread->uu_arg[1];
\t\t\tif (n > 4) {
\t\t\t\tn = 4;
\t\t\t}
\t\t\tif (n > 0 && copyin((user_addr_t)uthread->uu_arg[0], mib, n * sizeof(int)) == 0) {
\t\t\t\tprintf("lafa-sys: pid 1 sysctl %d.%d.%d.%d -> %d\\n",
\t\t\t\t    mib[0], mib[1], mib[2], mib[3], error);
\t\t\t} else {
\t\t\t\tprintf("lafa-sys: pid 1 sysctl (mib unreadable) -> %d\\n", error);
\t\t\t}
\t\t} else {
\t\t\tprintf("lafa-sys: pid 1 code %d -> %d\\n", code, error);
\t\t}
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
