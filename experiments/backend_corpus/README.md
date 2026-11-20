# Backend corpus: the 2,497 Verilog-frontend designs, and the 2,330 that synthesize

Built 2026-11-16 from memlab216 data. Nothing here is newly run; it indexes existing runs.

| file | rows | what |
| --- | --- | --- |
| `backend_corpus_2497.csv` | 2497 | every design in the backend corpus (`216:/proj/workarea/yangao/verilog_only.txt`, 2026-10-05): split, language, frontend route, post-synth cells / cell area / pilot status on nangate45, sky130hd, gf180 |
| `backend_eligible_2330.txt` | 2330 | the designs with a synthesized netlist on all three platforms (`synth_ok_all3=1`) |

**Where the 2,497 come from.** The handoff package `rtl-certified-handoff-0002` has 4,011 designs. The corpus is
every design whose frontend is Yosys-readable Verilog/SystemVerilog: frontend route `verilog-native` (2,453) or
`verilog-slang` (44). The 1,514 VHDL (`ghdl-*`) designs are excluded. It is not an ID prefix: IDs span C00001..C04011.
This set is identical to the nangate45 / sky130hd / gf180 pilot corpora (`n45_full`, `full_out`, `gf180_full`) and to
dev ∪ held-out of `../splits/`.

**Why 167 drop out.** 165 have no post-synthesis netlist on any platform. On nangate45 those failed at Yosys
canonicalize/parse (100), synth (54), `1_2_yosys` (9), synth timeout (2), or oversize (1); that tally also counts
C00766. Two more synthesized on only some platforms: `C00766__RAM_128KB__picorv32_xilinx` (gf180 only, 4.6M
cells) and `C02683__ddr3_axi__core_ddr3_controller` (not gf180). Neither can reach PnR on every platform without
frontend repair.

Cells = post-synthesis cell count from each platform's pilot (20% utilization, loose period), so they depend on
the platform's library. The size class uses nangate45 cells, the same key as the split.

## Size summary (2,330 eligible designs)

```
total 2497  synth_ok_all3 2330  split dev 1249 held_out 1246 none 2
eligible by split: Counter({'held_out': 1165, 'dev': 1163, 'none': 2})
eligible by language: [('verilog', 1732), ('systemverilog', 557), ('systemverilog+verilog', 41)]
families (eligible): 1122
nangate45 cells: min 1 p10 18 p25 165 median 803 p75 5259 p90 28842 p95 99420 p99 444330 max 3014650 sum 55199876
nangate45 cell area um2: median 1230 p90 40500 max 4790000
sky130hd cells: min 1 p10 17 p25 148 median 678 p75 4533 p90 22879 p95 79559 p99 279766 max 2154451 sum 38081744
sky130hd cell area um2: median 5490 p90 193000 max 22000000
gf180 cells: min 1 p10 17 p25 150 median 705 p75 4500 p90 24653 p95 79689 p99 322594 max 2323712 sum 44236219
gf180 cell area um2: median 28300 p90 986000 max 104000000
n45 class 0-100     472  dev  242 held_out  229
n45 class 100-1k    779  dev  388 held_out  390
n45 class 1k-5k     484  dev  237 held_out  247
n45 class 5k-20k    317  dev  157 held_out  160
n45 class >20k      278  dev  139 held_out  139
```

## Designs that completed the whole backend flow on all three platforms (signoff ignored)

"Completed" = the run's `result.json` status is `pass`, i.e. ORFS reached `6_final` (synthesis through detailed
route and final report). DRC/LVS verdicts are not considered.

| file | designs | source |
| --- | --- | --- |
| `backend_complete_all3_pilot.txt` | 2142 of 2330 (dev 1080, held-out 1060, unsplit 2) | the pilot corpora `n45_full`, `full_out`, `gf180_full` (loose period, 20% utilization), both halves |
| `backend_complete_all3_frozen_dev.txt` | 1090 of 1163 eligible dev | the frozen-spec re-runs `rerun_nangate45_20261018`, `rerun_sky130hd_20261022`, `rerun_gf180_20261026` (dev only) |

Pilot: 2142 complete on all three, 64 on two, 44 on one, 80 on none; incomplete per platform: sky130hd 172,
nangate45 115, gf180 105. Losses concentrate in the largest designs: the >20k-cell class keeps 165 of 278, every
other class keeps >=95%.

### Size of the 2142 (post-synthesis cells from each platform's pilot)

| | min | p10 | p25 | median | p75 | p90 | p95 | p99 | max | mean |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| nangate45 | 1 | 17 | 148 | 704 | 3613 | 15609 | 32081 | 91383 | 183341 | 6052 |
| sky130hd | 1 | 16 | 136 | 595 | 3122 | 12615 | 25557 | 65746 | 111248 | 4765 |
| gf180 | 1 | 16 | 134 | 603 | 3110 | 12455 | 28497 | 72522 | 145470 | 5062 |

nangate45 classes: <100: 457, 100-1k: 755, 1k-5k: 463, 5k-20k: 302, 20k-100k: 151, >=100k: 14.
Heavy-tailed: the largest 5% (107 designs) hold 53% of all cells; 143 designs have <=10 cells.
Cross-platform log10(cells) correlation 0.997-0.999 (same designs, same rank order); sky130hd/gf180 are ~7-10%
smaller than nangate45 at the median. 1057 repo families; largest lvdc_simulation (62).

## Designs with >= 100 post-synthesis cells on ALL three platforms (1836)

Filter on the 2330 synthesizable designs: keep a design only if nangate45, sky130hd AND gf180 each report >= 100 cells. Files: `backend_ge100_all3_1836.txt` (names), `backend_ge100_all3_1836.csv` (per-platform cells / area / pilot status). 26 designs are >= 100 on some platforms but not all: `backend_ge100_borderline_26.txt`.

kept 1836 (dev 910, held_out 925, none 1); families 979; flow complete on all 3 (pilot) 1663
| platform | min | p10 | p25 | median | p75 | p90 | p95 | p99 | max | mean | total cells | median area µm² |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| nangate45 | 104 | 219 | 464 | 1540 | 9008 | 43199 | 169055 | 467444 | 3014650 | 30056 | 55183346 | 2280 |
| sky130hd | 100 | 194 | 397 | 1371 | 7300 | 33078 | 107861 | 288969 | 2154451 | 20733 | 38066373 | 10400 |
| gf180 | 101 | 197 | 411 | 1382 | 7882 | 36530 | 139765 | 353077 | 2323712 | 24086 | 44221289 | 53600 |

| cells | nangate45 | sky130hd | gf180 |
|---|---|---|---|
| 100–1k | 757 | 826 | 816 |
| 1k–5k | 484 | 458 | 464 |
| 5k–20k | 317 | 307 | 301 |
| 20k–100k | 162 | 145 | 147 |
| ≥100k | 116 | 100 | 108 |

| split | nangate45 median | sky130hd median | gf180 median |
|---|---|---|---|
| dev | 1534 | 1352 | 1357 |
| held_out | 1542 | 1386 | 1398 |
## Per-platform lists: >= 100 post-synthesis cells on THAT platform

Each platform filtered independently over the 2497 corpus, using that platform's own synthesis (a design only needs to synthesize on that platform). Files: `backend_ge100_<platform>.txt` / `.csv`. Their intersection is the 1836 list above.

| platform | designs | dev | held_out | flow complete (pilot) | min | p25 | median | p75 | p90 | p95 | max | mean |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| nangate45 | 1859 | 922 | 936 | 1757 | 101 | 444 | 1516 | 8837 | 41548 | 167736 | 3014650 | 29695 |
| sky130hd | 1849 | 919 | 929 | 1692 | 100 | 391 | 1334 | 7237 | 32707 | 107305 | 2154451 | 20594 |
| gf180 | 1845 | 914 | 930 | 1751 | 101 | 401 | 1357 | 7788 | 36530 | 139765 | 4630588 | 26479 |

| cells | nangate45 | sky130hd | gf180 |
|---|---|---|---|
| 100–1k | 779 | 838 | 824 |
| 1k–5k | 484 | 458 | 464 |
| 5k–20k | 318 | 308 | 301 |
| 20k–100k | 162 | 145 | 147 |
| ≥100k | 116 | 100 | 109 |

intersection 1836, union 1864
## Per platform: completed the full backend flow (pilot status pass) AND >= 100 cells on that platform

Files: `backend_complete_ge100_<platform>.txt` / `.csv`. Completion = the pilot run reached 6_final (synthesis through detailed route); signoff not considered.

| platform | designs | dev | held_out | min | p10 | p25 | median | p75 | p90 | p95 | p99 | max | mean | total cells |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| nangate45 | 1757 | 871 | 885 | 101 | 206 | 432 | 1411 | 7754 | 31121 | 75547 | 202607 | 305264 | 13999 | 24596861 |
| sky130hd | 1692 | 846 | 845 | 100 | 183 | 363 | 1110 | 5212 | 16467 | 31205 | 72595 | 111248 | 6167 | 10433875 |
| gf180 | 1751 | 866 | 884 | 101 | 189 | 378 | 1154 | 6184 | 22108 | 45075 | 142964 | 188536 | 9480 | 16600062 |

| cells | nangate45 | sky130hd | gf180 |
|---|---|---|---|
| 100–1k | 756 | 819 | 818 |
| 1k–5k | 464 | 441 | 454 |
| 5k–20k | 308 | 297 | 293 |
| 20k–100k | 159 | 132 | 143 |
| ≥100k | 70 | 3 | 43 |