#!/usr/bin/env python3
"""patch-init-cache-probe.py -- make init print where the dyld shared cache really is.

dyld refuses to load the shared cache:

    dyld cache '(null)' not loaded: code signature registration for shared cache failed

and the kernel side of that path is innocent: the F_ADDFILESIGS_RETURN handler never sees a blob
over 1 MB, cs_native4k_prepare succeeds for everything it is given, and
_shared_region_map_and_slide is never called. The '(null)' is the cache *path*, so dyld never
opened the file -- a userspace/root-layout problem.

On iOS 16 the cache lives in the OS cryptex. init mounts it (mount_cryptex "OS") and switches root
to the flash, so the question is whether the paths dyld tries exist at that point. This patch makes
init stat the candidates and print the result, plus one check that goes red when the path dyld uses
has no cache behind it.

    python3 ci/patch-init-cache-probe.py <hakios tree>
"""
import pathlib
import sys

MARK = "HAKIOS-CACHE-PROBE"

ANCHOR = """	mount_cryptex("OS", ",c=1", devpath);
	mount_cryptex("App", ",c=2", devpath);
	check(bind_dir("/private/preboot/redwood", "/redwood"), "this image back on /redwood");
"""

NEW = """	mount_cryptex("OS", ",c=1", devpath);
	mount_cryptex("App", ",c=2", devpath);
	/*
	 * HAKIOS-CACHE-PROBE: dyld says
	 *   dyld cache '(null)' not loaded: code signature registration for shared cache failed
	 * and never opens a cache file, so its path came out NULL. Print where the cache is and where
	 * dyld will look for it, right after the cryptexes are mounted and before root is final.
	 */
	{
		static const char *const paths[] = {
			"/private/preboot/Cryptexes/OS/System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64",
			"/System/Cryptexes/OS/System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64",
			"/System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64",
			"/private/preboot/Cryptexes/OS/System/Library/Caches/com.apple.dyld",
			"/System/Library/Caches/com.apple.dyld",
		};
		unsigned i;

		for (i = 0; i < sizeof(paths) / sizeof(paths[0]); i++) {
			struct stat cst;

			if (stat(paths[i], &cst) == 0) {
				printf("  cache probe: %s -> %lld bytes\\n", paths[i], (long long)cst.st_size);
			} else {
				printf("  cache probe: %s -> %s\\n", paths[i], strerror(errno));
			}
		}
		struct stat cst;

		check(stat(paths[2], &cst) == 0 && cst.st_size > 1000000,
		    "the shared cache is where dyld looks for it");
	}
	check(bind_dir("/private/preboot/redwood", "/redwood"), "this image back on /redwood");
"""

# The checks print one line each and the kernel console scrolls fast; by the time the screen is
# looked at, the block above is long gone. Print it again after the final tally, so it is the tail.
TAIL_ANCHOR = '\tprintf("\\n=== %d checks, %d failed ===\\n", checks, failures);\n'

TAIL_NEW = '''\tprintf("\\n=== %d checks, %d failed ===\\n", checks, failures);
\t/* HAKIOS-CACHE-PROBE: again, last, so these are the tail of the screen at handover. */
\t{
\t\tstatic const char *const tail_paths[] = {
\t\t\t"/System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64",
\t\t\t"/System/Cryptexes/OS/System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64",
\t\t\t"/private/preboot/Cryptexes/OS/System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64",
\t\t};
\t\tunsigned k;

\t\tfor (k = 0; k < sizeof(tail_paths) / sizeof(tail_paths[0]); k++) {
\t\t\tstruct stat tst;

\t\t\tif (stat(tail_paths[k], &tst) == 0) {
\t\t\t\tprintf("  cache tail: %s -> %lld\\\\n", tail_paths[k], (long long)tst.st_size);
\t\t\t} else {
\t\t\t\tprintf("  cache tail: %s -> %s\\\\n", tail_paths[k], strerror(errno));
\t\t\t}
\t\t}
\t}
'''


def main(root):
    f = pathlib.Path(root) / "userland/src/init.c"
    if not f.exists():
        print(f"patch-init-cache-probe: {f} not found")
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-init-cache-probe: already applied")
        return 0
    if ANCHOR not in text:
        print("patch-init-cache-probe: MISS: the cryptex mount block is not the expected shape")
        return 1
    text = text.replace(ANCHOR, NEW, 1)
    if TAIL_ANCHOR in text:
        text = text.replace(TAIL_ANCHOR, TAIL_NEW, 1)
        print("patch-init-cache-probe: tail print after the check tally")
    else:
        print("patch-init-cache-probe: MISS: the final tally printf is not where expected")
        return 1
    f.write_text(text, errors="surrogateescape")
    print("patch-init-cache-probe: init now prints where the shared cache is")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-init-cache-probe.py <hakios tree>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
