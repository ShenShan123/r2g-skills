# M1 Via-in-Pin Behavior in the Pinned OpenROAD Version

Version: OpenROAD 26Q3-318-g6b9d7fb806. Source root: /home/yangao/r2g_toolchain/OpenROAD-flow-scripts/tools/OpenROAD/src/drt. This historical investigation only read source and asap7_asym_via_20261116 logs; it did not modify tools or inputs.

## Root cause

At src/pa/FlexPA_acc_point.cpp:798, viaMaxExt() identifies access points within three layer widths of a cell side boundary. ASAP7 M1 width is 18 nm, giving 54 nm. For these points is_side_bound=true, and the condition at line 835 skips **Y-direction** enclosure checking while retaining X checks.

filterViaAccess() tests the via-in-pin layer range at line 868, calls viaMaxExt() at 929, and rejects a candidate only when via_in_pin && max_ext at 931. Thus side-edge access points may extend via metal beyond the pin in Y even with M1 via-in-pin enabled. This is weaker than the README line 201 claim of fully contained enclosure.

The exception is triggered by geometry; no disabling configuration was found. It explains enabled via-in-pin logs alongside protruding M1 landings. Independent marker analysis places residual V0 errors on these side-edge branches.

Via-in-pin operates during pin-access candidate filtering. Detailed routing can later call routeNet_postAstarPatchMinAreaVio() at src/dr/FlexDR_maze.cpp:3275; subsequent patch paths do not consult via-in-pin settings.

## Existing parameters

| Parameter | Actual behavior | Relevance |
|---|---|---|
| -via_access_layer M1 | Disables planar access on this layer and below | Mostly redundant: default layer 2 is M1, and via-in-pin already disables planar access in createMultipleAccessPoints() |
| -clean_patches | Removes extra patches when minimum-area constraints allow | Does not repair a protruding via landing itself |
| -disable_via_gen | Disables automatic via generation for unmatched directions | Generation covers routing-layer ranges; the lowest routing layer is M2 and V1 is outside this range |
| -min_access_points N | Raises target candidate count | Does not guarantee location or strict enclosure |
| -no_pin_access | Reuses existing access points | Does not improve invalid points |

In ORFS flow/scripts/detail_route.tcl:44, DETAILED_ROUTE_ARGS replaces automatically assembled additional_args. A clean_patches experiment must retain original constraints such as -via_in_pin_bottom_layer M1 -via_in_pin_top_layer M1 -droute_end_iter 64 -verbose 1.

## Minimal verifiable repair

The initial preferred candidate is a conservative LEF access window: restrict routable pin regions and retain remaining real metal as OBS. Verify the window is a subset of the original pin, keep native GDS and logic unchanged, and rerun full DRC/LVS.

If windows make pins inaccessible, an isolated OpenROAD build can enforce Y checking when explicit via-in-pin covers the layer, retaining old behavior otherwise. At the time of this analysis, that source repair was proposed but not built. The later successful UART implementation is documented in the main multi-design report.

Source locations:

- src/pa/FlexPA_acc_point.cpp:325: planar-access disabling.
- :798: viaMaxExt; :868: enabling condition; :931: candidate rejection.
- src/dr/FlexDR_maze.cpp:2160: area-based patch cleanup; :3275: later minimum-area patching.
- src/drt-global.h:48: via generation enabled and patch cleanup disabled by default.
- src/io/io_parser_helper.cpp:130: generated-via layer range.
