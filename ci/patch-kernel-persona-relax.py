#!/usr/bin/env python3
"""HAKIOS-PERSONA-RELAX: let a spawner use the persona-management spawn attribute.

After patch-kernel-spawndriver.py the DRIVER entitlement check is gone; the only other
`return EPERM` in exec_validate_spawnattr_policy() is the persona-management check:

    if (!IOCurrentTaskHasEntitlement(PERSONA_MGMT_ENTITLEMENT)) {
        return EPERM;
    }

This kernel has no entitlement model (miniamfi), so this second check can never pass either.
Short-circuit it the same way (`0 && ...`) and name the change on the console once.

Idempotent; exits 0 when already applied, 1 when the anchor is gone.

    python3 ci/patch-kernel-persona-relax.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-LAFA-PERSONA-RELAX"

ANCHOR = "\tif (!IOCurrentTaskHasEntitlement( PERSONA_MGMT_ENTITLEMENT)) {\n"

REPLACEMENT = (
    "\t/* " + MARK + ": no entitlement model here, so the persona-management spawn\n"
    "\t * attribute cannot be granted -- allow it. */\n"
    "\tif (0 && !IOCurrentTaskHasEntitlement( PERSONA_MGMT_ENTITLEMENT)) {\n"
)


def main() -> int:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    f = next(root.rglob("bsd/kern/kern_exec.c"), None)
    if f is None:
        print("patch-persona-relax: MISS kern_exec.c")
        return 1
    text = f.read_text(encoding="utf-8", errors="replace")
    if MARK in text:
        print("patch-persona-relax: already applied")
        return 0
    n = text.count(ANCHOR)
    if n == 0:
        print("patch-persona-relax: MISS anchor")
        return 1
    text = text.replace(ANCHOR, REPLACEMENT)
    f.write_text(text, encoding="utf-8")
    print("patch-persona-relax: %d persona check(s) allowed" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
