#!/usr/bin/env python3
"""HAKIOS-LAFA-UFSHC-VER4: let this kernel's UFS host controller driver accept UFSHCI 4.0.

The phone's controller reports VER 0x00000400 (UFSHCI 4.0), and hakios.ufshc.c refuses anything
newer than 3.x:

    } else if (ver < 0x200 || ver >= 0x400) {
        no = "a controller version other than 2.x or 3.x";
    }

so ufs_native=1 never takes effect: the kernel keeps asking the loader for every block, and when
the loader is handed the controller and stops answering ("ufs: no answer from the loader to
request N in 20 s"), the storage dies -- which is what keeps killing the boot of the flash root.

UFS 4.0 (JESD223E) keeps the UTRL/UTRD/UTMRL layout the driver uses for reads (the driver only
ever issues whitelisted reads); the same-SoC reference runs its own driver on the same controller.
Accept 2.x..4.x and let the existing self-test (TEST UNIT READY, READ CAPACITY, a made-up
whitelist) prove whether it works: if the controller does not answer, the driver still says
"not used" and falls back to the loader.

Idempotent.

    python3 ci/patch-kernel-ufshc-ver4.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-LAFA-UFSHC-VER4"
OLD = "} else if (ver < 0x200 || ver >= 0x400) {"
NEW = "} else if (ver < 0x200 || ver >= 0x500) {   /* %s: UFSHCI 4.0 too */" % MARK


def find_driver(root: pathlib.Path):
    # the built driver is synced from hakios/drivers/storage/ (outside the xnu tree)
    for cand in (
        root.parent.parent.parent / "drivers/storage/hakios.ufshc.c",
        root.parent.parent / "drivers/storage/hakios.ufshc.c",
    ):
        if cand.exists():
            return cand
    return next(root.rglob("hakios.ufshc.c"), None)


def main() -> int:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    f = find_driver(root)
    if f is None:
        print("patch-ufshc-ver4: MISS hakios.ufshc.c")
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-ufshc-ver4: already applied")
        return 0
    if OLD not in text:
        print("patch-ufshc-ver4: MISS version check in %s" % f)
        return 1
    text = text.replace(OLD, NEW, 1)
    f.write_text(text, errors="surrogateescape")
    print("patch-ufshc-ver4: %s accepts UFSHCI 4.0 (ver < 0x500)" % f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
