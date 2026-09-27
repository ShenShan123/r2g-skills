# R5 v9：DEV action 接入核心 primitive，Memory 权限仍关闭

继 APEX v9 source-only binder 和 augmented DEV oracle 后，新增 `rtl/skid_payload_action_v9.py`：从所给 RTL、公开 context 和固定模板重新生成含 source/witness digest 的精确 action payload；执行前再次重算并拒绝陈旧、篡改、额外答案字段或不支持的源。加入 `apply_rtl_action` primitive catalog，版本由 `rtl-actions-v0.9` 升为 `rtl-actions-v1.0`。这是显式 **software delta**，不是 Memory delta。

现阶段没有 v9 source-bound Asset 桥、TRAIN raw verifier 或可检索的 v9 Memory。`source_selection` 对缺少 source-binding replay 的 v9 candidate 明确拒绝，`structured_candidate` 对缺少注册模板的 v9 domain 拒绝；`lifecycle` 两条准入路径和 `registry` 直接晋升路径对 v9 domain／profile（含嵌套 payload）硬拒绝，理由 `v9_raw_train_authority_not_implemented`。即使调用方给出全部真 gate 或伪造 strict receipt，也不授予 promoted authority；未来只有新 TRAIN generation 和独立核验才能改变该状态。

离线回归：v9 action 10/10（core dispatch、精确编辑、健康/非目标/陈旧/篡改拒绝、无文件网络旁路、无 source proof candidate 拒绝、伪造晋升拒绝）；原 v9 binder 9/9；既有 v8 TRAIN authority 30/30。所有当前 `memory/tehm/**/*.py` 语法解析通过。action 输出与 APEX DEV clean 源逐字节相同是 evaluator 事后核对；本次没有新 simulator 调用，不增加独立任务或修复样本。既有 DEV oracle 已在独立构建下验证该候选的 target/preservation PASS。

下一门槛仍是合法 TRAIN 来源、严格 raw authority、v9 软件冻结、预登记的独立新目标及共享软件三视图 fresh oracle。元数据发现批次 1 未取得新目标，不能以其 0/2 当方法失败率；无模型调用、Memory 更新、生产晋升或 GitHub 推送。
