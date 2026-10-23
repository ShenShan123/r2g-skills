#!/usr/bin/env bash
# Package a validated OpenROAD + Yosys install into a relocatable, no-root tarball.
#
# Why: the conda litex-hub channel froze openroad/yosys in Feb 2024 (yosys
# 0.38) and conda-forge has no openroad, so "conda binaries + a current ORFS"
# cannot synthesize at all (fresh-user test, 2026-10-23: yosys 0.38 fails the
# ORFS canonicalize step on the nangate45 liberty). The toolchain every r2g
# dataset was produced with is a source build; this ships exactly that build.
#
#   make_toolchain_bundle.sh --orfs <ORFS checkout with tools/install> --out <dir>
#                            [--klayout /usr/bin/klayout] [--tcl-lib DIR]
#
# Layout (relocatable: every ELF gets RUNPATH relative to $ORIGIN):
#   <name>/openroad/bin/{openroad,sta}
#   <name>/yosys/{bin,share}        (yosys finds share/ and yosys-abc next to itself)
#   <name>/lib/                     every non-glibc shared library they load
#   <name>/share/tcl8.6/            Tcl script library (export TCL_LIBRARY)
#   <name>/klayout/                 (--klayout) KLayout + its own lib/, a trimmed
#                                   Python stdlib and the Ruby stdlib: nangate45
#                                   DRC/LVS need it and conda's klayout recipe is
#                                   unsatisfiable, so no-root users had no way to get one
#   <name>/BUNDLE.json              versions, ORFS commit, glibc floor, file digests
# glibc itself is NOT bundled (it cannot be relocated); the floor is recorded.
set -euo pipefail
ORFS=""; OUT=""; TCL_LIB=""; KLAYOUT=""; PATCHELF="${PATCHELF:-patchelf}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --orfs) ORFS="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    --tcl-lib) TCL_LIB="$2"; shift 2 ;;
    --klayout) KLAYOUT="$2"; shift 2 ;;
    -h|--help) sed -n 2,20p "$0"; exit 0 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done
[[ -n "$ORFS" && -n "$OUT" ]] || { echo "need --orfs and --out" >&2; exit 2; }
command -v "$PATCHELF" >/dev/null || { echo "patchelf not found (pip install patchelf)" >&2; exit 2; }
INST="$ORFS/tools/install"
OR="$INST/OpenROAD/bin/openroad"; YS="$INST/yosys/bin/yosys"
[[ -x "$OR" && -x "$YS" ]] || { echo "no built openroad/yosys under $INST" >&2; exit 2; }

or_ver="$("$OR" -version 2>/dev/null | tail -1 | tr -d ' ')"
ys_ver="$("$YS" -V | awk '{print $2}')"
orfs_commit="$(git -C "$ORFS" rev-parse HEAD)"
name="r2g-toolchain-openroad-${or_ver}-yosys-${ys_ver}"
kl_ver=""
if [[ -n "$KLAYOUT" ]]; then
  kl_ver="$("$KLAYOUT" -b -v | awk '{print $2}')"
  name="$name-klayout-$kl_ver"
fi
B="$OUT/$name"
rm -rf "$B"; mkdir -p "$B/openroad/bin" "$B/lib" "$B/share"
cp -a "$INST/OpenROAD/bin/openroad" "$INST/OpenROAD/bin/sta" "$B/openroad/bin/"
cp -a "$INST/yosys" "$B/yosys"

# glibc and the loader stay the host's
GLIBC_RE='^(libc|libm|libpthread|libdl|librt|libutil|libresolv|libnsl|libanl|libmvec|ld-linux-x86-64)\.so'
mapfile -t ELFS < <(find "$B/openroad/bin" "$B/yosys/bin" -type f -exec sh -c 'head -c4 "$1" | grep -q ELF && echo "$1"' _ {} \;)
declare -A SEEN=()
queue=("${ELFS[@]}")
while [[ ${#queue[@]} -gt 0 ]]; do
  f="${queue[0]}"; queue=("${queue[@]:1}")
  # resolve as the ORIGINAL machine does (its RUNPATH), never via LD_LIBRARY_PATH
  while read -r so path; do
    [[ -z "$path" || "$path" == "not" ]] && { echo "unresolved dependency of $f: $so" >&2; exit 3; }
    [[ "$so" =~ $GLIBC_RE ]] && continue
    [[ -n "${SEEN[$so]:-}" ]] && continue
    SEEN[$so]=1
    cp -L "$path" "$B/lib/$so"; chmod u+w "$B/lib/$so"
    queue+=("$B/lib/$so")
  done < <(env -u LD_LIBRARY_PATH ldd "${f/#$B\/openroad\/bin/$INST/OpenROAD/bin}" 2>/dev/null \
           | awk '/=>/{print $1, $3} /not found/{print $1, "not"}')
done

for f in "${ELFS[@]}"; do
  rel="$(realpath --relative-to="$(dirname "$f")" "$B/lib")"
  "$PATCHELF" --set-rpath "\$ORIGIN/$rel" "$f"
done
for f in "$B"/lib/*.so*; do "$PATCHELF" --set-rpath '$ORIGIN' "$f" 2>/dev/null || true; done

# Tcl's script library (init.tcl): both binaries load the one bundled libtcl8.6
if [[ -z "$TCL_LIB" ]]; then
  for c in "$(dirname "$(env -u LD_LIBRARY_PATH ldd "$OR" | awk '/libtcl8.6/{print $3}')")/tcl8.6" /usr/share/tcltk/tcl8.6; do
    [[ -f "$c/init.tcl" ]] && { TCL_LIB="$c"; break; }
  done
fi
[[ -f "$TCL_LIB/init.tcl" ]] || { echo "no Tcl script library found; pass --tcl-lib" >&2; exit 3; }
cp -a "$TCL_LIB" "$B/share/tcl8.6"

# Thin launchers so callers need no environment: the real ELF becomes <tool>.bin
# and the launcher points Tcl at the bundled init.tcl. yosys still finds its
# share/ and yosys-abc (it resolves them from /proc/self/exe, i.e. the .bin).
# OpenROAD's embedded Python stdlib is NOT shipped (~210 MB): ORFS uses
# `openroad -python` only for a user-supplied *.py RUN_SCRIPT, never in the flow.
for t in openroad/bin/openroad openroad/bin/sta yosys/bin/yosys; do
  mv "$B/$t" "$B/$t.bin"
  rel="$(realpath --relative-to="$(dirname "$B/$t")" "$B/share/tcl8.6")"
  cat > "$B/$t" <<WRAP
#!/bin/sh
d=\$(cd "\$(dirname "\$0")" && pwd)
TCL_LIBRARY="\$d/$rel" exec "\$d/$(basename "$t").bin" "\$@"
WRAP
  chmod 755 "$B/$t"
done

# ---- KLayout (optional) -------------------------------------------------------
if [[ -n "$KLAYOUT" ]]; then
  K="$B/klayout"; kinst="$(dirname "$(readlink -f "$KLAYOUT")")/../lib/klayout"
  [[ -d "$kinst" ]] || { echo "no klayout install dir at $kinst" >&2; exit 3; }
  mkdir -p "$K/lib"
  cp -a "$kinst/." "$K/"                 # libklayout_*, db_plugins/, lay_plugins/, pymod/
  cp -L "$KLAYOUT" "$K/klayout.bin"; chmod -R u+w "$K"
  # the embedded interpreters' standard libraries. KLayout starts Python for
  # every -r script and reads KLAYOUT_PYTHONHOME, not PYTHONHOME.
  pyver="$(env -u LD_LIBRARY_PATH ldd "$K/klayout.bin" | grep -oE 'libpython3\.[0-9]+' | head -1 | sed 's/lib//')"
  rbver="$(env -u LD_LIBRARY_PATH ldd "$K/klayout.bin" | grep -oE 'libruby-?[0-9.]+' | head -1 | grep -oE '[0-9]+\.[0-9]+')"
  mkdir -p "$K/python/lib" "$K/ruby"
  rsync -a --exclude test --exclude tests --exclude site-packages --exclude dist-packages \
        --exclude __pycache__ --exclude idlelib --exclude tkinter --exclude lib2to3 \
        --exclude ensurepip --exclude turtledemo "/usr/lib/$pyver" "$K/python/lib/"
  rsync -a "/usr/lib/ruby/$rbver.0" "$K/ruby/"
  [[ -d "/usr/lib/x86_64-linux-gnu/ruby/$rbver.0" ]] && \
    rsync -a "/usr/lib/x86_64-linux-gnu/ruby/$rbver.0/" "$K/ruby/$rbver.0/x86_64-linux-gnu/"
  chmod -R u+w "$K"
  # its OWN lib/: it was built against the distro libstdc++/Qt, which must not be
  # mixed with the newer libstdc++ the openroad build needs (top-level lib/).
  # Collect from everything (binary, libklayout_*, plugins, interpreter
  # extension modules) until no new library appears.
  while :; do
    added=0
    while read -r so path; do
      [[ "$so" =~ $GLIBC_RE || -e "$K/lib/$so" || -e "$K/$so" ]] && continue
      cp -L "$path" "$K/lib/$so"; chmod u+w "$K/lib/$so"; added=1
    done < <(find "$K" -type f \( -name klayout.bin -o -name '*.so*' \) -print0 \
             | xargs -0 -n1 sh -c 'env -u LD_LIBRARY_PATH ldd "$1" 2>/dev/null' _ \
             | awk '/=> \//{print $1, $3}' | sort -u)
    [[ $added == 0 ]] && break
  done
  while IFS= read -r -d '' f; do
    if [[ "$(dirname "$f")" == "$K/lib" ]]; then
      # some of these are klayout's own helper libs (liblefdef, libpcb, ...)
      # that link back to libklayout_* one level up
      "$PATCHELF" --set-rpath '$ORIGIN:$ORIGIN/..' "$f" || echo "patchelf failed: $f" >&2
    else
      up="$(realpath --relative-to="$(dirname "$f")" "$K")"
      "$PATCHELF" --set-rpath "\$ORIGIN/$up:\$ORIGIN/$up/lib" "$f" 2>/dev/null || true
    fi
  done < <(find "$K" -type f \( -name klayout.bin -o -name '*.so*' \) -print0)
  cat > "$K/klayout" <<WRAP
#!/bin/sh
d=\$(cd "\$(dirname "\$0")" && pwd)
KLAYOUT_PYTHONHOME="\$d/python" PYTHONHOME="\$d/python" PYTHONNOUSERSITE=1 \\
RUBYLIB="\$d/ruby/$rbver.0:\$d/ruby/$rbver.0/x86_64-linux-gnu" exec "\$d/klayout.bin" "\$@"
WRAP
  chmod 755 "$K/klayout"
fi

# ---- self-check: every non-glibc dependency resolves INSIDE the bundle --------
leaks="$(find "$B" -type f \( -name '*.bin' -o -name '*.so*' -o -name 'yosys-abc' \) -print0 \
         | xargs -0 -n1 sh -c 'env -u LD_LIBRARY_PATH ldd "$1" 2>/dev/null' _ \
         | awk '/=> \//{print $1, $3} /not found/{print $1, "NOTFOUND"}' | sort -u \
         | grep -vE "$GLIBC_RE" | grep -v " $B/" || true)"
if [[ -n "$leaks" ]]; then
  echo "bundle is not self-contained; these resolve outside it:" >&2; echo "$leaks" | head -20 >&2; exit 4
fi

floor="$(find "$B" -type f \( -name '*.bin' -o -name '*.so*' -o -name 'yosys-abc' \) -exec objdump -T {} + 2>/dev/null \
         | grep -oE 'GLIBC_[0-9]+\.[0-9]+' | sort -uV | tail -1)"
python3 - "$B" "$or_ver" "$ys_ver" "$orfs_commit" "${floor#GLIBC_}" "$kl_ver" <<'EOF'
import hashlib, json, os, sys
b, orv, ysv, commit, floor, klv = sys.argv[1:7]
files = {}
for root, _, names in os.walk(b):
    for n in sorted(names):
        p = os.path.join(root, n)
        if os.path.isfile(p) and not os.path.islink(p):
            files[os.path.relpath(p, b)] = hashlib.sha256(open(p, "rb").read()).hexdigest()
env = {"OPENROAD_EXE": "openroad/bin/openroad", "STA_EXE": "openroad/bin/sta",
       "YOSYS_EXE": "yosys/bin/yosys"}
if klv:
    env["KLAYOUT_CMD"] = "klayout/klayout"
json.dump({"openroad": orv, "yosys": ysv, "klayout": klv or None, "orfs_commit": commit, "glibc_min": floor,
           "env": env,
           "files": files}, open(os.path.join(b, "BUNDLE.json"), "w"), indent=1)
EOF
tar --owner=0 --group=0 --numeric-owner -C "$OUT" -czf "$OUT/$name.tar.gz" "$name"
sha="$(sha256sum "$OUT/$name.tar.gz" | awk '{print $1}')"
echo "$sha  $name.tar.gz" > "$OUT/$name.tar.gz.sha256"
echo "bundle: $OUT/$name.tar.gz  ($(du -h "$OUT/$name.tar.gz" | cut -f1), sha256 $sha)"
echo "orfs_commit=$orfs_commit glibc_min=${floor#GLIBC_} libs=${#SEEN[@]}"
