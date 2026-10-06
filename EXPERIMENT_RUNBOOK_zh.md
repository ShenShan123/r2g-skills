# 实验操作手册

`EXPERIMENT_PLAN.md` 管方向：要证明哪些 claim、跑哪些实验。
这份管操作：**上一轮踩过哪些坑、下一轮该怎么做**。

## 这份文档的边界

**写**：脚本本身表达不出来的东西 —— 该保留哪些文件、该先声明哪条规则、
该用多长的窗口测速率、哪些失败是工具的真实限制而非 bug。

**不写**：已经改进代码的修正。下一轮跑的就是改好的代码，不会再犯。

**例外（2026-10-06 发现）**：上面这条只在代码真的入了版本控制时才成立。
本轮的全部修正都写在 216 的 `/proj/workarea/yangao/` 下，而那九个文件
（`orfs_baseline.py`、`signoff_pass.py`、`funnel.py`、`backfill.py`、
`reaper.sh`、`chain5.sh`/`chain7.sh`、`pdn_status.py`、`rate2.py`）**仓库里没有**。
216 一重装，或者有人从仓库起步，修正就全丢了。
→ **开跑前先确认这些脚本已入库**，否则这份文档里"已修复无需记录"的部分全是空话。

---

## 一、开跑前要保留哪些文件（本轮最大的教训）

本轮 harvest 只留了 `6_final.{def,spef,sdc}` + `1_synth.v`。后果：

| 想做的事 | 能不能补 | 原因 |
|---|---|---|
| DRC | **能** | ORFS 的 GDS 是从 `6_final.def` 流出的（`def2stream.py`，`-rd in_def=…`），不是从 ODB |
| LVS | **不能** | r2g 的 `run_lvs.sh` 正确拒绝：`make lvs` 会重建物理阶段，而签核器不能重新生成它要评判的版图（RMD-P0-01） |

**空间从来不是约束**：harvest 后单设计中位 1.3 MB，全语料 2.5 GB，而卷有 35 TB。
当初裁掉 GDS/ODB 是过度节省。

**下一轮必须保留**（已写进 `orfs_baseline.py` 的 harvest 清单，但换机器要重新确认）：

```
6_final.def   6_final.spef  6_final.sdc           已有
6_final.gds   6_final.v                           本轮补上，DRC/LVS 直接可读
6_final.odb                                       LVS 的 CDL 要它
1_synth.odb … 5_route.odb                         ← 关键：r2g 的 LVS 护栏要完整中间链
reports/  logs/                                   已有，cells/面积/卡住阶段都从这里回填
```

没有中间产物，LVS 只有两条路：重跑整条流程（约 28 小时 / 2000 设计），
或者跳过护栏——而跳过等于重跑（`make lvs` 自己会重建），
**代价一样但评判的是另一个版图**，漏斗里"过 DRC 的"和"过 LVS 的"说的不是同一批。不要做。

## 二、并发与资源

**12 窗口 × 8 核，不要加到 16。** 本轮实测（3.5 小时 / 143 设计）：

```
12 窗口 → 16 窗口
吞吐   57 → 41 设计/小时      中位 220 → 300s      p90 1970 → 2520s
并且有 6 个设计被挤过了超时上限
```

我当初加到 16 的依据是**3 秒的 CPU 采样**，还把 16 个 OS 线程误读成 16 个工作线程
（实际是 8 个 OpenMP worker 跑在 95% + 8 个空闲运行时线程）。
→ **规则**：`窗口数 × NUM_CORES ≈ 物理核数`；改并发要用小时级窗口验证，不是秒级采样。

**线程数不是加速手段。** DRC deck 里 `threads(4)` 写死，机器 96 核，看着像浪费。
实测 4 / 16 / 32 线程：314 / 320 / 318 秒，**零加速**（违例数相同，证实它只是并行开关）。
原因见第八节。而且 campaign 规模下 24 个作业各 4 线程本来就填满机器，线程数无意义。

## 三、启停操作

**停 campaign 必须用 SIGTERM 或 SIGKILL，绝不能用 SIGINT / Ctrl-C。**

runner 没装信号处理。SIGINT 会抛 `KeyboardInterrupt` → 触发 `finally` →
把被打断的运行**写成一条假的失败 result.json** → resume 看到文件就跳过 → 数据污染。
TERM/KILL 直接终止、不跑 finally。

```bash
kill -TERM -<pgid>          # 收整个进程组，不是只杀 python
podman stop -a -t 5 && podman rm -a -f   # --rm 容器在客户端被杀后会继续跑
# 验证：停前停后 result.json 计数必须不变（本轮 566 → 566 ✓）
```

**容器泄漏**：`proc.kill()` 只杀 `podman run` 客户端，容器分离后继续跑，
设计的 openroad 一直吃约 750% CPU 而 worker 槽位已给了下一个设计。
修正已入 `orfs_baseline.py`（命名容器 + 三条击杀路径都清），
但**仍然要挂 `reaper.sh` 做安全网**——本轮它拦下 **63 个**孤儿，约 504 核时。

取容器年龄用 `podman inspect --format '{{.State.StartedAt.Unix}}'`，
**不要** `date -d` 解析原始 `StartedAt`："2026-10-04 07:27:12 -0700 PDT"
里数字偏移后又跟时区名，GNU date 直接拒绝——我第一版回收器就是这样静默失效的。

**长任务必须 `setsid nohup … </dev/null &` 脱离**。本轮我有四次 ssh 被杀、
远端任务跟着死，白跑一个多小时。

## 四、测量纪律（我本轮反复犯的错）

**速率只能从 `driver.log` 算，不能用 `result.json` 的 mtime。**
回填会重写所有 result.json，把我自己的速率测量弄坏过一次
（报出"近 6 小时 43.3/小时"，实际是回填留下的痕迹）。

**窗口要够长。** 本轮 ETA 我报错五次：8–9h → 22h → 36h → 48h → 3h → 20h。
每次根源相同：窗口被非典型设计混合主导。实测每 2 小时的吞吐在 **22.5–109.5/小时**
之间摆动（4.9 倍）。早期 PDN 救回的小设计会把速率抬到 109，后段大设计密集区压到 22。
→ **用 ≥12 小时窗口**，并且说明这是哪个窗口测的。

**几个让我报错结论的脚本陷阱**：

- `pgrep -f "xxx.sh"` 会匹配到我自己的命令行 → 误判"已在运行"（回收器因此没启动）
  / 误判"仍在运行"。用 `/proc/<pid>/cmdline` 精确比对。
- `while read` 会吞掉没有末尾换行的最后一行 → 清理负对照时漏删一个假数据文件。
- Python 里用 `_` 同时当丢弃名和循环变量 → 把模块名写进了 CDL 文件头。
- 自己的监控脚本会过时：`detach.sh` 的 case 分支写死 chain5/chain6，
  换成 chain7 后漏报，让我误以为收尾链死了。

**结论要证伪过才算。** 本轮两次教训：
① 把"三种 LVS 配置判决一致"当成等效性证据——而三者都是失败，一致毫无意义。
② 规模阈值在 10 个样本上看到"干净分界"，44 个样本时就被一个 1,787 cells 的
   timeout 推翻。**样本少时不要下结论。**

## 五、语料同质性：政策必须宣告在前

本轮三次不得不修"一个语料里两套政策"，每次代价都是归档 + 重跑：

| 问题 | 后果 | 处理 |
|---|---|---|
| timeout 上限 5400s → 10800s | 15 个设计在已废弃的上限下被判死 | 归档重跑 |
| PDN 失败 → 加 32 µm core 下限 | 81 个在旧策略下失败 | 归档重跑 |
| 签核深度（差点） | 若只给后段设计做 LVS，就是两种深度 | 统一只做 DRC |

**宣告在前的做法有效，两次都成功了**：
- 周期选择规则写在扫描脚本里、跑之前就定死（"real-clock 设计中 timing-met ≥80% 的最紧周期"）
- PDN 的 32 µm 下限在任何重跑之前写进脚本注释和 chain 脚本头部

→ **规则**：任何中途的政策改动，要么归档 + 重跑受影响的子集，要么不要改。
归档而不是删除（本轮两个归档目录：`_pdn_failed_pre_fallback_*`、`_timeout_old_5400s_cap_*`）。

## 六、阈值要实测，不要照抄

**PDN 最小 core = 32 µm**（sky130hd）。推导过程：

```
报错给出需求   offset 13.57 + strap 15.2 = 28.8 µm
实测阶梯       30 µm 请求 → snap 到 28.3 → met5 仍失败
               32 µm 请求 → snap 到 30.6 → 通过，全流程到 6_final.gds
救回率         275 个走回退，273 成功 = 99%（剩 2 个是下游 route_failed）
```

**这个数字只对 sky130hd 有效。** nangate45 要重新测同样的阶梯——
平台的 `pdn.tcl` 里 strap 宽度/pitch/offset 都不同，代码不会提醒你去重测。

**另一条重要的反面教训**：规模预测 timeout 的方向是单向的。

```
规模大 → 会超时     成立：108,117 以上 41 个设计完成数为 0，1414 个实测零误判
超时 → 规模大       不成立：有个 1,787 cells 的卡在 grt 跑满 3 小时
```

所以规模天花板是**充分条件不是必要条件**，拿它当筛选器会漏掉小设计的病态情况。
更好的互补手段是**按阶段超时**：本轮 73 个 timeout 里 61 个（84%）卡在 `5_1_grt`，
给 grt 单独设超时能抓到 84% 且不预先排除任何设计。两者下一轮一起上。

## 七、漏斗怎么读才诚实

**三层归因，按死在哪个阶段分，不按失败类名分。**
本轮 73 个 timeout 里 71 个属后端，但 2 个卡在 `1_2_yosys`（综合没跑完）属前端。

```
RTL 前端   约 49%   解析/综合/yosys 规范化失败 —— 语料自身质量，E2 修不了
执行预算   约 35%   timeout / oom / oversize —— 我们给的资源不够
后端流程   约 16%   综合成功但 PnR 失败 —— E2 的修复对象
```

**timeout 是删失观测不是失败**：只知道"3 小时没跑完"，不知道最终会不会成功。
标成"超出预算未完成"，将来放宽预算还能重算。

**时序只对有约束路径的设计有意义。** SDC 只有 `create_clock`，
没有 `set_input_delay`/`set_output_delay`，所以没有 reg-to-reg 路径的设计
报 `worst slack max INF`，而 `wns max 0.00`（worst **negative** slack，无违例时钳 0）
会让它**读起来像达标**。本轮 2018 个 pass 里 **199 个是 INF**（113 虚拟时钟 + 83 有真实时钟但无 reg2reg）。

```
我最初报的   real clock + wns>=0      390/465 = 84%
诚实的       有约束路径 + slack>=0    312/389 = 80%
```

→ 漏斗必须把"无约束路径"单列一桶，不要并入达标。

## 八、签核：本轮做到哪、下轮怎么做全

**用 r2g 的 `run_drc.sh`，不是 ORFS 原生 `make drc`。** r2g 带三样原生没有的东西，
每一样本轮都撞到过：直调 `KLAYOUT_CMD` 绕开会孤立进程的 ORFS 包装；
`_bounded_run.sh` 进程组监督；`_restage_for_signoff.sh` 专为"工作区已清理"设计。

**LVS 的两个官方缺陷，r2g 已经解决**（在 `run_lvs.sh` 里）：

```
' / ' 节点/模型分隔符被 KLayout 数成一个引脚  → "6 expected, got 7"，每个 sky130 设计都挂
'short' 电阻值非数字                          → "Too many specs for a R, C or L device"
配套：r2g 自己的规则 assets/platforms/sky130hd/lvs/sky130hd_r2g.lylvs（短接 0 欧电阻）
```

我手搓调用时**原原本本撞到这两个**，绕了很多弯才发现 r2g 早就修了。
→ **先看 r2g 有没有现成的，再动手。**

**各平台该用哪种 DRC（文档写定的）**：

```
nangate45  路由器 DRC（detailedroute__route__drc_errors=0）—— ORFS 自己就这么签核
sky130hd   KLayout sky130hd.lydrc 完整 deck
asap7      社区 deck 有不可消除的假违例底噪，权威解是 Calibre（未安装）
Magic 路径 明确被否：over-reports std-cell li/mcon，且与 extract_drc 未接线
```

`router-clean ≠ lydrc-clean *by design*`：文档拿 ORFS 自己的 gcd 参考设计证明过
——路由器判干净，deck 跑出 20 个违例。两者是不同口径，不能混报。

**`DRC_BEOL_ONLY` 实际是 nangate45 专用**：sky130hd 的 deck 没有顶层
`ANTENNA = true` 行，r2g 的变换会拒绝执行（而不是跑一个半变换的 deck）。
而且它跳过 FEOL + ANTENNA，范围变窄，0 违例只能记 `clean_beol` 不能记 `clean`。

**一条未验证的算法加速**（下轮验）：deck 同时开 `tiles(1000.um)` 和 `deep`，
这两个是冲突的分解策略。实测同一设计：

```
tiles + deep + threads(4)   出厂    328s
deep，去掉 tiles                    321s   ← 几乎无变化，证实 deep 压制了 tiles
tiles + threads(16)，去 deep        237s   ← 快 28%
```

但**等效性未验证**：我选的对比设计是 DRC 干净的，三种模式都 0 违例，
"违例数相同"证明不了任何事。而且 `flat` 模式在 LVS deck 上有前科
（约 12,840 个虚假差异，因为 deck 的 `align`/`equivalent_pins`/`purge` 按层次化名字索引）。

→ **下轮验法**：全量 DRC 跑完后，每个设计的 `6_drc.lyrdb` 都留在 run 目录里，
出厂模式的精确 marker 集合已经在盘上。只需对**有违例的那批设计**再跑一次去 deep 的模式，
用 `tools/probe_nangate45_drc_mode.py` 的判据比对（marker 的坐标、标签、重数逐项相同，
不是只比数量）。通过则签核时间降 28%，不通过就记进 failure-patterns.md
——那个 probe 脚本建了却从没留下结论，正好填上。

## 九、平台备忘

**asap7 不是"工具没下载"。** 容器里 `asap7/` 的 `config.mk`/`drc`/`lef`/`lib`/`gds` 全在，
实测全流程跑通、零错误、布线 DRC 0 违例。它被 r2g 排除是**签核质量决定**：
社区 DRC deck 有不可消除的假违例底噪、没有 LVS deck、权威的 Calibre deck 没装。
→ baseline 口径（流程完成 + 时序 + 路由器 DRC）下 asap7 可用；
signoff-loop 的严格签核闭环下不可用，recipe 永远无法晋升。

**VHDL 本轮全部排除（1510 个）**，不只是偏好：GHDL 插件 `ghdl.so` 是给 yosys 0.51 编的，
在 0.64 上报 ABI 错（`undefined symbol: _ZN5Yosys5RTLIL2ID12CE_OVER_SRSTE`）。
下轮要跑 VHDL，先解决插件版本。

**选语料清单的脚本要存盘。** 本轮 `verilog_only.txt`（2453）漏掉了 44 个 Verilog/SV 设计，
它们和入选设计在元数据上毫无差别，而生成列表的脚本没保存，**无法复原当时为什么漏掉**。
已补进列表（→2497）。下轮：筛选脚本入库，并记录判据。

## 十、开跑前检查清单

```
[ ] 驱动脚本已入版本控制（否则这份文档第一节的"例外"适用）
[ ] harvest 清单含 6_final.{gds,v,odb} + 1_synth..5_route 中间产物
[ ] 并发 = 窗口数 × NUM_CORES ≈ 物理核数（不要凭短采样加并发）
[ ] 本平台的 PDN core 下限已实测（不要照抄 sky130hd 的 32 µm）
[ ] 周期/超时/规模阈值等政策已写进脚本头部，跑之前定死
[ ] reaper.sh 已挂（即使代码里的容器清理已修）
[ ] 长任务用 setsid nohup 脱离
[ ] 速率/ETA 只从 driver.log 的 ≥12 小时窗口算
[ ] 选语料的筛选脚本已入库，判据已记录
```
