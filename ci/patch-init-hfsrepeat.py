#!/usr/bin/env python3
"""patch-init-hfsrepeat.py -- print the data-volume verdict again and again.

The HFS+ mount of /private/var decides whether Darwin keeps anything between boots, and
its one-line verdict scrolls away before the phone can be photographed.  This adds a
short repeat loop right after mount_writable(): ten lines, about three seconds apart
(a plain busy wait, so no extra header is needed).
"""
import re
import sys
from pathlib import Path

MARK = "HAKIOS-HFSREPEAT"
ANCHOR = "\tmount_writable();\n"

BLOCK = (
    "\tmount_writable();\n"
    "\t/* " + MARK + ": keep the data-volume verdict on screen long enough to photograph */\n"
    "\tfor (int hakios_hr = 0; hakios_hr < 10; hakios_hr++) {\n"
    "\t\tprintf(\"HAKIOS: /private/var is %s (repeat %d of 10)\\n\",\n"
    "\t\t    var_kept ? \"HFS Plus, kept between boots\" : \"a memory filesystem (HFS+ mount failed)\",\n"
    "\t\t    hakios_hr + 1);\n"
    "\t\tfor (volatile unsigned long hakios_hw = 0; hakios_hw < 400000000UL; hakios_hw++) {\n"
    "\t\t\t/* busy wait: no header, about three seconds on this SoC */\n"
    "\t\t}\n"
    "\t}\n"
)


def fail(msg: str) -> int:
    print(f"patch-init-hfsrepeat: {msg}", file=sys.stderr)
    return 1


def main() -> int:
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
    if ANCHOR not in text:
        return fail("mount_writable(); anchor not found")
    if "var_kept" not in text:
        return fail("var_kept not found -- is this the init that mounts a data volume?")

    text = text.replace(ANCHOR, BLOCK, 1)
    candidate.write_text(text, encoding="utf-8", errors="surrogateescape")
    print("patch-init-hfsrepeat: added the 30 s verdict loop")
    return 0


if __name__ == "__main__":
    sys.exit(main())
