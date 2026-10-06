# Task Contract 与存储参考（0.2.0）

本文是 Task 0.2.0 的面向实现者参考，覆盖九项能力、输入输出、持久化模型、查询游标和结果保留策略。权威契约定义在 [`contracts.py`](../src/yushuos_task/contracts.py)，字段规范化与状态迁移定义在 [`domain.py`](../src/yushuos_task/domain.py)，SQLite DDL 和 Repository 定义在 [`storage.py`](../src/yushuos_task/storage.py)。Repository 的冻结 Python 接口与错误边界见 [`STORAGE-API.md`](STORAGE-API.md)。

## 能力与请求形状

所有请求都通过 Core capability 执行，并将 `target.store_id` 绑定到 Core 提供的 `task_store` 资源。单条 Task 操作还需同时在 `fields.task_id` 和 `target.task_id` 提供相同 ID。Task ID 的格式为 `tsk_` 加 32 位小写 UUID hex。Task 0.2.0 不提供插件专属 CLI 或外部系统写入。

| Capability | Effect / Permission | 输入字段 | 成功 `data` |
|---|---|---|---|
| `task.create` | `internal_write` / `task.write` | `title` 必填；可选 `notes`、`priority`、`project_ref`、`source_ref`、`due_at`、`estimate_minutes`、`tags` | 正常：`task`、`changed`、`operation_status="committed"`、`result_state="available"`；结果过期时使用下文的最小过期结构。 |
| `task.get` | `read_only` / `task.read` | `task_id` 必填；`include_deleted` 可选，默认 false | `{ "task": Task }` |
| `task.list` | `read_only` / `task.read` | 以下过滤字段与分页字段均可选 | `{ "tasks": Task[], "next_cursor": string \| null }` |
| `task.update` | `internal_write` / `task.read` + `task.write` | `task_id`、`expected_version`、`changes` 必填 | 与 create 相同的写结果分支。 |
| `task.complete` | `internal_write` / `task.read` + `task.write` | `task_id`、`expected_version` 必填 | 与 create 相同的写结果分支。 |
| `task.reopen` | `internal_write` / `task.read` + `task.write` | `task_id`、`expected_version` 必填 | 与 create 相同的写结果分支。 |
| `task.cancel` | `internal_write` / `task.read` + `task.write` | `task_id`、`expected_version` 必填 | 与 create 相同的写结果分支。 |
| `task.archive` | `internal_write` / `task.read` + `task.write` | `task_id`、`expected_version` 必填 | 与 create 相同的写结果分支。 |
| `task.delete` | `internal_write` / `task.read` + `task.delete` | `task_id`、`expected_version` 必填 | 与 create 相同的写结果分支。 |

`task.update.changes` 是字段白名单对象，只允许 `title`、`notes`、`priority`、`project_ref`、`source_ref`、`due_at`、`estimate_minutes`、`tags`。禁止修改 `id`、`status`、`version`、任何创建/更新时间和完成/删除/归档时间。状态只能由 complete/reopen/delete/cancel 操作改变；archive 只设置 archived_at。除 create 外，每个修改请求都必须带 `expected_version`，并在 SQLite 写锁内比较；版本冲突先于 no-op 判定。

`task.list` 支持以下字段：`status`（1–4 个不同状态组成的数组）、`include_deleted`、`include_archived`、`project_ref`（省略与显式 null 可区分）、`priority`、`due_before`、`due_after`、`tag`、`updated_after`、`limit`（1–200，默认 50）和 `cursor`。可查询 `deleted` 状态时必须显式传 `include_deleted=true`。默认查询排除 deleted 和 archived_at 非 null 的任务；include_archived=true 显式包含归档。取消任务默认可见；显式状态过滤不会自动改变这一授权式可见性约束。

## Task 公共模型与规范化

Task 的公开 JSON 字段固定如下；内部 `store_id` 不返回给调用方。

| 字段 | 类型 / 规则 |
|---|---|
| `id` | `tsk_` + 32 位小写 UUID hex。 |
| `title` | trim 后 1–500 字符，必填。 |
| `notes` | 字符串或 null，最多 20,000 字符；保留原文，不 trim。 |
| `status` | `open`、`completed`、`cancelled`、`deleted`。新建值为 `open`。 |
| `priority` | `low`、`normal`、`high`、`urgent`；默认 `normal`。 |
| `project_ref`、`source_ref` | 字符串或 null；非 null 值 trim 后 1–200 字符。`project_ref` 是业务分类，不是权限边界。 |
| `due_at` | RFC3339 时刻或 null；必须显式带时区，归一化为 UTC 六位小数并以 `Z` 结尾。日期型截止时间由 Host 解释。 |
| `estimate_minutes` | 整数或 null；范围 1–10,080。布尔值不作为整数接受。 |
| `tags` | 最多 32 个字符串；每个 trim 后 1–64 字符，按首次出现顺序去重。 |
| `created_at`、`updated_at` | UTC 六位小数、`Z` 结尾的时间字符串。 |
| `archived_at` | UTC 六位小数时间或 null；独立于 status，archive 设置、reopen 清空。 |
| `completed_at`、`deleted_at` | 同格式时间字符串或 null；与状态保持一致。 |
| `version` | 从 1 开始的正整数；仅有实际业务变化时递增。 |

输入 schema 先检查 JSON 类型和结构；`title`、引用和 tag 的长度约束在 Task domain trim 后执行，以保证 Core schema gate 与领域规则一致。输出 schema 使用规范化后的精确长度约束。`due_at`、`due_before`、`due_after` 和 `updated_after` 均使用严格 RFC3339 校验；UTC 归一化时区偏移小时不得大于 23，分钟不得大于 59。

## 写结果、no-op 与事件

首次提交的正常写结果为：

```json
{
  "task": { "id": "tsk_…", "version": 1 },
  "changed": true,
  "operation_status": "committed",
  "result_state": "available"
}
```

重复 complete、已处于目标状态的 transition 或内容未变化的 update 可返回 `changed=false`。Task 版本、时间戳不变，且不得创建 event intent。Task SQLite 内的业务修改、原结果快照、提交证明和事件意图处于同一事务。Task DB 与 Core ledger 不宣称跨文件事务；Core receipt、outbox 和事件由 Core SDK 管理。该写入与恢复接口说明见 [`STORAGE-API.md`](STORAGE-API.md)。

被删除的 Task 是终态：可通过显式 `include_deleted=true` 读取；重复 delete 是 no-op；其他修改和 transition 报 `task.deleted_terminal`。业务删除为软删除，不物理移除 Task 或 tags。

### Event contract

Task 声明七类事件：`task.created`、`task.updated`、`task.completed`、`task.reopened`、`task.deleted`、`task.cancelled`、`task.archived`。私有提交证明中的事件意图只允许以下形状，禁止额外字段；`task_id` 必须等于该次提交的 Task ID：

```json
{"type":"task.created","resource_refs":{"task_id":"tsk_00000000000000000000000000000001"}}
```

事件类型的权威列表是 `contracts.py` 的 `TASK_EVENTS`；私有意图结构由 `storage.py` 的 `_insert_proof` 校验。Core SDK 按自己的 Event Envelope 合同添加稳定 event ID、source/provider、request/project 和因果链信息。Task 不另建 EventBus，不给意图添加标题、notes、状态正文或变更字段。真实集成测试覆盖 receipt/outbox、PersistentEvent 导入及去重。

## SQLite schema v2

一个 Task SQLite 文件只绑定一个 `store_id`。`schema_meta` 记录单例 schema 版本与 store 身份；DDL 和首次获准业务写入在同一事务中创建。每次读取或写入都会校验 schema 版本和 store 身份；未知/部分 schema、高版本或 store 不匹配均 fail closed。表、列、键与索引的完整冻结说明见 [`STORAGE-API.md`](STORAGE-API.md)。

| 表 | 主键 / 关键内容 | 作用 |
|---|---|---|
| `schema_meta` | `singleton=1`；`schema_version=2`、`store_id`、`created_at` | 验证数据库身份及 DDL 版本。 |
| `tasks` | `(store_id,id)`；Task 公共字段及内部 `store_id` | 保存 Task 当前事实；`id` 另有唯一约束。 |
| `task_tags` | `(store_id,task_id,tag)`；顺序由 `position` 保存 | 保存有序去重标签；复合外键指向 Task。 |
| `task_request_commits` | `(plugin_id,store_id,request_id)` | 保存 Core 绑定身份、JCS fingerprint、operation/provider pins、task_id、提交状态、结果正文与到期时刻、事件意图和恢复状态。 |

`task_request_commits` 是 Task 同一 SQLite 事务内的 commit proof，不是第二份 Core claim/receipt ledger。proof 主键包含 provider/plugin identity、store 与 request，避免裸 `request_id` 跨插件或 store 碰撞。它保存 `fingerprint_scheme`、`request_fingerprint`、`operation_name`、`core_project_ref`、provider version/digest、`task_id`、`operation_status`、`changed`、`error_code`、`result_body`、`committed_at`、`result_body_expires_at`、`event_intents` 和 `event_recovery_state`。

## 结果正文保留与恢复

Task 成功结果正文从首次提交时刻起保留 180 天。到期后结果在逻辑上立即过期：原 request 重放返回 `operation_status="committed"`、`result_state="expired"`、`code="task.result_expired"`、`task_id` 和 `original_request_id`，不得包含 `task` 或 `changed`。这表示业务已经提交，但原结果正文已不可重放。

过期正文只在下一次新获准的 Task 写请求事务中物理清空；已经到期的正文会在该事务内更新为 SQL `NULL`，proof 与 Task 继续保留。只读查询、preview、replay 和 recover 不执行清理。`task.result_expired` 的标准分支由 [`validate_result_data`](../src/yushuos_task/contracts.py) 验收；Task 数据库的完整恢复签名见 [`STORAGE-API.md`](STORAGE-API.md)。

## 当前错误代码

领域层对可确定请求错误使用下列稳定代码。Core/runner 最终状态仍由 Core 协议决定；尤其是业务 commit 已完成但 Core receipt 尚不确定时，写响应会保持 `unknown`，要求对原请求执行 Core recovery，而不谎报业务失败。

| Code | 含义 |
|---|---|
| `task.validation_error` | 输入字段、目标、过滤器、时间或游标不符合 Task 契约。 |
| `task.store_scope_mismatch` | 请求 store 与 Task DB 或 Core resource binding 不一致。 |
| `task.schema_version_unsupported` | 数据库 schema 缺失、不完整、不兼容或版本高于实现。 |
| `task.not_found` | Task 或对应提交证明不存在。 |
| `task.deleted_terminal` | deleted Task 不允许执行该修改/状态操作。 |
| `task.version_conflict` | `expected_version` 与当前 version 不匹配。 |
| `task.request_conflict` | 相同 `(plugin_id, store_id, request_id)` 被不同请求身份复用。 |
| `task.storage_error` | Storage 层通用错误基类。 |
| `task.database_error` | SQLite 打开、锁等待、约束或事务错误。 |
| `task.result_expired` | 成功 commit 的原结果正文已过期；它出现在正常的最小终态 `data` 中。 |

旧 schema v1 在只读查询时兼容读取，archived_at 返回 null。首次获准写入先锁定写库并用 SQLite online backup 保存 `<db>.schema-v1-<uuid>.bak`，再在同一 Task 事务重建 schema v2；备份失败拒绝写入，迁移异常回滚。迁移保存全部任务、标签、schema_meta.created_at 及提交证明原字段，不重写 JSON 快照、provider pins 或事件意图。完整规则见 [0.2.0 扩展规格](TASK-0.2.0.md)。
