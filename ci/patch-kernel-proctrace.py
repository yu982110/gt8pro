#!/usr/bin/env python3
"""在 exec 成功后打印一行内核日志（本移植还没有控制台，这是唯一能看见用户态的办法）。

    python3 ci/patch-kernel-proctrace.py <xnu 源码树>
"""
import sys
from pathlib import Path

ANCHOR = """\t\tgoto badtoolate;
\t}

\t/*
\t * Set up the shared cache region in the new process.
"""

INSERT = """\t\tgoto badtoolate;
\t}

\t/* HAKIOS-LAFA: the image is loaded: say what is about to run (the port has no console yet) */
\t{
\t\tconst char *lafa_path = (imgp->ip_ndp != NULL) ? imgp->ip_ndp->ni_cnd.cn_nameptr : "?";
\t\tprintf("proctrace: pid %d exec ok: %s entry %llx\\n",
\t\t    proc_getpid(p), lafa_path, (unsigned long long)load_result.entry_point);
\t}

\t/*
\t * Set up the shared cache region in the new process.
"""


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    src = root / "bsd" / "kern" / "kern_exec.c"
    text = src.read_text(encoding="utf-8", errors="surrogateescape")
    if "HAKIOS-LAFA: the image is loaded" in text:
        print("patch-proctrace: already patched")
        return 0
    if ANCHOR not in text:
        print("patch-proctrace: anchor not found", file=sys.stderr)
        return 1
    src.write_text(text.replace(ANCHOR, INSERT, 1), encoding="utf-8", errors="surrogateescape")
    print("patch-proctrace: exec trace inserted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
