#!/usr/bin/env python3
"""HAKIOS-LAFA-NOLOGD: refuse to exec logd while its crash chain freezes the kernel.

The freeze is reproducible every boot and always follows the same shape: logd dies with
SIGTRAP, launchd cleans the child up, and a few system calls later the kernel stops
(ticker included) for good.  proc_info() itself is clean (688 entry/done pairs), so the
remaining way to test the hypothesis without a debugger is to take logd out of the boot.

The executable path is only available where the image is set up, so the check sits at the
top of __mac_execve(): read uap->fname, take its basename, and return ENOENT for logd and
logd_helper.  launchd then sees the spawn fail (its own error path) instead of a crashing
child, and if the system survives past the usual ~60-90 s the crash chain is the trigger.

Remove this patch again once logd's own crash is fixed.

    python3 ci/patch-kernel-nologd.py <xnu tree>

Idempotent; exits 0 when already applied, 1 when the anchor is gone.
"""
import pathlib
import sys

MARK = "HAKIOS-LAFA-NOLOGD"
ANCHOR = (
    "int\n"
    "__mac_execve(proc_t p, struct __mac_execve_args *uap, int32_t *retval __unused)\n"
    "{\n"
)
BLOCK = (
    "\t/* " + MARK + ": keep logd out while its crash chain freezes the kernel.\n"
    "\t * Delete this block once logd's own crash is understood. */\n"
    "\t{\n"
    "\t\tchar lafa_nop[160];\n"
    "\t\tsize_t lafa_nopn = 0;\n"
    "\n"
    "\t\tif (copyinstr(uap->fname, lafa_nop, sizeof(lafa_nop), &lafa_nopn) == 0) {\n"
    "\t\t\tchar *lafa_base = lafa_nop;\n"
    "\t\t\tchar *lafa_c;\n"
    "\n"
    "\t\t\tfor (lafa_c = lafa_nop; *lafa_c != '\\0'; lafa_c++) {\n"
    "\t\t\t\tif (*lafa_c == '/') {\n"
    "\t\t\t\t\tlafa_base = lafa_c + 1;\n"
    "\t\t\t\t}\n"
    "\t\t\t}\n"
    "\t\t\tif (strcmp(lafa_base, \"logd\") == 0 ||\n"
    "\t\t\t    strcmp(lafa_base, \"logd_helper\") == 0) {\n"
    "\t\t\t\tloader_log_line(\"HAKIOS-NOLOGD: logd refused\\n\");\n"
    "\t\t\t\treturn ENOENT;\n"
    "\t\t\t}\n"
    "\t\t}\n"
    "\t}\n"
)
DECL = "extern void loader_log_line(const char *line);   /* " + MARK + " */\n"



# The daemons are not execve()'d by launchd -- xpcproxy posix_spawn()s them, so the
# execve hook alone never sees logd.  Hook the spawn path as well.
PSPAWN_ANCHOR = (
    "int\n"
    "posix_spawn(proc_t ap, struct posix_spawn_args *uap, int32_t *retval)\n"
    "{\n"
)
PSPAWN_BLOCK = (
    "\t/* " + MARK + ": same refusal on the posix_spawn path xpcproxy uses. */\n"
    "\t{\n"
    "\t\tchar lafa_nop[160];\n"
    "\t\tsize_t lafa_nopn = 0;\n"
    "\n"
    "\t\tif (copyinstr(uap->path, lafa_nop, sizeof(lafa_nop), &lafa_nopn) == 0) {\n"
    "\t\t\tchar *lafa_base = lafa_nop;\n"
    "\t\t\tchar *lafa_c;\n"
    "\n"
    "\t\t\tfor (lafa_c = lafa_nop; *lafa_c != '\\0'; lafa_c++) {\n"
    "\t\t\t\tif (*lafa_c == '/') {\n"
    "\t\t\t\t\tlafa_base = lafa_c + 1;\n"
    "\t\t\t\t}\n"
    "\t\t\t}\n"
    "\t\t\tif (strcmp(lafa_base, \"logd\") == 0 ||\n"
    "\t\t\t    strcmp(lafa_base, \"logd_helper\") == 0) {\n"
    "\t\t\t\tloader_log_line(\"HAKIOS-NOLOGD: logd spawn refused\\n\");\n"
    "\t\t\t\treturn ENOENT;\n"
    "\t\t\t}\n"
    "\t\t}\n"
    "\t}\n"
)

def main():
    if len(sys.argv) < 2:
        print("usage: patch-kernel-nologd.py <xnu tree>")
        return 2
    root = pathlib.Path(sys.argv[1])
    f = next(root.rglob("bsd/kern/kern_exec.c"), None)
    if f is None:
        print("patch-nologd: MISS bsd/kern/kern_exec.c")
        return 1
    text = f.read_text(encoding="utf-8", errors="replace")
    if MARK in text:
        print("patch-nologd: already applied")
        return 0
    if ANCHOR not in text:
        print("patch-nologd: MISS the __mac_execve signature")
        return 1
    text = text.replace(ANCHOR, ANCHOR + BLOCK, 1)
    if PSPAWN_ANCHOR in text:
        text = text.replace(PSPAWN_ANCHOR, PSPAWN_ANCHOR + PSPAWN_BLOCK, 1)
        print("patch-nologd: posix_spawn also refuses logd")
    else:
        print("patch-nologd: MISS the posix_spawn signature")
    if DECL not in text:
        i = text.find("#include")
        j = text.find("\n", i)
        text = text[:j + 1] + DECL + text[j + 1:]
    f.write_text(text, encoding="utf-8")
    print("patch-nologd: __mac_execve refuses logd and logd_helper with ENOENT")
    return 0


if __name__ == "__main__":
    sys.exit(main())
