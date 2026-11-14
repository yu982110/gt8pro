#!/usr/bin/env python3
"""patch-kernel-procsample.py -- who is running, written where a reset cannot lose it.

Userland cannot answer that question on this port: logd dies of SIGTRAP before it writes a line,
so a black screen after MSUEarlyBootTask says nothing about SpringBoard or backboardd.
loader_log_line() (hakios's own, osfmk/arm64/loader_return.c) appends whole lines to the loader's
ring; the loader drains that ring while the kernel runs and writes it to the flash every 5 s, so a
line written through it survives a reset -- and it needs no logd, no console and no userland.

This patch, all of it in bsd/kern/bsd_init.c:

  * adds #include <kern/thread_call.h> and the declaration of loader_log_line();
  * prints one line from bsdinit_task(), right after load_init_program() has exec'd launchd (the
    moment userland begins), saying which image pid 1 became;
  * starts a thread_call that three times, LAFA_PROCSAMPLE_DELAY (45) seconds apart, walks allproc
    under proc_list_lock and reports every process as

        proc: <p_comm> pid=<pid>

    followed by the count,

        procs: N alive

45 s after the exec is late enough for launchd's boot tasks to have spawned something and early
enough to still be the same run; three samples show the list change.

The script is idempotent (the marker rides in the inserted comments), validates every anchor up
front and exits 1 instead of writing a half-patched tree.

Reading the log afterwards, the expected lines and the failure criteria are in docs/PROCSAMPLE.md
(the live ring is at hybridswap + 64..68 MiB; the composed log at its last 1 MiB).

    python3 ci/patch-kernel-procsample.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-LAFA-PROCSAMPLE"
FILE = "bsd/kern/bsd_init.c"

# Every anchor below is in Apple's xnu-8796.121.2 and survives what the kernel workflows already
# apply:
#   * hakios.patch only adds includes, sets DEBUG_BSDINIT to 1 and adds a devfs hunk, all before
#     bsd_init()'s tail -- bsdinit_task() is untouched;
#   * patch-kernel-fb.py inserts after <mach/vm_param.h> and rootvnode_rw_lock, and before
#     bsd_init_kprintf("vfs_mountroot\n") -- none of that is in bsdinit_task() either;
#   * the livelog probes live in osfmk/, not here.
INCLUDE_ANCHOR = "#include <kern/clock.h>\n"
INCLUDE_LINE = "#include <kern/thread_call.h>      /* HAKIOS-LAFA-PROCSAMPLE: the 45 s samples */\n"
PROC_INTERNAL = "#include <sys/proc_internal.h>\n"
FUNC_ANCHOR = "void\nbsdinit_task(void)\n"
ARM_ANCHOR = "\tload_init_program(p);\n"

BEGIN = "/* " + MARK + ": begin"
END = "/* " + MARK + ": end */"

FUNC_BLOCK = r'''/* HAKIOS-LAFA-PROCSAMPLE: begin -- the process list, written where a reset cannot lose it.
 *
 * Userland cannot report it: logd dies (SIGTRAP) before writing a line, so a black screen after
 * MSUEarlyBootTask says nothing about SpringBoard and backboardd. loader_log_line() goes to the
 * loader's own ring instead (osfmk/arm64/loader_return.c): the loader drains that ring while the
 * kernel runs and writes it to the flash every few seconds -- no logd, no console, no userland.
 * Three samples, LAFA_PROCSAMPLE_DELAY seconds apart, so the list is also seen changing.
 */
extern void loader_log_line(const char *line);

#define LAFA_PROCSAMPLE_ROUNDS  3u
#define LAFA_PROCSAMPLE_DELAY   45              /* seconds between samples */

static thread_call_t lafa_procsample_call;
static unsigned int lafa_procsample_round;

static void
lafa_procsample_dump(__unused thread_call_param_t p0, __unused thread_call_param_t p1)
{
	proc_t p;
	char line[128];
	unsigned int n = 0;
	uint64_t deadline;

	snprintf(line, sizeof(line), "procsample: sample %u of %u\n",
	    lafa_procsample_round + 1, LAFA_PROCSAMPLE_ROUNDS);
	loader_log_line(line);

	/*
	 * allproc holds every process that exists, under the proc list lock. Nothing below sleeps
	 * or takes a second lock, so the walk is one short critical section; a process that is
	 * forking is still SIDL and may show an empty name, which is itself worth seeing.
	 */
	proc_list_lock();
	ALLPROC_FOREACH(p) {
		snprintf(line, sizeof(line), "proc: %s pid=%d\n", p->p_comm, p->p_pid);
		loader_log_line(line);
		n++;
	}
	proc_list_unlock();

	snprintf(line, sizeof(line), "procs: %u alive\n", n);
	loader_log_line(line);

	if (++lafa_procsample_round < LAFA_PROCSAMPLE_ROUNDS) {
		clock_interval_to_deadline(LAFA_PROCSAMPLE_DELAY, NSEC_PER_SEC, &deadline);
		thread_call_enter_delayed(lafa_procsample_call, deadline);
	}
}

/*
 * Called from bsdinit_task() the moment load_init_program() has exec'd launchd: pid 1's image is
 * in place and userland is about to start. p is the init proc, so this line also proves what pid 1
 * became -- that alone answers "did the handover to launchd happen" without any other channel.
 */
static void
lafa_procsample_start(proc_t p)
{
	uint64_t deadline;
	char line[128];

	lafa_procsample_call = thread_call_allocate(lafa_procsample_dump, NULL);
	if (lafa_procsample_call == NULL) {
		loader_log_line("procsample: thread_call_allocate failed\n");
		return;
	}
	snprintf(line, sizeof(line),
	    "procsample: pid %d is \"%s\"; %u samples, %u s apart\n",
	    p->p_pid, p->p_comm, LAFA_PROCSAMPLE_ROUNDS, LAFA_PROCSAMPLE_DELAY);
	loader_log_line(line);
	clock_interval_to_deadline(LAFA_PROCSAMPLE_DELAY, NSEC_PER_SEC, &deadline);
	thread_call_enter_delayed(lafa_procsample_call, deadline);
}
/* HAKIOS-LAFA-PROCSAMPLE: end */
'''

ARM_BLOCK = (
    "\t/* HAKIOS-LAFA-PROCSAMPLE: launchd has been exec'd; arm the process-list samples */\n"
    "\tlafa_procsample_start(p);\n"
)


def count(text, needle):
    return text.count(needle)


def main(root):
    root = pathlib.Path(root)
    path = root / FILE
    if not path.is_file():
        print(f"patch-procsample: MISS: {path} not found (is {root} an xnu source tree?)")
        return 1

    # Keep the tree's line endings: xnu is LF, but a Windows checkout can be CRLF.
    with path.open("r", encoding="utf-8", errors="surrogateescape", newline="") as f:
        original = f.read()
    crlf = "\r\n" in original
    text = original.replace("\r\n", "\n") if crlf else original

    # Already applied? Every piece has to be there, or a half-patched tree would be blessed.
    if BEGIN in text or END in text:
        missing = []
        if BEGIN not in text:
            missing.append("the begin marker")
        if END not in text:
            missing.append("the end marker")
        if INCLUDE_LINE not in text:
            missing.append("the thread_call include")
        if "lafa_procsample_dump" not in text:
            missing.append("lafa_procsample_dump()")
        if "\tlafa_procsample_start(p);\n" not in text:
            missing.append("the call in bsdinit_task()")
        if missing:
            print("patch-procsample: MISS: " + FILE + " carries the marker but is missing "
                  + ", ".join(missing) + "; clean it by hand before re-applying")
            return 1
        print("patch-procsample: already applied")
        return 0

    # Check every anchor before touching anything: a write that stops half way is worse than none.
    problems = []
    if count(text, PROC_INTERNAL) != 1:
        problems.append(f"{PROC_INTERNAL.strip()!r} appears {count(text, PROC_INTERNAL)} times "
                        "(proc_t/allproc/proc_list_lock come from it)")
    if count(text, INCLUDE_ANCHOR) != 1:
        problems.append(f"{INCLUDE_ANCHOR.strip()!r} appears {count(text, INCLUDE_ANCHOR)} times")
    if count(text, FUNC_ANCHOR) != 1:
        problems.append(f"{FUNC_ANCHOR.strip()!r} appears {count(text, FUNC_ANCHOR)} times")
    if count(text, ARM_ANCHOR) != 1:
        problems.append(f"{ARM_ANCHOR.strip()!r} appears {count(text, ARM_ANCHOR)} times")
    elif text.find(ARM_ANCHOR) < text.find(FUNC_ANCHOR):
        problems.append(f"{ARM_ANCHOR.strip()!r} is not inside bsdinit_task()")
    if problems:
        for p in problems:
            print("  MISS: " + p)
        print("patch-procsample: nothing written")
        return 1

    # 1. The timer API (kern/clock.h, already included, gives clock_interval_to_deadline()).
    text = text.replace(INCLUDE_ANCHOR, INCLUDE_ANCHOR + INCLUDE_LINE, 1)

    # 2. Declaration + callout + arm helper, immediately above the function that arms them.
    text = text.replace(FUNC_ANCHOR, FUNC_BLOCK + "\n" + FUNC_ANCHOR, 1)

    # 3. The arm itself: after launchd exec'd, before bsdinit_task() returns to userland.
    text = text.replace(ARM_ANCHOR, ARM_ANCHOR + ARM_BLOCK, 1)

    if crlf:
        text = text.replace("\n", "\r\n")
    with path.open("w", encoding="utf-8", errors="surrogateescape", newline="") as f:
        f.write(text)

    print(f"patch-procsample: {FILE}: thread_call include, lafa_procsample_*(), "
          "and the arm in bsdinit_task()")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-kernel-procsample.py <xnu tree>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
