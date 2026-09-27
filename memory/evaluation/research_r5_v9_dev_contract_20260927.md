# R5：下一软件代次 v9 的 DEV 合约草案（2026-09-27）

触发证据：gen6 v8 的 C1 clean native PASS 但结构 `UNQUALIFIED`（`246bc74`）；第二批同机制元数据发现零候选（`01aee1a`）。这不改变 gen6 的方法、M0 或负结果。v9 是新的开发代次；其任何成功都先标 `DEV`，不能回填 gen6 transfer／ΔMemory。

## 机制与来源角色

保持窄义机制：一个主输出寄存器被下游阻塞时，额外暂存一个已握手 payload；阻塞解除后应从暂存槽而非实时输入取回原 beat。不是任意二入口 FIFO、任意 `~data` 反转或全 RTL 自动修复。现有 AXIS/ZipCPU/drewbabel 仍是已观察 TRAIN 来源，LibSV/GAXI 等仍是已观察 DEV；不得换代次重新标为未见。`SigmanticAI/apex-inference-chip` 的 `rtl/xbr/stream_skid.sv` 仅可作 v9 DEV：当前 clone SHA `5998060b8da273b8d10e4c9e8565217d6c346f60`、clean、Apache-2.0；它含主输出 `m_data`、暂存 `skid_data/skid_valid`，在阻塞时捕获、释放时 drain。仓库另有 `reference/run1_gemm_systolic/rtl/stream_skid.sv`，必须审计复制关系并禁止从 target 工作区读取 reference；APEX 不能充当本代 unseen target。

## 开发期故障／oracle（尚未执行）

只在 evaluator 的隔离副本内，对 APEX DEV 源唯一的 drain 分支 `m_data <= skid_data` 设计一个 `m_data <= s_data` 的条件性 payload 错源故障。先独立冻结 DUT 参数、有效握手轨迹、target（阻塞后已接受的第二 beat 在解除阻塞时原值、顺序、次数）与 preservation（无阻塞直通、reset、输出保持）检查，再编译 clean／fault／candidate；合法输入、实际 DUT 和 raw verdict 必须可核对。若无可靠 native skid 测试，研究者可建 `research-augmented` oracle，但必须明确其与仓库 native 测试不等价；不能用一条只识别改动坐标的断言授予功能资格。

## 受限 source-only binding 的验收

新 binder 仅从选定 Asset、buggy source closure、公开结构／接口、参数与宏推导唯一角色和修改槽；不接收 clean/reference、私有 oracle、mutation manifest、gold diff、测试输出或 `.git`。需证明握手接收、阻塞捕获、暂存有效位、释放 drain、主输出写入和所有可能 writer 的关系；无法完整证明即 `UNSUPPORTED`。输出沿用 `BOUND`／`NO_MATCH`／`AMBIGUOUS`／`UNSUPPORTED`、source digest、结构 witness 和重推导 action receipt，不把 parser 近似命中当语义证明。

同一冻结代码至少须在 APEX DEV 的合法 fault 上唯一绑定、真实改源且 oracle target/preservation PASS；在 clean APEX 上幂等 NO_MATCH；在双目标、无目标、参数／宏不支持、非目标相似结构（含 C1 双 selector 队列）、额外 writer、混淆注释／字符串、篡改 witness、旧源重放和答案路径访问上 fail-closed。alpha-renaming 是一致性测试，不算跨设计 transfer。

只有上述 DEV gate 与来源审计成立，才新建 v9 software epoch 与 TRAIN Memory generation，并预先锁定另一个未用于开发、具备实际 oracle 的独立目标。三视图共享 v9 软件与预算且各自重跑 fresh oracle；v9 新 binder 相对 v8 的能力变化单列 `software_delta`，不得充当 `memory_delta`。模型调用仍为零，直到用户对具体新任务另行授权。旧 gen6 可精确回放前，不因整理源码树删除其哈希覆盖的脚本。
