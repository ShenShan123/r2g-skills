# R5 v9 APEX DEV oracle（2026-09-27）

APEX `stream_skid.sv` 固定于 `SigmanticAI/apex-inference-chip` commit `5998060b8da273b8d10e4c9e8565217d6c346f60`，仅为已观察 DEV。上游 checkout clean；`rtl/xbr/stream_skid.sv` 与同仓库 `reference/run1_gemm_systolic/rtl/stream_skid.sv` 的 SHA256 同为 `3addbc1532e66facaa38d3ecb6303e6dd919dc765cd3e8bc9bb3854d04e0031c`，不能把 reference 视作独立来源或暴露给未来 target agent。

在 `/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/dev/v9-apex-r1/` 先冻结 spec SHA256 `2e7d9f4a07c90e09fa25fdcea494f92f8ad2b46704f46c470d5447b009193b46` 和公开接口 research-augmented testbench SHA256 `42c785ecdd8b738884212f06ae146eac3d6ee88970bdbd79c558145f3a30c688`，再执行独立 clean／fault 构建。两臂 Verilator 5.038 `--binary --timing --assert` 编译 exit 0。clean 的 `+TARGET` 与 `+PRESERVATION` 均 PASS。

按预登记在 evaluator 副本中唯一地将 drain RHS 从 `skid_data` 改成实时 `s_data`，fault SHA256 `da50a68d67a4fb1a2197a749ef1c3649bce34875edd45bca22a58f18037bf66b`，`diff -u` 仅该一行。故障臂 `+TARGET` exit 134 且明确报 `TARGET_DRAIN_FAIL expected=3c actual=e7`，`+PRESERVATION` exit 0 且无背压直通／reset 检查 PASS。四份原始日志、编译日志及逐项核对的 7 个输入／日志哈希见隔离包 `oracle/receipt.json`，receipt SHA256 `779c1df19339385db9d59852825d6d6d5ad05409986c7d8f8851b900f220b4f8`。

结论只授予此一条 augmented DEV payload-drain probe 的判定力；不等于 APEX native skid suite 合格、所有背压行为覆盖、source-only binder 修复、未见迁移或 ΔMemory。候选动作尚未执行，Memory 未更新，模型调用为零。后续 binder 只能看 buggy source closure、已选 Asset 与公开 context，不得读取这个 evaluator 包或上游 reference。
