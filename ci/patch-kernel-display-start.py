#!/usr/bin/env python3
"""patch-kernel-display-start.py -- start Sweet's display services by hand.

ci/patch-kernel-display.py replaces the port's Mini display drivers with Sweet's and comments out the
two calls GenericARMPE made to start them:

    /* HAKIOS-LAFA-DISPLAY: the display services are SweetSurface / SweetFramebuffer now */
    /* MiniIOSurfaceStart(this); */
    /* MiniIOMobileFramebufferStart(this); */

Sweet relies on personalities in iokit/KernelConfigTables.cpp (IOClass = AppleCLCD,
IOProviderClass = IOResources) and this kernel never matches them: the boot log shows no
"SWEET SURF: IOCoreSurfaceRoot registered" and no "SWEET FB:" line at all, so the IOMobileFramebuffer
service does not exist and nothing can open it.  The port's own Mini drivers never relied on that
either -- MiniIOSurfaceStart() allocates, attaches and starts the service itself.

This patch puts that pattern back, next to where the two calls were commented out, and adds the
helper to SweetFramebuffer.cpp (which sees both class definitions).

    python3 ci/patch-kernel-display-start.py <xnu source tree>
"""
import pathlib
import sys

MARK = "HAKIOS-LAFA-DISPLAY-START"

HELPER = r'''

/* %MARK%: the config table's personalities are not probed on this kernel, so the two services are
 * created and started by hand, the way MiniIOSurfaceStart()/MiniIOMobileFramebufferStart() did it.
 * Each IOLog below is the line to look for in the boot log: no line, no service. */
extern "C" void
sweet_display_start(IOService *provider)
{
	IOCoreSurfaceRoot *surf;
	AppleCLCD *clcd;

	surf = OSTypeAlloc(IOCoreSurfaceRoot);
	if (surf == NULL) {
		IOLog("SWEET SURF: out of memory\n");
	} else if (!surf->init() || !surf->attach(provider)) {
		IOLog("SWEET SURF: init/attach failed\n");
	} else if (!surf->start(provider)) {
		IOLog("SWEET SURF: start failed\n");
	} else {
		IOLog("SWEET SURF: started by hand\n");
	}

	clcd = OSTypeAlloc(AppleCLCD);
	if (clcd == NULL) {
		IOLog("SWEET FB: out of memory\n");
	} else if (!clcd->init() || !clcd->attach(provider)) {
		IOLog("SWEET FB: init/attach failed\n");
	} else if (!clcd->start(provider)) {
		IOLog("SWEET FB: start failed\n");
	} else {
		IOLog("SWEET FB: started by hand\n");
	}
}
'''.replace("%MARK%", MARK)

IOMFB_COMMENT = "/* MiniIOMobileFramebufferStart(this); */"
IOMFB_ANCHOR = "/* HAKIOS-LAFA-DISPLAY: the display services are SweetSurface / SweetFramebuffer now */"

CALL = ("    /* " + MARK + ": the services exist only if this runs (see SweetFramebuffer.cpp) */\n"
        "    sweet_display_start(this);\n")

DECL = ("\n/* " + MARK + " */\nextern \"C\" void sweet_display_start(IOService *provider);\n")


def read(p):
    return p.read_text(encoding="utf-8", errors="surrogateescape")


def write(p, text):
    p.write_text(text, encoding="utf-8", errors="surrogateescape", newline="")


def main(root):
    root = pathlib.Path(root)
    fb = root / "iokit" / "Kernel" / "SweetFramebuffer.cpp"
    pe = root / "iokit" / "Kernel" / "GenericARMPE.cpp"
    if not fb.is_file():
        print("patch-display-start: MISS: no SweetFramebuffer.cpp (run patch-kernel-display.py first)")
        return 1
    if not pe.is_file():
        print("patch-display-start: MISS: no GenericARMPE.cpp")
        return 1

    text = read(fb)
    if MARK not in text:
        if "#include \"SweetSurface.h\"" not in text:
            print("patch-display-start: MISS: SweetFramebuffer.cpp does not include SweetSurface.h")
            return 1
        write(fb, text + HELPER)
        print("patch-display-start: helper added to SweetFramebuffer.cpp")
    else:
        print("patch-display-start: helper already there")

    text = read(pe)
    if MARK in text:
        print("patch-display-start: call already there")
        return 0
    if IOMFB_COMMENT not in text:
        print("patch-display-start: MISS: the commented Mini start is not in GenericARMPE.cpp")
        return 1
    at = text.find(IOMFB_COMMENT)
    end = text.find("\n", at) + 1
    text = text[:end] + CALL + text[end:]
    # declaration: after the last #include at the top
    last_inc = 0
    for line in text.split("\n"):
        if line.lstrip().startswith("#include"):
            last_inc = text.find(line) + len(line)
    if last_inc > 0:
        text = text[:last_inc] + "\n" + DECL + text[last_inc:]
    write(pe, text)
    print("patch-display-start: sweet_display_start(this) inserted after the commented Mini start")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: patch-kernel-display-start.py <xnu source tree>")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
