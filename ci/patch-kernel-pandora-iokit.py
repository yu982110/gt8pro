#!/usr/bin/env python3
"""patch-kernel-pandora-iokit.py -- the Adreno 840 user client for this kernel's IOKit.

Installs data-a-live's PandoraAdrenoGpu.cpp (NativeAdreno's user-client ABI v1, what Turnip's
Darwin backend opens) and wires it the way the reference port does (xnu patch 0177):

  iokit/Kernel/arm/PandoraAdrenoGpu.cpp     the client (talks to bsd/dev/pandora_gpu.c's
                                            exported pandora_gpu_* functions, level 14)
  iokit/Kernel/arm/NativeAdrenoABIv1.h      the ABI header
  iokit/conf/files.arm64                    + one line, optional iokitcpp
  iokit/KernelConfigTables.cpp              + a match entry: IOClass PandoraAdrenoGpu on
                                            IOResources, match category PandoraAdrenoGpu

Boot-arg pandora_gpu_metal=1 publishes a second service (AGXMetalG14X, for NativeMetal); without
it only PandoraAdrenoGpu exists, which is what Turnip takes
(TU_DARWIN_SERVICE=PandoraAdrenoGpu TU_DARWIN_OPEN_TYPE=0x4147).

    python3 ci/patch-kernel-pandora-iokit.py <hakios tree>
"""
import pathlib
import re
import sys

MARK = "HAKIOS-PANDORA-IOKIT"
HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE / "iokit" / "PandoraAdrenoGpu.cpp"
HDR = HERE / "iokit" / "NativeAdrenoABIv1.h"

CONF_LINE = "iokit/Kernel/arm/PandoraAdrenoGpu.cpp                           optional iokitcpp"
CONF_ANCHORS = [
    "iokit/Kernel/arm/SweetFramebuffer.cpp",
    "iokit/Kernel/arm/FuxiAdrenoUserClient.cpp",
]
CONF_TABLE = "iokit/KernelConfigTables.cpp"
# Appending after the LAST entry means the comma goes in front and the closing brace has none --
# the same form patch-kernel-hid-config.py uses ("   ,{" ... "   }").  A leading "{" with no comma
# makes the whole table invalid JSON, and IOCatalogue then panics at IOCatalogue.cpp:121
# ("handshake structure not initialized"), which is exactly what happened twice.
TABLE_CLOSE = re.compile(r'^[ \t]*"\)";[ \t]*$', re.MULTILINE)
TABLE_ADD = ("    \"   ,{\"\n"
             "    \"     'IOClass'         = PandoraAdrenoGpu;\"\n"
             "    \"     'IOProviderClass' = IOResources;\"\n"
             "    \"     'IOMatchCategory' = PandoraAdrenoGpu;\"\n"
             "    \"   }\"\n")

# This port does not probe the config table's personalities at all -- the display and HID
# services are "started by hand" from GenericARMPE.cpp for the same reason -- so the user client
# gets the same treatment: a helper that allocates, attaches and starts it, called from
# GenericARMPE right after the HID services.
HELPER_MARK = "HAKIOS-PANDORA-ADRENO-START"
HELPER = """

/* %MARK%: start the Adreno user client by hand.  Its personality in
 * iokit/KernelConfigTables.cpp is never probed on this kernel (the display and HID services are
 * started by hand for the same reason), so without this call the service that Turnip opens
 * would simply not exist. */
extern "C" void loader_log_line(const char *line);

extern "C" void
pandora_adreno_start(IOService *provider)
{
	PandoraAdrenoGpu *svc = OSTypeAlloc(PandoraAdrenoGpu);

	if (svc == nullptr) {
		loader_log_line("HAKIOS-GPU: PandoraAdrenoGpu out of memory\\n");
	} else if (!svc->init() || !svc->attach(provider)) {
		loader_log_line("HAKIOS-GPU: PandoraAdrenoGpu init/attach failed\\n");
	} else if (!svc->start(provider)) {
		loader_log_line("HAKIOS-GPU: PandoraAdrenoGpu start failed\\n");
	} else {
		loader_log_line("HAKIOS-GPU: PandoraAdrenoGpu started by hand\\n");
	}

	AGXAccelerator *acc = OSTypeAlloc(AGXAccelerator);

	if (acc == nullptr) {
		loader_log_line("HAKIOS-GPU: AGXAccelerator out of memory\\n");
	} else if (!acc->init() || !acc->attach(provider)) {
		loader_log_line("HAKIOS-GPU: AGXAccelerator init/attach failed\\n");
	} else if (!acc->start(provider)) {
		/* expected without pandora_gpu_metal=1: it publishes only for NativeMetal */
		loader_log_line("HAKIOS-GPU: AGXAccelerator not started (pandora_gpu_metal=0)\\n");
	} else {
		loader_log_line("HAKIOS-GPU: AGXAccelerator started by hand\\n");
	}
}
""".replace("%MARK%", HELPER_MARK)

PE_ANCHOR = "    sweet_hid_start(IOService::getResourceService());\n"
# The declaration has to be at file scope (an `extern "C"` declaration inside a function body is
# not valid C++: "expected unqualified-id").  patch-kernel-hid-start.py leaves its declaration
# beside the display one, so this one sits beside the HID one.
PE_DECL_ANCHOR = 'extern "C" void sweet_hid_start(IOService *provider);\n'
PE_DECL = ("\n/* " + HELPER_MARK + " */\n"
           "extern \"C\" void pandora_adreno_start(IOService *provider);\n")
PE_ADD = ("    /* " + HELPER_MARK + ": the Adreno user client, which Turnip opens as\n"
          "     * TU_DARWIN_SERVICE=PandoraAdrenoGpu / TU_DARWIN_OPEN_TYPE=0x4147. */\n"
          "    pandora_adreno_start(IOService::getResourceService());\n")


def main(root):
    root = pathlib.Path(root)
    dst = root / "iokit/Kernel/arm/PandoraAdrenoGpu.cpp"
    hdr = root / "iokit/Kernel/arm/NativeAdrenoABIv1.h"
    conf = root / "iokit/conf/files.arm64"
    table = root / CONF_TABLE

    for p in (SRC, HDR):
        if not p.exists():
            print("patch-pandora-iokit: %s missing (run tools/port-iokit.sh)" % p)
            return 1
    for p in (conf, table):
        if not p.exists():
            print("patch-pandora-iokit: %s not found" % p)
            return 1

    dst.parent.mkdir(parents=True, exist_ok=True)
    body = SRC.read_text(encoding="utf-8")
    if MARK not in body:
        body = "/* %s */\n" % MARK + body
    dst.write_text(body, encoding="utf-8")
    hdr.write_text(HDR.read_text(encoding="utf-8"), encoding="utf-8")
    print("patch-pandora-iokit: iokit/Kernel/arm/PandoraAdrenoGpu.cpp (%d lines)"
          % len(body.splitlines()))

    t = conf.read_text(errors="surrogateescape")
    if "PandoraAdrenoGpu.cpp" not in t:
        done = False
        for anchor in CONF_ANCHORS:
            if anchor in t:
                # put it right after the last line that starts with that anchor
                idx = t.index(anchor)
                eol = t.index("\n", idx)
                t = t[:eol + 1] + CONF_LINE + "\n" + t[eol + 1:]
                done = True
                break
        if not done:
            t = t.rstrip("\n") + "\n" + CONF_LINE + "\n"
        conf.write_text(t, errors="surrogateescape")
        print("patch-pandora-iokit: iokit/conf/files.arm64 += PandoraAdrenoGpu.cpp")
    else:
        print("patch-pandora-iokit: iokit/conf/files.arm64 already lists it")

    t = table.read_text(errors="surrogateescape")
    if "PandoraAdrenoGpu" in t:
        print("patch-pandora-iokit: KernelConfigTables.cpp already has the entry")
    else:
        matches = list(TABLE_CLOSE.finditer(t))
        if not matches:
            print('patch-pandora-iokit: MISS the closing ")"; in KernelConfigTables.cpp')
            return 1
        at = matches[-1].start()
        t = t[:at] + TABLE_ADD + t[at:]
        table.write_text(t, errors="surrogateescape")
        print("patch-pandora-iokit: KernelConfigTables.cpp += PandoraAdrenoGpu on IOResources")

    # 4. and start it by hand, the way this port starts the display and HID services
    g = dst.read_text(encoding="utf-8")
    if HELPER_MARK in g:
        print("patch-pandora-iokit: PandoraAdrenoGpu.cpp already has the start helper")
    else:
        at = g.rfind("#endif")
        if at < 0:
            print("patch-pandora-iokit: MISS the trailing #endif in PandoraAdrenoGpu.cpp")
            return 1
        g = g[:at] + HELPER + g[at:]
        dst.write_text(g, encoding="utf-8")
        print("patch-pandora-iokit: PandoraAdrenoGpu.cpp += pandora_adreno_start()")
    pe = root / "iokit/Kernel/GenericARMPE.cpp"
    if not pe.exists():
        print("patch-pandora-iokit: MISS iokit/Kernel/GenericARMPE.cpp")
        return 1
    p = pe.read_text(errors="surrogateescape")
    if "pandora_adreno_start(IOService::getResourceService())" in p:
        print("patch-pandora-iokit: GenericARMPE.cpp already starts it")
    elif PE_ANCHOR not in p:
        print("patch-pandora-iokit: MISS the HID start call in GenericARMPE.cpp "
              "(the hid-start step has to run first)")
        return 1
    else:
        if PE_DECL_ANCHOR in p:
            p = p.replace(PE_DECL_ANCHOR, PE_DECL_ANCHOR + PE_DECL, 1)
        elif PE_DECL.strip() not in p:
            print("patch-pandora-iokit: MISS the HID declaration in GenericARMPE.cpp "
                  "for the file-scope declaration")
            return 1
        p = p.replace(PE_ANCHOR, PE_ANCHOR + PE_ADD, 1)
        pe.write_text(p, errors="surrogateescape")
        print("patch-pandora-iokit: GenericARMPE.cpp calls pandora_adreno_start()")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
