#!/usr/bin/env python3
"""patch-init-hfsrepeat.py -- print the data-volume verdict over and over.

The HFS+ mount of /private/var decides whether Darwin keeps anything between boots, and
its one-line verdict scrolls away before the phone can be photographed.  This patch adds
a short repeat loop right after mount_writable(), so the verdict stays on the console for
half a minute.

python3 ci/patch-init-hfsrepeat.py <userland tree>
"""
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
    "\t\tsleep(3);\n"
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
        return fail("mount_writable(); anchor not found (has exec-launchd already moved it?)")
    # var_kept has to exist as a file-scope name for the loop to compile
    if "var_kept" not in text:
        return fail("var_kept not found -- is this the init that mounts a data volume?")

    # sleep() lives in unistd.h, which this init does not include yet
    if "#include <unistd.h>" not in text:
        import re as _re
        m = _re.search(r"^#include [^\\n]*\\n", text, _re.M)
        if m is None:
            return fail("no #include line at all to hang <unistd.h> on")
        text = text[:m.end()] + "#include <unistd.h>\\n" + text[m.end():]

    text = text.replace(ANCHOR, BLOCK, 1)
    candidate.write_text(text, encoding="utf-8", errors="surrogateescape")
    print("patch-init-hfsrepeat: added the 30 s verdict loop")
    return 0


if __name__ == "__main__":
    sys.exit(main())
