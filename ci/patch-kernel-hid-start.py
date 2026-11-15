#!/usr/bin/env python3
"""HAKIOS-LAFA-HID-START: start the Sweet HID services by hand.

The HID family now compiles into the kernel (SweetHID.cpp / IOHIDEvent*.cpp), but its two
personalities in iokit/KernelConfigTables.cpp are never probed on this kernel -- the same
thing that happened to the display services, which is why GenericARMPE starts them by hand.

This patch adds one helper to iokit/Kernel/arm/hid/SweetHID.cpp:

    extern "C" void sweet_hid_start(IOService *provider);

which allocates, attaches and starts SweetHIDButtons and SweetHIDTouch, and calls it from
iokit/GenericARMPE.cpp right after the display services.  Each step logs a line so the
boot log says exactly how far it got.

    python3 ci/patch-kernel-hid-start.py <xnu source tree>
"""
import pathlib
import re
import sys

MARK = "HAKIOS-LAFA-HID-START"

HELPER = r'''

/* %MARK%: start the HID services by hand (the config table's personalities are not probed
 * here, exactly like the display services).  backboardd is the consumer: no HID objects,
 * nothing to serve, and SpringBoard waits forever. */
extern "C" void loader_log_line(const char *line);

extern "C" void
sweet_hid_start(IOService *provider)
{
	SweetHIDButtons *buttons = OSTypeAlloc(SweetHIDButtons);

	if (buttons == NULL) {
		loader_log_line("HAKIOS-HID: BUTTONS out of memory\n");
	} else if (!buttons->init() || !buttons->attach(provider)) {
		loader_log_line("HAKIOS-HID: BUTTONS init/attach failed\n");
	} else if (!buttons->start(provider)) {
		loader_log_line("HAKIOS-HID: BUTTONS start failed\n");
	} else {
		loader_log_line("HAKIOS-HID: BUTTONS started by hand\n");
	}

	SweetHIDTouch *touch = OSTypeAlloc(SweetHIDTouch);

	if (touch == NULL) {
		loader_log_line("HAKIOS-HID: TOUCH out of memory\n");
	} else if (!touch->init() || !touch->attach(provider)) {
		loader_log_line("HAKIOS-HID: TOUCH init/attach failed\n");
	} else if (!touch->start(provider)) {
		loader_log_line("HAKIOS-HID: TOUCH start failed\n");
	} else {
		loader_log_line("HAKIOS-HID: TOUCH started by hand\n");
	}
}
'''.replace("%MARK%", MARK)

CALLS = ("    /* " + MARK + ": backboardd needs the HID objects to exist */\n"
         "    sweet_hid_start(this);\n")

DECL = ("\n/* " + MARK + " */\n"
        "extern \"C\" void sweet_hid_start(IOService *provider);\n")

ANCHOR_CALLS = ("    /* HAKIOS-LAFA-DISPLAY-START: the services exist only if these run "
                "(see SweetSurface/SweetFramebuffer.cpp) */\n"
                "    sweet_surface_start(this);\n"
                "    sweet_framebuffer_start(this);\n")


def main():
    if len(sys.argv) < 2:
        print("usage: patch-kernel-hid-start.py <xnu tree>")
        return 2
    root = pathlib.Path(sys.argv[1])
    ok = 0

    hid = next(root.rglob("SweetHID.cpp"), None)
    if hid is None:
        print("patch-hidstart: MISS SweetHID.cpp (apply the HID patch first)")
        return 1
    text = hid.read_text(encoding="utf-8", errors="replace")
    if MARK in text:
        print("patch-hidstart: SweetHID.cpp already has the helper")
    else:
        text += HELPER
        hid.write_text(text, encoding="utf-8")
        print("patch-hidstart: sweet_hid_start() added to SweetHID.cpp")
    ok += 1

    gpe = next(root.rglob("GenericARMPE.cpp"), None)
    if gpe is None:
        print("patch-hidstart: MISS GenericARMPE.cpp")
        return 1
    text = gpe.read_text(encoding="utf-8", errors="replace")
    if "sweet_hid_start(this)" in text:
        print("patch-hidstart: GenericARMPE.cpp already calls sweet_hid_start()")
    elif ANCHOR_CALLS in text:
        text = text.replace(ANCHOR_CALLS, ANCHOR_CALLS + CALLS, 1)
        if "sweet_hid_start(IOService" not in text:
            # Put the declaration right after the display one: that spot is already past
            # the includes, so IOService is a known type there.  The call block itself is
            # inside a function, so the declaration must not go there.
            anchor = 'extern "C" void sweet_surface_start(IOService *provider);\n'
            i = text.find(anchor)
            if i >= 0:
                at = i + len(anchor)
                text = text[:at] + DECL + text[at:]
            else:
                print("patch-hidstart: no display declaration to anchor beside")
                return 1
        gpe.write_text(text, encoding="utf-8")
        print("patch-hidstart: GenericARMPE.cpp calls sweet_hid_start()")
    else:
        print("patch-hidstart: MISS the display-start call block in GenericARMPE.cpp")
        return 1
    ok += 1
    return 0 if ok == 2 else 1


if __name__ == "__main__":
    sys.exit(main())
