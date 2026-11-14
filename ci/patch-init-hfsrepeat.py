#!/usr/bin/env python3
"""patch-init-hfsrepeat.py -- print the data-volume verdict again and again.

The HFS+ mount of /private/var decides whether Darwin keeps anything between boots, and its
one-line verdict scrolls away before the phone can be photographed.  This adds a short repeat loop
that prints the verdict ten times, about three seconds apart (a plain busy wait, so no extra header).

**Where the loop goes matters.**  It used to be inserted right after the `mount_writable();` call in
main(), and so was patch-init-exec-launchd's hand-over block -- which is applied later and therefore
lands *between* the call and this loop:

    mount_writable();
    { HAKIOS-EXEC-LAUNCHD ... execve("/redwood/sbin/launchd.darwin", ...) }   <- applied last
    for (10x) printf("HAKIOS: /private/var is ...")                            <- never reached

execve never returns, so the verdict was never printed: an init built by that chain could not say
whether the data volume had mounted, and a boot was read as "no line = no mount" when the line had
simply become dead code.

The loop is now inserted at the **end of mount_writable() itself**, before its closing brace: whatever
runs after the call cannot skip it, and the verdict is printed where var_kept was just decided.

    python3 ci/patch-init-hfsrepeat.py <userland tree>
"""
import sys
from pathlib import Path

MARK = "HAKIOS-HFSREPEAT"

# the function's own tail: its last check() call, the closing brace of the block it is in, and (TAIL)
# the function's closing brace.  Nothing else in init.c ends that way.
HEAD = '\t\tcheck(grafted, "graft the package manager\'s prefix onto the data volume");\n\t}\n'
TAIL = "}\n"
ANCHOR = HEAD + TAIL

LOOP = (
    "\t/* " + MARK + ": the verdict, inside mount_writable(), where no exec can jump over it */\n"
    "\tfor (int hakios_hr = 0; hakios_hr < 10; hakios_hr++) {\n"
    "\t\tprintf(\"HAKIOS: /private/var is %s (repeat %d of 10)\\n\",\n"
    "\t\t    var_kept ? \"HFS Plus, kept between boots\" : \"a memory filesystem (HFS+ mount failed)\",\n"
    "\t\t    hakios_hr + 1);\n"
    "\t\tfor (volatile unsigned long hakios_hw = 0; hakios_hw < 400000000UL; hakios_hw++) {\n"
    "\t\t\t/* busy wait: no header, about three seconds on this SoC */\n"
    "\t\t}\n"
    "\t}\n"
)


def fail(msg):
    print(f"patch-init-hfsrepeat: {msg}", file=sys.stderr)
    return 1


def main():
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} <userland tree>", file=sys.stderr)
        return 2

    root = Path(sys.argv[1])
    candidate = root / "src" / "init.c"
    if not candidate.is_file():
        candidate = root / "init.c"
    if not candidate.is_file():
        return fail(f"{root}/src/init.c not found")

    text = candidate.read_text(encoding="utf-8", errors="surrogateescape")
    if MARK in text:
        print("patch-init-hfsrepeat: already applied")
        return 0
    if "var_kept" not in text:
        return fail("var_kept not found -- is this the init that mounts a data volume?")
    if text.count(ANCHOR) != 1:
        return fail(f"the end of mount_writable() is not there once ({text.count(ANCHOR)} times)")

    # the loop goes inside the function: after its last statement, before its closing brace
    text = text.replace(ANCHOR, HEAD + LOOP + TAIL, 1)
    candidate.write_text(text, encoding="utf-8", errors="surrogateescape")
    print("patch-init-hfsrepeat: the verdict loop is inside mount_writable(), 10 lines about 3 s apart")
    return 0


if __name__ == "__main__":
    sys.exit(main())
