# GitHub RTL corpus 第一波资格审查（2026-09-24）

状态：**10 个公开仓库已克隆并固定 HEAD；原生 clean baseline 与 oracle-sensitivity 开始验证；尚无 TEHM transfer、ΔMemory 或论文 final 样本。** 第一波的两个已观察来源组 `alexforencich`、`secworks` 均只能作为 development，不得改名为 unseen final held-out。

## 固定来源

克隆根目录：`/data1/zhangdy/RTL/RTL_testbench/`。每个仓库有自己的 `.git`，本轮末尾均为 clean。按 source group 聚类，而非按仓库数计独立样本。

| 仓库 | 固定 HEAD | source group | 当前 native baseline |
|---|---|---|---|
| alexforencich/verilog-uart | `1b867e53af738e4a8bc7c839ca2f1c07f40382dc` | alexforencich | UART RX 2/2 clean；数据反相负控**未被检测** |
| alexforencich/verilog-axis | `48ff7a7e2ef782cf778d47910cf85835c64b1bce` | alexforencich | axis_register 8-bit/REG_TYPE=2：9/9 clean；数据反相负控 9/9 FAIL |
| alexforencich/verilog-i2c | `a65be4045e898a52e791c6ee71f8f79a7cd2e129` | alexforencich | 未运行；原生 MyHDL 依赖待隔离 |
| secworks/aes | `80dc4718e1dcbbdb4b0dd1bdb393d8f7b98981dc` | secworks | 20/20 clean；结果字反相负控产生 20/20 ERROR |
| secworks/sha256 | `837c5cc396f001d18f2c765721c585716eb439ae` | secworks | 5/5 clean；敏感性待测 |
| secworks/chacha | `7eaba360df9fed9fc2db98d5f3df81cf01e5b604` | secworks | 3/3 clean；敏感性待测 |
| YosysHQ/picorv32 | `ef203c2b0a3fb793280f5114941416c425c5b461` | yosyshq | 未运行；`test_ez` 仅烟测，完整测试需固件/工具链 |
| freecores/i2c | `3b067f00ccced753b0502024766a51f58f3e04bc` | freecores | 未运行；无仓库根 LICENSE，旧 bench 的退出码不能直接当 oracle |
| olofk/serv | `f200eb2ed7b69ac1c6b8eddd47654522aeee5ce8` | olofk | 未运行；FuseSoC/仿真依赖待核验 |
| ultraembedded/riscv | `7ae6f803e30f78c6ea3121e73c3adf50ff912730` | ultraembedded | 未运行；SystemC/Verilator 依赖待核验 |

除 freecores/i2c 的根目录未见统一 LICENSE 外，其余仓库找到根许可证文件并记录 SHA256；**文件存在不等于许可法律审查通过**。所有 Git SHA、remote、license 摘要、RTL/TB 路径及工具版本详见冻结的 `/data1/zhangdy/RTL/RTL_testbench/_qualification/first-wave-r2-20260924/qualification.json`，receipt digest `sha256:656e0b28ab8447dfc404216b74a2e9ebd0e6f51f1146e0dfd76b1afeb14d24d2`。`research_pilot.py verify-github-rtl` 重新检查 10 个 checkout 和三个 Secworks 原始日志，`valid=true, errors=[]`。本机 Icarus/vvp 11.0。

## 原生 oracle 不是“仿真退出 0”

- Secworks 三个 top testbench 由审查过的显式 Icarus 文件清单在仓库外运行，正向 PASS 需要退出 0、且唯一的 `All N test cases completed successfully` 汇总、`N>0`、无 ERROR/FAIL/FATAL 行。完整 stdout/stderr/命令和输入摘要保留在上述 qualification 的 `baselines/`。AES 错误注入时仿真**仍退出 0**，但原生汇总明确是“20 tests completed - 20 test cases did not complete successfully”；只看退出码会产生假 PASS。
- cocotb 依赖按 verilog-uart 的 `tox.ini` 钉住 Python 3.10、pytest 7.2.1、cocotb 1.7.2、cocotb-bus 0.2.1、cocotb-test 0.2.4、cocotbext-axi 0.1.20、cocotbext-uart 0.1.2，隔离安装在 `_qualification/pydeps-py310/`，没有修改系统包或上游 clone。原始 RTL/TB 逐字节复制到隔离 staging；pytest wrapper 与 cocotb 内部 JUnit 分开核对。
- `uart_rx` clean：pytest 1/1、内层 cocotb 2/2，JUnit SHA256 `1b1cd73b94ca880219ebc184a5d51b52109e0b08ffccb6c762de78fee0103fa3`。预先记录后仅把 staged `m_axis_tdata` 反相；内层仍 2/2 PASS，JUnit SHA256 `05bddd5806d130ca4d2178f08156ab9f18fb256397fed0303fbb634c457d6df0`。原生测试读取并打印接收字节，但没有断言等于发送字节；因此**不具备这个数据错误类别的判定力**，不能据 clean PASS 授予 repair oracle 资格。
- `axis_register` 原版固定一个参数点 `DATA_WIDTH=8, REG_TYPE=2`：pytest 1/1、内层 9/9，JUnit SHA256 `9e37a0f739a113ec829473835c425b35aa77bba3280fe89837c43e1484f2df6e`。预登记后只把 REG_TYPE>1 分支的 staged `m_axis_tdata` 反相，内层 9/9 FAIL，JUnit SHA256 `74668aa590495a6d136f753cb931f761a763ce21a4cf4074e7f1fc317a17dcb1`。这只验证该参数点/该数据错误类别；九个参数点中的其余八个未运行。
- `aes` staged 结果读取字反相的预登记负控产生 20/20 ERROR，simulation stdout SHA256 `f1950c7d41074265eb8fce96249cf6adc73a90fb916d0d07a8a59f315e66e810`，原仓库源码未变。所有故障注入都只是开发期 oracle sensitivity，不是 TEHM 找到的候选或收益。

负控 preregistration、单行源差异、原始 log/JUnit 在 `_qualification/uart-rx-negative-control-20260924/`、`axis-register-negative-control-20260924/` 和 `aes-negative-control-20260924/`。开发期 clean baseline 分别在 `uart-rx-20260924/` 与 `axis-register-20260924/`。这些本机目录不在 Git 内，后续迁移/发布需另做可携带归档与隐私/许可审查。

## 下一道实验闸门

现有冻结 `rtl_acceptance_completion_guard_binding_v1` 对第一波仓库中的 249 个 RTL Verilog 文件做 source-only 检查，**0 个匹配**；没有读取 testbench/答案作为绑定输入。与此前本地工程目录的零匹配一致，所以目前不能把旧五案的 answer-free transfer + ΔMemory 协议“原封不动”执行到这批来源上。不能为得到 PASS 手改 binder、route 或答案。按附件建议，先继续从已发现的 native tests 提取有 oracle 敏感性的 development task，按 repo/source group 固定未来分割，并完成 clean/negative 控制；若要扩大 binder 支持域，必须另起版本、仅用 development 训练/校准，保留真正未见来源。旧五案外部冻结包已被删除，精确复现原 policy/load/审计还需要其备份。
