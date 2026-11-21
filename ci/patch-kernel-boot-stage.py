#!/usr/bin/env python3
"""A boot-stage word the loader can read, because this kernel cannot print.

Where we are: PE_kputc is 0 at runtime (the loader reads the pointer itself), the kernel's
log ring is empty, and no loader_checkpoint() in arm_init ever fires -- so the kernel stops
before arm_init's first checkpoint, i.e. inside arm_vm_init or earlier.  kprintf is the
thing being repaired, so it cannot be the thing that reports progress.

Upstream (the SM8850 port that boots iOS 16) paints stage numbers on the framebuffer with
sweet_early_mark() instead.  That needs a working framebuffer path, which is a bigger
change and would mix variables; this port instead puts a number in a plain word the loader
already reads every second, and lets the loader print it.  Difference from upstream, stated
on purpose: no framebuffer, no console, one new field, appended at the end of the struct
both sides already share.

    kernel: struct loader_live gets `uint64_t stage` at the end; loader_stage(n) sets it.
    loader: struct kernel_live gets the same field; the diag line prints it.
    instrumentation: loader_stage(N) after each existing loader_checkpoint(N) in arm_init,
    plus marks at the entry and exit of arm_vm_init and around the kprintf setup.

usage: patch-kernel-boot-stage.py <xnu source dir>
"""
import pathlib
import re
import sys

MARK = "HAKIOS-BOOT-STAGE"

# ------------------------------------------------------------------ kernel: loader_return.c
K_STRUCT_OLD = "\tconst uint64_t *sound;          /* loader_sound: the same for a recording of the sound */\n};\n"
K_STRUCT_NEW = """\tconst uint64_t *sound;          /* loader_sound: the same for a recording of the sound */
\tvolatile uint64_t stage;        /* HAKIOS-BOOT-STAGE: last mark loader_stage() set (see below) */
};
"""

K_FUNC_OLD = "/*\n * A screen recording (the display driver makes one with boot-arg rec_secs)"
K_FUNC_NEW = """/*
 * HAKIOS-BOOT-STAGE: the boot stage the kernel last reached.  Written with a plain store and
 * read by the loader every second; kprintf cannot report progress because PE_kputc is 0
 * until PE_init_kprintf runs, which is exactly what this is here to find.
 */
void
loader_stage(uint64_t n)
{
\tloader_live.stage = n;
}

/*
 * A screen recording (the display driver makes one with boot-arg rec_secs)"""

# ---------------------------------------------------------------- loader: struct + diag
L_STRUCT_OLD = "\tuint64_t sound;                 /* (a virtual address too) the same for a recording of the sound */\n"
L_STRUCT_NEW = """\tuint64_t sound;                 /* (a virtual address too) the same for a recording of the sound */
\tuint64_t stage;                 /* HAKIOS-BOOT-STAGE: the last mark the kernel set */
"""

L_DIAG_OLD = """			print("kputc diag: PE_kputc %x serial %x unbuffered %x kern_lo %x\\n","""
L_DIAG_NEW = """			print("stage diag: kernel stage %x\\n", (uint64_t)kernel_live->stage);
			print("kputc diag: PE_kputc %x serial %x unbuffered %x kern_lo %x\\n","""


def patch_kernel(root):
    rc = 0
    p = pathlib.Path(root) / "osfmk/arm64/loader_return.c"
    if not p.exists():
        print("patch-boot-stage: %s not found" % p)
        return 1
    t = p.read_text(errors="surrogateescape")
    if MARK in t:
        print("patch-boot-stage: loader_return.c already done")
    else:
        if K_STRUCT_OLD not in t:
            print("patch-boot-stage: MISS struct loader_live tail")
            rc = 1
        else:
            t = t.replace(K_STRUCT_OLD, K_STRUCT_NEW, 1)
            print("patch-boot-stage: loader_live.stage added")
        if K_FUNC_OLD not in t:
            print("patch-boot-stage: MISS loader_stage insertion point")
            rc = 1
        else:
            t = t.replace(K_FUNC_OLD, K_FUNC_NEW, 1)
            print("patch-boot-stage: loader_stage() added")
        p.write_text(t, errors="surrogateescape")
    return rc


def patch_loader(root):
    p = pathlib.Path(root) / "xnuloader/loader.c"
    if not p.exists():
        # the loader is not part of the kernel tree in CI; its half of this is applied to
        # the local xnuloader/loader.c and rebuilt with tools/rebuild-with-cmdline.sh
        print("patch-boot-stage: no xnuloader/loader.c here (fine: the loader is built locally)")
        return 0
    t = p.read_text(errors="surrogateescape")
    changed = 0
    for old, new, what in ((L_STRUCT_OLD, L_STRUCT_NEW, "struct kernel_live.stage"),
                           (L_DIAG_OLD, L_DIAG_NEW, "stage diag line")):
        if new in t:
            continue
        if old not in t:
            print("patch-boot-stage: MISS loader anchor: %s" % what)
            continue
        t = t.replace(old, new, 1)
        changed += 1
        print("patch-boot-stage: %s" % what)
    if changed:
        p.write_text(t, errors="surrogateescape")
    return 0


def instrument(root):
    """After every loader_checkpoint(N) in arm_init.c, set the stage word too."""
    p = pathlib.Path(root) / "osfmk/arm/arm_init.c"
    if not p.exists():
        print("patch-boot-stage: %s not found" % p)
        return 1
    t = p.read_text(errors="surrogateescape")
    if MARK in t:
        print("patch-boot-stage: arm_init.c already instrumented")
        return 0
    n_before = len(re.findall(r"loader_checkpoint\((\d+)\);", t))
    if "extern void loader_stage" not in t:
        m = re.search(r"^#include .*$", t, re.M)
        if m:
            t = t[:m.end()] + "\n/* HAKIOS-BOOT-STAGE: set by ci/patch-kernel-boot-stage.py */\nextern void loader_stage(uint64_t n);" + t[m.end():]
            print("patch-boot-stage: loader_stage() declared at file scope")
    t = re.sub(r"loader_checkpoint\((\d+)\);", r"loader_checkpoint(\1); loader_stage(\1);", t)
    # and around the kprintf setup, which is what PE_kputc depends on
    t = re.sub(r"\n(\t*kernel_startup_initialize_upto\(STARTUP_SUB_KPRINTF\);)\n",
               "\n\tloader_stage(200); /* HAKIOS-BOOT-STAGE: before the kprintf setup */\n\\1\n", t, count=1)
    t = re.sub(r"(\t*kprintf\(\"kprintf initialized\\\\n\"\);)\n",
               "\\1\n\tloader_stage(201); /* HAKIOS-BOOT-STAGE: after it */\n", t, count=1)
    if MARK not in t and "loader_stage(200)" not in t and "loader_stage(201)" not in t and n_before == 0:
        print("patch-boot-stage: MISS arm_init.c anchors (no checkpoints, no kprintf setup)")
        return 1
    t = t.replace("loader_checkpoint(", "loader_checkpoint(", 1)
    if MARK not in t:
        # put the marker in a comment at the top so a second run is a no-op
        t = t.replace('#include <pexpert/pexpert.h>', '#include <pexpert/pexpert.h>\n/* HAKIOS-BOOT-STAGE: see ci/patch-kernel-boot-stage.py */', 1)
    p.write_text(t, errors="surrogateescape")
    print("patch-boot-stage: %d checkpoints marked, kprintf marks around the setup" % n_before)
    return 0


def main(root):
    rc = 0
    rc |= patch_kernel(root)
    rc |= patch_loader(root)
    rc |= instrument(root)
    return rc


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
