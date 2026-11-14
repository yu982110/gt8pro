#!/usr/bin/env python3
"""HAKIOS-LAFA-SPAWNSITE: number every failure exit inside posix_spawn().

The errno probe proves the posix_spawn syscall returns EPERM, and none of the
hand-picked EPERM sites fire.  posix_spawn() funnels ~40 different failures into one
`bad:` label, so the only honest way to find the culprit is to tag every `goto bad;`
with a number and let one boot tell us which one ran.

For each occurrence of `goto bad;` inside posix_spawn() (between its definition and the
next top-level function) a line is inserted before it:

    loader_log_line("HAKIOS-SPAWN-SITE: N\n");

The same script prints the N -> source line map, which is written to
docs/spawn-sites.txt by the CI step.

Idempotent; exits 0 when already applied, 1 when the function is not found.
"""
import pathlib
import sys

MARK = "/* HAKIOS-LAFA-SPAWNSITE */"
FUNC_START = "posix_spawn(proc_t ap, struct posix_spawn_args *uap, int32_t *retval)"
GOTO = "\t\t\tgoto bad;"


def main():
    if len(sys.argv) < 2:
        print("usage: patch-kernel-posixspawn-audit.py <xnu tree>")
        return 2
    root = pathlib.Path(sys.argv[1])
    hits = 0
    for p in root.rglob("kern_exec.c"):
        text = p.read_text(errors="replace")
        if MARK in text:
            print("patch-spawnsite: already applied in", p)
            hits += 1
            continue
        if FUNC_START not in text:
            continue
        start = text.find(FUNC_START)
        # end of the function: next line that starts a new top-level definition
        nxt = text.find("\nint\n", start)
        nxt2 = text.find("\nstatic int\n", start)
        cands = [x for x in (nxt, nxt2) if x > 0]
        end = min(cands) if cands else len(text)
        body = text[start:end]
        lines = body.split("\n")
        out = []
        n = 0
        mapping = []
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped in ("goto bad;",):
                n += 1
                indent = line[:len(line) - len(line.lstrip())]
                out.append(indent + MARK)
                out.append(indent + 'loader_log_line("HAKIOS-SPAWN-SITE: %d\\n");' % n)
                mapping.append((n, i + 1))
            out.append(line)
        if n == 0:
            print("patch-spawnsite: no goto bad found in posix_spawn")
            continue
        text = text[:start] + "\n".join(out) + text[end:]
        p.write_text(text)
        print(f"patch-spawnsite: {n} numbered failure exits inside posix_spawn")
        for num, ln in mapping:
            print(f"  site {num:2} -> function line {ln}")
        hits += 1
    return 0 if hits else 1


if __name__ == "__main__":
    sys.exit(main())
