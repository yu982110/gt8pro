#!/usr/bin/env python3
"""patch-init-hfsrepeat.py -- print the data-volume verdict again and again.

The HFS+ mount of /private/var decides whether Darwin keeps anything between boots, and its one-line
verdict scrolls away before the phone can be photographed.  This adds a short repeat loop that prints
the verdict ten times, about three seconds apart (a plain busy wait, so no extra header is needed).

**Where the loop goes matters, and it is not "after the call".**  It used to be inserted right after
the `mount_writable();` call in main(), and so was patch-init-exec-launchd's hand-over block -- which
CI applies later and with the same anchor, so it lands *between* the call and this loop:

    mount_writable();
    { HAKIOS-EXEC-LAUNCHD ... execve("/redwood/sbin/launchd.darwin", ...) }   <- applied later
    for (10x) printf("HAKIOS: /private/var is ...")                            <- never reached

execve never returns, so the verdict was never printed: an init built by that chain could not say
whether the data volume had mounted, and a boot was read as "no line = no mount" when the line had
simply become dead code.

The loop now goes at the **end of mount_writable()'s own body**, before its closing brace, found by
matching braces rather than by a literal anchor: CI builds a fresh clone of the upstream repository,
so an anchor that exists only in a local copy would MISS there and fail the build. Whatever runs
after the call cannot skip a statement inside the function.

    python3 ci/patch-init-hfsrepeat.py <userland tree>
"""
import sys
from pathlib import Path

MARK = "HAKIOS-HFSREPEAT"
FUNCTION = "mount_writable(void)"

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


def blank(text):
    """The same text with comments and literals replaced by spaces: indices still line up, so a
    brace found in this is a brace of the code."""
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
        elif c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = " "
            i = j
        elif c in "\"'":
            q, j = c, i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == q:
                    j += 1
                    break
                j += 1
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
        else:
            i += 1
    return "".join(out)


def closing_brace(text, after):
    """The index of the brace that closes the block opened at or after `after`."""
    skeleton = blank(text)
    open_at = skeleton.find("{", after)
    if open_at < 0:
        return -1
    depth = 0
    for i in range(open_at, len(skeleton)):
        if skeleton[i] == "{":
            depth += 1
        elif skeleton[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1


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
    at = text.find(FUNCTION)
    if at < 0:
        return fail(f"{FUNCTION} not found")
    close = closing_brace(text, at)
    if close < 0:
        return fail(f"the body of {FUNCTION} could not be followed to its closing brace")

    text = text[:close] + LOOP + text[close:]
    candidate.write_text(text, encoding="utf-8", errors="surrogateescape")
    line = text[:close].count("\n") + 1
    print(f"patch-init-hfsrepeat: the verdict loop is inside mount_writable(), before its brace at line {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
