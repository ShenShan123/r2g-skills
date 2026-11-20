#!/usr/bin/env bash
# run18.sh <tag> <defgraph dir>: convert all 18 dev designs, 6 at a time, record wall seconds per design
tag=$1; S=$2; mkdir -p runs/$tag; : > runs/$tag/times.tsv
ls dev | xargs -P 6 -I{} bash -c 'a=$(date +%s.%N); R2G_DEFGRAPH='"$S"' ./convert_local.sh dev/{} runs/'"$tag"'/{} > runs/'"$tag"'/{}.log 2>&1; rc=$?; b=$(date +%s.%N); printf "%s\t%s\t%.1f\n" {} $rc $(echo "$b - $a" | bc) >> runs/'"$tag"'/times.tsv'
