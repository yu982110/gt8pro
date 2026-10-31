#!/usr/bin/env python3
"""给 hakios 的 loader 打 lafa 平台补丁（CI 里在 `make loader-open` 之前跑）。

两处改动：
1. 新增 `platform_lafa`：GIC 0x17000000/0x17080000、8 核、MPIDR 第 8 位（值来自手机设备树）。
2. 选平台：GOP 的 framebuffer 是 0xFC800000（lafa 的 splash_region）时用 lafa。
3. 没有 payload 时不再 `reboot()`，而是停住 —— 否则横幅一闪就重启，看不到。

    python3 ci/patch-loader-lafa.py hakios

幂等。
"""
import sys
from pathlib import Path

MARK = "platform_lafa"

PLATFORM = '''
static const struct platform platform_lafa = {
	/* SM8850 "canoe": 6 performance + 2 prime Oryon cores, no little core.
	 * GIC and cores from the phone's own device tree (docs/lafa-dt.md). */
	.name = "lafa", .io_base = 0x00400000, .io_size = 0x3FC00000,
	.gic_dist = 0x17000000, .gic_redist = 0x17080000, .uart = 0, .log_other_cpu = 1,
	.cores = 8, .core_shift = 8,
};
'''

OLD_SELECT = """\t\tif (SCREEN.base == 0xE1000000ULL) {
\t\t\tPLAT = &platform_redwood;
\t\t} else {
\t\t\tramfb_phone_screen();
\t\t}"""

NEW_SELECT = """\t\tif (SCREEN.base == 0xE1000000ULL) {
\t\t\tPLAT = &platform_redwood;
\t\t} else if (SCREEN.base == 0xFC800000ULL) {
\t\t\tPLAT = &platform_lafa;
\t\t} else {
\t\t\tramfb_phone_screen();
\t\t}"""

OLD_TAIL = """\tif (have_payload) {
\t\trun_kernel(0, 0);
\t}
\treboot();"""

NEW_TAIL = """\tif (have_payload) {
\t\trun_kernel(0, 0);
\t}
\tprint("no payload: holding\\n");
\tfor (;;) {
\t\t__asm__ volatile("wfi");
\t}
\treboot();"""


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "hakios")
    src = root / "xnuloader" / "loader.c"
    text = src.read_text(encoding="utf-8")
    if MARK in text:
        print("patch-loader: already patched")
        return 0

    anchor = "\t.small_cores = 0x0f,\n};\n"
    if anchor not in text:
        print("patch-loader: redwood platform anchor not found", file=sys.stderr)
        return 1
    text = text.replace(anchor, anchor + PLATFORM, 1)

    if OLD_SELECT not in text:
        print("patch-loader: GOP selection snippet not found", file=sys.stderr)
        return 1
    text = text.replace(OLD_SELECT, NEW_SELECT, 1)

    if OLD_TAIL not in text:
        print("patch-loader: tail snippet not found", file=sys.stderr)
        return 1
    text = text.replace(OLD_TAIL, NEW_TAIL, 1)

    src.write_text(text, encoding="utf-8")
    print("patch-loader: platform_lafa added, GOP selection and no-payload hold patched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
