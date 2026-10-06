# Task Plugin 0.1.0：批准规格的工程化冻结副本

这是用户已批准设计的行为约束。Implementation Ready 文档和实现以此为准；Core SDK 函数签名由 Core 工作流冻结后补充。

## 目标和边界
- 独立 Task 仓库，插件 id yushuos.task，Python包 yushuos_task，发行版本0.1.0。
- Core补丁0.3.1，Task requires yushuos-core>=0.3.1,<0.4；Python>=3.11，依赖SQLite标准库，无外部App/LLM/Agent框架。
- v3/json-stdio-v2，七个业务capability，standalone。无排程、通知、GUI、子任务、DAG、云同步、NLP、默认自动化、旧数据导入。
- 同一Core root一个任务库。Task业务project_ref仅分类；Core Request/PluginContext.project_ref仍为运行项目身份，不混用。
- Task sqlite私有，不跨插件碰DB；Core owns 唯一claim/locks/receipt/outbox/EventBus/workflow/scheduler。
- 目前Task远端地址由用户稍后提供。可以先完成本地代码、普通提交、ZIP和验证，不自行创建GitHub仓库。

## 能力和权限
- task.create: command/internal_write，task.write。
- task.get、task.list: query/read_only，task.read。
- task.update、complete、reopen: command/internal_write，task.read + task.write。
- task.delete: command/internal_write，task.read + task.delete。
- 插件级permissions为空，逐能力声明。拒绝默认整体授权。
- 全部请求target.store_id，manifest resource_scopes store_id -> task_store，Core binding匹配。
- 已有任务操作同时有fields.task_id和target.task_id，必须一致。task.list无task_id。

## Task主模型
id,title,notes,status,priority,project_ref,due_at,estimate_minutes,tags,source_ref,created_at,updated_at,completed_at,deleted_at,version。
- ID tsk_ + uuid4.hex，内部生成，不新增ID库。
- title trim 1..500 Unicode字符，notes null或<=20000字符（可空字符串）。
- priority low/normal/high/urgent，默认normal。
- estimate_minutes null或真整数1..10080，bool拒绝。
- tags <=32，tag trim 1..64，case-sensitive，去重保持首次顺序。
- project_ref/source_ref null或trim后1..200字符。不查询外部引用存在性。
- due_at null或严格带时区RFC3339时刻。所有保存时间UTC六位小数；日期型解释在Host，不隐含猜测。
- status只有open/completed/deleted；deleted终态，softdelete，无restore/purge。
- version初始1，实际业务变化+1。

## 修改规则
- create仅title必填，其他缺省default/null/[]。不同request相同内容可创建不同Task。
- update/complete/reopen/delete expected_version全部必填，真整数>=1。
- 对新request先检查expected_version，再判断no-op。旧版本即使目标状态已存在也报task.version_conflict。
- 原request重放先核验身份和fingerprint，再返回原结果，不重新检查最新Task版本。
- update白名单仅title/notes/priority/project_ref/source_ref/due_at/estimate_minutes/tags；缺省不修改，nullable显式null清空。
- 禁止update id/status/version/created_at/updated_at/completed_at/deleted_at和所有未知字段，无任意patch或MergePatch。
- completed Task普通字段可update；deleted Task不允许update/complete/reopen。
- complete open->completed，completed_at now；reopen completed->open，completed_at null。
- delete open/completed->deleted，deleted_at now；保留原completed_at。
- 版本匹配时：complete completed、reopen open、delete deleted、相同update返回changed=false。
- HARD: changed=false不得创建任何event intent，也不增version/updated_at。

## Query
- get/list默认隐藏deleted；get include_deleted=true可见deleted。
- list默认open+completed，status=deleted必须include_deleted=true。
- filters status/project_ref/priority/due_before/due_after/tag/updated_after。
- project_ref缺省all，显式null仅unassigned。
- due_before<=、due_after>=，due null不进入时间范围；updated_after严格>。
- limit默认50，最大200，正整数；created_at DESC,id DESC keyset cursor。
- cursor严格解析绑定store/filter/last sort key，不允许SQL/predicate，不承诺跨并发完整snapshot。
- get/list不claim、不创建业务DB、不变业务version/times，不清理过期正文，不产生event。

## 请求身份
- Core共享ledger request_id仍全局唯一；不同plugin/store也不能在同一ledger复用原ID。
- Task提交证明PK(plugin_id,store_id,request_id)，version/digest只是pins，不进入PK。
- request_fingerprint使用Core SDK JCS helper，SHA256(RFC8785 UTF8 {capability,fields,target})。
- 使用通过Schema校验、但未做Task trim/default/timestamp归一化的请求字段。
- 键排序等价；缺省与显式default、数组顺序、Unicode序列不自动等价。
- 排除request_id、trace、运行时间、重试计数；intent/Core project/provider另行不可变绑定检查。
- Core旧Request.fingerprint()保持legacy-v1。operation_support local_commit_v1的v3内部写用jcs-operation-v1，方案持久化不可猜。

## Storage schema v1
- tasks:主模型全部字段+内部store_id；id唯一，version>0，status约束。
- task_tags:task_id/tag/position，task_id/tag唯一，FK与顺序。
- task_request_commits:复合PK，request_fingerprint/fingerprint_scheme/operation_name/core_project_ref/provider_version/provider_digest/task_id/operation_status/changed/error_code/result_body/committed_at/result_body_expires_at/event_intents/event_recovery_state。
- schema_meta:schema_version和当前store_id。
- 索引涵盖排序、status、project、due、updated、priority、tags、正文到期清理。
- event_recovery_state none(无event)/pending(待确认ledger)/ledger_recorded(收据outbox持久化)，不表示消费者送达。
- schema未知新版本拒绝写，migration事务化，失败旧DB可恢复。
- SQLite busy有界等待，BEGIN IMMEDIATE内版本检查+变更+proof；两个进程同版本只有一个成功。

## 写、事件与Crash
Core gates -> SDK claim -> Task单事务业务+proof+快照+事件意图 -> SDK单ledger事务receipt+outbox -> 本地marker -> response。
不宣称Task sqlite与Core ledger跨文件ACID。没有第二套operation-ledger。
- event types task.created/updated/completed/reopened/deleted。
- emission只有type和resource_refs.task_id；无task_ref/正文/oldstatus/changedfields。
- Core tick/worker导入事件，稳定ID去重。Host可通过Core get接续，无自动event参数绑定。
- 显式resume只recover已提交结果，不重做业务；没有proof保持unknown+lock，Host核验原进程停止后history resolve。
- 原provider/version/digest变化failclosed。recover保留originalrun/root/causation/depth。
- abandoned保留lock；人工不同resolution不能被插件覆盖。
- 成功正文snapshot重放，不从最新Task拼结果。replay只读私有proof。

## 180天结果正文
- 从首次提交180天，到期立即不可重放；下一次真正获准写调用清理expired result_body。
- read/preview/replay不purge；长期无write时物理文件仍可能有过期正文。
- 请求身份/fp/originalstatus/task_id/changed/event-recovery元数据永久保留。
- 只清理操作快照，不删除Task/softdeleted事实。
- expired Result.status=succeeded，data={operation_status:committed,result_state:expired,code:task.result_expired,task_id,original_request_id}。
- expired不包含task或changed，不把成功改失败；Core仍可补receipt/event。
- normal write data保留task/changed，附operation_status committed/result_state available。
- preview status preview、data planned/preview+proposed fields、changed=false，无正式task_id/claim/业务DB。

## Core补丁
nullable schema(null/一个实类型+null)、v3 operation_support local_commit_v1、runner action replay/recover、reconcile_confirmed、status provenance。
- replay仅终态重复请求；recover仅显式resume，重新检查权限/resource门禁。
- recovery会在同ledger事务记录核验结论并补事件，保留originalreceipt，防止人工裁决冲突。
- Core只保存safe refs和provenance，无Task正文。
- provenance解释request/运行项目/rule/provider，不宣称caller用户/Agent身份认证。

## 开发与验收
baseline d7e5ce4 Core138tests已通过。先Core接口，再Task契约，再Storage冻结。
Stage4黄金链必须含create+claim+Taskcommit+proof+receipt/outbox+event+crash recovery+replay；之后才扩展其他capabilities。
八工作流Core/Contract/Storage/Write/Query/Recovery/Tests/Docs，公共schema/manifest只有一个负责人。
TDD先red再green，所有测试使用临时数据，无真实外部写入。不覆盖原用户工作区、不forcepush。
验收覆盖全部cap/status/version/noop、并发、请求身份、四个crash窗、retention180day、events dedupe、权限deny/scope、provider mismatch、abandoned、query/preview readonly、隐私、10000tasks、三OS真实runner/ZIP/install。
完整交付Core0.3.1、Task0.1 ZIP、migration、tests、README中英、capabilityguide、BusinessPluginAuthorGuide、Demo、逐项验证报告。
长期资产contracts/fixtures、reference-plugin、作者指南；参考骨架不得复制Core基础设施。
