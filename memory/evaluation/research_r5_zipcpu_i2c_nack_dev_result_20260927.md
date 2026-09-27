# R5 I²C ZipCPU NACK augmented DEV oracle v1 结果

依据[预登记合约](research_r5_zipcpu_i2c_nack_dev_contract_20260927.md)，`ZipCPU/wbi2c` 固定提交 `afa64c2c151731fd4bcd5b3af9dd3b9a84c857e2` 已从未获准的 TRAIN 候选转成 `DEV_ORACLE_AUGMENT`。原生 master bench 首先在未改 RTL 的隔离根通过；它没有缺席地址 NACK 的正向错误位断言，因此此前 clean `SUCCESS!` 不能授予该机制的 TRAIN 准入。上游 checkout 始终干净，Git archive 的 clean/fault 根无 `.git`。

新 oracle 只在两臂相同的 C++ bench 上作两个版本化变更：沿用已存在的 `rand()` 分支及 `srand(2)` 代替 `/dev/urandom`，并在原读写测试后增加有界的 `0x52` 缺席设备检查，要求观察 busy→idle、完成中断与公开命令状态 `ERR=1`。两臂增强 bench SHA256 均为 `1533a1bb7108270931f2a4dd1ecdd733a711945d1ba25def4f61165e99d051fb`。fault RTL 仅将 `r_busy && ll_i2c_err` 分支的 `last_err <= 1'b1;` 改为 `last_err <= 1'b0;`；无其他 RTL 差异。两臂分别用同一本机 Verilator 5.035 构建，无编译错误。

增强 clean 原生退出 0，末尾 `NACK_STATUS_PASS` / `SUCCESS!`；fault 编译通过、旧读写检查继续执行，增强检查报告 `NACK_STATUS_FAIL status=00a06301`（busy/ERR 位均为 0），末尾 `FAIL`，退出 1。由于 stdout 与 stderr 合流时缓冲交织，fault 标记在合并日志中插入较早的行；C++ 控制流仍将该检查置于所有旧检查之后。不能用合并日志行序宣称时间序；保留原始日志与源码来审计。两份 native log SHA256 分别为 `bdf2f36b6b8a5809a316bd60b35c2a3e1608bef95e64ca4bbf79ae23ae4db9ec` 和 `f7cd41b939b1c74e1a15fa08b327e42d2c7d289ff579bcc7fcb01e3274f43a6c`。

完整原始构建、测试、可执行文件与[审计回执](/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/dev/zipcpu-wbi2c-nack-v1/audit.json)保存在 `_r5_pilot/dev/zipcpu-wbi2c-nack-v1/`；回执的文件摘要、同 bench 身份、唯一 RTL 修改、日志结尾与错误位由独立只读脚本重算通过。结论限于 `DETECTED_FOR_REGISTERED_SCOPE` 的 DEV oracle 判定力：不是 TEHM candidate 修复、合法 TRAIN experience、来源独立认证、Memory 三视图或 ΔMemory 增益。该来源已进入方法开发历史，不可在本代重新标成未见目标或独立 TRAIN 支持；无模型调用、无推送。
审计回执 SHA256：`b9b153302624534441cc74d84b110c88a701413cba20ff56bd787d4eb4879c96`。
