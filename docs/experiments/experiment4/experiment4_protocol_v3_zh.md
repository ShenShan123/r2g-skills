# 实验四：物理设计转图能力确认性实验协议 v3

## 研究问题

比较固定 R2G 转换器与 GPT-5.6 Sol、Claude Opus 5、Qwen3.7 Max 在相同开发预算下生成的
通用转换器，能否把未见物理设计产物转换为语义正确、阶段因果正确且可直接加载的四阶段 PyG 图。
测试对象是冻结程序，不是逐题 Agent；hidden test 展开后禁止调用 LLM 或修改转换器。

## 数据冻结

- 总计 31 个设计：canary 1、development 6、hidden test 24；
- development 与 hidden test 的 small/medium/large 分别为 `2/2/2` 和 `8/8/8`；
- 全部输入来自实验一 strict-clean baseline，且四阶段输入完整；
- 不使用任何图输出或模型表现选样；
- 任务、仓库、源码闭包、逐文件哈希均与旧 v1/v1.1/v2 隔离；
- 对去注释、去格式并匿名化标识符后的 7-token shingle 做 Jaccard 审计，跨集合及集合内阈值固定为
  `0.50`。最终 31 个样本 465 对的最大相似度必须小于该阈值。

## 公平开发

三个 LLM 获得相同的公开字段语义、单位、可见阶段、输入配置和输出契约；均可使用 Python 标准库
与已公开的 Yosys/OpenDB 解析接口，但不得读取 R2G 源码。每模型最多 6 次串行调用、总计
400,000 provider usage Token、单次 completion 最多 32,768 Token。在 6 个 development case 上
达到 `6/6` 即提前停止，否则按预注册字典序冻结最佳轮次。hidden test 不提供反馈。

R2G 使用经过旧暴露数据研发和独立验证后的固定实现。其研发历史不与 LLM 单次开发 Token 混作同一
成本口径；分别报告 R2G 转换执行成本、LLM converter-development 成本和四种方法的测试执行成本。

## 主要终点

每个 hidden case 同时满足以下两类条件才计为 `semantic usable`：

1. 原生输出通过静态契约、官方 validator、配置不可变性、结构统计及无超时门；
2. 独立 oracle 的 alignment、causal、identity、label、mask、numeric、topology 七组核心语义均为
   PASS，且 oracle 自身没有错误。

主指标为 `semantic usable rate = semantic usable / 24`。原生 strict usable rate 作为接口可用性
副指标单独报告，不能替代语义正确性。

## 次要终点

- gate/pin/net/IO identity 与三类核心拓扑边的 precision、recall、F1；
- OpenDB 可独立核验的坐标、中心、直接 net bbox/HPWL；
- route DEF wirelength、SPEF ground capacitance、STA setup/hold 的覆盖量与误差；
- small/medium/large 分层成功率；
- 每 case wall time、峰值 RSS、输出体积；
- LLM 开发调用数、Token 和学习曲线；
- 公开 development 样本上的等价输入扰动和多图 batch smoke。

尚无独立 oracle 的 Liberty 全字段、pin shape/layer、拆分或改名 net 的 RC、RC/timing 边标签、
congestion 数值不计入“正确”分子，统一标记 `NOT_VERIFIED`。没有适用标签记为
`NOT_APPLICABLE`；身份不足记为 `UNASSESSABLE`，后者不能算通过。

## 冻结与恢复

- canary 和 development 可在 converter 冻结前物化；hidden 不可物化；
- 三个 converter 均冻结并记录源码 SHA256 后，必须运行 `hidden_evaluation` 启动审计；
- 审计必须绑定 cohort、未见库存、独立语义验证、runtime/protocol、模型路由及工具链哈希；
- 环境故障允许在同一冻结程序上重跑；确定性程序错误与超时计失败；
- 仅跳过输入、程序与输出 attestation 全部一致的已完成 case；
- hidden 结果出现后不得改变阈值、字段范围、oracle 或预算。任何必要修订都必须另建新版本并将本轮
  降级为 pilot。

## 解释边界

本实验验证的是从标准 EDA 产物到特定四阶段图契约的可靠转换能力，不等价于证明图对所有 GNN
任务最优。后续预测实验应作为独立研究问题，不并入本实验主分。
