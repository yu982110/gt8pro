#!/usr/bin/env python3
"""HAKIOS-PANIC-LOG: write every panic() message into the loader's log.

The kernel resets at ~57 s with no panic text anywhere the loader can see: the console hook
gets the ordinary prints, the panic buffer at PANIC_LOG_BASE holds nothing, and the reset comes
in the same second the machine stops, so the loader's silence detection (even at 12 s) is too
late.  The one channel that survives is the loader's own log buffer, which persist_live()
writes to the partition every second.

So: format the panic message at the very top of panic() and hand it to loader_log_line(), the
same hook the other port patches use.  The first parameter's name is taken from the signature
the patch matched, so it works whether it is called fmt, format or string.

Idempotent; exits 0 when already applied, 1 when no anchor is found.

    python3 ci/patch-kernel-panic-log.py <xnu tree>
"""
import pathlib
import re
import sys

MARK = "HAKIOS-PANIC-LOG"

DECL = "extern void loader_log_line(const char *line);   /* " + MARK + " */\n"


def block(name: str) -> str:
    return (
        "\t/* " + MARK + ": the panic text goes into the loader's log buffer, which is on the\n"
        "\t * partition a second later even if this boot never returns. */\n"
        "\t{\n"
        "\t\tchar lafa_pl[256];\n"
        "\t\tva_list lafa_plap;\n"
        "\n"
        "\t\tva_start(lafa_plap, " + name + ");\n"
        "\t\tvsnprintf(lafa_pl, sizeof(lafa_pl), " + name + ", lafa_plap);\n"
        "\t\tva_end(lafa_plap);\n"
        "\t\tloader_log_line(\"[PANIC] \");\n"
        "\t\tloader_log_line(lafa_pl);\n"
        "\t\tloader_log_line(\"\\n\");\n"
        "\t}\n"
    )


def main() -> int:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    f = next(root.rglob("osfmk/kern/debug.c"), None)
    if f is None:
        print("patch-panic-log: MISS osfmk/kern/debug.c")
        return 1
    text = f.read_text(encoding="utf-8", errors="replace")
    if MARK in text:
        print("patch-panic-log: already applied")
        return 0
    # the definition: a signature line at column 0 followed by the opening brace
    m = re.search(r"(?m)^(panic\(([^)\n]*)\)\s*)\n\{\n", text)
    if m is None:
        print("patch-panic-log: MISS panic() definition")
        return 1
    params = m.group(2)
    first = params.split(",")[0].strip()
    nm = re.search(r"(\w+)\s*$", first)
    if nm is None:
        print("patch-panic-log: MISS first parameter name in", repr(params))
        return 1
    name = nm.group(1)
    print("patch-panic-log: signature", repr(m.group(1)), "-> parameter", name)
    text = text[:m.end()] + block(name) + text[m.end():]
    if DECL not in text:
        text = DECL + text
    f.write_text(text, encoding="utf-8")
    print("patch-panic-log: installed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
