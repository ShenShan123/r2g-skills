# 停止扩展试验与原规则复核

## 执行状态

2026-09-16 UTC，在核验进程身份后，向203的进程组3964389、3973944发送SIGTERM，
停止 mean_banked_margin_repeat2_203 的runner及ORFS子进程。复查未发现残留的
run_mean、OpenROAD、run_command.py进程或tmux会话；本次检查也未发现用户crontab
或匹配的监督进程。没有启动新实验，没有删除已有证据，没有修改正式成绩或论文。
此次停止是用户取消试验，不是完整运行后判定修复失败。

## 计分结论

full_r2g_supplement_208/complete.json记录完成13题，strict_clean为8：7个DRC、1个setup。
这是现有Full R2G补充实验结果，不改变原M0/M1/M2/M3结果，也不改成独立未见测试的表述。

原action_policy.json保护RTL源码闭包、10 ns时钟、面积和检查集合。
允许的配置包括分层综合、ABC映射目标、提前布局时序修复、指定边的引脚排除，
SETUP_SLACK_MARGIN只登记0.2。源码CSD改写、存储读取结构改写和0.30余量均不能
直接计入这一动作空间下的成绩，功能等价也不等于满足原动作范围。

Pipelined IIR后来通过的结果属于源码改写开发证据。均值滤波器属于A-propose，
本来就不是B组13题之一。不能据此把原Full R2G改成9/13或10/13。

## 剩余五题的判断

| 设计 | 已核查的证据 | 工程判断 |
| --- | --- | --- |
| FFT8 | 基线WNS -0.366280 ns；固定面积左侧排除组合 -0.416959 ns；历史 -0.307005 ns面积不同 | 缺口最小，但没有明确的新有效原范围动作；不继续盲试 |
| Pipelined IIR | 原补充臂分层布局修复后 -2.053510 ns；后来clean依赖源码改写 | 原范围尚无消除缺口的证据 |
| Blake2s_core | 分层布局修复由 -2.965470改善至 -2.000020 ns，但新增70个DRC | 有时序改善但不是成功，还需同时消除物理回归 |
| Blake2s | 已记录固定面积开发结果仍约 -3.110458 ns；较好历史值使用更大面积 | 原范围投入产出希望较低 |
| Tanh | 基线约 -13.091900 ns；原补充臂没有合法匹配策略 | 原范围没有支持近期可clean的证据；不可把源码重写结果混入 |

以上不是证明所有允许组合均不可能成功，也不把未执行的策略算成物理失败。
FFT局部尺寸和换位筛查已经做过：统一增大8个FA驱动没有改善；局部换位只在
估计寄生下改善约0.016444 ns，未消除违例，且不是最终签核结果。
单纯负裕量较小不能保证增加驱动或重复布局就会成功。

## 建议

论文采用现有8/13（61.54%）作为这条Full R2G补充臂结果，保留各题失败原因。
停止为达到预设10/13而进行无上限探索。只有发现明确的、尚未试过且符合原规则的
路径级机制，并获得新的运行授权，才考虑有限追加试验；当前不派发。
源码重写研究保留为后续扩展能力证据，不晋升冒充原规则recipe，不覆盖原成绩。

## 证据入口

- full_r2g_supplement_208/action_policy.json
- full_r2g_supplement_208/complete.json
- fft_best_audit/review_zh.md
- fft_fixed_left_review/review_zh.md
- fft_fixed_left_review/local_screen_review_zh.md
- setup_path_analysis_20260913/analysis_zh.md
- remaining_five_priority_zh.md（其中FFT面积待核查事项已由上述后续审计解决）
