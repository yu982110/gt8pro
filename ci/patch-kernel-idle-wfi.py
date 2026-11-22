#!/usr/bin/env python3
"""HAKIOS-LAFA-IDLE: make machine_idle() a plain WFI instead of the port's deep idle.

Evidence (2026-11-22 14:38, gpu85 + flight recorder): with the logd crash chain blocked the
system settles, launchd enters sigsuspend, the loader's ALIVE telemetry stops at ~39-49 s,
and the machine hard-resets (PS_HOLD, "kernel crash Fulldump") ~15-20 s later.  The one run
that survived 202 s (gpu79) had a kernel that had already panicked and never reached idle.
So the suspect is the first idle entry: machine_idle() -> wfe_process_recommendation() /
Idle_context() (power collapse).

This patch replaces that block with a plain `wfi` (shallow idle, no WFE deadline, no
Idle_context), so one boot says whether the deep-idle path is what trips the platform.

Idempotent; exits 0 when already applied, 1 when no anchor is found.

    python3 ci/patch-kernel-idle-wfi.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-LAFA-IDLE"

BLOCK = (
    "\tif (wfe_process_recommendation() == false) {\n"
    "\t\t/* If WFE recommendation absent, or WFE deadline\n"
    "\t\t * arrived with no interrupt pending/processed,\n"
    "\t\t * fall back to WFI.\n"
    "\t\t */\n"
    "\t\tIdle_context();\n"
    "\t}\n"
)

REPL = (
    "\t/* " + MARK + ": plain WFI, no WFE deadline and no Idle_context (power collapse).\n"
    "\t * The machine hard-resets the first time it goes idle; this keeps the core in a\n"
    "\t * shallow WFI so the A/B says whether the deep-idle path is what trips it. */\n"
    "\t__asm__ volatile(\"wfi\" ::: \"memory\");\n"
)


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: patch-kernel-idle-wfi.py <xnu tree>")
        return 2
    root = pathlib.Path(sys.argv[1])
    f = next(root.rglob("osfmk/arm64/machine_routines.c"), None)
    if f is None:
        print("patch-idle-wfi: MISS osfmk/arm64/machine_routines.c")
        return 1
    text = f.read_text(encoding="utf-8", errors="replace")
    if MARK in text:
        print("patch-idle-wfi: already applied")
        return 0
    if BLOCK not in text:
        # fallback: replace just the call, keep the surrounding comments
        if "\t\tIdle_context();\n" not in text:
            print("patch-idle-wfi: MISS machine_idle body")
            return 1
        text = text.replace("\t\tIdle_context();\n",
                            "\t\t/* " + MARK + " */\n\t\t__asm__ volatile(\"wfi\" ::: \"memory\");\n", 1)
        f.write_text(text, encoding="utf-8")
        print("patch-idle-wfi: Idle_context call replaced with wfi (fallback anchor)")
        return 0
    text = text.replace(BLOCK, REPL, 1)
    f.write_text(text, encoding="utf-8")
    print("patch-idle-wfi: machine_idle body replaced with wfi")
    return 0


if __name__ == "__main__":
    sys.exit(main())
