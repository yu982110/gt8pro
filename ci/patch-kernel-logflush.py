#!/usr/bin/env python3
"""patch-kernel-logflush.py -- make the kernel's live log visible to the loader while it runs.

The loader reads the kernel's console log through the shared kernel_live structure, which
points at loader_log in the kernel's bss.  loader_return.c cleans that range to the point of
coherency, but only on the way out (loader_return): a running kernel's writes sit in its own
cache, so the loader's live drain sees nothing.  Flush once per kilobyte written -- cheap
enough for a log, and it makes 'oem logtail' and the loader's own screen drain show the boot
as it happens.

Anchor: the character store in loader_log_put_locked().
"""
import sys
import pathlib

MARK = "HAKIOS-LAFA-LOGFLUSH"


def patch(root):
    tree = pathlib.Path(root)
    f = tree / "osfmk/arm64/loader_return.c"
    if not f.exists():
        print(f"patch-logflush: {f} not found")
        return 1
    src = f.read_text(errors="surrogateescape")
    if MARK in src:
        print("patch-logflush: already applied")
        return 0

    anchor = "\t\t\tloader_log[loader_log_len++] = c;"
    if anchor not in src:
        # the tree indents differently; try the flat form
        anchor = "loader_log[loader_log_len++] = c;"
        if anchor not in src:
            print("patch-logflush: character store not found")
            return 1

    flush = '''
			/* HAKIOS-LAFA-LOGFLUSH: the loader reads this buffer through a different mapping;
			 * without a clean its live drain sees the kernel's cache instead of the text. Once
			 * per kilobyte is enough for a screen that updates a few times a second. */
			{
				static uint32_t logflush_at;
				if ((loader_log_len & 1023u) < (logflush_at & 1023u)) {
					for (uintptr_t p = (uintptr_t)loader_log & ~63UL;
					     p < (uintptr_t)loader_log + loader_log_len; p += 64) {
						__asm__ volatile("dc cvau, %0" :: "r"(p) : "memory");
					}
					__asm__ volatile("dsb ish" ::: "memory");
				}
				logflush_at = loader_log_len;
			}
'''
    # insert right after the store's closing brace line
    lines = src.split("\n")
    for i, line in enumerate(lines):
        if anchor.strip() in line:
            # find the end of this if-block (first line that is exactly a closing brace at the same depth)
            depth = line.count("{") - line.count("}")
            j = i + 1
            while j < len(lines) and depth > 0:
                depth += lines[j].count("{") - lines[j].count("}")
                if depth <= 0:
                    break
                j += 1
            lines.insert(j, flush)
            break
    else:
        print("patch-logflush: could not find the insertion point")
        return 1

    f.write_text("\n".join(lines), errors="surrogateescape")
    print("patch-logflush: live-log cache flush inserted")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-kernel-logflush.py <xnu tree>")
        raise SystemExit(2)
    raise SystemExit(patch(sys.argv[1]))
