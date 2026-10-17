#!/usr/bin/env bash
# Build a WRITE-ISOLATED copy of a corpus run directory.
#
# signoff45.py copies the DRC report and log back into the run directory it is
# given. Pointing that at a symlink TO the corpus directory makes the checker
# overwrite the corpus: C00358's original drc_signoff.log is gone, replaced by a
# test run's, and 15 designs' 6_drc.lyrdb were rewritten by a timing
# measurement. The artifacts are equivalent -- same deck, same GDS -- but the
# corpus is no longer what the pilot left, and the "report newer than the GDS"
# provenance check now sees reports dated today.
#
# The fix is a directory of symlinks to the FILES rather than a symlink to the
# directory: reads follow the links into the corpus, writes land here. No GDS is
# copied.
#
# The links must be ABSOLUTE. The first version used `ln -sf "$f"` with a
# relative $f, so every link resolved only from /proj/workarea/yangao and the
# isolated directory was silently unreadable -- the same defect as the relative
# podman bind mount from earlier today. Hence the self-check at the end: this
# script refuses to report success on links it cannot read.
#
# Usage: isolate_run.sh <corpus_runs_dir> <design_name> <dest_runs_dir>
set -uo pipefail
SRC=$(cd "${1:?corpus runs dir}" && pwd)       # absolute, always
NAME=${2:?design name}
DEST=${3:?destination runs dir}

s="$SRC/$NAME"
[ -d "$s" ] || { echo "找不到 $s" >&2; exit 1; }
d="$DEST/$NAME"
mkdir -p "$d"
n=0
for f in "$s"/*; do
  b=$(basename "$f")
  ln -sfn "$f" "$d/$b" && n=$((n+1))
done
# paths the checker WRITES must not be links into the corpus, or the write
# follows the link into it
for w in drc_signoff.log lvs_signoff.log 6_drc.lyrdb netgen_lvs.log netgen_lvs.rpt; do
  [ -L "$d/$w" ] && rm -f "$d/$w"
done

# self-check: a link that cannot be read is worse than no link, because the
# checker will report a tool failure instead of a bad path
bad=0
for must in config.mk 6_final.gds; do
  if [ ! -r "$d/$must" ]; then
    echo "!! $d/$must 读不到 -- 链是坏的" >&2
    bad=1
  fi
done
[ "$bad" = 0 ] || exit 1
echo "已隔离 $NAME: $n 个条目链入，可写路径留空，config.mk 与 6_final.gds 可读 ✓"
