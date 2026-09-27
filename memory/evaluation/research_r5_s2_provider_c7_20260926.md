# R5 S2 C7：真实 DeepSeek DEV 三策略校准

已完成 **3 次真实 DeepSeek V4.1-Flash 调用、3 次隔离 native 评估**。三个策略均首轮 PASS，均自行提出 `replace_sources`，候选源码完全相同。**未观察到 TEHM 额外收益，也没有执行模型提出的 Memory asset action。** 这是已观察过的 `dev_task_001` 校准，不是新的独立 transfer、ΔMemory 归因、FINAL 或论文统计样本。

## 授权与固定协议

用户分别明确授权调用预算、V4.1-Flash 版本，以及将具体 buggy RTL、公开任务/枚举反馈和 TEHM 绑定模板发送到官方 endpoint。首次执行被自动审批在进程创建前拦截，零请求；补齐具体 payload 授权后执行，不绕过拦截。

官方 `https://api.deepseek.com/chat/completions`，请求/响应 model 均为 `deepseek-flash`；登记版本 DeepSeek V4.1-Flash。依据 [官方模型说明](https://api-docs.deepseek.com/quick_start/pricing/) 和 [chat completion 合约](https://api-docs.deepseek.com/api/create-chat-completion/)，固定 `thinking=disabled`、temperature=0、JSON object、stream=false、max_tokens=2000。三个响应带有不同 request ID 和相同 system fingerprint；证据保留服务标识，不宣称固定了不可变权重版本或随机种子。

预算：1 DEV task × 3 policies，每臂最多 2 次、全局最多 6 次；每次输入 ≤8000、输出 ≤2000，累计 ≤60000 tokens；零 HTTP 重试。PASS/UNKNOWN 停止该臂。三个首轮均 PASS，未消耗第二轮额度；剩余额度不用于新增任务或事后选优。

沿用 C5 相同 prompt/action catalog、source-only worker、C2 私有 evaluator 和 C4 已实际检索并冻结的公共/私有回执。C7 不伪称重新执行了 C4 后端查询。No Memory/Legacy 的公共 Memory 都为空，因此请求字节完全一致；Legacy EMPTY 仅适用于已登记查询，不代表 Legacy 全局无能力。TEHM 接收一个未绑定答案的公共模板。

多轮协议在首次调用前登记：每轮均从原始 buggy RTL 产生候选，第二轮仅见本臂上一轮的 action、candidate digest 和公共枚举/固定拒绝状态，不重新查询另一个源码版本。实测双轮仅限明确标注的 synthetic 演练，真实运行没有第二轮。

## 实际结果与成本

| 策略 | 真实调用 | 输入 tokens | 输出 tokens | 动作 | target / preservation / native |
|---|---:|---:|---:|---|---|
| No Persistent Memory | 1 | 1650 | 1114 | replace_sources | PASS / PASS / PASS |
| Legacy Memory | 1 | 1650 | 1114 | replace_sources | PASS / PASS / PASS |
| TEHM | 1 | 1879 | 1114 | replace_sources | PASS / PASS / PASS |

合计 **8521 tokens**，其中输入 5179、输出 3342；usage 以官方响应为准，缓存命中也计入输入。TEHM 本次多 229 输入 tokens，不能据一个 DEV 任务外推效益或成本分布。真实 HTTP 错误/重试为 0；三次均 finish_reason=stop。账本预留 30000，实际使用与预留明确区分，不退款或转移给其他臂。

三份候选 SHA256 均为 `652723dccd678e33d23547086b3efae819a9060ff4897b1391331beaca5e7253`，与先前 DEV 正确候选相同，但 C7 输入包不包含该已修好源码。候选来自实际响应，模型只获得 buggy 源和白名单公开上下文。已观察 DEV 仍可能受模型预训练/公开源码记忆影响；本次不作 unseen 或 answer-free transfer 结论。

输入控制使用“序列化请求 UTF-8 字节数 + 512”保守准入检查，**不是精确官方 tokenizer，也不证明对任意未来模型的通用 token 上界**。每次仍预留完整 8000 输入 tokens；原始 usage 缺失、不一致或超限会停止整批。本次实际三份输入均远低于 8000。首轮请求字节分别 6263、6263、7011。

## 实现、审计与恢复

新增 `research_r5_s2_provider.py`、`research_r5_s2_provider_run.py` 和 6 组离线 checks。显式 LiveLedger 复用 C6 的 flock/fsync/一次性 claim/预算状态机，历史 OFFLINE_ONLY 组件和历史冻结证据未重写。凭据仅在进程内选择唯一官方 Flash block，不 source `.llm_env`，不被后续 Pro block 覆盖。拒绝 HTTP redirect，不自动重试；密钥不进入 prompt、bundle、日志或 Git。

6 组 provider 检查通过。另一次禁网 synthetic 双轮演练实际评估六个候选，三臂各 FAIL→PASS，检验原源重置、同臂反馈与 60000 预留上限；其中 usage 是明确标记的伪造 fixture 数据，不计为真实用量。

原始审计从三份 HTTP response 重新解析 usage/提案，从 ledger 验证调用和预算，从 native XML、stdout summary、进程退出码重建判定，并核对 private raw files 和候选摘要。C2 内层历史 `SCRIPTED_DEV_NATIVE_INTEGRATION_NOT_AGENT` 标签只描述适配器开发来源；C7 外层记录真实模型提案 provenance，不把 C2 内层 model_calls=0 当作本批调用总数。

第二副本实际恢复：从已校验 original tar 解压，在禁网 namespace 中重放保存的三个模型响应，再运行三次 source-only worker/native evaluator。规范化三行结果完全一致，24 个请求/响应/提案/动作/源码/预处理/反馈文件逐字节一致。**恢复新增模型调用 0**；native 重跑不增加样本数。VVP 地址相关字节不要求相同。

工作根：`/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/agent/s2-provider-c7/`。
第二副本：`/data1/zhangdy/.cache/tmp/tehm-r5-s2-provider-5wvaxzaf/`，**同机临时存储，不是异机灾备**。

- 冻结 plan：`2135421c72809d163b02ba5a7cf48a547f1ebe0299be8c3fbe80dc3505f2c491`
- 恢复回执：`333b7f03859d75379d8a2ab3f5dbfa4cf0415983e2c8c22c050e5f3aaca27d94`
- 入口：`prepare.py`、冻结 `research_r5_s2_provider_run`、`audit.py`、`recover.py`；`seal.json` 索引最终包及校验。

## 主链边界

这次补上了“真实 provider → 共享 controller → 候选 → 私有 oracle → 官方 usage → 保存响应恢复”的 DEV 路径；没有改 frozen gen5、TRAIN Memory、F1、上游仓库，没有在线学习或 push。

R5-7 的已知 DEV Agent 接线已实测，但独立来源 Agent 比较/ΔMemory 收益尚未成立；R5-8 论文规模的最终来源抽样、独立性和样本量依据仍未完成。下一步回到这些任务，不继续打磨本 DEV 结果、不花剩余调用追求 TEHM 正收益，也不把三臂/两子测试当作三个/六个独立样本。
