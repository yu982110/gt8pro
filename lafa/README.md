# realme GT8 Pro (lafa)

A HakiOS device repository: what is true of this device and of no other. The kernel, the loader, the drivers and the tools are
the common repository's; the sm8850 family's facts are in `soc/`, carried with the device. How the three are put together:
[docs/porting/repositories.md](https://github.com/oosexgang/hakios-xnu-kernel/blob/main/docs/porting/repositories.md).

## The phone

| | |
|---|---|
| Model / codename | RMX6699 / **lafa** |
| SoC | Qualcomm SM8850, "canoe" — Snapdragon 8 Elite Gen 5 (2 × Oryon prime + 6 × performance, Adreno 840) |
| Display | 6.79", 3136 × 1440, 144 Hz, AMOLED |
| Page size | 4096 (`BOARD_KERNEL_PAGESIZE := 4096` in the recovery tree, header v4) |
| Partitions | A/B, plus OPPO's `my_*` dynamic partitions; `boot`, `init_boot`, `vendor_boot`, `dtbo` |
| Started from | `fastboot boot` (RAM only) — once the UEFI port and the bootloader allow it |

Where the facts came from: the recovery device tree
[`android_device_realme_lafa-AERA`](https://github.com/AERA-Recovery/android_device_realme_lafa-AERA) (codename, platform,
`TARGET_BOARD_PLATFORM := sm8850`, `qcom-adreno840`, 4 KiB pages) and realme's own specification page. Anything not in that
list — the touch controller, the panel's timing, the UFS LUNs, the modem's memory — is **not known yet** and must be read from
this phone's device tree (`tools/collect-lafa.sh` in the port kit).

| File | What |
|---|---|
| `device.conf` | what the device is, and how far each part of it works: its page in the documentation is made from it |
| `configs/lafa_defconfig` | the common kernel, the SoC's fragment, this device's: `make lafa_defconfig` |
| `configs/lafa.config` | what is this device's own: the drivers built in, the arguments it needs |
| `soc/` | its SoC family's layer (sm8850): `soc.conf`, `configs/sm8850.config`, the SoC's drivers; carried here so that this one repository is all the device needs |
| `drivers/` | drivers of its own, if it needs any (`drivers/<subsystem>/conf`, as in the common repository) |
| `uefi/` | the phone's platform for HakiOS's UEFI — **not written yet**, see its README |
| `pull-firmware.sh` | the phone's own firmware, off the phone over adb, into `fw-*/` (never committed) |

Every phone driver of the common repository is left out to begin with: their addresses and pins are the phone they were written
for. Put one back in `configs/lafa.config` when it has been made to work here, and say so in `device.conf`.

## State, honestly

- `tools/hakios-device.py check lafa` passes: the repository holds together as a repository.
- **Nothing has run on a phone.** No UEFI for this board exists, so there is no screen and no loader yet; every `status` line
  in `device.conf` is `missing`, and it stays that way until something is seen on the phone.
- The order to change that: the port kit's `docs/RUNBOOK.md` (P0 `fastboot boot` test → P1 collection → P2 UEFI → P3 this
  repository → P4 first boot → P5 drivers).
