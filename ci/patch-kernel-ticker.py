#!/usr/bin/env python3
"""patch-kernel-ticker.py -- a heartbeat written straight into the live ring, once every five seconds.

Why it matters: the boot now dies right after launchd's last boot task, and the log simply stops. Two
very different faults look exactly like that:

  * the kernel is alive but its kprintf/console path is stuck (a buffer the shell keeps writing to
    goes nowhere), or
  * the kernel itself stopped (a spin with interrupts off).

A tick written with loader_log_line() -- the port's own ring writer in osfmk/arm64/loader_return.c,
which the loader persists to the boot partition every couple of seconds -- does not go through the
console at all. If the ticks keep coming after the log stops, the kernel is alive and only its console
output is stuck; if they stop too, the kernel died.

    python3 ci/patch-kernel-ticker.py <xnu tree>

Idempotent (the marker rides in the inserted comments), and every anchor is checked before a byte is
written. The anchors are the ones patch-kernel-procsample.py already proved on xnu-8796.121.2:
bsd/kern/bsd_init.c, the load_init_program(p) call in bsdinit_task().
"""
import pathlib
import sys

MARK = "HAKIOS-TICK"

FILE = "bsd/kern/bsd_init.c"
INCLUDE_ANCHOR = "#include <kern/clock.h>\n"
INCLUDE_LINE = "#include <kern/thread_call.h>      /* HAKIOS-TICK: the 5 s ticks */\n"
FUNC_ANCHOR = "void\nbsdinit_task(void)\n"
ARM_ANCHOR = "\tload_init_program(p);\n"

FUNC_BLOCK = r'''/* HAKIOS-TICK: begin -- a heartbeat that does not use the console.
 *
 * Once a second-and-a-bit of boot is behind us, kprintf goes through the console and can be lost;
 * loader_log_line() writes the ring the loader reads from another core, which is on disk even when
 * the machine stops dead. Five seconds apart, enough to see the moment it stops.
 */
#define LAFA_TICK_DELAY   5
#define LAFA_TICK_ROUNDS  240

static thread_call_t lafa_tick_call;
static unsigned int lafa_tick_round;

static void
lafa_tick_dump(__unused thread_call_param_t p0, __unused thread_call_param_t p1)
{
	char line[64];
	uint64_t deadline;

	snprintf(line, sizeof(line), "HAKIOS-TICK %u\n", lafa_tick_round + 1);
	loader_log_line(line);
	if (++lafa_tick_round < LAFA_TICK_ROUNDS) {
		clock_interval_to_deadline(LAFA_TICK_DELAY, NSEC_PER_SEC, &deadline);
		thread_call_enter_delayed(lafa_tick_call, deadline);
	}
}

/* Called from bsdinit_task() right after load_init_program(): the boot is far enough along. */
static void
lafa_tick_start(__unused proc_t p)
{
	uint64_t deadline;

	lafa_tick_call = thread_call_allocate(lafa_tick_dump, NULL);
	if (lafa_tick_call == NULL) {
		loader_log_line("HAKIOS-TICK: thread_call_allocate failed\n");
		return;
	}
	clock_interval_to_deadline(LAFA_TICK_DELAY, NSEC_PER_SEC, &deadline);
	thread_call_enter_delayed(lafa_tick_call, deadline);
}
/* HAKIOS-TICK: end */
'''

ARM_BLOCK = ("\t/* HAKIOS-TICK: arm the heartbeat that does not use the console */\n"
             "\tlafa_tick_start(p);\n")


def main(root):
    path = pathlib.Path(root) / FILE
    if not path.exists():
        print("patch-kernel-ticker: %s not found" % path)
        return 1
    text = path.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-kernel-ticker: already applied")
        return 0

    missing = []
    if text.count(INCLUDE_ANCHOR) != 1:
        missing.append("include anchor (%d)" % text.count(INCLUDE_ANCHOR))
    if text.count(FUNC_ANCHOR) != 1:
        missing.append("bsdinit_task() (%d)" % text.count(FUNC_ANCHOR))
    if text.count(ARM_ANCHOR) != 1:
        missing.append("load_init_program(p) (%d)" % text.count(ARM_ANCHOR))
    if missing:
        print("patch-kernel-ticker: MISS: " + ", ".join(missing))
        return 1

    text = text.replace(INCLUDE_ANCHOR, INCLUDE_ANCHOR + INCLUDE_LINE, 1)
    text = text.replace(FUNC_ANCHOR, FUNC_BLOCK + "\n" + FUNC_ANCHOR, 1)
    text = text.replace(ARM_ANCHOR, ARM_ANCHOR + ARM_BLOCK, 1)
    path.write_text(text, errors="surrogateescape")
    print("patch-kernel-ticker: 5 s ticks armed after load_init_program()")
    print("  a boot that stops logging but keeps ticking is a console fault, not a dead kernel")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-kernel-ticker.py <xnu tree>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
