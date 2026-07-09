# Install optional tools from source

Use this on older Linux machines when copied binaries fail with errors like:

```text
version `GLIBC_2.34' not found
```

That error means the binary was built on a newer glibc than the target machine.
Do not configure the skill to use that binary. Build or install the tool on the
target machine instead.

## vhd2vl

This package includes vhd2vl source at:

```text
source/vhd2vl-master-source.tar.gz
```

Build it on the target machine:

```bash
cd /home/user5/rtl-acquire/optional_tools
mkdir -p build
tar -xzf source/vhd2vl-master-source.tar.gz -C build
make -C build/vhd2vl-master/src
mkdir -p bin
cp -f build/vhd2vl-master/src/vhd2vl bin/vhd2vl
chmod +x bin/vhd2vl
bin/vhd2vl --version
```

If build tools are missing, install or load equivalents for:

```text
gcc make flex bison
```

Then configure:

```bash
export R2G_ACQUIRE_VHD2VL_BIN=/home/user5/rtl-acquire/optional_tools/bin/vhd2vl
```

## sv2v

The source machine only had a prebuilt sv2v binary, not the sv2v source tree.
On an older target OS, that binary may fail due to glibc mismatch. Build or
install sv2v directly on the target machine.

Recommended source build when network and Haskell Stack are available:

```bash
cd /home/user5/rtl-acquire/optional_tools
mkdir -p build
git clone https://github.com/zachjs/sv2v.git build/sv2v
make -C build/sv2v
mkdir -p bin
cp -f build/sv2v/bin/sv2v bin/sv2v
chmod +x bin/sv2v
bin/sv2v --version
```

This requires:

```text
git make stack
```

Then configure:

```bash
export R2G_ACQUIRE_SV2V_BIN=/home/user5/rtl-acquire/optional_tools/bin/sv2v
```

If you cannot build sv2v, leave `R2G_ACQUIRE_SV2V_BIN` unset. The core Verilog
expansion flow still works; only some SystemVerilog repair fallbacks are lost.

## DVC

DVC is optional and is used only by dataset snapshot/versioning paths. Install
it in the Python environment only if needed:

```bash
python -m pip install dvc
```

Core synthesis, graph conversion, validation, and publish gating do not require
DVC.
