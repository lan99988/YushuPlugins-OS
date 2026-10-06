# Task Storage API v2

本文件冻结 Storage/Domain 与写路径、恢复、查询工作流之间的接口。SQLite 业务状态与提交证明由 `TaskStore` 同一事务提交；Core 的 claim、receipt、outbox 和事件仍由 Core SDK 管理。

## Repository

```python
TaskStore(path: str | Path, *, plugin_id: str = "yushuos.task")

store.get_task(store_id, task_id, *, include_deleted=False) -> Task | None
store.list_tasks(store_id, filters: TaskFilters, *, limit=50, cursor=None) -> TaskPage

store.commit_create(task: Task, identity: CommitIdentity) -> CommitOutcome
store.commit_mutation(
    task_id, expected_version, identity, *, changes=None, now=None
) -> CommitOutcome

store.lookup_commit(plugin_id, store_id, request_id) -> TaskCommitProof | None
store.mark_events_recorded(identity) -> str  # none | pending | ledger_recorded
```

`commit_create` 和 `commit_mutation` 使用 `BEGIN IMMEDIATE`，在一次 Task SQLite 事务中检查 store/schema、写 Task 与 tags、清理本 store 已到期的结果正文、写原结果快照和事件意图。新库的 v2 DDL 也在该事务中执行。`commit_mutation` 必须先比较 `expected_version`，再判断业务变化；无变化时 version/time 不变，`event_intents=[]` 且 `event_recovery_state='none'`。

`CommitIdentity` 为不可变记录，字段顺序如下：

```python
CommitIdentity(
    plugin_id, store_id, request_id,
    request_fingerprint, fingerprint_scheme, operation_name,
    core_project_ref, provider_version, provider_digest,
)
```

写路径必须把已通过 `StateStore.claim(request)` 的请求和当前 `PluginContext` 投影成该身份。证明唯一键是 `(plugin_id, store_id, request_id)`；scheme、fingerprint、operation、Core project 和 provider pins 均需精确匹配。相同身份已有证明时 Repository 返回首次提交的快照或过期终态，不再次修改 Task；相同键身份不匹配时抛 `TaskRequestConflict`。

`CommitOutcome.data` 与成功插件响应一致：正常返回 `{task, changed, operation_status: "committed", result_state: "available"}`；过期返回 `{operation_status: "committed", result_state: "expired", code: "task.result_expired", task_id, original_request_id}`，不含 `task` 或 `changed`。`CommitOutcome.event_intents` 与证明行一致。create/transition 的事件由 Repository 根据实际变化产生；无变化禁止生成事件。

`lookup_commit` 为只读恢复查询，返回含完整身份 pins、原快照（可能已清理）、截止时间、原 event intents、恢复标记的 `TaskCommitProof`。`mark_events_recorded` 在 Core `reconcile_confirmed` 成功后调用，只把 `pending` 推进到 `ledger_recorded`；无事件时保持 `none`，已记录调用保持幂等。

## Domain

```python
task_from_fields(store_id, fields, *, now, task_id=None) -> Task
Task.to_dict() -> dict[str, JSON]
TaskFilters.from_fields(fields) -> TaskFilters
```

`task_from_fields` 负责 create 字段白名单、缺省值、trim、标签去重、严格时区 RFC3339 归一化和 UTC 六位小数时间。`task_id=None` 时才生成 `tsk_` + UUID4 hex；预览不得调用。`Task.to_dict()` 不含内部 `store_id`。Repository 的分页游标绑定 store 与归一化后的完整 filter，排序键固定为 `(created_at DESC, id DESC)`。

## 存储错误

- `TaskStoreIdentityError`：同一 DB 被请求绑定到不同 store。
- `TaskSchemaVersionError`：数据库 schema 缺失、不匹配或高于实现版本。
- `TaskNotFound`、`TaskDeletedError`、`TaskVersionConflict`：领域操作不允许；版本冲突提供 expected/current version。
- `TaskRequestConflict`：相同提交证明主键被不同原请求身份复用。
- `TaskValidationError`：业务字段、过滤器或游标不符合契约。

这些异常由 runner 映射为契约错误；Repository 不直接调用 Core SDK，也不创建第二份 claim/receipt ledger。

## Schema v2 冻结

`TaskStore._create_schema_v2()` 是可执行 DDL 唯一来源。新数据库设置 `PRAGMA user_version=2` 并插入唯一的 `schema_meta(singleton=1, schema_version=2, store_id, created_at)` 行；DDL 和首次业务写入在同一事务中。每次读写都同时校验 SQLite 版本、元数据版本和绑定的 `store_id`。版本 0 只有在完全空库时才可初始化；高版本、部分 schema 或 store 不匹配均 fail closed。

以下表、列、键与索引在 v2 冻结：

| 表 | 列和键 | 约束 |
|---|---|---|
| `schema_meta` | `singleton INTEGER PRIMARY KEY`, `schema_version INTEGER`, `store_id TEXT`, `created_at TEXT` | `singleton=1`、`schema_version=2`；一库一个 Task store。 |
| `tasks` | `store_id,id,title,notes,status,priority,project_ref,due_at,estimate_minutes,source_ref,created_at,updated_at,completed_at,deleted_at,archived_at,version`; 主键 `(store_id,id)`、`UNIQUE(id)` | status/priority 枚举；version > 0；`tsk_` + 32 位小写 hex；title/ref/tag trim 后长度约束；持久时间为 27 字符 UTC；completed/deleted 时间与状态一致；estimate 为 null 或 1–10080。 |
| `task_tags` | `store_id,task_id,tag,position`; 主键 `(store_id,task_id,tag)`、唯一 `(store_id,task_id,position)` | 复合外键指向 tasks；tag trim 后 1–64 字符；position 0–31。Task 业务删除是软删除，不删除标签。 |
| `task_request_commits` | `plugin_id,store_id,request_id,request_fingerprint,fingerprint_scheme,operation_name,core_project_ref,provider_version,provider_digest,task_id,operation_status,changed,error_code,result_body,committed_at,result_body_expires_at,event_intents,event_recovery_state`; 主键 `(plugin_id,store_id,request_id)` | 复合外键指向 Task；scheme 固定 `jcs-operation-v1`；只接受七种 Task 写能力和 `operation_status='committed'`；changed 为 0/1；恢复状态为 `none/pending/ledger_recorded`；无事件当且仅当事件 JSON 为 `[]`。 |

索引冻结为 `tasks_created_order(store_id,created_at DESC,id DESC)`、`tasks_status_order(store_id,status,created_at DESC,id DESC)`、`tasks_project_order(store_id,project_ref,created_at DESC,id DESC)`、`tasks_priority_order(store_id,priority,created_at DESC,id DESC)`、`tasks_due_order(store_id,due_at,created_at DESC,id DESC)`、`tasks_updated_order(store_id,updated_at,created_at DESC,id DESC)`、`task_tags_by_value(store_id,tag,task_id)`，以及部分索引 `task_commits_expiry(store_id,result_body_expires_at) WHERE result_body IS NOT NULL`。

`task_request_commits` 仅保存提交证明与恢复元数据，不是第二份 Core operation ledger。180 天到期后只把 `result_body` 设为 SQL `NULL`，保留证明行、Task、标签、请求身份、provider pins 和事件恢复信息。

旧 schema v1 可只读访问，无迁移副作用。首次写入在 BEGIN IMMEDIATE 后备份，再事务迁移到 v2。备份文件与数据库同目录，命名 `<db>.schema-v1-<uuid>.bak`；失败回滚保留原库。原提交 proofs 逐字段复制，结果正文不增加 archived_at。TaskFilters 新增 include_archived=False，默认状态含 cancelled，游标绑定包含两个可见性标志。详见 [0.2.0 扩展规格](TASK-0.2.0.md)。
