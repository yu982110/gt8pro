#!/usr/bin/env python3
"""patch-kernel-ufsrw-probe.py -- print the ufsrw boot-arg and the writable flag it sets.

/private/var refuses the HFS+ volume with EROFS, and the only EROFS left in sweet_ufs.c is
"device opened for write while su.writable is false" (d_open/strategy). su.writable is set from
PE_parse_boot_argn("ufsrw", ...) in sweet_ufs_init. Boot with ufsrw=1 goes in, so the question is
what the parser actually saw. This prints both values once, at driver init.

    python3 ci/patch-kernel-ufsrw-probe.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-UFSRW-PROBE"

ANCHOR = """	su.writable = (rw != 0);
"""

NEW = ANCHOR + """	/* HAKIOS-UFSRW-PROBE: what the parser saw, and what it made of it. */
	{
		uint32_t hakios_probe = 0;
		bool hakios_parsed =
		    PE_parse_boot_argn("ufsrw", &hakios_probe, sizeof(hakios_probe));

		printf("HAKIOS: ufsrw probe: parsed %d value %u writable %d\\n",
		    hakios_parsed, hakios_probe, su.writable);
	}
"""


def main(root):
    f = pathlib.Path(root) / "bsd/dev/sweet_ufs.c"
    if not f.exists():
        print("patch-ufsrw-probe: sweet_ufs.c not found")
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-ufsrw-probe: already applied")
        return 0
    if text.count(ANCHOR) != 1:
        print("patch-ufsrw-probe: MISS: the writable assignment appears %d times"
              % text.count(ANCHOR))
        return 1
    text = text.replace(ANCHOR, NEW, 1)
    f.write_text(text, errors="surrogateescape")
    print("patch-ufsrw-probe: prints the ufsrw boot-arg and the writable flag")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-kernel-ufsrw-probe.py <xnu tree>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
