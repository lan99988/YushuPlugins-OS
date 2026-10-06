# Plugin Specs（Draft，待运行代码接合）

草案目标：Core 0.3.1 / manifest v3 / json-stdio-v2；完整 machine-readable input/output 定义位于 contracts/<slug>。所有 capability 均拒绝未知字段；所有私有本地数据显式绑定 store_id。默认列表 limit=50，最大100；写变更使用 expected_version 做乐观并发控制，失败返回 version_conflict。

| 插件 | slug/provider | 能力与领域约束 | 主要错误与验收 |
|---|---|---|---|
| Task | task / yushuos.task | task.create/get/list/update/complete/reopen/delete/cancel/archive；按 Task v1.0字段；创建默认待处理，不推断截止日，完成保留，飞书原生任务权威 | invalid_input/not_found/version_conflict/provider_error；验证字段映射、状态转换和删除需显式调用 |
| Capture | capture | create/get/list/route/mark_processed/archive/restore；route仅记宿主分类，processed必须带引用或忽略原因 | 缺引用且无ignored_reason拒绝；route不产生外部副作用 |
| Idea | idea | create/get/list/update/archive/discard/mark_converted；标题、描述、标签、状态 | 转换只记状态，不隐式创建任务 |
| Bug | bug | create/get/list/update/resolve/reopen/close；severity、复现步骤 | 无效严重级别拒绝；状态迁移幂等且带版本 |
| Finance | finance | statement create/get/list；snapshot create/get；month close；summary get；金额为Decimal十进制字符串 | 禁止浮点损失、OCR和投资估值；余额精确求和 |
| Relationship | relationship | person CRUD；interaction add/list；status get；互动需时间、渠道、摘要 | 外键 person 必须存在且同 store |
| Habit | habit | CRUD；checkin/undo_checkin/pause/resume；cadence和target显式 | 同日重复 checkin 按 idempotency 返回既有结果 |
| Body | body | state/record get、latest、history；sleep/training/recovery record | 时间区间有效；不可推断缺失测量值 |
| Life admin | lifeadmin | CRUD/complete/renewal.record；续期含 item、日期、提供方、金额与币种 | 完成不删除；日期及金额校验 |
| Decision | decision | CRUD；option.add/choose/outcome.record/review | option 必须属于 decision；选择保留理由 |
| Feishu | feishu | calendar event CRUD/busy；task CRUD/complete；binding create/list；message.send | provider真实回执；权限或超时不得伪报成功 |
| IMA | ima | note create/get/list/append/search；notebook list；kb get/list/search；media get；binding | provider真实回执；查询分页且限定授权范围 |
| Planner | planner | plan CRUD；schedule generate/preview/apply/replan；next_action.suggest | 硬约束→priority→due_at→stable id；energy config；无AI |
| Deep work | deepwork | session start/pause/resume/finish/cancel/get/list；interruption.record | 非法状态迁移拒绝；时长非负 |
| Competition | competition | CRUD；milestone.add/progress.record/assessment.record | 记录须绑定赛事；时间和指标有效 |
| Knowledge | knowledge | CRUD/archive；concept.create/link；insight.create；search | 引用同 store；链接端点存在 |
| Creation | creation | CRUD/advance_stage/publish/archive | 发布需明确时间及目标；阶段序列校验 |
| Review | review | generate/get/list/finalize/compare；结构化独立记录 | generate/compare host_required；必须返回具名结构化结果 |
| Personal model | personal_model / yushuos.personal_model | hypothesis CRUD；evidence.attach；evaluate | evaluate host_required，仅用显式证据，概率有校准定义 |
| Cognition | cognition | analyze；pattern CRUD；review | analyze/review host_required；证据显式且不可虚构 |

验收由 `tests/test_contract_inventory.py` 机械核对20个唯一slug、各固定能力清单、字段约束、标准 envelope、拒绝秘密字段及特殊约束。该测试只证明合同库存形状，不替代provider端到端验收。

