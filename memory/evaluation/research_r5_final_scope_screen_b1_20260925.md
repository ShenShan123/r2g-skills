# R5 最终来源预审 B1：两个封装不能增加独立未见来源

## 结论与计数

依 R5 §7.4、§14、§16/R5-8 及已固定论文计量协议，本批预先选择两个已获取仓库中的 scope，检查源码依赖与既往暴露。两者都复用了实际 TRAIN/DEV 已观察的机制实现，**不进入“独立未见来源”最终测试分层**。候选分母保留为 2，新增独立未见来源为 0，新增方法任务为 0；没有 simulator、binder、route/select 或模型调用。

| 预登记 scope | 直接证据 | 处置 |
|---|---|---|
| `alexforencich/verilog-axis::axis_pipeline_register` | 原生 ordered filelist 同时编译 `axis_pipeline_register.v` 与 `axis_register.v`；top 的参数化循环实际实例化 `axis_register`。后者与 gen5 引用的 TRAIN clean source 逐字节相同；top 本身也在原 QF-1 的 249 文件静态扫描中 | 不计作新独立来源。将来若研究同源组合复用，须另行登记、单独表述，不从本次排除推断其功能失败 |
| `pulp-platform/axi::axi_cut` | top 的五条通道引用 `cc_spill_register`，其进一步引用 `cc_spill_register_flushable`。Bender 精确锁定的 common_cells commit 与既往 DEV checkout commit 不同，但两层模块及两个 include 的内容摘要均相同 | 不因仓库、commit 或通道数不同而新增独立样本；五条通道不是五个来源。完整原生测试资格本批未运行 |

这是源码内容层面的排除证据，不是 owner 名称判断、全量 parser 认证、完整 elaboration，或新的功能结果。先前静态扫描也不等于已经执行过该 top 的功能任务；第一项排除的主要依据是实际 TRAIN 机制叶节点复用，而不只是扫描记录。

## 冻结与身份

- 冻结方法：`2ce921a599c406ab63331561a182d6f4f1bef8cb`；gen5 Memory report `sha256:f31072bf2c233974c9fbbf4bb4c30539942f07a57a5b6b7e9022611e90f3206b`。本批不修改它们。
- 预登记在本轮首次读取候选 RTL/TB 内容之前完成，时间 `2026-09-26T03:11:34Z`（本地 9 月 25 日）；此前只查文件存在、Git 状态和既有角色记录。预登记明确不承诺候选未见或独立，且不按 v7 结果挑选。
- 预登记 SHA256：`64abb94745e035ca7892b738d2fb61c5727aec651247bfba4102998a28f5c8f9`。
- verilog-axis commit：`48ff7a7e2ef782cf778d47910cf85835c64b1bce`；wrapper SHA256 `89bcac460e3822dbbed3f2dbd4d9902faac1262d9dcad867b39c97a6f4402fcc`；TRAIN 叶节点 SHA256 `599fde2d6c2d806643bbffb7c444297e69a71871f962d4b741ec1914342e0d39`。
- axi commit：`70b8e54fd460e3308e58be596ceb3566a6e3576e`；top SHA256 `a455ac4f0ca4d88c289579bdcb37d75c6e76c665d3aace6b8d5cd9f2272f1696`。
- AXI 锁定 common_cells：`db42769334b4589b4b3fc671b34513bdb98be565`；既往 DEV checkout：`e73baaec2ca665cd80c3c384e9258e35242b829c`。直接从本地 Git 对象读取锁定版本，不以当前 checkout 替代依赖版本。
- 两版本共同 wrapper SHA256：`9fa980c80e230330d8ac7ef3af35bcec60014fd81a43f7adaf2ce3e6a4666094`；共同机制叶节点 SHA256：`734e199551750b46f0da373d6d8c70f13c84db24e8d61e06146717219e155f9b`。

## 可检查的证据与恢复

仓库外根目录：`/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/paper/final-scope-screen-b1/`。`screen.py` 保存并核对 19 份源码、依赖锁、QF、TRAIN/DEV 角色证据；不调用 TEHM runtime 或 simulator，也不重新授予旧 TRAIN 语义权威。既往私有登记留在 evaluator 目录，不提交 Git。

- `evidence-r1/result.json` SHA256：`383f7746d2d31cae2d8a69807bc79b46769672d96fd47e100f44bcad27b4c3c3`。
- `evidence-r1/snapshot.json` SHA256：`912e39cc401144b3094c4f18c0d2d04c17ed0b27ac39bb90140765f6f0dc85a7`。
- 冷审计拒绝 11 项篡改：预登记/候选/依赖/既往源内容变化、丢弃候选、伪造独立来源数和伪造功能 PASS。它们是审计检查，不是新增实验样本。
- 24 文件最小证据包 SHA256：`a1ee1ea6a58d9951eb95adc42aba2fea0d7fc264ea809a063a9782f73b11850e`；seal receipt SHA256：`2d8fc1e95d5e093e864a304e5790fd11ece1fe0dcd7efd1998de66a41bb8b9d5`。
- 匹配副本位于 `_r5_pilot/archive/final-scope-screen-b1/` 与 `/tmp/tehm-r5-scope-b1-w_n2q0sd/`。两个本地设备为 2081/2050；归档恢复后输入逐字节一致，并在无原始 `/data1`、无网络的隔离环境执行完整来源审计。**本次恢复的是来源审计，不是新增 RTL 功能实验；同机临时存储也不是异地长期备份。**

```sh
PYTHONDONTWRITEBYTECODE=1 python3 /data1/zhangdy/RTL/RTL_testbench/_r5_pilot/paper/final-scope-screen-b1/screen.py verify --root /data1/zhangdy/RTL/RTL_testbench/_r5_pilot/paper/final-scope-screen-b1/evidence-r1
```

## 对 R5-8 的影响

本轮数据质量检查将匹配单位落实到传递依赖的机制源码，排除了“新 top / 新 commit / 多通道即新来源”的重复计数。候选排除不是 method failure，也不改变已有 gen5 四个 arm 均 0/1 的实验读数。

两项排除不证明现有 corpus 或全部公开 RTL 已穷尽。后续选择必须仍围绕已固定机制和原生 post-backpressure payload oracle，独立预登记一个有界候选集合；优先检查真正不同的实现，不能把本批 wrapper 改名后补入。仅在方法冻结后进行资格审查，不自动构成开发泄漏；需要排除的是已经用于方法开发/训练的具体源、任务或答案，并保留来源聚类关系。

`final_test_ready=false`，最终数据清单、可用独立组和统计样本依据仍未建立。模型调用预算仍为 0，Agent 三策略比较未运行。无上游修改、Memory 更新、production promotion、清理或 push。
