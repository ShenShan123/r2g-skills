#!/usr/bin/env bash
# Collect a KLayout install and everything it loads into one directory, ready for
# make_toolchain_bundle.sh --klayout-dir. Needs only bash, ldd, cp, find and tar,
# so it runs inside the signoff container image (which has no patchelf/rsync):
# that is where the KLayout the r2g datasets were signed off with lives.
#
#   collect_klayout.sh <path to klayout binary> <out dir>
#
# Out dir: klayout.bin, libklayout_* + db_plugins/ lay_plugins/ pymod/, lib/
# (every non-glibc dependency, resolved by THIS machine's loader), python/lib/
# (trimmed stdlib), ruby/ (stdlib), VERSION.
set -euo pipefail
KL="$1"; K="$2"
GLIBC_RE='^(libc|libm|libpthread|libdl|librt|libutil|libresolv|libnsl|libanl|libmvec|ld-linux-x86-64)\.so'
kinst="$(dirname "$(readlink -f "$KL")")/../lib/klayout"
[[ -d "$kinst" ]] || { echo "no klayout install dir at $kinst" >&2; exit 3; }
rm -rf "$K"; mkdir -p "$K/lib" "$K/python/lib" "$K/ruby"
cp -a "$kinst/." "$K/"                    # libklayout_*, db_plugins/, lay_plugins/, pymod/
cp -L "$KL" "$K/klayout.bin"
"$KL" -b -v | awk '{print $2}' > "$K/VERSION"

# the embedded interpreters' standard libraries (KLayout starts Python for every
# -r script; the DRC/LVS decks are Ruby)
pyver="$(env -u LD_LIBRARY_PATH ldd "$KL" | grep -oE 'libpython3\.[0-9]+' | head -1 | sed 's/lib//')"
rbver="$(env -u LD_LIBRARY_PATH ldd "$KL" | grep -oE 'libruby-?[0-9]+\.[0-9]+' | head -1 | grep -oE '[0-9]+\.[0-9]+')"
tar -C /usr/lib -cf - --exclude=test --exclude=tests --exclude=site-packages --exclude=dist-packages \
    --exclude=__pycache__ --exclude=idlelib --exclude=tkinter --exclude=lib2to3 --exclude=ensurepip \
    --exclude=turtledemo "$pyver" | tar -C "$K/python/lib" -xf -
tar -C /usr/lib/ruby -cf - "$rbver.0" | tar -C "$K/ruby" -xf -
if [[ -d "/usr/lib/x86_64-linux-gnu/ruby/$rbver.0" ]]; then
  mkdir -p "$K/ruby/$rbver.0/x86_64-linux-gnu"
  tar -C "/usr/lib/x86_64-linux-gnu/ruby/$rbver.0" -cf - . | tar -C "$K/ruby/$rbver.0/x86_64-linux-gnu" -xf -
fi
chmod -R u+w "$K"

# every library the binary, libklayout_*, the plugins and the interpreters'
# extension modules load, until no new one appears. Resolved with the ORIGINAL
# paths still in place (the copies keep their original RUNPATH until finalized).
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
echo "collected KLayout $(cat "$K/VERSION") into $K ($(ls "$K/lib" | wc -l) libs, python $pyver, ruby $rbver)"
