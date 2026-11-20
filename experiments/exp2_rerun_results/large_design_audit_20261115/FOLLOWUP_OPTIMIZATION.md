# R2G 小范围提速检查与再次重跑

用户授权：找到明确的小改动就修复重跑，否则保留当前运行。

确认问题：正式实验每批 revision_batch=50，旧 DeepSeek 的 86 个 FINAL
cohort 与 GPT 的 73 个 FINAL cohort 实际均为 50–54 个仓库，但调度器默认
minimum_calibration_revisions=100。两组数据库均无
phase2_round_calibration_round 记录，导致 FINAL 批次的产出/成本反馈未用于
该路径的查询优先级更新。已有 mid-round 故障校准是独立机制，不能混为一谈。

修改：正式 R2G runner 传递 --minimum-calibration-revisions=args.revision_batch。
仅增加一对参数，沿用已有实现；通用调度器默认值、并发、预算、评分标准不变。
可配置门槛测试覆盖 50 个仓库并检查持久化产出记录。相关测试 165 项通过。
实际时间收益由本次重跑测量，不预先宣称加速比例。

未采用 VHDL 仓库过滤：虽然下载占比偏高，旧两组仍分别从这些仓库获得
24、25 个 publishable precheck；仓库主语言不能代替候选源文件语言。

新提交：b8d88b25220d83f29942709e5f432be5b600e6a8。
新 campaign：exp2-v5fix2-20261115。
两组均于 2026-11-15 12:17:19 UTC+8 启动，使用全新状态/缓存。
203 checkout：/data/yangao/exp2_rerun/r2g-exp2-v5fix2-20261115。
203 runs：/data/yangao/exp2_rerun/runs/exp2-v5fix2-20261115。
启动核验：模型路由 2/2 可用，协议 lint 通过，所有实现绑定 hash 匹配，
与保留的 native 组使用相同 task spec；DeepSeek CPU 28–31，GPT CPU 32–35。

上一轮 exp2-v5fix-20261115 已停止，记录保留于原目录，含 SUPERSEDED 标记。
核验无该 campaign 残余进程。native 组仍为 exp2-v5b-20261114，未重跑。
启动证据见同目录 launch_verification_v5fix2.json。当前仅确认启动；尚无最终实验结果。
