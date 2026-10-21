#!/usr/bin/env bash
# Deploy a file to the campaign host safely: write <dest>.new, verify its sha256
# against the local copy ON the host, then rename over <dest>.
#
# Rename, never overwrite in place. bash reads a script incrementally by byte
# offset, so overwriting a script that a running instance is executing makes it
# resume at a stale offset in the new text -- on 2026-10-21 that killed a
# finished 2.5 h A/B with "syntax error near unexpected token" one line after
# its flow returned. A rename replaces only the directory entry; the running
# instance keeps reading the old inode it already holds open. The host forbids
# scp, so the bytes travel over ssh stdin.
#
# Usage: tools/deploy216.sh <local file> <absolute path on memlab216>
set -euo pipefail
src=$1; dst=$2; host=${DEPLOY_HOST:-memlab216}
want=$(sha256sum "$src" | cut -c1-64)
ssh -o ConnectTimeout=120 "$host" "cat > '$dst.new'" < "$src"
ssh -o ConnectTimeout=120 "$host" "got=\$(sha256sum '$dst.new' | cut -c1-64); \
  if [ \"\$got\" != '$want' ]; then echo 'sha mismatch, not deployed'; rm -f '$dst.new'; exit 1; fi; \
  chmod --reference='$dst' '$dst.new' 2>/dev/null || true; mv -f '$dst.new' '$dst'; \
  echo \"deployed $(basename "$src") -> $dst (sha ${want:0:16})\""
