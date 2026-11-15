#!/usr/bin/env python3
"""HAKIOS-LAFA-HIDCFG: wire the Sweet HID sources into the xnu build.

0058-sweet-hid-event-services.patch ships the whole HID stack, but its two integration
hunks fail on our tree (both files were already touched by the display patches), and
`patch --forward` leaves the rest applied.  Without them the new .cpp files are never
compiled and the kernel has no HID family, so backboardd has nothing to serve.

This script re-applies exactly those two edits:
  1. iokit/conf/files.arm64      -- add the four hid sources
  2. iokit/KernelConfigTables.cpp -- add SweetHIDButtons / SweetHIDTouch personalities

Idempotent; exits 0 when already applied or when the anchors changed.
"""
import pathlib
import sys

FILES_MARK = "iokit/Kernel/arm/hid/SweetHID.cpp"
FILES_TAIL = "iokit/Kernel/arm/SweetFramebuffer.cpp"
FILES_ADD = (
    "iokit/Kernel/arm/hid/SweetHID.cpp                                optional iokitcpp\n"
    "iokit/Kernel/arm/hid/IOHIDEvent.cpp                              optional iokitcpp\n"
    "iokit/Kernel/arm/hid/IOHIDEventServiceQueue.cpp                  optional iokitcpp\n"
    "iokit/Kernel/arm/hid/IOHIDEventServiceUserClient.cpp             optional iokitcpp\n"
)

TBL_MARK = "SweetHIDButtons"
TBL_ANCHOR = (
    "    \"     'IONameMatch'     = 'sweet,display';\"\n"
    "    \"   },\"\n"
)
TBL_ADD = (
    "    \"   {\"\n"
    "    \"     'IOClass'         = SweetHIDButtons;\"\n"
    "    \"     'IOProviderClass' = IOResources;\"\n"
    "    \"     'IOMatchCategory' = SweetHIDButtons;\"\n"
    "    \"   },\"\n"
    "    \"   {\"\n"
    "    \"     'IOClass'         = SweetHIDTouch;\"\n"
    "    \"     'IOProviderClass' = IOResources;\"\n"
    "    \"     'IOMatchCategory' = SweetHIDTouch;\"\n"
    "    \"   },\"\n"
)


def main():
    if len(sys.argv) < 2:
        print("usage: patch-kernel-hid-config.py <xnu tree>")
        return 2
    root = pathlib.Path(sys.argv[1])
    hits = 0

    f = root / "iokit/conf/files.arm64"
    if f.exists():
        text = f.read_text(encoding="utf-8", errors="replace")
        if FILES_MARK in text:
            print("patch-hidcfg: files.arm64 already has the hid sources")
            hits += 1
        elif FILES_TAIL in text:
            idx = text.find(FILES_TAIL)
            eol = text.find("\n", idx) + 1
            text = text[:eol] + FILES_ADD + text[eol:]
            f.write_text(text, encoding="utf-8")
            print("patch-hidcfg: hid sources added to files.arm64")
            hits += 1
        else:
            print("patch-hidcfg: MISS anchor in iokit/conf/files.arm64")

    c = root / "iokit/KernelConfigTables.cpp"
    if c.exists():
        text = c.read_text(encoding="utf-8", errors="replace")
        if TBL_MARK in text:
            print("patch-hidcfg: KernelConfigTables already has the hid personalities")
            hits += 1
        elif TBL_ANCHOR in text:
            text = text.replace(TBL_ANCHOR, TBL_ANCHOR + TBL_ADD, 1)
            c.write_text(text, encoding="utf-8")
            print("patch-hidcfg: hid personalities added to KernelConfigTables.cpp")
            hits += 1
        else:
            print("patch-hidcfg: MISS anchor in iokit/KernelConfigTables.cpp")

    return 0 if hits >= 2 else 1


if __name__ == "__main__":
    sys.exit(main())
