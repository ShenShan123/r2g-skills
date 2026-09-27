# R5 I²C ZipCPU TRAIN 候选筛查结果

按[执行前登记](research_r5_i2c_train_source_screen_20260927.md)只获取 `ZipCPU/wbi2c`，克隆位置 `/data1/zhangdy/RTL/RTL_testbench/ZipCPU/wbi2c`，remote `https://github.com/ZipCPU/wbi2c.git`，固定 HEAD `afa64c2c151731fd4bcd5b3af9dd3b9a84c857e2`，checkout 干净。根 `README.md` 与所读 RTL/bench 文件头声明 GPLv3；没有单独根 `LICENSE` 文件。本轮仅在本地隔离执行，不向 TEHM 主仓库复制/发布该 RTL。不同 owner 只是来源独立性的初筛，不等于 lineage 审计通过。

用 `git archive` 将固定提交复制到 `_r5_pilot/train/zipcpu-wbi2c-r1/clean/`（无 `.git`），只构建 `wbi2cmaster` 及其原生 C++ master bench，不调用需外部 ZipCPU/WB2AXIP 依赖的 CPU targets。工具为本机 Verilator `5.035 devel rev v5.034-106-ge6a997e31`、g++ 11.4.0；两步编译退出 0。原生 `wbi2cm_tb` 退出 0 且末尾 `SUCCESS!`，含正常从地址 `0x50` 读写及错误状态位保持 0 的断言。原始构建日志与测试日志在上述 `clean/`；SHA256 分别为 `eb37c056bf32d1dbb0f65a95a95af9758121a543d5df37836501ace22a0acbe3`、`56f824de86b1a596822968bd339427da65826bdca8edfe38dd67379b2a2feb65`、`5347dc7ba04bd85045d464e8f7f8565f15b405e237b651ce569d67707d720fee`；可执行文件 SHA256 `86488eccb907e61cadc049dee319839a17022cf5e26eaff2ff1ead70d6ea4ee6`。上游源码 checkout 未改。

**本机制的 TRAIN 准入仍为 `PENDING_ORACLE`**：原 bench 只针对正常 ACK/读写路径断言 `status[30]==0`，未驱动不存在的设备并断言 `status[30]==1`。RTL 有 `last_err`／`ll_i2c_err` 路径，但这只是可观察的结构，不是原生测试判定力。`wbi2cm_tb.cpp` 用 `/dev/urandom` 生成传输数据，`srand(2)` 并未固定这些字节；重跑的输入身份需另行收口。没有针对该机制的 ZipCPU fault-sensitivity 试验，更没有实际 TRAIN 修复动作、rollback、Knowledge/Asset 准入或 Memory delta。

本批筛查到此停止，不把 clean `SUCCESS!` 冒充 NACK status 的合格 oracle。若为此候选新增 augmented NACK 检查或据所读 ZipCPU 结构调整 binder，应升新的 DEV/oracle generation，并把该来源视为已用于开发，不再充作同代次独立 TRAIN 支持；另一合法 TRAIN 来源仍需单独建立。`freecores/i2c` 的 RTL/TB 仍未读取，也未成为未见目标。无模型调用、无 GitHub 推送。
