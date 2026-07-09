# Optional tools for rtl-acquire

This directory contains optional helper tools for long-tail repair paths.
They are not required for the normal Verilog -> Nangate45 -> graph conversion flow.

## Included binaries

- `bin/sv2v`: SystemVerilog-to-Verilog fallback converter.
- `bin/vhd2vl`: VHDL-to-Verilog fallback converter.

These copied binaries are convenience fallbacks only. On older Linux machines
they may fail with `GLIBC_x.y not found`. If that happens, do not use them;
build native binaries on the target machine using `INSTALL_FROM_SOURCE.md`.

Configure the skill to use them:

```bash
export R2G_ACQUIRE_SV2V_BIN=/home/user5/rtl-acquire/optional_tools/bin/sv2v
export R2G_ACQUIRE_VHD2VL_BIN=/home/user5/rtl-acquire/optional_tools/bin/vhd2vl
```

Or add the same exports to `references/env.local.sh`.

## DVC

`dvc` is optional and is used only by snapshot/versioning paths such as
`scripts/publish/record_dataset_snapshot.py`.

No local `dvc` executable was available on the source machine, so no DVC binary
is bundled here. Install it on the target machine only if dataset versioning is
needed:

```bash
python -m pip install dvc
```

Core synthesis, graph conversion, validation, and publish gating do not require
DVC.

## Runtime note

These binaries were copied from the source machine and may expect a compatible
Linux x86-64 environment. If they fail to start on the target machine, build or
install native `sv2v` / `vhd2vl` there and update the two environment variables
above.
