#!/usr/bin/env python3
"""HAKIOS-LAFA-HIDCFG: wire the Sweet HID sources into the xnu build.

0058-sweet-hid-event-services.patch ships the whole HID stack, but its two integration
hunks fail on our tree (both files were already touched by the display patches), and
`patch --forward` leaves the rest applied.  Without them the new .cpp files are never
compiled, the config table never matches them, and the kernel has no HID family -- so
backboardd has nothing to serve and SpringBoard waits forever.

This script re-applies exactly those two edits the way the display patch does it:
  1. iokit/conf/files.arm64       -- append the four hid sources
  2. iokit/KernelConfigTables.cpp -- add SweetHIDButtons / SweetHIDTouch before the
                                     closing `)";` of gIOKernelConfigTables

Idempotent; exits 0 when already applied, 1 when a target cannot be patched.
"""
import pathlib
import re
import sys

MARK = "HAKIOS-LAFA-HIDCFG"

FILES_ADD = (
    "iokit/Kernel/arm/hid/SweetHID.cpp                                optional iokitcpp\n"
    "iokit/Kernel/arm/hid/IOHIDEvent.cpp                              optional iokitcpp\n"
    "iokit/Kernel/arm/hid/IOHIDEventServiceQueue.cpp                  optional iokitcpp\n"
    "iokit/Kernel/arm/hid/IOHIDEventServiceUserClient.cpp             optional iokitcpp\n"
)

TABLE_BLOCK = (
    "#if defined(__arm64__)\n"
    "    /* " + MARK + ": the HID event services (iokit/Kernel/arm/hid/SweetHID.cpp).\n"
    "     * backboardd serves HID; without this family it initialises and waits forever. */\n"
    '    "   ,{"\n'
    "    \"     'IOClass'         = SweetHIDButtons;\"\n"
    "    \"     'IOProviderClass' = IOResources;\"\n"
    "    \"     'IOMatchCategory' = SweetHIDButtons;\"\n"
    '    "   }"\n'
    '    "   ,{"\n'
    "    \"     'IOClass'         = SweetHIDTouch;\"\n"
    "    \"     'IOProviderClass' = IOResources;\"\n"
    "    \"     'IOMatchCategory' = SweetHIDTouch;\"\n"
    '    "   }"\n'
    "#endif\n"
)


def main():
    if len(sys.argv) < 2:
        print("usage: patch-kernel-hid-config.py <xnu tree>")
        return 2
    root = pathlib.Path(sys.argv[1])
    ok = 0

    f = root / "iokit/conf/files.arm64"
    if f.exists():
        text = f.read_text(encoding="utf-8", errors="replace")
        if "iokit/Kernel/arm/hid/SweetHID.cpp" in text:
            print("patch-hidcfg: files.arm64 already lists the hid sources")
        else:
            if not text.endswith("\n"):
                text += "\n"
            text += FILES_ADD
            f.write_text(text, encoding="utf-8")
            print("patch-hidcfg: four hid sources appended to files.arm64")
        ok += 1
    else:
        print("patch-hidcfg: MISS iokit/conf/files.arm64")

    c = root / "iokit/KernelConfigTables.cpp"
    if c.exists():
        text = c.read_text(encoding="utf-8", errors="replace")
        if MARK in text:
            print("patch-hidcfg: KernelConfigTables already has the hid personalities")
            ok += 1
        else:
            matches = list(re.finditer(r'^[ \t]*"\)";[ \t]*$', text, re.MULTILINE))
            if not matches:
                print('patch-hidcfg: MISS the closing ")"; in KernelConfigTables.cpp')
            else:
                at = matches[-1].start()
                text = text[:at] + TABLE_BLOCK + text[at:]
                c.write_text(text, encoding="utf-8")
                print("patch-hidcfg: SweetHIDButtons/SweetHIDTouch added to gIOKernelConfigTables")
                ok += 1
    else:
        print("patch-hidcfg: MISS iokit/KernelConfigTables.cpp")

    return 0 if ok == 2 else 1


if __name__ == "__main__":
    sys.exit(main())
