#!/usr/bin/env python3
"""HAKIOS-NOSWAP: take the whole display-present path out, to find out whether it is what kills
the machine at 60-90 s.

The runs die with no panic at the moment backboardd first presents: on the boot that showed it,
the screen's last line was the kernel console being paused by AppleCLCD::swap() (see the comment
in that function), i.e. the first swap.  Everything inside swap() has been turned off one at a
time (hakios_dpu_probe=0, hakios_scanout=0, sweetfb_console=1) and the machine still went at the
same second, but the CPU copies, scanPanel() and the property publishes were always still there.

This makes swap() return before any of it.  The panel keeps whatever the loader last drew, the
clients keep their notifications, and if the machine now lives past 120 s, the present path is
the trigger; if it still stops at ~76 s, the present path is not it.

Idempotent; needs patch-kernel-display.py applied first (swap() lives in that patch).

    python3 ci/patch-kernel-display-noswap.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-NOSWAP"
ANCHOR = "AppleCLCD::swap(const uint8_t *rec)\n{\n"
INSERT = (
    "\t/* " + MARK + ": the whole present path is off for this test (see the patch header). */\n"
    "\treturn;\n"
)


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: patch-kernel-display-noswap.py <xnu tree>")
        return 2
    root = pathlib.Path(sys.argv[1])
    hits = 0
    for name in ("SweetFramebuffer.cpp", "AppleCLCD.cpp"):
        f = next(root.rglob(name), None)
        if f is None:
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        if MARK in text:
            hits += 1
            continue
        if ANCHOR not in text:
            continue
        text = text.replace(ANCHOR, ANCHOR + INSERT, 1)
        f.write_text(text, encoding="utf-8")
        hits += 1
        print("patch-display-noswap: swap() disabled in " + str(f.relative_to(root)))
    if hits == 0:
        print("patch-display-noswap: MISS AppleCLCD::swap")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
