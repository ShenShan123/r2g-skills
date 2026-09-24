# Revision5：skid payload 来源历史 DEV 审计增量

状态：**历史原始材料及本地固定版本完整性通过；两个候选来源组的分离线索增强；`independent_lineages_verified=false`。** 本记录补充[此前的浅克隆预检](research_r5_skid_lineage_precheck_20260924.md)，不把旧报告在当时的观察改写成已完成认证。它不提供 TRAIN 任务、合格修复、Knowledge L3 或 M+ 准入。

## 取证范围与冷复核

原始公开历史只存在 evaluator/DEV 目录 `/data1/zhangdy/RTL/RTL_testbench/_qualification/r5-lineage-history-20260924-QGq6q1/`；不提交到 TEHM Git，不进入 learner staging。三个查询均固定到本地 checkout HEAD，使用 GitHub `commits?path=<RTL>&sha=<HEAD>&per_page=100`；重新查询响应头为 HTTP 200，未见后续 `Link` 页。完整原始 JSON 和首版 RTL 字节已保存，并由 [冷审计器](../tehm/evaluation/research_r5_lineage_history_audit.py)中的 SHA256 锁、首版 commit `added` 记录及 Git blob SHA-1 交叉验证。复核命令：

```sh
PYTHONDONTWRITEBYTECODE=1 python3 memory/tehm/evaluation/research_r5_lineage_history_audit.py \
  --history-dir /data1/zhangdy/RTL/RTL_testbench/_qualification/r5-lineage-history-20260924-QGq6q1 \
  --corpus-root /data1/zhangdy/RTL/RTL_testbench
```

结果 `valid=true`，三个 `origin_blob_verified=true`，输出仍明确 `independent_lineages_verified=false`。冷审计器同时核对两个 checkout 干净、HEAD 与三个当前 RTL SHA256 符合先前固定版本；没有修改上游 checkout。

| DEV 模块 | 固定 HEAD 下文件级提交 | 最早文件提交及 `added` blob | 观察到的来源线索 |
|---|---:|---|---|
| `alexforencich/verilog-axis/rtl/axis_register.v` | 17 | [2014-09-14 `74fa967`](https://github.com/alexforencich/verilog-axis/commit/74fa9670712d2252493872681a653403689e8fc5)，`42fa50104afda37c2443c41fe5c2c987c3dd2148` | 首版文件头 Alex Forencich；提交主题为新增 AXI stream register。 |
| `alexforencich/verilog-axis/rtl/axis_broadcast.v` | 7 | [2019-02-28 `b60886a`](https://github.com/alexforencich/verilog-axis/commit/b60886a0eca31198f9a79874f73e21963c51656e)，`c285ff3e3797d00562f0f93cf1c076f0644548dc` | 首版文件头 Alex Forencich；同提交新增该模块的 testbench。与 register 仍按一个来源组计。 |
| `ZipCPU/wb2axip/rtl/skidbuffer.v` | 22 | [2019-05-16 `ded500c`](https://github.com/ZipCPU/wb2axip/commit/ded500c75dba4c528bf642947461227785365cbc)，`dfd7115f535ea564e88217be7fc39f61191d1087` | 首版文件头 Dan Gisselquist / Gisselquist Technology；同提交新增 formal 配置。 |

GitHub 仓库 API 在本次查询中将 `alexforencich/verilog-axis` 和 `ZipCPU/wb2axip` 都标为 `fork=false`。这是平台元数据，不排除手动复制、重写、共同来源或后续迁移。锁定的三个目标文件各只有一个 `module`，未发现 `` `include`` 或子模块例化；这仅限定目标文件级依赖，不代表整个项目无共享第三方依赖。原始与当前 RTL 的简易非注释行序列比对未发现跨两仓库的长段逐行相同代码，但受命名和结构重写影响，**不能当作无复制证明**。相反，同仓库 register/broadcast 的相似实现不增加支持 lineage 数。

## 判定及剩余 gate

当前可审计结论是两个**候选**源码家族，各模块有可追到首版新增提交的公开历史；文件内容与原始提交 blob 可交叉核对。还没有完成足以授予 `independent_lineages_verified` 的跨项目复制/生成器/主要依赖审计和签收，因此 [TRAIN 准备状态](research_r5_train_readiness_20260924.md)保持 **NO-GO**。尤其不能用 17+7+22 条提交充当 46 个独立样本，不能把已有 DEV 故障和研究者增强 oracle 计作合法 TRAIN，也不能给 `knowledge.authority` 手填第二 lineage。

后续若获准建立独立 TRAIN campaign，仍须先冻结任务角色、来源关系与可见性；再保存 baseline→action→合格 oracle／保持义务的真实回执，按现行 `assets.lifecycle` 和 `knowledge.authority` 严格判定。目标选择、M−/M+/Mremove 与论文统计均不能由本次来源历史结果提前启动。
