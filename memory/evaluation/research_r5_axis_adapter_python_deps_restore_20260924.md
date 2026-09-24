# Revision5 §19：`axis_adapter` 资格 case 的 Python 依赖异路径重放

状态：**已把本次实际使用的 Python 3.10 依赖树完整封包并从另一设备恢复，在当前宿主 Python/Icarus 环境中重新执行同一 clean/fault 范围；未达到跨机器自包含或长期备份。** 这是[单 case 恢复演练](research_r5_axis_adapter_recovery_rehearsal_20260924.md)的依赖增量，不增加独立实验样本。

原依赖树 `/data1/zhangdy/RTL/RTL_testbench/_qualification/pydeps-py310` 约 19 MiB、584 个普通文件、无 symlink，包含 cocotb 1.7.2、cocotb-test 0.2.4、cocotbext-axi 0.1.20、pytest 7.2.1 等 wheel 元数据及原生扩展。仅把这棵树打包到 `/tmp/tehm-r5-axis-adapter-recovery-20260924-bLKhEQ/pydeps-py310.tar`，SHA256 `c6fedd480dd6983ad3dbf15e7325d200861d6b2d1ee0c1fe0fdfc75c5e6b5025`；tar 条目均在单一 `pydeps-py310/` 前缀下。异路径恢复到 `/tmp/.../dependencies/pydeps-py310/` 后，逐文件比较无差异。`/usr/bin/python3` 的导入路径实测指向恢复树内的 cocotb 与 pytest，而非原 `_qualification`。

再从此前恢复的预登记、RTL 和原生测试构建一个**新的** `reexecution-restored-deps/`，只把 `PYTHONPATH` 指到恢复依赖树，其余固定参数、seed 和 runner 不变。新回执 digest `sha256:23901f7b4ba0e0a26839a98e965626959574be827863f1dfb3f5a6d8632e5d95`；独立冷重放为 `valid=true`，clean 9/9 PASS、fault 7/9 PASS/2 FAIL、`DETECTED`。仿真日志可见从 `/tmp/.../dependencies/pydeps-py310/cocotb` 加载的库。原始资格回执、第一次恢复演练新回执和本次依赖恢复新回执分别保留，不相加为三项故障或重复样本。

宿主仍提供 Python 3.10.13、Icarus/VVP 11.0 及 glibc 等共享库。当前 `/usr/bin/python3` SHA256 `a496fb283fdc628f969e49365fbf7765af55137f0c3d8ea6165dad2507be0cf1`，`/usr/bin/iverilog` SHA256 `1ba67856249142771239573b5d51c7f6e4d67a1e7931a0d0fab56e0d473d2167`，`/usr/bin/vvp` SHA256 `075b114070eed3bc72e0e4559decf40520f3b693b1da9c94bba1defe8896966d`。恢复版 `libcocotbvpi_icarus.vpl` 仍由宿主提供 `libm`、`libgcc_s`、`libpthread`、`libc` 和动态链接器；这些系统库及 Python/Icarus 安装包未入 archive。因此结论限定为**同一机器 ABI 下的 Python 依赖树可恢复并实际消费**；`/tmp` 副本可被清理，R5 的持久第二副本与完整可移植环境 gate 仍开放。
