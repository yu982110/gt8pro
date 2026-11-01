#!/usr/bin/env python3
"""记录 pid 1 的 mount() 及其返回值（stage0 挂 /dev/md1 失败时，这是唯一能看见的线索）。

    python3 ci/patch-kernel-mounttrace.py <xnu 源码树>
"""
import sys
from pathlib import Path

ANCHOR = """\terror = mount_common(fstypename, pvp, vp, &cn, uap->data, flags, KERNEL_MOUNT_FMOUNT, labelstr, ctx);

\tzfree(ZV_NAMEI, cn.cn_pnbuf);
"""

INSERT = """\terror = mount_common(fstypename, pvp, vp, &cn, uap->data, flags, KERNEL_MOUNT_FMOUNT, labelstr, ctx);

\t/* HAKIOS-LAFA: what the first user-space program mounts, and whether it worked */
\tif (proc_getpid(p) == 1) {
\t\tconst char *lafa_data = (const char *)(uintptr_t)uap->data;
\t\tprintf("lafa-mount: \\"%s\\" on \\"%s\\" data \\"%s\\" = %d (%s)\\n",
\t\t    fstypename, cn.cn_pnbuf, lafa_data != NULL ? lafa_data : "(null)",
\t\t    error, error == 0 ? "ok" : "FAILED");
\t}

\tzfree(ZV_NAMEI, cn.cn_pnbuf);
"""


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    src = root / "bsd" / "vfs" / "vfs_syscalls.c"
    text = src.read_text(encoding="utf-8", errors="surrogateescape")
    if "HAKIOS-LAFA: what the first user-space program mounts" in text:
        print("patch-mounttrace: already patched")
        return 0
    if ANCHOR not in text:
        print("patch-mounttrace: anchor not found", file=sys.stderr)
        return 1
    text = text.replace(ANCHOR, INSERT, 1)
    src.write_text(text, encoding="utf-8", errors="surrogateescape")
    print("patch-mounttrace: pid 1 mount trace inserted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
