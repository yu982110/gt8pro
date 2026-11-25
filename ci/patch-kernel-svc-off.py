#!/usr/bin/env python3
"""HAKIOS-SVC-OFF: hakios_services=0 skips the by-hand display/HID/GPU starts.

The port starts these services "by hand" from GenericARMPE.cpp at mountroot time and they run
unconditionally (pandora_gpu=0 and sweetfb_auto=0 only change what the drivers do inside, not
whether they start -- the r2 logs still show "SWEET FB started by hand" and "PandoraAdrenoGpu
started by hand").  So a boot could not yet answer "does the kernel reset even with no display,
HID or GPU service running?".

This adds one runtime switch around the four calls:

    hakios_services=0   -> skip sweet_surface_start / sweet_framebuffer_start / sweet_hid_start /
                           pandora_adreno_start
    (absent or 1)       -> normal

Must run after patch-kernel-display-start.py, patch-kernel-hid-start.py and
patch-kernel-pandora-iokit.py (it needs their call lines).

Idempotent; exits 0 when already applied, 1 when the anchor is gone.

    python3 ci/patch-kernel-svc-off.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-SVC-OFF"

HELPER = (
    "\n/* " + MARK + ": one switch for the by-hand service starts. */\n"
    "static int\n"
    "hakios_svc_on(void)\n"
    "{\n"
    "\tuint32_t lafa_svc = 1;\n"
    "\n"
    "\tPE_parse_boot_argn(\"hakios_services\", &lafa_svc, sizeof(lafa_svc));\n"
    "\treturn lafa_svc != 0;\n"
    "}\n"
)

ANCHOR = 'extern "C" void sweet_hid_start(IOService *provider);\n'

CALLS = [
    "    sweet_surface_start(this);\n",
    "    sweet_framebuffer_start(this);\n",
    "    sweet_hid_start(IOService::getResourceService());\n",
    "    pandora_adreno_start(IOService::getResourceService());\n",
]


def main() -> int:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    f = next(root.rglob("pe/arm/GenericARMPE.cpp"), None)
    if f is None:
        print("patch-svc-off: MISS GenericARMPE.cpp")
        return 1
    text = f.read_text(encoding="utf-8", errors="replace")
    if MARK in text:
        print("patch-svc-off: already applied")
        return 0
    if ANCHOR not in text:
        print("patch-svc-off: MISS sweet_hid_start declaration")
        return 1
    text = text.replace(ANCHOR, ANCHOR + HELPER, 1)
    n = 0
    for call in CALLS:
        if call in text:
            indented = call.replace("\n", "")
            text = text.replace(call, "    if (hakios_svc_on()) { " + indented.strip() + " }\n", 1)
            n += 1
    f.write_text(text, encoding="utf-8")
    print("patch-svc-off: installed, %d of %d calls guarded" % (n, len(CALLS)))
    return 0 if n > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
