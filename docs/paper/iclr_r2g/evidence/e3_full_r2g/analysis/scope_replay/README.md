# Full R2G 适用范围离线回放

日期：2026-09-16。仅读取原运行记录，未启动ORFS、未修改本体、知识库、论文或正式成绩。
运行：`python3 replay.py`；测试：`python3 -m unittest -v test_replay.py`（6项通过）。

## 结论

暂不收窄生产recipe范围。已检查的简单WNS门槛会误伤成功案例，且本轮没有重复动作可消除。
现有结果保持：13题、11次修复ORFS、8题clean。调用数不包含baseline及开发验证成本。

| Setup最低WNS门槛 | 保留的修复调用 | 回放clean | 漏掉的原成功案例 |
| --- | ---: | ---: | --- |
| -3.0 ns（现有） | 11 | 8/13 | 无 |
| -2.5 ns | 9 | 7/13 | iir_biquad_axis |
| -2.0 ns | 8 | 7/13 | iir_biquad_axis |
| -1.0 ns | 8 | 7/13 | iir_biquad_axis |

保留成功的IIR基线WNS为-2.69817 ns，修复后+0.106265 ns；FFT8和Pipelined IIR的
基线缺口更小但未成功。因此“只修轻微违例”不是有效的无损筛选依据。
本表是基于历史结果的敏感性分析，不是重新测得的正式成绩，也不是提前注册的筛选实验。

## 核验范围

- 每题读取原selection文件的运行前setup_scope_evidence，筛选函数不读取题名或修复结果。
- 仅在筛选之后使用原trial结果计算保留成功数；未模拟被跳过之后其他策略的替代行为。
- 输入JSON的SHA256列入replay_results.json，读取结束再次核对未变化。
- 按题核对动作配置，重复效果次数为0；不能凭去重宣称本轮能省调用。
- 本体diagnose_signoff_fix.py已经跳过两个目标配置均生效的层次综合加布局修复动作。
- 原Full R2G runner已有单题策略排除、配置效果去重，以及任务result.json断点跳过。

## 不能直接据此修改的条件

现有setup范围证据主要是平台、ABC_AREA、routing_clean和WNS。尚未建立一个有足够
成功/失败路径证据支撑的结构判据，能稳定保留成功案例并拒绝失败案例。三个失败题
的已有关键路径分析不足以独立验证此类判据，不能直接按题名、算法名或事后结果屏蔽。
这一结论不证明未来无法开发更好的范围预测器。

若事先知道哪三次会失败，本轮最多可省3/11（27.27%）调用；这是事后理想上限，
不是当前agent具备的预测能力。失败缓存可用于相同完整任务及工具环境下的重复请求，
但不能降低本轮首次遇到该题的历史成本。缓存也不能把网络、超时等不确定失败当作
稳定物理失败永久跳过。当前未新增跨运行缓存功能。

## 后续决定

不为降低调用数而修改已完成的原成绩，不启动新物理实验。保留8/13和11次实测调用。
只有以后积累了可在动作执行前获得、且有独立验证支持的路径级预测特征，才考虑新的
范围版本，并同时检查误拒成功案例的比例与总修复率，而不只看ORFS次数。

证据：../full_r2g_supplement_208/complete.json及其tasks/*/selection_01.json；
本体r2g-skills/signoff-loop/knowledge/setup_scope.py；本体scripts/reports/diagnose_signoff_fix.py；
../run_full_r2g_supplement.py。完整输入路径与哈希见replay_results.json。
