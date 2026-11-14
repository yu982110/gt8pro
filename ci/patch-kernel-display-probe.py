#!/usr/bin/env python3
"""patch-kernel-display-probe.py -- make the display driver say who opens it and who presents.

ci/patch-kernel-display.py writes SweetFramebuffer.cpp (AppleCLCD, the IOMobileFramebuffer service).
Its messages go through IOLog, which never reaches the live ring this port logs to, so the boot log
cannot tell "nobody opened the framebuffer" from "opened but never presented a frame".

This inserts three loader_log_line() calls, which do reach the ring:

    HAKIOS-DISPLAY: AppleCLCD start
    HAKIOS-DISPLAY: newUserClient        <- someone opened the framebuffer
    HAKIOS-DISPLAY: lazyMap              <- the panel memory was mapped
    HAKIOS-DISPLAY: swap                 <- a frame was presented (the DPU probe follows this)

    python3 ci/patch-kernel-display-probe.py <xnu source tree>
"""
import pathlib
import sys

MARK = "HAKIOS-DISPLAY-PROBE"
FILE = "iokit/Kernel/SweetFramebuffer.cpp"

DECL = ('\n/* ' + MARK + ': loader_log_line reaches the live ring, IOLog does not. */\n'
        'extern "C" void loader_log_line(const char *line);\n')

INSERTS = [
    ("AppleCLCD::start(IOService *provider)", "HAKIOS-DISPLAY: AppleCLCD start\\n"),
    ("AppleCLCD::newUserClient(", "HAKIOS-DISPLAY: newUserClient\\n"),
    ("AppleCLCD::lazyMap(", "HAKIOS-DISPLAY: lazyMap\\n"),
    ("AppleCLCD::swap(const uint8_t *rec)", "HAKIOS-DISPLAY: swap\\n"),
]


def main(root):
    p = pathlib.Path(root) / FILE
    if not p.is_file():
        print("patch-display-probe: MISS: no", FILE, "(run patch-kernel-display.py first)")
        return 1
    text = p.read_text(encoding="utf-8", errors="surrogateescape")
    if MARK in text:
        print("patch-display-probe: already applied")
        return 0
    added = 0
    for anchor, line in INSERTS:
        i = text.find(anchor)
        if i < 0:
            print(f"patch-display-probe: MISS: anchor not found: {anchor}")
            continue
        # find the opening brace after the signature, then the end of that line
        brace = text.find("{", i)
        if brace < 0:
            continue
        nl = text.find("\n", brace)
        indent = "        "
        text = text[:nl + 1] + indent + 'loader_log_line("' + line + '");\n' + text[nl + 1:]
        added += 1
    if added == 0:
        return 1
    # the declaration goes after the last #include of the file
    last = 0
    for ln in text.split("\n"):
        if ln.lstrip().startswith("#include"):
            last = text.find(ln) + len(ln)
    if last > 0:
        text = text[:last] + "\n" + DECL + text[last:]
    p.write_text(text, encoding="utf-8", errors="surrogateescape", newline="")
    print(f"patch-display-probe: {added} probes inserted into {FILE}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: patch-kernel-display-probe.py <xnu source tree>")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
