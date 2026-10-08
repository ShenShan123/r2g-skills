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

本轮 harvest 最初只留了 `6_final.{def,spef,sdc}` + `1_synth.v`。后果：

| 想做的事 | 能不能补 | 原因 |
|---|---|---|
| DRC | **能** | ORFS 的 GDS 是从 `6_final.def` 流出的（`def2stream.py`，`-rd in_def=…`），不是从 ODB |
| LVS | **能，但要 `6_final.odb`** | 网表用 `write_cdl` 从冻结的 ODB 导出；没有 ODB 就真的只能重跑 |

**空间从来不是约束**：harvest 后单设计中位 1.3 MB，全语料 2.5 GB，而卷有 35 TB。
当初裁掉 GDS/ODB 是过度节省。

**必须保留**（已写进 `orfs_baseline.py` 的 harvest 清单，但换机器要重新确认）：

```
6_final.def   6_final.spef  6_final.sdc           基本
6_final.gds   6_final.v                           DRC/LVS 直接可读
6_final.odb                                       LVS 导网表要它 —— 这一个就够
reports/  logs/                                   cells/面积/卡住阶段都从这里回填
```

每设计约 200 MB，全语料约 500 GB，占 35 TB 的 1.4%。
**不需要留 `1_synth.odb … 4_cts.odb`**——那是 1.07 GB/设计、全语料 2.6 TB，而且留了也没用（见下）。

### 为什么"留全链"是错的解法（2026-10-06 纠正）

这一节最初写的是"r2g 的 LVS 护栏要完整中间链，没有中间产物 LVS 就只能重跑整条流程"。
**这个判断错了两次，值得完整记下来，因为错的方式比结论有价值。**

第一次：以为缺的是 `5_route.odb` / `6_final.odb`。补上，仍然失败。

第二次（真实原因）：`make lvs` 的依赖链回溯到 RTL 综合——

```
lvs → 6_lvs.lvsdb → objects/6_final_concat.cdl → 6_final.cdl → cdl.tcl → 6_final.odb
                                                 ↑ make 问到这里，会越过已存在的 6_final.odb
                                                   去查 4_cts.odb、3_place.odb… 一路到
                                                   1_1_yosys_canonicalize.rtlil，断在缺失的 RTL
```

所以"补几个 odb"永远不够：**要么全链，要么另找路**。全链 2.6 TB 虽然放得下，
但它让签核依赖一堆与判决无关的字节，而且 make 一旦决定重建某一步就违反 RMD-P0-01。

**正解是绕开 make，照 Makefile 自己的配方跑三步**：`write_cdl -masters` 从冻结 ODB 导网表 →
`cat` 拼平台 CDL → `klayout -r <平台 deck>`。已修进 `signoff-loop/scripts/flow/run_lvs.sh`
（`R2G_LVS_FROZEN`：`auto` 默认 / `1` 强制 / `0` 禁用），回归测试
`tests/test_run_lvs_frozen_layout.py`。

### 护栏为什么没拦住（这是本轮最该记的一条）

`run_lvs.sh` 的 RMD-P0-01 预检用 `make --question` 问
`5_route.odb` / `6_final.{def,v,sdc}`。这四个**确实都在、确实是最新的**，
于是预检什么都不打印、返回 0 —— **`lvs_preflight.log` 是 0 字节，
旁边却是一个根本起不来的 `make lvs`**。

预检没坏，它回答的是另一个问题。

- 不要"修"成去预检 `6_final.cdl`：完整树里那个文件也不存在，答案永远是"要重建"，
  于是每个 run 都走回退，等于没有护栏。
- 正确的触发点是 **make 自己失败之后**：`No rule to make target` 意味着
  make 在跑任何配方之前就停了，**什么都没构建**，所以此时回退是安全的。
- 触发条件必须是"make 跑不起来"，**不能是"rc 非 0"**——否则真正的
  `Netlists don't match` 会被回退劫持。回归测试里专门有这条负向控制。

### 等价性要用产物证明，不是用判决一致证明

回退路径省掉了 `cdl.tcl` 的 `load.tcl` 前导（liberty + 约 80 个 make 变量）。
我推断 liberty 到不了 CDL 输出，但推断不是证据。验证方式是**同一个版图跑两条路径**：

```
                    make lvs（20 个中间 odb 齐全）      直接路径（只读 odb/gds）
6_final.cdl         608 行  md5 93e51383de02           608 行  md5 93e51383de02   逐字节相同
extracted.cir       md5 42f5873d09df                   md5 42f5873d09df           相同
判决                Netlists match                     Netlists match
```

**"两边都通过"本身证明不了任何事**——本轮早些时候我把三种 LVS 配置"判决一致"
当成等价性证据，而那三种其实全都失败了，一致的是失败。
settle 这件事的是 md5，不是判决。

### 顺带的一条资源事实

冻结路径的 LVS 在小设计上只要 **1.6 秒**，比同一设计的 DRC（约 320 秒）便宜两个数量级。
所以"补签核"的成本预算里，DRC 是全部，LVS 几乎免费。

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
- **按命令行文本定位进程的操作，会把发出该操作的命令本身算进去**（2026-10-07，
  同一个坑当天踩了四次，第四次是在我刚总结完它之后）：

  | 形式 | 后果 |
  |---|---|
  | `pgrep -f "xxx.sh"` | 误判"已在运行" |
  | `ps -ef \| grep -c '[c]reate_doc.py'` | 等待循环数到自己，永不退出 |
  | `pkill -f resume_doc.py` | **杀掉自己所在的那个 shell**，同一命令里后面的 `cat`/`python3` 全没执行 |
  | `nohup` 后台进程 | 被 harness 清理掉，只跑了十几个请求 |

  → 用 `pgrep -x`（匹配可执行名）或方括号转义 `'[r]esume_doc'`；
  **绝不要在同一条命令里既 pkill 又执行后续步骤**。
  需要长时间持续发网络请求的任务（本轮是飞书 API 的 184 次调用），
  **在这个环境里只有前台 + 长 timeout 可靠**，`nohup` 和 `run_in_background` 都被杀过。

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

## 六之二、时钟周期是个"干净旋钮"——它只动时序（2026-10-07 实测）

**结论**：周期只影响时序，不影响 DRC、LVS 和流程结局。所以跨周期的签核数据可以
合并比较，而且**想用周期去暴露非时序问题是徒劳的**。

第一遍 80 次运行（20 份设计 × 4 周期 × 完整流程 + DRC + LVS 签核）：

| 方向 | 设计 | 周期 | 结果 |
|---|---|---|---|
| 放宽 8 倍 | 12 份基线有违例 | 5 → 40 ns | **11/12 违例数完全不变**（含 228 条那份，一条不差）；1 份在 10 ns 时 55→44 但 20/40 ns 又回 55，不单调 |
| 收紧 10 倍 | 8 份基线干净 | 5 → 0.5 ns | 流程 8/8 完成、签核 DRC **0 条**、LVS 全通过；唯一变化是负裕量设计 2 → 7 份 |

**机制**：裕量随周期线性平移，10 份里 9 份斜率 **1.00**（放宽 1 ns，裕量就多 1 ns，
关键路径延迟没变）。斜率小于 1 只出现在两种设计上：被压到极限的（27,516 单元那份
0.85，5 ns 下只剩 0.14 ns 裕量）和中等规模刚好在能力边界的（mac 2,149 单元 0.46）。
→ **优化器只在"够努力就能达标"的窗口里工作**：太宽松不动，紧到不可达就放弃。

### 实验设计要点（这三条缺一条结论就不成立）

1. **定向分配**。两组的问题方向相反，周期也要相反分配：有违例的只往放宽方向测
   （看能否消失），干净的只往收紧方向测（看能否出现）。
   对已有 228 条违例的设计再收紧，信息量极低；对干净设计再放宽，什么都不会发生。
   总运行次数和对称网格相同，但测量放在信号可能出现的方向上。
2. **重复是对照，不是形式**。没有同条件下的运行间方差，跨周期差异无法与噪声区分。
3. **压平任务队列**。一次只投一个周期（12 个任务）会让 9/12 的 worker 空转 17 分钟——
   格子必须等最慢设计。把 4 个周期的 48 个任务一起投给 `orfs_baseline.py`
   （`--periods 'sky130hd=5,10,20,40'`），worker 一份跑完立刻领下一个。
   **但不要为此降 `--cores-per-design`**：线程数可能改变布线结果，而 DRC 违例数正是被测量。

### 两次被证伪的推理（比结论更值得记）

- **"收紧周期能暴露更多问题"** → 实测 0/20。收紧 10 倍，新增问题**全部**是时序不达标，
  没有一例新的 DRC / LVS / 流程中止。原计划错了。
- **"布线器 DRC 在所有周期都是 0，所以周期不影响 DRC"** → 无效证据。
  0 → 0 的一致性什么都证明不了，因为违例从未出现过，无法观察它是否变化。
  这个错误直接决定了上面的定向设计：**A 组必须选已有违例的，"消失"才可观测；
  B 组必须选干净的，"出现"才可观测。**
- 附带第三次（小）：根据"20/40 ns 先跑完"推断"周期越紧布线越慢"，
  核对耗时后发现四个周期没有系统差异，那只是调度顺序的假象。

### 范围限制

本语料的 DRC 违例**全是 metal2/3 的间距和宽度**（m3.2 占 96.8%）。间距违例本质是
布线密度现象，与时序无关——这和"周期无影响"在机制上自洽。但别的违例类别未必一样，
比如天线效应依赖连线长度，而连线长度可能随优化改变。本语料没触发天线违例，这一类没测到。

## 六之三、周期该定多少：period_min 已经在产物里（2026-10-07）

**不需要试跑标定。** 每份设计的 `6_finish.rpt` 里都有 `period_min`——OpenROAD 报的
该设计关键路径能支持的最快周期。2,159 份里读到 1,545 份。

| 单元数区间 | 份数 | 80% 够用 | 90% 够用 | 95% 够用 | 最差 |
|---|---|---|---|---|---|
| 1–100 | 192 | 1.18 | 1.34 | 1.50 | 3.8 |
| 101–1,000 | 633 | 2.52 | 2.97 | 3.55 | 8.9 |
| 1,001–3,000 | 257 | 4.10 | 4.92 | 5.75 | 17.5 |
| 3,001–6,000 | 122 | 5.72 | 6.22 | 7.35 | 16.8 |
| 6,001–10,000 | 97 | 6.83 | 8.90 | 18.69 | 82.2 |
| 10,001–100,000 | 241 | 7.97 | 9.95 | 15.50 | 81.2 |

**可信度**：用 `period_min ≤ 5ns` 预测"在 5 ns 下达标"，与实际一致 **1,538/1,545 = 99.5%**。
所以这不是估算，是读数。

**为什么固定 5 ns 是个坏选择**（同一批数据的达标率）：

```
1–100 单元        100.0%   ← 约束形同虚设，学不到东西
101–1,000          99.1%   ← 也几乎虚设
1,001–3,000        90.7%
3,001–6,000        68.9%
6,001–10,000       53.6%
10,001–100,000     50.2%
```

→ 按规模分区间取上表"80% 够用"列（取整 2.5 / 4 / 6 / 7 / 8 ns），每个区间都落在
约 80%，两边都有样本可比。**注意大设计区间内部离散度极大**：10,001–100,000 区间
80% 只要 7.97 ns，但 95% 要 15.50 ns，最差 81.2 ns——那 20% 不是"差一点点"，
是结构性的（见下条）。

### 修复分层：插 buffer 不是未用的手段

**ORFS 在三个阶段已经跑过时序修复**（`floorplan.tcl` / `cts.tcl` / `global_route.tcl`
都调 `repair_timing_helper`），而 `scripts/util.tcl` 里它默认开着：
`unbuffer`（插删缓冲）、`sizeup`（加驱动）、`swap`（引脚交换）、`vt`（阈值电压交换）、
门复制、last gasp——`SKIP_*` 系列默认全是 0。

→ **那 237 份时序未达标，是"以上全试过之后仍不达标"**。再手动插 buffer 是重复劳动。
这也解释了斜率 1.00：不是优化器没动，是动过了但动不了（94 级逻辑深度 × 每级 53 ps
已是单元库物理极限）。

**真正没用过的手段**（我们自己关掉的 / 从没设的）：

| 旋钮 | 现状 | 为什么对 |
|---|---|---|
| `ABC_AREA=1` | **我们每份设计都设了**，综合按面积优化 | ORFS 另有 `abc_speed_gia_only.script`；换速度模式直接改逻辑深度，正对着已查明的机制 |
| `TNS_END_PERCENT` | 从没设 | 决定修多少条路径，不只修最差那条 |
| `SETUP_SLACK_MARGIN` | 从没设 | 修到正裕量而非刚好 0 |

### fmax 回退会让指标失去定义

"差一点点就修、差太多就启用 fmax"听起来合理，但**审稿人会问：这不是修复，是改需求**。
更严重的是指标混了：用声明周期的设计量的是"在 X ns 下是否达标"，用 fmax 的量的是
"能跑多快"，放进同一个通过率，那个百分比不代表任何单一含义。而且回退条件依赖**结果**
（差太多才用 fmax），这是选择效应。

→ **两个实验分开，各自自洽**：

```
实验一  固定规格（回答"能不能修好"）
  按规模分区间声明周期 → baseline 跑 → r2g 修，但不许改周期
  可用手段：ABC_SPEED 重综合、TNS_END_PERCENT、SETUP_SLACK_MARGIN、利用率调整、SDC 细化
  指标：失败设计的修复率

实验二  最优性能（回答"能跑多快"）
  不设固定周期，两边都做 fmax 搜索
  指标：同等签核质量下的 Fmax 对比
```

差太多的那些**不用 fmax 回退**，改成用 `period_min` 识别为结构性限制单独归类报告：
"这 N 份的逻辑深度决定了它最快只能跑 X ns"——这是诚实且有信息量的结论，不是失败。

## 六之四、利用率：我们的 20% 才是那个人为设定（2026-10-07）

**事实**：`CORE_UTILIZATION = 20` 是**我们自己定的**（`orfs_baseline.py`
`write_config`）；`PLACE_DENSITY` 我们从没设过，走平台默认
（sky130hd 0.60、nangate45 0.30、sky130hs 0.50、gf180 0.40，这些是上游值）。

**ORFS 上游自带设计实际用的利用率**（从本机检出的 `designs/` 读，不是容器里的）：

```
sky130hd    aes 35   gcd 38   riscv32i 45   ibex 50   jpeg 50   chameleon 60
nangate45   dynamic_node 40   ariane133 50   tinyRocket 60（density 0.75）   swerv 65
asap7       aes_lvt 40   aes-block 47   gcd 65   aes 70（0.65）   cva6 70（0.69）   ethmac 70（0.75）

我们的全部 2,497 份：20
```

→ **上游没有一个低于 35%。我们用的 20% 比所有上游设计都松。** 所以 3.8% 的 DRC
违例率、86.5% 的完成率，是在"比所有上游设计都宽松"的条件下测的。把利用率提到
45–50%（上游中间值）**不是制造问题，是把条件调回正常**。

### 一个用错证据的教训

第一次查时，容器里 ORFS 的 `designs/` 显示"3,265 个设计用 util=20"，看起来是强有力的
惯例证据。**但那 3,265 个全是 r2g 自己生成的**（`aicV_*`、`repair_probe_*` 写进了
ORFS 的 `designs/`）——3,319 个里只有 9 个不是我们的。
**那等于用我们自己的默认值来证明我们的默认值是对的。** 查本机检出才拿到真数据，
而真数据说的是相反的话。
→ **规则**：引用"惯例"时，先确认那份样本不是自己写进去的。

### "加大利用率会出现各种问题"——现有数据不支持

从保留的 ORFS 日志读达成利用率（`Effective utilization` / `Design area ... %`）：

| | 最终利用率 30% 以上 | 最密的一份 |
|---|---|---|
| DRC 有违例 83 份 | 16.9% | 57% |
| DRC 干净（随机 160 份） | 14.4% | **64%** |

两组分布基本重叠，干净组里最密的还超过违例组最密的。
**在 20–64% 区间内，密度与 DRC 违例无关。**

这是本轮第三次"机制说得通但数据不支持"。但也**不能反推"80% 也无关"**——
真正的拥塞可能在 65% 以上才出现，而语料里几乎没有那个区间。要回答只能做实验，
且必须覆盖 65–80%（跑 20/35/50/65/80 五档，而不是在 20–40 里打转），
同样需要定向分配 + 重复对照 + 事先声明判据。

**界限在哪**：升到 50–65% 有上游背书（sky130hd 的 chameleon 用 60），升到 85% 没有——
那才是刻意制造问题。

## 六之五、基线到底有多"上游"（2026-10-07 核实）

论文必须声明这个。逐项用 md5 对比容器里的文件和上游 `git HEAD`：

| 项 | 状态 |
|---|---|
| 流程调用方式 | `orfs_baseline.py` 直接 `make DESIGN_CONFIG=...`，**没走 r2g 的 `run_orfs.sh`** |
| `flow/Makefile`、`scripts/{floorplan,cts,global_route,synth}.tcl` | md5 **全部与上游一致** |
| `platforms/sky130hd/config.mk` | 一致 |
| `platforms/sky130hd/drc/sky130hd.lydrc` | **一致**——DRC 规则本身是上游的 |
| `platforms/nangate45/config.mk` | 一致 |
| `platforms/nangate45/lef/*.tech.lef` | **不一致**：r2g 加了天线属性（有 `.r2g-pre-antenna.orig` 备份佐证） |
| ORFS 版本 / 工具 | 26Q3-54-ga5ff7ef7d，openroad 26Q3-318、yosys 0.64、klayout 0.30.7，均上游构建 |

**DRC**：判定标准是上游 deck，r2g 只替换了执行壳（`run_drc.sh`，因为上游
`klayout.sh` wrapper 会留孤儿进程）。那 1,840 条违例不是 r2g 判的。

**LVS**：sky130hd 上游自带 `sky130hd.lylvs`，但我们实际走 Netgen + Magic
（r2g 的 `run_netgen_lvs.sh`），因为上游 KLayout deck 在 sky130hd 上有三个缺陷；
Netgen 路线用的是 sky130 官方 PDK 自带的 `sky130A_setup.tcl`。
nangate45 **上游根本没有 LVS deck**，`FreePDK45.lylvs` 是 r2g 加的。

→ **怎么写**：
- sky130hd 的结果可直接称为"上游 ORFS 基线"。唯一 r2g 成分是进程管理（不影响判定）
  和 Netgen LVS 路线（判定更严，不更松）。
- **nangate45 必须加说明**：平台 LEF 被加了天线属性、LVS deck 由 r2g 提供，
  这两项上游都没有。没有它们就无法做天线检查和 LVS，所以这不是放水，
  是补齐上游缺失的签核能力——但不声明就等于把 r2g 的贡献算进了基线。


## 六之六、平台选择：第三平台选 gf180，不选 asap7（2026-10-07 讨论 + 核实）

### 能做完整签核的平台只有两个，第三个要补

| 平台 | DRC deck | LVS deck | CDL | 来源 |
|---|---|---|---|---|
| sky130hd | SkyWater 官方（ORFS 自带，md5 与上游一致） | 官方 + Netgen | PDK 自带 | 真实晶圆厂 |
| nangate45 | 学术 PDK 自带 | **r2g 自己造的** 455 行 | r2g 生成 | 预测性，无真实工艺 |
| ihp-sg13g2 | IHP 官方 maximal 201KB / minimal 54KB | **IHP 官方** `lvs/sg13g2.lvs` + `run_lvs.py` | PDK 自带（含 7 个 SRAM 宏） | 真实晶圆厂 |
| gf180 | ORFS 里**没有**；需装 gf180mcu PDK（结构同 sky130A） | 同上 | 同上 | 真实晶圆厂 |
| asap7 | 社区逆向，**有不可消除假违例下限** | **不存在** | 无 | 预测性，无真实工艺 |

**纠正一处口误**：先前报 ihp-sg13g2「无 LVS」是错的——只搜了 `*.lylvs` 后缀，
漏掉了它用的 `lvs/sg13g2.lvs`。它的签核材料其实是 ORFS 里最全的。
→ **教训**：查某平台有没有某种 deck，列全目录，不要按后缀猜。

### asap7 为什么不能当签核平台（四层理由，可直接用于汇报）

1. **判据本身就错**。社区 `asap7.lydrc` 跑 ORFS **自己的黄金参考设计 gcd** 得 **20 条违例**，
   而 ORFS 官方声明 gcd 是干净的。ORFS 对 asap7 的实际签核门槛是
   `detailedroute__route__drc_errors=0`（布线器内部检查，写在
   `designs/asap7/gcd/rules-base.json`）；那个 KLayout deck 是 `make drc` 的
   **非默认、非门控**目标，ORFS 从没打算用它签核。
   （上游 OpenROAD 讨论 #854 在同一个 gcd 上量到 348 条——不同版本下假违例数还会变，
   这本身就说明它不可作判据。）
2. **这些违例修不掉**。每份设计至少 8 条、没有一份是 0，分三类：
   约 ⅓ 在标准单元版图内部（GATE、LIG/LISD/SDT/V0 等 MOL 层，详细布线器根本不在那些层画东西）；
   约 ⅙ 是 tech-LEF 的 via 宽度 AUX 不匹配（连 8 个单元的设计都有）；
   其余是布线器模型与 deck 的 BEOL 分歧。**三类都与规模、密度、周期无关**，
   所以任何流程参数都动不了，`clean_beol` 也不可达。
3. **没有 LVS deck**。
4. **真值是受控分发的，而且可能已经不兼容**。不是「隐藏」——DRM 是公开的，
   社区才能照着逆向；受控的是**可执行的加密 Calibre deck**：asap.asu.edu 申请、
   .edu 邮箱 + 人工审批 + 不可再分发。这台机器有 Calibre `2025.1_16.10` 和 license，
   **没有 deck**（只有 418 字节占位 README）。而 deck 的测试版本是 **2017.4**，
   ASU 自己警告「与 2018.2 的 xACT 引擎不兼容，之后的版本也可能不兼容」——
   比我们的版本老 8 年，加密 SVRF 对版本极敏感。
   → **拿到之后还得冒烟测才知道能不能用**：`R2G_CALIBRE_SMOKE=1 run_calibre_drc.sh`，
   它检测 deck 加载/版本/license 失败时写 `status=incompatible`，不写假 clean。
   在冒烟测通过前，任何依赖 asap7 的计划都建立在未验证的前提上。

**关键区别**：nangate45 缺的是**工具**（我们造了 LVS deck，因为它是简单学术 PDK，
正确答案明确）；asap7 缺的是**真值**（预测性 PDK 不对应任何真实工艺，
那 ⅓ 在单元内部的违例到底是「单元真违规」还是「deck 还原错了」，
只能对照官方 deck 判断）。**缺工具可以造，缺真值造不出来。**

**论证上的额外劣势**：asap7 和 nangate45 同是预测性 PDK，加了它就是
「1 真实 + 2 预测性」，反而放大现有最大弱点。想要先进节点的说服力，
正确做法是诚实声明范围（「本工作在三个真实开源 PDK 上验证，
先进节点的 FinFET 和多重曝光规则未覆盖」），而不是拿一个站不住的 7nm 结果。

### 平台硬指标对比（单一默认配置，已更正）

| 平台 | 节点 | 路由层 | 标准单元 | liberty | 工艺真实性 |
|---|---|---|---|---|---|
| sky130hd | 130nm | 6 | 441 | 13M | 真实（SkyWater） |
| nangate45 | 45nm | 10 | 134 | 6.4M | 预测性 |
| gf180 | 180nm | 5（默认 5LM_1TM） | 229 | — | 真实（GlobalFoundries） |
| ihp-sg13g2 | 130nm | 7 | 84–117 | 1.7M | 真实（IHP） |
| asap7 | 7nm | 11 | 212 | — | 预测性 |

**我在这张表上犯过两个错，都是取错文件造成的**（值得记，因为同类错误会再犯）：

- 「gf180 有 5,496 个标准单元，是现有的 12 倍」——**错**。
  那是把同一批 **229** 个单元在 24 个金属/track 变体的 `.lef` 里数了 24 遍。
  一次运行只加载一个 `sc.lef`。各平台库规模其实都在 84–441 这同一量级。
- 「gf180 只有 2 层布线」——**错**。按字母序取到了 `2LM_1TM`，
  而默认配置是 `METAL_OPTION ?= 5LM_1TM`，即 **5 层**。
  → **规则**：读平台参数要先看 `config.mk` 的默认值，不要 `ls | head -1`。

### gf180 的真实优势：唯一能把布线资源作为单一变量的平台

它在**同一个 PDK** 下提供 **24 种金属/track 配置**：

```
2LM / 3LM / 4LM / 5LM / 6LM      布线层 2 到 6
6K / 9K / 11K / 30K              顶层金属厚度
7t / 9t                          单元高度（7 轨 / 9 轨）
```

→ 可以在**同一 PDK、同一批设计、同一套规则**下只改布线层数做受控实验。
跨平台比较永远有「多个变量同时变」的归因问题（换平台同时改变规则、单元、层数、节点），
**gf180 的多配置能力让「布线资源」这一个变量能被单独验证**——
这接上「周期只影响时序」的结论，正好回答「那什么才影响 DRC」。

**附带好处**：gf180mcu 的 PDK 结构与 sky130A 相同
（`libs.tech/{klayout,netgen,magic}`），所以签核走 **PDK 自带的 Netgen + Magic**——
与 sky130hd 同一套机制，可直接复用刚验证过的 `run_netgen_lvs.sh`
（6/6 份设计 1–6,921 单元读出 Circuits match uniquely，三个负对照全检出）。
基线纯度论证也一致（都用 PDK 官方材料，不是我们造的）。
而 ihp-sg13g2 走 KLayout LVS——我们刚在 sky130hd 的 KLayout deck 上踩完三个缺陷，
不知道 IHP 的 deck 有没有同类问题。

→ **验证顺序**：装 gf180mcu → 5–10 份已知在 130 上干净的小设计跑全流程 →
`run_netgen_lvs.sh` 带负对照 → 通了定它，不通再试 ihp-sg13g2。约半天。

### 顺带澄清：库规模不影响跑的速度

标准单元 = 预先画好版图的基本逻辑门，是综合的目标词汇表。441 个不是 441 种逻辑功能，
而是约 80–100 种功能 × 若干驱动强度（`inv_1` 到 `inv_16` 是同一反相器的 16 倍驱动差别）。
**每种门有几档驱动，直接决定 resizer 有多少回旋空间**——「加大驱动」就是把
`inv_1` 换成 `inv_4`。

| 环节 | 受库规模影响？ |
|---|---|
| liberty 解析 | 是，但每阶段读一次，秒级（13M vs 1.7M 差 7 倍，可忽略） |
| 综合技术映射 | 略受，但 ABC 成本主要由电路规模决定 |
| resizer 替换搜索 | 略受，库大则候选多——但修复质量也可能更好 |
| 布局 / CTS / **详细布线** / DRC / LVS | **不受**，由实例数、线网数、层数、几何复杂度决定 |

实测佐证：`axil_crossbar`（12,672 单元）在 sky130hd 上 160 分钟里绝大部分是详细布线，
liberty 解析在日志里是秒级。→ **真正影响速度的是设计规模 × 布线层数，库规模可忽略。**

## 六之七、两个待决策项：超时上限与利用率（2026-10-07）

### 超时上限 3h → 6h（老师提议）：能救 85%，但一半是另一类问题

用已完成的 2,159 份拟合标度（≥1000 单元，873 份）：**耗时 ≈ exp(-0.28) × 单元^0.81**

```
100,000 单元  → 预计  2.3 小时
300,000 单元  → 预计  5.5 小时
1,000,000     → 预计 14.4 小时
2,154,451     → 预计 26.7 小时   ← 语料里最大的那份
```

据此外推 105 份超时设计（规模 1,787 到 2,154,451，其中 96 份在 10 万以上）：

| 外推耗时 | 份数 | 占比 | 含义 |
|---|---|---|---|
| **3h 内本该跑完** | **44** | **41.9%** | **规模不该超时——是卡住了，加预算救不了** |
| 3–6h | 45 | 42.9% | 6 小时正好有效 |
| 6–12h | 9 | 8.6% | 6 小时不够 |
| 12–24h | 6 | 5.7% | — |
| 24h 以上 | 1 | 1.0% | 215 万单元那份 |

→ **结论**：6 小时上限合理（能救 42.9%），但**必须同时查那 44 份「本该跑完却超时」的**，
它们是真正的线索：规模最小 1,787 单元却耗尽 3 小时，说明某个阶段卡死而不是算力不足。
单纯重跑只会再超时一次。
→ **操作要求**（本文档「语料同质性」那条的直接应用）：超时上限是**政策**，
改了就必须归档 + 重跑受影响子集，一个语料里不能混两个上限。
本轮已有先例：`_timeout_old_5400s_cap_*` 归档目录。

### 利用率：现有语料不用重跑，但正式实验前必须先标定

**现有 2,497 份（util=20）是否作废**：不作废。它在声明条件下是合法测量，
周期实验也已证明 DRC/LVS 不受周期污染。它作为**先导实验**的身份完整。
**但 20% 比所有上游设计都松**（上游 35–70），所以它支持的结论要限定为
「在宽松利用率下」，不能当作「实践中会遇到的问题分布」。

**正式实验怎么办**：正式实验本来就是**新的一轮**（要用按规模分区间的周期），
所以利用率的改动**搭这一轮的车，不需要单独重跑**。现有 2,497 份留作先导数据。

**但有个前提必须先验证**：利用率提到 45% 之后流程完成率会掉多少？现在不知道，
而它决定正式实验可行不可行：

```
45% 下完成率仍 ≥85%   方案可行
掉到 60%              大量设计到不了签核，失败类型会被「布局/布线失败」主导
                      而不是时序 —— 要重新考虑利用率
```

→ **标定实验**：60 份设计（按规模分层）× 利用率 20/35/45/55 四档 × 2 次重复
= 480 次运行。比正式实验小两个数量级，但能把参数定死。

**重复仍然必要，理由与周期实验不同**：A 组两遍的签核 DRC 违例数**逐条相同**
（48 个格子，重复间差异全为 0），所以在宽松利用率下流程是确定性的。
但那是**优化器舒适区**的确定性；高利用率下布局器在能力边界工作，
**那恰恰是非确定性最可能出现的地方**。所以不能用低利用率的确定性去免掉高利用率的重复。

**界限**：升到 50–65% 有上游背书（sky130hd 的 chameleon 用 60），升到 85% 没有——
那才是刻意制造问题。密度实验若要做，必须覆盖 65–80%（现有语料只到 64%），
跑 20/35/50/65/80 五档，同样需要定向分配 + 重复 + 事先声明判据。


## 六之八、gf180 实测：流程和 DRC 可用，LVS 卡在 LEF 缺井引脚（2026-10-08）

### 结果速览

| 项 | 状态 |
|---|---|
| 流程 | ✅ 5/5 pass（93–3,025 单元，80–240 秒），产物齐全含 `6_final.gds`/`.odb` |
| PDK | ✅ `conda install -c litex-hub open_pdks.gf180mcuc`（144 MB），208 和 216 都已装 |
| 官方 DRC | ✅ 跑通：53 个规则表拼成 7,884 行 deck，1,281 条规则，33.6 秒 |
| 官方 KLayout LVS | ⚠️ 跑通但判 `Netlists don't match`，根因见下 |
| Magic+Netgen LVS | ⚠️ 同一根因 |

PDK 的签核材料比 ORFS 平台目录齐得多：DRC 191 个文件、LVS 291 个文件，都带官方
`run_drc.py` / `run_lvs.py`。ORFS 的 `platforms/gf180/` 只有 RCX 规则，没有 DRC/LVS deck。

### 怎么驱动官方 deck（两个坑）

**DRC 必须自己拼装。** `main.drc` 只是 409 行的前导，真正的规则在 `rule_decks/*.drc`，
由 `run_drc.py` 的 `generate_drc_run_template` 拼成一个文件。照它的逻辑复现：

```
main.drc + 所有 rule_decks/*.drc（排除 antenna|density|main|layers_def|tail）+ tail.drc
并把 layers_def.drc 复制到运行目录（main.drc 按相对名加载它）
```

绕开 `run_drc.py` 是必要的：它 `import docopt` 和 `klayout.db`，而 conda 的 eda 环境
没有 python、容器的 python 没有 pip、216 的系统 python 也没有 pip。
deck 本身是纯 KLayout 脚本，`klayout -b -r` 直接跑即可（和 `run_drc.sh` 驱动
sky130hd 的 deck 同一个做法）。

**`FEOL`/`BEOL` 默认是 false，而且不传不会报错。** `bool_check?(obj)` 只认
`obj.to_s.downcase == 'true'`，所以必须显式 `-rd feol=true -rd beol=true -rd conn_drc=true`。
不传的话：**退出码 0、无任何报错、报告文件正常生成，但一条规则都没执行，违例数 0**。
→ 这是个会骗人的陷阱，和本文档反复强调的「fail-soft 不是 pass」同一类。
验收标志：日志里必须出现 `Running all FEOL rules` / `Running all BEOL rules`，
且 lyrdb 的 `<category>` 数应在千级（实测 1,281）。

**LVS 不用拼装**：`gf180mcu.lvs` 用 `%include rule_decks/...`，KLayout 自己处理。
它的输入是 `$input`(GDS)、`$schematic`(参考网表)、`$topcell`、`$report`、
`$target_netlist`，加 `$lvs_sub`(衬底名，默认 `gf180mcu_gnd`)、
`$schematic_simplify`、`$purge_nets`、`$top_lvl_pins`、`$run_mode`(flat/deep)。

### DRC 的 820 条违例是真问题，不是 asap7 那种下限

规模标度实验（5 份设计，93 到 3,025 单元）：

| 设计 | 单元 | 违例 | 违例/单元 |
|---|---|---|---|
| corescore_emitter_uart | 93 | 820 | 8.8 |
| wbarbiter | 111 | 1,264 | 11.4 |
| rom_divide | 521 | 3,759 | 7.2 |
| avg_n_per_clk | 1,108 | 11,241 | 10.1 |
| rotate_mapper | 3,025 | 28,174 | 9.3 |

**严格线性，每单元约 9 条。** 对比 asap7 的每份 8–25 条、与规模无关——那是来自单元库和
tech LEF 的固定下限，流程动不了；这里是流程产出的几何，流程可控。

只有 4 种规则触发，在所有规模下分布一致：

| 规则 | 含义 | 占比 | 性质 |
|---|---|---|---|
| V2.1 / V3.1 | via2 / via3 尺寸必须恰好 0.26 µm | 约 82%，两者数量**始终完全相等** | 布线器画的 via 尺寸与 deck 要求不符 → ORFS gf180 tech LEF 的 VIA 定义问题 |
| DF.13_MV / DF.14_MV | 衬底接触(tap)到 NCOMP 的最大距离 15 µm（MV 5V 器件；LV 是 20 µm） | 约 18% | tap cell 放得不够密 → tapcell 间距要按 MV 的 15 µm 设，不是 LV 的 20 µm |

**ORFS 自己从未发现这两类**：它对 gf180 的门槛和 asap7 一样只用布线器内部检查，
我们跑的 5 份 `route_drc` 全是 0。

### LVS 已通（2026-10-08 补）：四个缺陷叠在一起，最后一个才让前三个现形

**结果**：5/5 份设计（93 到 3,025 单元，规模跨 32 倍）读出 `Circuits match uniquely`，
器件数和网络数两边逐项精确相等。

| 设计 | 单元 | 器件 | 网络 |
|---|---|---|---|
| corescore_emitter_uart | 93 | 106 = 106 | 120 = 120 |
| wbarbiter | 111 | 330 = 330 | 478 = 478 |
| rom_divide | 521 | 527 = 527 | 538 = 538 |
| avg_n_per_clk | 1,108 | 1,280 = 1,280 | 1,558 = 1,558 |
| rotate_mapper | 3,025 | 3,002 = 3,002 | 3,784 = 3,784 |

**四个缺陷**（`run_netgen_lvs.sh`，提交 bd5aa01）：

1. **ORFS 的 gf180 std-cell LEF 缺 VNW/VPW 井引脚**。`write_cdl` 直接报出来：
   `[WARNING ODB-0286] Terminal VNW of CDL master ... not found in LEF`。
   于是网表把井端口填成 `_unconnected_N`，比对看到 332 个原理图网络对 120 个提取网络。
   → 改读 PDK 自己的 std-cell LEF（25,973 行，`PIN VNW USE POWER` / `PIN VPW USE GROUND`），
   平台的 tech LEF 保留。
2. **引脚有了但没连接**——ORFS 的 PDN 从未布过它不知道存在的井。
   → 声明物理事实：`add_global_connection -net VDD -pin_pattern {^VNW$} -power`
   和 `-net VSS -pin_pattern {^VPW$} -ground`。
   验证：`_unconnected_108 _unconnected_109` → `VDD VSS`。
3. **有 ODB 时脚本走 `read_db`，整段 LEF 逻辑被跳过**，而那个 ODB 正是 ORFS 用缺引脚的
   LEF 建的，所以全局连接无处可连。
   → gf180 即使有 ODB 也强制从 LEF + DEF 重建；其他平台继续优先用 ODB
   （两个独立序列化比"版图和网表同源于一个 DEF"多一层独立性）。
4. **LEF 路径只展开了 `$(PLATFORM_DIR)`**，而 gf180 的 TECH_LEF 是
   `lef/gf180mcu_$(METAL_OPTION)_$(KVALUE)K_$(TRACK_OPTION)_tech.lef`。
   未展开的文件名不存在 → `read_lef` 被静默跳过 → DEF 报
   `ODB-0421`「UNITS DISTANCE MICRONS 换算因子 2000 大于数据库单位」外加
   `unknown site GF018hv5v_green_sc9`。
   **这两条症状都像 DEF 的问题，其实都是缺 tech LEF。** 这是整件事里最隐蔽的一环。
   → `_r2g_lef` 现在展开 METAL_OPTION / KVALUE / TRACK_OPTION / POWER_OPTION / CORNER。

### 为什么用 Netgen 而不是 PDK 的 KLayout LVS deck

两者都在、都能跑完并给出判决。用实测区分：

| | 填充单元处理 | 在填充过的数字版图上的实测 |
|---|---|---|
| KLayout LVS deck（291 文件） | **没有任何处理** | 器件只配上 21%（75/362）、网络 13%、引脚 11% |
| Netgen setup | **11 条 `ignore class`**，覆盖 `__antenna`/`__endcap`/`__fill_`/`__fillcap_`/`__filltie` | 全部匹配 |

→ KLayout 那套是为定制模拟版图设计的；Netgen 那套就是为数字流程设计的。
（顺带：KLayout LVS 还要 `metal_level` 显式传——它默认 `6LM` 而 ORFS 用 5LM；
`combine=true` 在这里反而有害，因为 CDL 侧是逐个 MOS 展开的、没有 `m=N`；
`write_cdl -masters` 只用于查端口顺序，不会把 229 个单元定义拼进 CDL，要显式 `cat`；
PDK 内部两套 CDL 方言，标准单元 CDL 的二极管用位置参数 + `$m=1`，
KLayout 的 reader 只认 `AREA=`/`PJ=`/`m=` 那种——只有 `antenna` 一个单元受影响。）

### 检出能力的边界（三个负对照，其中一个暴露了盲区）

| 破坏 | 结果 |
|---|---|
| 改接一个输入（`_087_ I` → `_087_ ZN`） | 网络 120 vs **121**，`Netlists do not match` ✅ |
| 删掉一个实例 | `LVS NOT EXECUTED`——DEF 的 COMPONENTS 计数不一致，openroad 诚实拒绝（不是假通过） |
| **单个实例换驱动强度**（`inv_2` → `inv_4`） | 网络 120 vs 120，`Circuits match uniquely` ❌ **未检出** |

未检出的原因查清了：破坏确实两边都在（`powered.v` 里 `inv_4` 出现 1 次，
`extracted.spice` 里 0 次、`inv_2` 30 次），但版图侧根本没有 `inv_4` 这个子电路，
Netgen 把网表侧那个找不到对应的实例展平，而 `inv_2` 和 `inv_4` 的拓扑相同
（各 1 对 PMOS/NMOS，只有 W 不同），展平后晶体管数和连接都不变。
W 的比较只在**配对上的**器件之间进行，这里走的是另一条路径。

**这个盲区已补上（2026-10-08）**，而且证据就在 Netgen 自己的报告里，只是解析从没读过：

```
Flattening unmatched subcell gf180mcu_fd_sc_mcu9t5v0__inv_4
  in circuit corescore_emitter_uart (1)(1 instance)
```

Netgen 明确声明了它展平了一个无法配对的子单元，然后仍判 `Circuits match uniquely`。
判别力检验（这才决定能不能用作判据）：

| 运行 | `Flattening unmatched` 行数 |
|---|---|
| 5 份干净 gf180 设计（93–3,025 单元） | 0 |
| 已检出的负对照（改接输入） | 0 |
| sky130hd 干净运行 | 0 |
| **漏检的负对照（换驱动强度）** | **1** |

零误报、精确命中。`run_netgen_lvs.sh` 现在在「匹配 + 存在展平未配对子单元」时判
`mismatch`，状态 `flattened_unmatched_subcell`，并把那几行原文打到 stderr。
重跑验证：换驱动强度 `status=mismatch`（原先 clean）、改接输入仍 `mismatch`、
干净设计仍 `clean`。

### 井连接的检出能力：已构造对照验证到底（2026-10-08）

原先记录的二阶风险是：报告里 42 处 `**Mismatch**` 全是 `SUB | VPW` ×21 和
`w_n86_453# | VNW` ×21，Netgen 用 `Cell pin lists ... altered to match` 自己对齐，
可能掩盖真正的井错误。追到底了。

**第一步，消掉那个自动对齐。** 根因和 LEF 那次同构：

```
PDK 的单元 GDS    21/10  text=VNW      ← n 井的名字
                  204/10 text=VPW      ← p 井的名字
ORFS 的单元 GDS   两层都没有
→ Magic 提取时井节点无名，只能生成 SUB 和 w_<n>#
```

→ Magic 提取改为**先读 PDK 的单元库 GDS**（带井标签）定义全部单元，
再 `gds noduplicates true` 读设计 GDS（只取顶层）。
效果：提取出的子电路端口从 `I VDD VSS Z SUB w_n86_453#` 变成
`I VDD VSS Z VNW VPW`，报告里 `altered to match` 和 `**Mismatch**` **双双归零**，
井端口两边同名、正常参与比对。sky130 不需要这一步——它的单元把井做成普通
`VPB`/`VNB` 引脚，liberty / LEF / GDS 三处都有。

**第二步，井错误到底能不能检出。** 五个对照，结论是清晰的分界：

| 破坏 | 影响范围 | 结果 |
|---|---|---|
| 单个实例 `VNW`: VDD → VSS | 1/447 | `Circuits match uniquely` ❌ |
| 单个实例 `VNW` → 信号网 `net9` | 1/447 | `Circuits match uniquely` ❌ |
| **全部 `VNW`: VDD → VSS** | 447/447 | `Netlists do not match` ✅ |
| **全部 `VNW` → 信号网** | 447/447 | `Netlists do not match` ✅ |

逐层排除过"产物缺失"这个解释：提取侧和网表侧的实例都有 6 个端口，
井端口都实际连着（提取侧第 5、6 位是 `VDD VSS`），所以单实例那两例是
**两边确实不同而 Netgen 判了匹配**，是比对行为而非信息缺失。

**→ 这个边界正好覆盖本流程唯一能产生的井错误。** 全局连接是按引脚名统一施加的
（`add_global_connection -pin_pattern {^VNW$}`），错就是 447 个全错，
不可能只错一个实例。所以对 r2g 的用途，井连接的检出是完整的。
若将来引入**逐实例**的井绑定（例如多电压域、深 N 井隔离），这个盲区会变成真风险，
届时必须重新验证。

### 跨平台回归：几处改动不是 gf180 专属的

三处改动对**所有**平台生效，其中一处会改变判决，所以必须回归：

| 改动 | 影响面 |
|---|---|
| `PWR_PIN` 参数化 | 全平台（原先硬编码 sky130 的 `VPWR`） |
| `_r2g_lef` 展开更多变量 | 全平台 |
| **`Flattening unmatched subcell` 判据** | **全平台，且把"匹配"改判为"不匹配"** |

**仓库测试抓到一个真缺陷,而且是我重犯的那一条**：我在 `_r2g_lef` 里写了 `local`，
而那个函数定义在写 tcl 的 `{ ... }` 组命令内部——bash 在那里运行时拒绝 `local`，
**`bash -n` 检查不出来**。`test_no_local_inside_the_tcl_group_command` 正是为这条而存在的，
它精确命中。另一个失败是测试自己的扫描窗口（固定 2,500 字符）被新代码撑破，
脚本是对的；窗口已改成按结构定位（`POWERED_NETLIST=` 到组命令结束），不再随长度漂移。
→ 26 个测试全过。

**实跑回归**：5 份 sky130hd 设计，7,011 到 111,248 单元（含语料最大的那份），
原本全是 `clean`，改动后仍全部 `clean`。

**但要说明覆盖面的局限**：2,497 份 sky130hd 里同时具备 `6_final.gds` 和 `clean` 判决的
**只有 5 份**——主线为省磁盘（每份约 1.1 GB）只给部分设计留了 GDS。
所以"没弄坏 sky130hd"的信心分三层，强度递减：
26 个单元测试全过（最强，且抓到了真缺陷）→ 5 份实跑回归（直接但样本小）→
代码审查（那个判据只在 Netgen 明确打印该行时触发，而 5 份干净 gf180 和
1 份 sky130hd 都是 0 行）。
→ **下轮保留产物时，把"留足够多的 GDS 以支撑签核回归"列进清单**。

### 补后的完整检出矩阵

| 错误类型 | 检出 |
|---|---|
| 连接错误（改接一个输入） | ✅ 网络 120 vs 121 |
| 拓扑改变（全局换驱动强度） | ✅ 网络 182 vs 208 |
| 单实例尺寸变体（`inv_2`→`inv_4`） | ✅（靠 `Flattening unmatched subcell` 判据） |
| 实例丢失 | openroad 诚实拒绝（DEF 计数不一致），非假通过 |
| 井整体接错（电源网或信号网） | ✅ |
| 井单实例接错 | ❌ 已知边界，本流程产生不出来 |
| 干净设计（5 份，93–3,025 单元） | ✅ 不误报 |

**过程记录**：井问题上做了 5 个对照，其中 1 个因 `sed` 缩进写错而无效
（被 md5 断言拦住，报 `WELL_NOOP` 而不是假的"未检出"——这是本文档第三次
栽在同一处，前两次在 DEF 上）。另外 2 个（单实例换电源网）事后判断为
**对照设计本身不够结构化**：只改变两张电源网的扇出计数（446/1 对 447/0），
不改变任何网络的存在性，拓扑比对放过它是有道理的。
中途还误判过一次"gf180 的 setup 没给 MOS 设 w/l 比较"——那是只 grep 到了
电阻那一段（`r_width`/`r_length`），MOS 的设置在第 122–125 行，和 sky130A 一样。
→ **不要从 `grep -A/-B` 的输出推断文件的真实缩进**，它会加自己的前缀；用 `cat -A`。

**顺带一条测试纪律**：第一轮负对照里两个 `sed` 用了 2 空格缩进而 DEF 实际是 4 空格，
破坏根本没发生，却给出了 `Circuits match uniquely`。
→ **每个负对照必须先断言破坏生效**（本轮改成比对 md5，不变则标记该对照无效）。
一个什么都没做的测试会给出通过的结果——这是本轮反复出现的同一类错误。

### LVS 原先卡住的根因（已解决，保留记录）：ORFS 的 gf180 LEF 缺 VNW/VPW 引脚

三条路（Magic+Netgen、KLayout LVS）都倒在同一处，证据链：

```
① write_cdl 的警告（决定性）
   [WARNING ODB-0286] Terminal VNW of CDL master ..._xor3_2 not found in LEF
   [WARNING ODB-0286] Terminal VPW of CDL master ..._xor3_2 not found in LEF
   → PDK 的 spice 里单元有 6 个端口（信号 + VDD + VNW + VPW + VSS）
     ORFS 的 LEF 只声明了 4 个（信号 + VDD + VSS）

② 于是生成的 CDL 把井端口填成假网络
   X_077_ cnt[1] _001_ VDD _unconnected_0 _unconnected_1 VSS ..._clkinv_2
                            ↑ 这两个就是 VNW / VPW

③ Magic 提取侧则如实给出井/衬底端口
   .subckt ..._dlyb_2 I VDD VSS Z SUB w_n86_453#
   而 Verilog 侧（write_verilog 也读同一份 LEF）只有 I VDD VSS ZN
   → Netgen: 器件数一致 106 vs 106，线网数 120 vs 332
```

对比 **sky130 为什么能工作**：sky130 的单元把井/衬底做成显式引脚 `VPB`/`VNB`，
liberty 和 LEF 都声明了，所以两侧都有、能对上
（`.subckt sky130_fd_sc_hd__fa_1 VNB VPB VGND VPWR B COUT A CIN SUM`）。

**这不是我们脚本的缺陷，是 ORFS 的 gf180 平台 LEF 与 PDK 单元定义不一致。**
修复方向（未验证，按代价排序）：
1. 给 ORFS 的 `gf180mcu_*_sc.lef` 补 VNW/VPW 的 PIN 定义（从 PDK 的 GDS/spice 取几何）；
2. 或在 LVS 前用 `lvs_sub` + Netgen 的 `property`/`ignore` 把井端口并到 VDD/VSS；
3. 或接受井端口缺失，只比对器件级连接（会削弱 LVS 的检出能力，不推荐）。

另有两个 KLayout LVS 侧的参数问题，和上面的根因独立：
- `run_mode=flat` 会把版图压成晶体管级（0 subckt / 1,430 器件），
  而 CDL 侧是单元级（1 subckt / 174 实例）——两边抽象层次不同，必然不匹配。要用 `deep`。
- 顶层端口名提取成数字（`1 5 8 9 15 …`）而不是信号名，因为 GDS 里没有端口标签。
  需要 `top_lvl_pins` 配合 GDS 的 text 标签，或从 DEF 补端口名。

### 顺带修掉的一个真缺陷

`run_netgen_lvs.sh` 的网表守卫硬编码了 sky130 的 `VPWR`：

```
if [[ … ]] || ! grep -q 'VPWR' "$POWERED_NETLIST" …
```

gf180 的单元把电源轨叫 `VDD`/`VSS`，于是一个**正确的 46 KB 网表**（515 个 `.VDD` 连接）
被判成「生成失败」。已参数化为 `PWR_PIN`（sky130→VPWR，gf180→VDD）。
这个缺陷会影响任何非 sky130 平台。同时补了平台分支（magic tech / netgen setup /
spice 路径）和 `SC_LIB_TT`（gf180 的典型角是 `__tt_025C_5v00.lib.gz`，
不是 sky130 的 `__tt_025C_1v80.lib`，而且是 gzip 的）。

### 顺带澄清：45 的 12 worker × 8 核为什么只跑出负载 33

不是配置问题。12 个容器所处阶段实测：yosys 4 个、floorplan 1 个、place_gp 2 个、
place_dp 1 个、cts 2 个、grt 2 个、**route 1 个**。
**只有详细布线会真正吃满 8 核**，其余阶段基本单线程。
所以 `workers × cores` 是峰值配置，只在多数设计同时进入详细布线时才满载——
sky130hd 主线同样如此，负载在 20–80 之间摆动。
→ 不要据此加 worker（详细布线高峰会超订），更不要降 `cores-per-design`
（线程数可能改变布线结果，见六之二）。


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

### 早返回路径会吃掉判决（2026-10-07，这条让三个设计从分母里消失）

漏斗一度读作 `flow 完成 2159，DRC 已签核 2156`。那 3 个不是"还没跑"——
`signoff.log` 里明明写着：

```
19:33:31  timeout  7229s  C00559__jpeg_output        97582 单元
01:54:18  timeout  7242s  C02848__sram_controller   106996 单元
03:28:45  timeout  7237s  C03279__sv_chip2           94170 单元
```

它们是全语料最大的三个设计，KLayout DRC 撞了 7200 秒上限。
**但 `result.json` 里一个字都没有**，而漏斗脚本只读 `result.json`。

原因在驱动脚本的结构：

```python
def one(run_dir, ...):
    ...
    except subprocess.TimeoutExpired:
        out.update(drc_status="timeout", ...)
        return out                      # ← 在末尾写回之前就返回了
    ...
    # 写回 result.json 在这里，三条早返回路径全都绕过了它
```

早返回有三条：`not_a_pass`、`gds_failed`、`timeout`。

**超时的设计和从未尝试的设计，在数据里长得一样。** 这正是 CLAUDE.md 警告的那类缺陷
——不是报错，是静默少算。

**修法不是逐条在 `return` 前补写回**：下一个分支还会漏。要让写回**绕不过去**：

```python
def one(...)                 只计算判决，不碰 result.json
def record(run_dir, out)     写回，对每种结局都调用
def one_and_record(...)      执行器只调这个
```

`record()` 里再加一道：判决为空或 `not_a_pass` 时不动记录，
这样"没判决"和"判了但失败"仍然分得开。

**同一个模板复制出去的脚本有同一个缺陷。** 本轮 `lvs130.py` 是照
`signoff_pass.py` 写的，一模一样的早返回问题，在它跑到最大设计之前修掉了
（已判决的会被跳过，所以修完续跑不浪费）。
**写完一个驱动脚本，先数一遍有几条 `return`。**

**回填用日志，不要重跑。** 那三个的判决在 `signoff.log` 里有确凿记录，
重跑只是再花 2 小时/个去得到同一个答案。但回填要留痕——
我在 `result.json` 里加了 `drc_backfilled_from: "signoff.log"`。

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

**第三个缺陷是本轮发现并修掉的**（`run_lvs.sh`，2026-10-06）：`make lvs` 的依赖链
回溯到 RTL 综合，所以**收获式的 run 它一个都签不了**，而 RMD-P0-01 预检问的四个文件
恰好都是最新的，于是放行后才在 `make lvs` 里炸。现已加冻结版图回退
（`R2G_LVS_FROZEN`，默认 `auto`）。完整原因、两次误判过程、等价性的 md5 证据
见第一节；失败类条目在 `signoff-loop/references/failure-patterns.md`
"`make lvs` cannot grade a harvested run"。

**签核的成本结构**（修正：上面那句"LVS 可以忽略"只对小设计成立）：

```
cells        LVS 耗时              同规模 DRC 参考
     1          6s
   104          8s
   652          9s
 2,216      16-19s
 7,813      80-83s                8,852 单元约 324s   ← LVS 便宜约 4 倍
282,473     >124 分钟未收敛       同规模 DRC 约 5-10 分钟  ← 反过来贵一个数量级以上
```

LVS 的网表提取是**超线性**的（`run_lvs.sh` 自己的注释记着 51K 单元约 2700 秒）。
所以排产要分段：几千单元以下按 DRC 估就够，十万单元以上必须给 LVS 单独留预算和超时。
（我写那个标度实验时漏了 `timeout`，282K 那个跑了 124 分钟才被手动停掉。
批量跑 LVS 一定要包 `timeout`，`run_lvs.sh` 自己有 5400s 默认上限，手搓调用没有。）

**sky130hd 本轮没有可用的 LVS 路径**，两条都断：

```
KLayout LVS   对 1 单元全加器就报 Netlists don't match
              对照验证过不是我的配方：ORFS 自己从 ODB 导网表、走完整 20 个 odb 的
              make 路径，判决相同（原生 deck 更早挂在 pin-count 缺陷上）
              → 命中 failure-patterns.md 已有的 "symmetric-matcher residual"
Netgen LVS    容器和 216 宿主机都没有 magic/netgen，也没有 sky130A 的 libs.tech
              装它要连完整 PDK 一起装，没有 conda、dnf 要 root
```

nangate45 的 KLayout LVS 是好的（5 个规模 × 2 种网表来源 = 10 次判决全部 match），
所以**完整签核漏斗这一轮由 nangate45 提供**，130 只出到 DRC 级。

**一条可复用的正面结论**：没有 `6_final.odb` 也能导网表——`read_lef` + `read_def`
重建的数据库产出的 CDL 和带电源 Verilog 都和 ODB 版等价（只差端口声明顺序），
`read_def` 会把 SPECIALNETS 的 `( * VPWR )` 通配展开成实例级电源连接。
细节和两处计量陷阱见 `failure-patterns.md` "A DEF-rebuilt database yields the same
netlists as the ODB"。这不是"可以不留 ODB"的理由（两边都源自同一个 DEF，少一层
独立性），而是**上一轮没留时的补救办法**。

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
[ ] 本平台的 PDN core 下限已实测（sky130hd 32 µm、gf180 110 µm，差 3.4 倍，必须实测）
[ ] 从带回退的运行抓日志时，按 driver 的 `retry` 行切换槽位——否则抓到的是第一遍
[ ] 用一份日志下结论前，先核它的时间戳/来源，确认是哪一次运行产生的
[ ] 周期/超时/规模阈值等政策已写进脚本头部，跑之前定死
[ ] reaper.sh 已挂（即使代码里的容器清理已修）
[ ] 长任务用 setsid nohup 脱离
[ ] 速率/ETA 只从 driver.log 的 ≥12 小时窗口算
[ ] 选语料的筛选脚本已入库，判据已记录
[ ] 周期按规模分区间取（六之三表的"80% 够用"列），不要全语料一个固定值
[ ] CORE_UTILIZATION 已对照上游自带设计（上游 35-70，我们默认的 20 比所有上游都松）
[ ] 基线纯度已用 md5 对比上游 git HEAD 核实，r2g 改动项已逐条记录（见六之五）
[ ] 定位进程不用 `-f` 文本匹配；pkill 绝不与后续步骤同命令
[ ] 需持续发网络请求的长任务用前台 + 长 timeout（nohup / 后台都被杀过）
[ ] 超时上限 6h（21600s）；改动过上限就归档重跑受影响子集，不混两个上限
[ ] 读平台参数先看 config.mk 的默认值，不要 `ls | head -1` 取到非默认变体
[ ] 查平台有没有某种 deck：列全目录，不要按后缀猜（漏过 ihp 的 lvs/sg13g2.lvs）
[ ] harvest 清单留足够多的 6_final.gds —— 否则签核改动无法做实跑回归
    （本轮 2,497 份里只有 5 份能做 LVS 回归）
[ ] 改过签核脚本就跑 `pytest r2g-skills/signoff-loop/tests/` 再实跑回归；
    跨平台生效的判据（尤其会改判决的）必须在旧平台上验证不误伤
```
