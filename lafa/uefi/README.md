# UEFI for lafa

The realme GT8 Pro's platform for HakiOS's UEFI (`uefi/` of the common repository: Mu-Silicium with HakiOS's
changes and banner). **It does not exist yet**: this directory is where it goes, and the derivation from the
OnePlus 15's `astonPkg` (same SM8850) is written out step by step in the port kit's
`docs/UEFI-lafa.md`.

What will be here, in `files/`, the way redwood's is:

- `Platforms/Realme/lafaPkg`: the memory map and the driver list;
- `Resources/Configs/lafa.toml`: the FD base and size, and the boot image's shape;
- `Resources/DTBs/lafa.dtb`: the device tree from the phone's own `vendor_boot` (see the port kit's
  `tools/collect-lafa.sh`), not from another phone of the family;
- `Binaries/lafa`: the vendor's UEFI drivers, **not tracked**: you make this directory yourself from your own
  phone, as `uefi/README.md` says.

```sh
devices/lafa/uefi/apply.sh     # = uefi/apply.sh lafa, once files/ is there
```

Until this exists, `xl` falls back to HakiOS's U-Boot (`docs/u-boot.md`), which needs its own board support for
SM8850 (`devices/lafa/u-boot/files/dts/...`), or there is no screen at all.
