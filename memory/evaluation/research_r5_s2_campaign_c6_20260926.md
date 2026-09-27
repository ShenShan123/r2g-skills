# R5 S2 C6：跨进程预算、单次领取与调用终态

承接 C1/C4/C5，补上共享 campaign 预算与调用状态机。**本阶段只支持 OFFLINE_ONLY，不实现或授权任何 provider、tokenizer 或真实 API 调用。** 计数和 usage 均是明确的测试 fixture；不能把 ledger 的 dispatch claim 当作已发生模型调用。无 EDA/native、Memory 检索或更新、新 repair/FINAL 任务。

## 实现与约束

新增 `tehm/evaluation/research_r5_s2_campaign.py`，使用 runner-owned 文件、POSIX flock 和每次事件 fsync；创建时还同步目录项。多进程共享一份不可变 task/counter/预算计划，按 task×policy 设置相同的调用、token 和评估上限，并检查全局调用/token 上限。预留 amount=input reservation+max output，失败和无效提案均不退款。

```text
reserve + fsync → claim once + fsync → transport terminal
  RECEIVED + valid reported usage → trusted candidate / invalid proposal
    candidate → reserve evaluation → trusted enum feedback
  ERROR/TIMEOUT → stop arm
  missing/malformed/overrun usage → halt new campaign dispatch
```

reserve 返回前已经持久化；claim 只能执行一次，prompt digest 必须匹配。重开后 RESERVED/DISPATCHED 保持不确定状态，不自动释放预算或重试。模型不提供 candidate digest、counter 身份或 evaluator verdict；这些调用参数仍由可信 runner 的 C1/C5 适配负责。过量 usage 原值保留，并停止后续 dispatch，**不是保证能撤回已经发出的外部请求**。

已有 C1 保持单进程组件身份，没有被替换或静默扩权。C6 计划拒绝 LIVE mode；tokenizer 的准确计量、provider usage 字段/缓存/推理 token 口径尚未实现。counter_id 是被固定的适配器身份字段，不等于已经验证了真实 token 数。哈希链用于损坏检测，不是抵抗同权限恶意账本持有者的签名。只验证本机 POSIX 文件系统，不认证 NFS/分布式锁或 provider 端幂等性；可信父目录不能被并发删除/替换。

## 实际离线探针

预登记 `_r5_pilot/agent/s2-campaign-c6/preregister.md` 后，运行 10 组检查：

| 检查 | 实测 |
|---|---|
| 12 个独立进程争抢全局 2 次额度 | 2 次预留成功、10 次拒绝；保留 20,000 fixture token 预留 |
| 8 个独立进程争抢同一 dispatch | 1 次领取成功、7 次拒绝 |
| 子进程 fsync claim 后直接退出 23 | 重新打开后仍 DISPATCHED，10,000 预留未释放；重复领取/同臂新请求均拒绝 |
| missing / malformed / bool usage / input overrun / output overrun | 5 个反例停止后续 dispatch，保留原 usage 和全额预留 |
| 预算与序列 | 每臂/全局 call-token、评估次数、counter/task/policy、stale prompt/candidate、重复终态和乱序拒绝 |
| 失败与成功状态 | ERROR/TIMEOUT 停臂；无效 proposal 消耗额度；整体 PASS/UNKNOWN 停臂，FAIL 可在预算内继续 |
| 故意损坏 | hash 改动、尾部不完整记录拒绝读取；ledger symlink 拒绝 |
| C5 两步 transcript | fixture no_action → synthetic FAIL → common transform → synthetic PASS，调用上限保持 |

C5 transcript 的 feedback 是显式合成值（包括首步全部义务 FAIL），不是重播 native 事实，也不是新运行的功能结果。实际源码变换可执行，不把合成 feedback 包装成测试通过。竞争测试的 global=2 是压力条件，不是三策略正式实验的配额计划。

全部测试 ledger 保留，包括两个故意破坏的文件及被拒绝的测试 symlink；未清理失败记录。16 项既有 C1、3 项 C2、67 项 paper bookkeeping 也通过。没有扩大已知 DEV 的 native 验证矩阵。

## 第二副本与真实恢复

第二份 `/tmp/tehm-r5-s2-campaign-_jrnmu9z/` 保存完整包；恢复只挂载系统 Python、复制的代码/公共输入和新的输出目录，禁网，不挂载 corpus、Memory、私有 TB 或凭据。恢复重新执行全部竞争、退出和状态机检查，不是只打开 tar。规范化 report/audit 逐字节相同；随机 call IDs、竞争获胜进程和事件链 digest 不要求相同。每份原始 ledger 的完整 hash chain 被审计，只有预登记的两个损坏文件不合法。

原始、恢复 tar 与解压精确文件集（含测试 symlink 的 link target）、代码摘要、launcher 退出状态和隔离挂载经另一次只读核验；恢复原始产物在工作根和第二副本都保存。`/tmp` 仍属同机易失存储，不是长期或异地备份。系统工具为外部环境依赖。

| 工件 | SHA256 |
|---|---|
| campaign module | `8b7a4d52ee5cd37c2d48a7e7e8b68974cac85b619131ea745075cbbb22d4813c` |
| input plan | `3429952ff75f5474a2b01f2a272c5e5957e6d487f9a6d641cc4e5a8363815d3f` |
| report / reproduced report | `f9e89dac1b3f08d2998091c9fc8384f92ace441d5e83bf43bd844f6b4a9bfce0` |
| raw audit | `08c0c91c9629488ac7cbca1a5aca65b0c7e013e077c234018f32cfc705b91210` |
| original archive | `ba2410028c98c992f53cd81b32fb7f4caba04532dc28cc8c24ab2d76b1e4d249` |
| recovery archive | `69eb4c9f93a6b055d52dbb3465c67f82a951af8a10a2496f9df86440a04683ca` |
| recovery receipt | `471e86c462883633aecee119553dcb28d0ed95e8081895c8171e0de4488dfe06` |
| seal | `00912f87a8a788e8de5b2f8ea5a239fbd669bf01951977ae3c9d961e8bc8cdbd` |

## 剩余主链与授权

用户已明确授权首批 1 DEV×3 策略、每策略最多 2 次、合计 6 次/60,000 tokens、每次 input≤8,000/output≤2,000、零重试、不用于 FINAL，并在核对版本后回复“接受 V4.1-Flash”。官方当前 API id 为 deepseek-flash；旧 deepseek-v4-flash 已转到该版本，不能将结果标作原版 V4。[官方说明](https://api-docs.deepseek.com/quick_start/pricing/) 指定凭据文件只核对了 DeepSeek 配置，未输出 key；重复配置包含后置 Pro，后续必须选 Flash block，不直接 source 后覆盖。授权记录为工作根 authorization-approved-20260926.json，不含凭据值；当前真实调用为 0，需先完成该服务计量/transport 和多轮 context 路径。C6 自身保持 OFFLINE_ONLY，不改写测试身份。论文 R5-8 的抽样、来源分组和样本依据仍开放，冻结 gen5/v7、Memory、上游与 F1 不变，无 push；R5 未完成。
