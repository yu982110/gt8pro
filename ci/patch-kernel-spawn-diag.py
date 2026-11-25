#!/usr/bin/env python3
"""HAKIOS-LAFA-SPAWN-DIAG: print every posix_spawn that returns an error, and who asked.

The boot-task child of launchd keeps failing with EPERM (rv 1) and the exec probe's site lines
are unconditional (they print whenever the code reaches the site, not when a check denies), so
they cannot say which check failed.  Print the final error of posix_spawn() together with the
caller's name instead:

    HAKIOS-SPAWN: launchd.darwin: posix_spawn error 1

The one place every failure passes through is the function's last `return error;`.  Idempotent.

    python3 ci/patch-kernel-spawn-diag.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-LAFA-SPAWN-DIAG"
OLD = "\tassert(exec_failure_reason == NULL);\n\treturn error;\n}\n"
NEW = ("\tif (error != 0) {                                         /* " + MARK + " */\n"
       "\t\tchar hk_self[32];\n"
       "\t\tproc_selfname(hk_self, sizeof(hk_self));\n"
       "\t\tprintf(\"HAKIOS-SPAWN: %s: posix_spawn error %d\\n\", hk_self, error);\n"
       "\t}\n"
       "\tassert(exec_failure_reason == NULL);\n"
       "\treturn error;\n"
       "}\n")


def main() -> int:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    f = root / "bsd/kern/kern_exec.c"
    if not f.exists():
        print("patch-spawn-diag: MISS bsd/kern/kern_exec.c")
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-spawn-diag: already applied")
        return 0
    if OLD not in text:
        print("patch-spawn-diag: MISS the final return of posix_spawn")
        return 1
    text = text.replace(OLD, NEW, 1)
    f.write_text(text, errors="surrogateescape")
    print("patch-spawn-diag: posix_spawn errors are printed with the caller's name")
    return 0


if __name__ == "__main__":
    sys.exit(main())
