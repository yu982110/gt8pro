#!/usr/bin/env python3
"""patch-kernel-ufsrw-probe.py -- say why a write-open is refused, at the moment it is refused.

/private/var refuses the HFS+ volume with EROFS and the driver's only EROFS left is "device
opened for write while su.writable is false". A print at driver init is long gone by the time the
kernel log ring is read back (launchd writes thousands of lines), so the probes go where the
refusal happens: device open and strategy. Both run when init mounts the volume, late enough to
stay in the ring.

    python3 ci/patch-kernel-ufsrw-probe.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-UFSRW-PROBE"

INIT_ANCHOR = """	su.writable = (rw != 0);
"""

INIT_NEW = INIT_ANCHOR + """	/* HAKIOS-UFSRW-PROBE: what the parser saw, and what it made of it. */
	{
		uint32_t hakios_probe = 0;
		bool hakios_parsed =
		    PE_parse_boot_argn("ufsrw", &hakios_probe, sizeof(hakios_probe));

		printf("HAKIOS: ufsrw probe: parsed %d value %u writable %d\\n",
		    hakios_parsed, hakios_probe, su.writable);
	}
"""

OPEN_ANCHOR = """	return ((flags & FWRITE) && !su.writable) ? EROFS : 0;
"""

OPEN_NEW = """	if ((flags & FWRITE) && !su.writable) {
		uint32_t hakios_probe = 0;
		bool hakios_parsed =
		    PE_parse_boot_argn("ufsrw", &hakios_probe, sizeof(hakios_probe));

		/* HAKIOS-UFSRW-PROBE: why this open for write comes back EROFS */
		printf("HAKIOS: ufs open for write refused: writable %d, ufsrw parsed %d value %u\\n",
		    su.writable, hakios_parsed, hakios_probe);
		return EROFS;
	}
	return 0;
"""

STRAT_ANCHOR = """	if (!rd && !su.writable) {
		error = EROFS;
"""

STRAT_NEW = """	if (!rd && !su.writable) {
		/* HAKIOS-UFSRW-PROBE: a write reaches the strategy layer while not writable */
		printf("HAKIOS: ufs strategy: write refused, writable %d\\n", su.writable);
		error = EROFS;
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
    for anchor, new, what in ((INIT_ANCHOR, INIT_NEW, "init"),
                              (OPEN_ANCHOR, OPEN_NEW, "open"),
                              (STRAT_ANCHOR, STRAT_NEW, "strategy")):
        if text.count(anchor) != 1:
            print("patch-ufsrw-probe: MISS: %s anchor appears %d times"
                  % (what, text.count(anchor)))
            return 1
        text = text.replace(anchor, new, 1)
    f.write_text(text, errors="surrogateescape")
    print("patch-ufsrw-probe: prints at init, open-for-write and strategy")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-kernel-ufsrw-probe.py <xnu tree>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
