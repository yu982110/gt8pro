#!/usr/bin/env python3
"""patch-kernel-display-start.py -- start Sweet's display services by hand.

ci/patch-kernel-display.py replaces the port's Mini display drivers with Sweet's and comments out the
two calls GenericARMPE made to start them:

    /* HAKIOS-LAFA-DISPLAY: the display services are SweetSurface / SweetFramebuffer now */
    /* MiniIOSurfaceStart(this); */
    /* MiniIOMobileFramebufferStart(this); */

Sweet relies on personalities in iokit/KernelConfigTables.cpp (IOClass = AppleCLCD,
IOProviderClass = IOResources) and this kernel never matches them: the boot log shows no
"SWEET SURF:" and no "SWEET FB:" line at all, so the IOMobileFramebuffer service does not exist and
nothing can open it.  The port's own Mini drivers never relied on that: MiniIOSurfaceStart()
allocates, attaches and starts the service itself.

Each class lives in its own file, so each file gets its own helper -- SweetSurface.cpp sees
IOCoreSurfaceRoot, SweetFramebuffer.cpp sees AppleCLCD -- and GenericARMPE.cpp calls both.

    python3 ci/patch-kernel-display-start.py <xnu source tree>
"""
import pathlib
import sys

MARK = "HAKIOS-LAFA-DISPLAY-START"

SURFACE_HELPER = r'''

/* %MARK%: start the surface root by hand (the config table's personalities are not probed here).
 * The IOLog line is the one to look for in the boot log: no line, no service. */
extern "C" void
sweet_surface_start(IOService *provider)
{
	IOCoreSurfaceRoot *root = OSTypeAlloc(IOCoreSurfaceRoot);

	if (root == NULL) {
		IOLog("SWEET SURF: out of memory\n");
	} else if (!root->init() || !root->attach(provider)) {
		IOLog("SWEET SURF: init/attach failed\n");
	} else if (!root->start(provider)) {
		IOLog("SWEET SURF: start failed\n");
	} else {
		IOLog("SWEET SURF: started by hand\n");
	}
}
'''.replace("%MARK%", MARK)

FB_HELPER = r'''

/* %MARK%: start the framebuffer service by hand (see SweetSurface.cpp for why). */
extern "C" void
sweet_framebuffer_start(IOService *provider)
{
	AppleCLCD *clcd = OSTypeAlloc(AppleCLCD);

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

CALLS = ("    /* " + MARK + ": the services exist only if these run (see SweetSurface/SweetFramebuffer.cpp) */\n"
         "    sweet_surface_start(this);\n"
         "    sweet_framebuffer_start(this);\n")

DECL = ("\n/* " + MARK + " */\n"
        "extern \"C\" void sweet_surface_start(IOService *provider);\n"
        "extern \"C\" void sweet_framebuffer_start(IOService *provider);\n")


def read(p):
    return p.read_text(encoding="utf-8", errors="surrogateescape")


def write(p, text):
    p.write_text(text, encoding="utf-8", errors="surrogateescape", newline="")


def append_helper(path, helper, label):
    text = read(path)
    if MARK in text:
        print(f"patch-display-start: {label} helper already there")
        return True
    write(path, text + helper)
    print(f"patch-display-start: {label} helper added")
    return True


def main(root):
    root = pathlib.Path(root)
    surf = root / "iokit" / "Kernel" / "SweetSurface.cpp"
    fb = root / "iokit" / "Kernel" / "SweetFramebuffer.cpp"
    pe = root / "iokit" / "Kernel" / "GenericARMPE.cpp"
    for p, what in ((surf, "SweetSurface.cpp"), (fb, "SweetFramebuffer.cpp"), (pe, "GenericARMPE.cpp")):
        if not p.is_file():
            print(f"patch-display-start: MISS: no {what} (run patch-kernel-display.py first)")
            return 1
    append_helper(surf, SURFACE_HELPER, "surface")
    append_helper(fb, FB_HELPER, "framebuffer")

    text = read(pe)
    if MARK in text:
        print("patch-display-start: the calls are already there")
        return 0
    if IOMFB_COMMENT not in text:
        print("patch-display-start: MISS: the commented Mini start is not in GenericARMPE.cpp")
        return 1
    at = text.find(IOMFB_COMMENT)
    end = text.find("\n", at) + 1
    text = text[:end] + CALLS + text[end:]
    last_inc = 0
    for line in text.split("\n"):
        if line.lstrip().startswith("#include"):
            last_inc = text.find(line) + len(line)
    if last_inc > 0:
        text = text[:last_inc] + "\n" + DECL + text[last_inc:]
    write(pe, text)
    print("patch-display-start: sweet_surface_start / sweet_framebuffer_start inserted at the PE init")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: patch-kernel-display-start.py <xnu source tree>")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
