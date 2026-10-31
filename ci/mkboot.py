#!/usr/bin/env python3
"""把 Mu-Silicium 的 UEFI 固件卷（FV）打成 ABL 能启动的 boot.img。

ABL 在 boot.img 里找的是 **EFI 应用**（PE/COFF），而 build_uefi.py 产出的是 EDK2 固件卷（FV，不是 PE）。
Mu-Silicium 的 `BootShim/` 就是那个 PE：它把 FV 复制到 FD_BASE 再跳进去。所以：

    kernel = gzip(BootShim.bin + SILICIUM_UEFI.fd) + <device>.dtb
    boot.img = mkbootimg --kernel <那个> --ramdisk <Resources/ramdisk> --header_version 1

    python3 ci/mkboot.py <Mu-Silicium 目录> <device> <FD_BASE> <FD_SIZE> <输出>

例：python3 ci/mkboot.py Mu-Silicium aston 0xA7100000 0x300000 boot-aston.img
"""
import gzip
import os
import subprocess
import sys


def main() -> int:
    if len(sys.argv) != 6:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    mu, device, fd_base, fd_size, out = sys.argv[1:]
    fd_path = os.path.join(mu, "Build", f"{device}Pkg", "RELEASE_CLANGPDB", "FV", "SILICIUM_UEFI.fd")
    shim_dir = os.path.join(mu, "BootShim")
    dtb = os.path.join(mu, "Resources", "DTBs", f"{device}.dtb")
    ramdisk = os.path.join(mu, "Resources", "ramdisk")
    mkbootimg = os.path.join(mu, "Resources", "Scripts", "mkbootimg.py")

    for p in (fd_path, dtb, ramdisk, mkbootimg):
        if not os.path.isfile(p):
            print(f"mkboot: missing {p}", file=sys.stderr)
            return 1

    for f in ("BootShim.elf", "BootShim.bin"):
        try:
            os.unlink(os.path.join(shim_dir, f))
        except FileNotFoundError:
            pass
    subprocess.check_call(
        ["make", "REQUIRES_KERNEL_HEADER=1", f"FD_BASE={fd_base}", f"FD_SIZE={fd_size}",
         "CFLAGS=--target=aarch64-linux-gnu -DPAYLOAD_BASE=0xF0000000"],
        cwd=shim_dir,
    )
    shim = open(os.path.join(shim_dir, "BootShim.bin"), "rb").read()
    fd = open(fd_path, "rb").read()
    want = int(fd_size, 16)
    if len(fd) != want:
        print(f"mkboot: FD is {len(fd):#x} bytes, FD_SIZE says {want:#x}", file=sys.stderr)
        return 1
    dtb_blob = open(dtb, "rb").read()

    kernel = out + ".kernel~"
    with open(kernel, "wb") as f:
        f.write(gzip.compress(shim + fd, 6))
        f.write(dtb_blob)
    try:
        subprocess.check_call([sys.executable, mkbootimg, "--kernel", kernel,
                               "--ramdisk", ramdisk, "--header_version", "1", "-o", out])
    finally:
        os.unlink(kernel)
    print(f"mkboot: {out}: {os.path.getsize(out)} bytes (shim {len(shim)}, fd {len(fd)}, dtb {len(dtb_blob)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
