# 实验四：物理设计转图能力对比协议 v2

## 1. 研究问题与假设

实验比较冻结的 R2G 转图流水线与 GPT-5.6 Sol、Claude Opus 5、Qwen3.7 Max
开发的通用转换器，在未见设计上把相同物理设计产物转换为四阶段 PyG 图的能力。
比较对象是最终冻结的程序，不是逐题 Agent：开发结束后 hidden test 禁止再次调用 LLM。

预注册三个问题：

1. 方法能否生成满足完整数据契约、可直接供下游使用的图；
2. 未达到严格通过时，产物在文件、schema、实体、拓扑和数值层面完成到什么程度；
3. 达到同等正确性需要多少开发 Token、API 调用、转换时间和峰值内存。

## 2. v1/v1.1 的定位

v1 和 v1.1 只作为不计分协议 pilot。它们在 hidden test 物化前暴露了三个测量缺陷：

- 输入配置只给概述，模型需要猜测 `yosys_v` 等内部字段；
- 严格验证器首错即停，使每轮只能获知一个独立缺陷，最佳轮次也无法按部分进展排序；
- JSON 字符串承载完整 Python 源码，Qwen 的 16K completion 又包含大量 reasoning Token，
  导致五轮源码均被截断。

v2 只修复任务接口和测量工具，不根据 hidden-test 表现调参。v1/v1.1 的结果不得与 v2
正式结果合并，也不得宣称为模型能力成绩。

## 3. 冻结数据

沿用原固定 seed 产生的 cohort：1 个 canary、6 个 development、24 个 hidden test；
small/medium/large 各占 hidden test 的 8 个，同一源码 family 不跨集合。选择只使用转图前
可知属性，不依据图输出或模型表现。v2 必须证明 cohort SHA256 与 pilot 一致。

hidden test 在三份 LLM 转换器全部冻结并通过审计之前不得物化。开发过程中不得返回 hidden
身份、路径、输入、R2G 输出或得分。

## 4. 中立公开契约包

所有 LLM 同时收到同一份 schema-only `public_contract_v2.json`：

- `method_config.json` 的全部精确键、类型和路径语义；
- 转换器必须生成的相对文件清单；
- 所有 CSV 的精确表头；
- PyG Data/HeteroData 的 graph、node-store、edge-store 属性，tensor dtype、rank 和非样本维；
- JSON sidecar 的键结构。

该契约从不计分 public canary 机械提取，只含 schema，不含 task ID、真实路径、CSV 行、tensor
值、设计专属计数、R2G 源码或 hidden 信息。它定义的是被评分接口，不提供转换算法，因此
不会把 R2G 实现泄露给基线。

## 5. LLM 开发预算

三个模型条件完全相同：6 个开发样本、最多 6 轮/6 次调用、每模型总计 400,000 Token、
单次 completion 上限 32,768 Token。预算按 provider 实际 usage 记账；请求前根据剩余总预算
动态收紧单次上限。达到 development `6/6` strict pass 即提前停止。

每轮返回完整替换源码，通过独立 delimiter envelope 传输，不把源码嵌入 JSON。每轮均在全部
6 个 development case 上从干净输出执行。反馈包括完整静态契约分数与有界问题列表，以及
严格验证、结构统计、异常签名、时间和输出体积。模型看不到原始日志和真实身份。

最佳轮次按以下预注册字典序冻结：strict passes、静态契约通过数、严格契约通过数、统计通过
数、验证通过数、较少结构问题、较早轮次。冻结后记录源码 SHA256。

## 6. 执行边界

- 四个方法读取相同 SHA256 输入，使用相同 Python/PyTorch/PyG 环境；
- LLM 转换器不得联网、调用 subprocess、读取 R2G 源码或按设计身份特殊处理；
- R2G 与 LLM 转换时间都只计算 config 输入到图产物完成，不计 PnR 和输入物化；
- 每 case 相同 3600 秒上限，初始单 worker；只有预注册 I/O 条件满足时才扩到 2；
- 环境故障可在同一冻结程序上重跑；确定性程序错误和超时保留为方法失败；
- 失败重跑先清空该 case 输出，成功断点只跳过 attestation 完整且版本/hash 一致的 case。

## 7. 评分

### 主指标

`Strict usable rate = hidden test 中完整通过 / 24`。完整通过要求静态契约、
`validate_four_stage.py`、结构统计、因果边界、NaN/mask、共享拓扑与标签、配置不可变性全部
通过，且无超时和禁止依赖。

### 次指标

1. 静态契约完成度：所需文件、CSV header、图/store 属性、tensor dtype/shape 的通过比例；
2. 转换完成率与验证器可执行率；
3. small/medium/large 分层 strict rate；
4. 转换 wall time、峰值 RSS、输出体积；
5. 开发 Token、调用数、达到最佳轮次的学习曲线。

主结论以 strict usable rate 为准；部分完成度用于解释失败深度，不能替代严格通过。三个 LLM
分别报告，同时可给宏平均，不只报告最优模型。

## 8. 正式启动门

正式调用前必须全部满足：

1. 单元测试通过；
2. R2G public canary 对 v2 静态契约为 100%；
3. 故意删文件/属性的负样本被 linter 同时报告多个独立问题；
4. 三模型各一次不计分 transport canary 均返回可解析的 delimiter envelope；
5. hidden input 目录仍只有 canary 和 development 共 7 个；
6. prelaunch audit 和运行时/protocol SHA256 清单通过。

正式 campaign 使用新目录，保留 pilot lineage，不覆盖任何旧结果。
