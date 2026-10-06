# Task 0.2.0 扩展规格

0.2.0 保留原七项 capability，新增 `task.cancel`、`task.archive`，继续使用 Core 0.3.1、Contract v3、json-stdio-v2 和 local_commit_v1。全部状态操作要求 `task_id`、`expected_version`，fields/target ID 一致，`target.store_id` 绑定 `task_store`。两个新增能力要求 `task.read` + `task.write`。

| 操作 | 接受的任务 | 结果与 no-op |
|---|---|---|
| task.cancel | open；cancelled 为 no-op | open → cancelled；completed 报 task.validation_error；deleted 报 task.deleted_terminal。保留 archived_at，重复 cancelled 不改版本/时间/事件。 |
| task.archive | 全部非 deleted 状态 | 首次设置 archived_at，保持 status 和 completed_at；已归档为严格 no-op。 |
| task.reopen | completed、cancelled 或 archived_at 非 null | status → open，completed_at 和 archived_at 清空；未归档的 open 为 no-op。 |
| task.delete | open、completed、cancelled，包含归档任务 | 保留既有 completed_at、archived_at，设置 deleted_at；deleted 为终态，重复 delete 为 no-op。 |

归档是可见性维度，并非 status 枚举值。`status` 的四个值为 open/completed/cancelled/deleted。已有 complete/update 行为保持兼容；例如 cancelled 执行 complete 无实际变化，需先 reopen 才能完成。

Task 公共模型增加 nullable `archived_at`，创建时为 null，禁止通过 task.update 修改。task.get 用 ID 直接读取归档任务；默认隐藏 deleted，显式 include_deleted=true 可读。task.list 默认包含 open/completed/cancelled，排除 deleted 与归档；include_deleted=true 和 include_archived=true 分别显式放开两个维度。status 条件不会绕过归档排除；两个标志均参与游标过滤指纹，不能跨可见性设置复用 cursor。

新 request 的 expected_version 检查先于 no-op/状态判定。相同原 request 的重放仍先验证完整身份及 provider pins，返回原始结果，避免用最新任务拼接响应。每次实际取消/归档只产生 `task.cancelled`/`task.archived`：

```json
{"type":"task.cancelled","resource_refs":{"task_id":"tsk_00000000000000000000000000000001"}}
```

事件意图只含 type 和 resource_refs.task_id，不含标题、notes、状态正文。原 proofs、结果快照、Core 原收据与事件保持不可变；event_recovery_state 的 pending→ledger_recorded 推进和现有 180 天正文清理策略继续适用。提交后进程中断由 Core 显式 resume 核对 proof 并补确认/事件；恢复不重做业务。

## schema v1 → v2

新库使用 user_version=2/schema_meta.schema_version=2。v2 增加 tasks.archived_at，扩展 status 与 operation_name 的 CHECK 约束。外键、标签顺序、索引和 proof 主键沿用基线。

旧库只读查询和 proof 查询支持 v1，返回的当前 Task 模型补 archived_at=null；不会改写数据库。首次获准写事务获得 BEGIN IMMEDIATE 锁并核对 store/schema 后，使用独立只读连接的 SQLite online backup 保存完整已提交 v1 数据，命名 `<db>.schema-v1-<uuid>.bak`，随后事务重建 v2 表。原任务、标签、schema_meta.created_at 和 proofs 全部复制；原 JSON 快照、provider 版本/摘要、事件意图、结果保留时刻逐字段保持。旧快照不会被补充新模型字段。

备份失败拒绝迁移；DDL/复制/外键检查异常回滚原库。备份先写入 `.bak.incomplete` 临时文件，完整关闭后才改名为 `.bak`；失败时尝试清理未完成文件，清理失败仍保留 `.incomplete` 标识。失败迁移的已完成备份继续保留。备份在 Task 私有数据目录，由用户在确认升级及恢复需求后管理；备份包含升级前原始正文，不参与后续在线库的 180 天清理。迁移没有跨 Task/Core 文件事务，也不修改 Core 台账。升级后的 v2 库不能由 0.1.0 直接读取；回退需停用插件并从 v1 备份恢复，同时由 Core 核对升级后的请求记录。

原 provider/version/digest 的恢复门禁保持不变。升级到 0.2.0 不授权跨 provider 重放 0.1.0 请求，迁移保存旧 pins 以供原 provider 核验。
