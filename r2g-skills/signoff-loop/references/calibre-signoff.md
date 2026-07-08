# Calibre signoff for ASAP7 (authoritative DRC/LVS) — scaffold + runbook

**Why this exists.** ASAP7's only *open-source* DRC deck is the community KLayout
`asap7.lydrc` (from `laurentc2/ASAP7_for_KLayout`), a reverse-engineering of the DRM that
carries an **irreducible false-violation floor** (`V*.M*.AUX`, `LIG*`, `V0`, `M4.S.5` —
tech-LEF-vs-deck artifacts present even on ORFS's own `gcd`, and OpenROAD discussion #854
measures 348 on `gcd`). No flow lever clears it — see
`failure-patterns.md` "ASAP7 residual-DRC-by-design". The **only** way to a genuinely
clean-able ASAP7 DRC/LVS signoff is the **official (encrypted) ASAP7 Calibre deck** run
under Mentor/Siemens Calibre. This machine HAS Calibre + a license; it does **not** have
the deck (only the repo's placeholder READMEs). This scaffold makes the flow *deck-ready*:
it runs the moment the deck is installed and cleanly skips until then.

## Status on this machine (2026-07-01)

| Piece | State |
| --- | --- |
| Calibre binary + license | ✅ `2025.1_16.10` at `$MGC_HOME`; `MGLS_LICENSE_FILE=27020@sv01` |
| ASAP7 Calibre DRC/LVS decks | ❌ absent — only 418-byte placeholder READMEs at `$ASAP7_PDK_DIR/calibre/ruledirs/{drc,lvs,rcx}/` |
| `run_calibre_drc.sh` + `extract_calibre_drc.py` | ✅ scaffolded, tested, **guarded** (skip until deck present) |
| Calibre LVS | ⏳ not yet scaffolded (needs a source CDL netlist; see below) |

## Getting the decks (manual — cannot be scripted)

The decks are **not redistributable** and there is **no direct download**. Request them at
<https://asap.asu.edu/download/> (requires a `.edu` email + license agreement acceptance +
CAPTCHA + manual approval). After approval you receive a `calibre/` directory; **replace**
the placeholder tree so these exact paths resolve (they match the script's defaults):

```
$ASAP7_PDK_DIR/calibre/ruledirs/drc/drcRules_calibre_asap7.rul
$ASAP7_PDK_DIR/calibre/ruledirs/lvs/lvsRules_calibre_asap7.rul
$ASAP7_PDK_DIR/calibre/ruledirs/rcx/rcxControl_calibre_asap7.rul
```

`ASAP7_PDK_DIR` defaults to `/proj/workarea/LIB/asap7/asap7_pdk_r1p7`.

## ⚠️ Version risk — smoke-test BEFORE trusting

The deck was tested with Calibre **`aoi_cal_2017.4_19.14`**; the ASU usage notes warn it is
*"incompatible with the xACT engine in 2018.2"* and *"other Calibre versions that succeed
2017.4 may be incompatible as well."* This machine runs **2025.1** — 8 years newer, and
encrypted SVRF is version-sensitive. So the FIRST thing to do once the deck lands:

```bash
R2G_CALIBRE_SMOKE=1 bash r2g-skills/signoff-loop/scripts/flow/run_calibre_drc.sh <project-dir> asap7
```

`run_calibre_drc.sh` detects a deck load/version/license failure in the Calibre log and
writes `status=incompatible` (not a fake clean/fail). If it comes back `incompatible`, the
options are: (a) obtain a Calibre version near 2017.4, or (b) stay on the honest KLayout
floor + skipped-LVS. Do **not** paper over an incompatible deck.

## Running Calibre DRC (once the deck is installed)

```bash
bash r2g-skills/signoff-loop/scripts/flow/run_calibre_drc.sh <project-dir> asap7
# -> <project-dir>/drc/calibre/<design>.drc.results + .summary
# -> <project-dir>/drc/calibre_drc_result.json  {status, total_violations, categories, engine:calibre}
```

It restages the backend `6_final.gds` (via `_restage_for_signoff.sh`), resolves the top
cell (`metadata.json` top_module → DESIGN_NAME, or `CALIBRE_TOP_CELL`), generates a standard
batch SVRF runset, and runs `calibre -drc -hier -turbo` under a killable `setsid timeout`.
`extract_calibre_drc.py` parses the ASCII results DB into the **same schema as
`extract_drc.py`** (so ingest is engine-agnostic) and ships the mtime **freshness guard**
from day one (a stale results DB under a fresh run log → `stale`, never a fabricated clean —
the lesson of the 2026-06-30 DRC fabricated-clean bug).

Env knobs: `ASAP7_PDK_DIR`, `CALIBRE_DRC_RULES`, `CALIBRE_TOP_CELL`, `CALIBRE_LAYERMAP`
(if Calibre reports empty layers — the ORFS GDS layer/datatype map must match the deck),
`CALIBRE_EXE`, `CALIBRE_DRC_TIMEOUT`, `R2G_CALIBRE_SMOKE`.

## Integration into the loop (next steps, not yet wired)

The scaffold is a standalone stage. To make Calibre the asap7 DRC *engine of record*:

1. **Engine switch in `run_drc.sh` / `fix_signoff.sh`**: when `platform==asap7` and the
   Calibre deck is present (and smoke-passes), prefer `run_calibre_drc.sh` and write its
   verdict to `reports/drc.json` (it already emits the `extract_drc` schema, with
   `engine:"calibre"`). Gate behind `R2G_DRC_ENGINE=calibre` initially.
2. **Ingest**: `reports/drc.json` flows through `ingest_run.py` unchanged (same fields).
   The `engine` field lets analytics separate authoritative-Calibre from KLayout-floor rows.
   A genuine `drc=clean` on asap7 becomes *achievable* → the learning loop can finally earn
   asap7 DRC promotions (today "no asap7 promotion" is honest platform truth *because* the
   KLayout deck is uncleanable; Calibre changes that premise — update the
   "ASAP7 residual-DRC-by-design" note when it does).
3. **LVS** (`run_calibre_lvs.sh`, TODO): needs a source netlist. ORFS `make cdl` currently
   fails on asap7 (`can't read "::env(CDL_FILE)"`) — that must be fixed first (generate a
   CDL/SPICE from the final netlist), then `calibre -lvs` against
   `lvsRules_calibre_asap7.rul` with `LVS REPORT`. Mirror the DRC scaffold's guard +
   freshness-guard + skip semantics.
4. **RCX** (`rcxControl_calibre_asap7.rul`): `calibre -xact` for signoff parasitics — a later
   parallel to `run_rcx.sh`.

## Tests

`tests/test_extract_calibre_drc.py` — results-DB counting, clean detection, the freshness
guard (stale→not-clean + fresh→clean converse), and honoring a fresh skip marker. The
`run_calibre_drc.sh` skip path is validated end-to-end on a real asap7 design (deck absent
→ `status=skipped` + download pointer, exit 0); the deck-present path (runset generation +
extract wiring) is validated with a Calibre stub. The **real** Calibre invocation against
the encrypted deck is the one piece that can only be validated once the deck is installed.
