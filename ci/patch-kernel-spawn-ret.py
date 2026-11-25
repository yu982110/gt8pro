#!/usr/bin/env python3
"""HAKIOS-SPAWN-RET: number every return EPERM inside exec_validate_spawnattr_policy().

The DRIVER entitlement check is already short-circuited (patch-kernel-spawndriver.py), yet
launchd's spawns still fail with EPERM and the exec probe's "site1" line is unconditional (it
prints before the check, so it does not say which branch refused).

This patch replaces every `return EPERM;` in exec_validate_spawnattr_policy() with a numbered
`loader_log_line("HAKIOS-SPAWN-RET: N\\n");` before the return, so one boot names the branch.

Must run after patch-kernel-spawndriver.py.  Idempotent.

    python3 ci/patch-kernel-spawn-ret.py <xnu tree>
"""
import pathlib
import re
import sys

MARK = "HAKIOS-SPAWN-RET"
FUNC = re.compile(r"exec_validate_spawnattr_policy\s*\([^)]*\)\s*\n\{\n", re.S)


def main() -> int:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    f = next(root.rglob("bsd/kern/kern_exec.c"), None)
    if f is None:
        print("patch-spawn-ret: MISS kern_exec.c")
        return 1
    text = f.read_text(encoding="utf-8", errors="replace")
    if MARK in text:
        print("patch-spawn-ret: already applied")
        return 0
    m = FUNC.search(text)
    if m is None:
        print("patch-spawn-ret: MISS exec_validate_spawnattr_policy")
        return 1
    start = m.end()
    # the function body: up to the line "}" at column 0
    end = text.find("\n}\n", start)
    if end < 0:
        print("patch-spawn-ret: MISS function end")
        return 1
    body = text[start:end]
    n = 0
    out = []
    for line in body.split("\n"):
        stripped = line.strip()
        if stripped.startswith("return EPERM"):
            indent = line[: len(line) - len(line.lstrip())]
            n += 1
            out.append('%sloader_log_line("HAKIOS-SPAWN-RET: %d\\n");' % (indent, n))
        out.append(line)
    text = text[:start] + "\n".join(out) + text[end:]
    f.write_text(text, encoding="utf-8")
    print("patch-spawn-ret: numbered %d EPERM returns in exec_validate_spawnattr_policy" % n)
    return 0 if n else 1


if __name__ == "__main__":
    sys.exit(main())
