# Revision5：跨来源 skid payload 受限绑定 DEV v3

状态：**仅 DEV source-only binder 草案；48/48 对抗检查通过；不构成 TRAIN、Knowledge/Asset 准入或 PILOT_TRANSFER。** v1/v2 代码与历史回执未改。这个 generation 是在观察过三个 DEV 故障之后开发的，故三个例子都不能冒充未见最终目标。

## 输入边界与适用范围

- `research_r5_skid_binding_v3.py` SHA256 `a018e43e475e72248f68086b15afe88aabd1e37670c0686613b31fa19eb2b43c`，对抗检查 SHA256 `3be44534fdda3e316c84ea926575a888809d2618704d9192377dabd7e168e316`。只接收已选 draft Asset 的固定 `binding_template`、buggy RTL 文本和公开参数；无仓库读取、私有 testbench、gold、mutation 坐标、oracle 调用或网络 API。绑定回执包括被操作源码 hash、唯一 RHS span、结构 witness 和 digest；应用时重绑相同源码，且只改一个 RHS。它只证明受限语法错配，不自证功能正确。
- v3 对 `axis_register` 和 `axis_broadcast` 的固定配置委托冻结 v2 匹配逻辑，新合约/回执独立。对 `ZipCPU/wb2axip` 新增 `skidbuffer` 的 `REG_OUTPUT` 形态，公开配置严格为 `{DW:8, OPT_OUTREG:1, OPT_LOWPOWER:0, OPT_PASSTHROUGH:0, OPT_INITIAL:1}`；其中 `OPT_INITIAL=1` 是源码默认值，测试编译未另行覆盖。只接受一个模块、唯一参数声明及默认值、唯一缓冲寄存器/握手 witness、唯一注册输出数据路径，并把 `if (r_valid)` 下误用 `i_data` 的 RHS 恢复为由本文件缓冲赋值和连线见证的 `r_data`。宏或其他代码穿入功能区、意外的模块前指令/模块后代码、错配置、重复或缺失证据均拒绝。FORMAL 尾部存在边界要求，但这不是完整预处理器或语义证明。
- 不以路径、文件名、仓库 SHA 或故障行号作绑定条件；具体变量/分支标识是有限结构合约的一部分。v3 不是对所有 skidbuffer 的通用修复器。`ZipCPU` 与 `alexforencich` 的源码独立关系仍待正式 lineage 审计；目前只是跨 owner DEV 形态检查。

## 现有 DEV 副本核对

| 形态 | fault SHA256 | clean SHA256 | 绑定与候选 |
|---|---|---|---|
| AXIS register | `000b18ba283dd3ad7ff6d9b5a2165df152441b662a43b4a34fd1ef8f88264e25` | `599fde2d6c2d806643bbffb7c444297e69a71871f962d4b741ec1914342e0d39` | 唯一 BOUND；单次改动逐字节等于 clean |
| AXIS broadcast | `00b6aff964ab877e6f02cda66fcc0e353104c75ac5e9c9d109ed70cf332084c4` | `a644b7542adf7552cb4713ab22856e1b76c58780da970d122351e45bc0bc51bf` | 唯一 BOUND；单次改动逐字节等于 clean |
| ZipCPU registered | `ca72b46e9dd7744f90d302811603140fc688418dbdde5d811dee5c502ea33fa3` | `ed1fda9192563fd7fd7033b564e8cf47ad6cb63ba4f578ed2c302037d47a5389` | 唯一 BOUND；单次改动逐字节等于 clean |

本次检查取自 `_qualification` 的既有隔离 stage，没有修改任何上游 checkout；候选与 clean 的逐字节相等仅是 DEV answer-aware 检查，不是未来目标输入。三种 clean 的功能判定需分别回到既有 oracle 回执。ZipCPU 的 research-augmented oracle 对该故障 `DETECTED`，但上游 native formal 仍为 `MISSED`，不能合并口径。

48/48 项通过，覆盖三形态的正例、健康 NO_MATCH、非目标拒绝、双模块、错 Asset、额外公开参数、重放/幂等、陈旧源码、span/digest 篡改及文件读取阻断；ZipCPU 另覆盖错误参数、前置 include/功能区宏、注释伪证据、重复赋值、未知 RHS 和存储 witness 丢失。旧 v2 对 ZipCPU 仍 `UNSUPPORTED`。执行入口：

```bash
PYTHONPATH=memory PYTHONDONTWRITEBYTECODE=1 python3 -m tehm.evaluation.research_r5_skid_binding_v3_checks \
  --register-clean /data1/zhangdy/RTL/RTL_testbench/_qualification/r5-dev-skid-payload-20260924/clean/stage/rtl/axis_register.v \
  --register-fault /data1/zhangdy/RTL/RTL_testbench/_qualification/r5-dev-skid-payload-20260924/fault/stage/rtl/axis_register.v \
  --broadcast-clean /data1/zhangdy/RTL/RTL_testbench/_qualification/r5-dev-broadcast-payload-20260924-r3/clean/stage/rtl/axis_broadcast.v \
  --broadcast-fault /data1/zhangdy/RTL/RTL_testbench/_qualification/r5-dev-broadcast-payload-20260924-r3/fault/stage/rtl/axis_broadcast.v \
  --zipcpu-clean /data1/zhangdy/RTL/RTL_testbench/_qualification/r5-dev-zipcpu-augmented-20260924-r1/clean/backpressure/stage/rtl/skidbuffer.v \
  --zipcpu-fault /data1/zhangdy/RTL/RTL_testbench/_qualification/r5-dev-zipcpu-augmented-20260924-r1/fault/backpressure/stage/rtl/skidbuffer.v \
  --unrelated /data1/zhangdy/RTL/RTL_testbench/ZipCPU/wb2axip/rtl/wbxbar.v
```

## 准入差距

本报告冻结时 v3 尚未接入核心；后续 [v3 shadow core 接线](research_r5_skid_core_v3_shadow_20260924.md) 已有 RAM-only Asset/action/source-replay 检查，但仍没有真实 route→select→bind→execute，也没有合法 TRAIN 行、L3 Knowledge 双 lineage 支撑、Asset 生命周期或独立 target 预登记。新增软件能力与后续 Memory 收益必须分开计；本结果只让 ZipCPU 这个 DEV 候选从“v2 无法绑定”推进到“v3 草案可唯一绑定”，不触发 M+ GO。
