#!/usr/bin/env python3
"""HAKIOS-FBFILL-KERNEL: paint the framebuffer from the kernel's display service.

The loader draws text on the panel, but the frame that counts is one the kernel's own display
stack puts there.  The port starts SweetFramebuffer (AppleCLCD) by hand at mountroot; this adds a
fill of the boot_args video framebuffer (PE_state.video) at the end of that start, so the panel
shows a solid colour that the kernel itself wrote.

Must run after patch-kernel-display-start.py (it needs sweet_framebuffer_start).  Idempotent.

    python3 ci/patch-kernel-fb-fill-kernel.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-FBFILL-KERNEL"

FILL = """
	/* %MARK%: the frame the host is waiting for, written by the kernel's display service. */
	if (PE_state.video.v_base != 0 && PE_state.video.v_height != 0 &&
	    PE_state.video.v_rowBytes >= PE_state.video.v_width * 4) {
		volatile uint32_t *lafa_fb = (volatile uint32_t *)PE_state.video.v_base;

		for (uint32_t lafa_y = 0; lafa_y < PE_state.video.v_height; lafa_y++) {
			volatile uint32_t *lafa_row = (volatile uint32_t *)
			    ((uintptr_t)lafa_fb + (uintptr_t)lafa_y * PE_state.video.v_rowBytes);

			for (uint32_t lafa_x = 0; lafa_x < PE_state.video.v_width; lafa_x++) {
				lafa_row[lafa_x] = 0x00ff00ff;   /* magenta, unmistakable */
			}
		}
		loader_log_line("HAKIOS-FBFILL-KERNEL: painted the framebuffer\\n");
	} else {
		loader_log_line("HAKIOS-FBFILL-KERNEL: no video framebuffer\\n");
	}
"""

ANCHOR = "extern \"C\" void\nsweet_framebuffer_start(IOService *provider)\n{\n"


def main() -> int:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    f = next(root.rglob("SweetFramebuffer.cpp"), None)
    if f is None:
        print("patch-fbfill-kernel: MISS SweetFramebuffer.cpp")
        return 1
    text = f.read_text(encoding="utf-8", errors="replace")
    if MARK in text:
        print("patch-fbfill-kernel: already applied")
        return 0
    if ANCHOR not in text:
        print("patch-fbfill-kernel: MISS sweet_framebuffer_start")
        return 1
    text = text.replace(ANCHOR, ANCHOR + FILL.replace("%MARK%", MARK), 1)
    f.write_text(text, encoding="utf-8")
    print("patch-fbfill-kernel: installed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
