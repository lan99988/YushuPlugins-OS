# YushuOS Task Plugin 0.1.0：实施基线

本文将已批准的产品与工程规格固化为实施顺序、接口门槛和交付检查。完整行为规则以同目录的 [`SPEC-SOURCE.md`](SPEC-SOURCE.md) 为准；本文件不得被实现方便性静默改写。发生冲突时，先由集成负责人更新规格与 fixtures，再修改实现。

## 目标与边界

交付独立安装的 `yushuos.task` 0.1.0，Python 包名为 `yushuos_task`。它同时是 Core 0.3.x 的 Contract v3 首个完整业务插件验证，以及后续业务插件的参考实现。插件使用 Contract v3、`json-stdio-v2`，Task 依赖 `yushuos-core>=0.3.1,<0.4`，Python 最低版本为 3.11；领域持久化使用 SQLite 标准库。

Task 保存任务事实并提供创建、读取、列表、修改、完成、重开和软删除。Task 不负责自然语言理解、日程安排、提醒、外部 App、AI 决策、GUI、子任务、DAG、云同步或旧数据导入。一个 Core root 对应一个任务库；Task 的业务 `fields.project_ref` 仅作分类，不能用作权限边界。Core 顶层 request/context 的 `project_ref` 始终是运行项目身份。

Task 私有 SQLite 只保存领域事实、原写请求结果快照和恢复证明。Core 保持唯一的请求 claim、共享锁、receipt、outbox、事件总线、workflow、scheduler 和人工核验记录。Task 不增加第二份 operation ledger，也不声称 Task SQLite 与 Core ledger 跨文件原子提交。请求正文不得进入 Core receipt、outbox、日志或 workflow 检查点。

## 冻结的业务接口

七个 capability 均为 `standalone`。插件级 `permissions` 为空，权限逐能力声明：

| Capability | Intent / Effect | Required permissions |
|---|---|---|
| `task.create` | `command / internal_write` | `task.write` |
| `task.get`, `task.list` | `query / read_only` | `task.read` |
| `task.update`, `task.complete`, `task.reopen` | `command / internal_write` | `task.read`, `task.write` |
| `task.delete` | `command / internal_write` | `task.read`, `task.delete` |

每个 request 都必须有 `target.store_id`，manifest 声明 `resource_scopes: {store_id: task_store}`，由 Core 的 resource binding 校验。访问已有任务时，同时传 `fields.task_id` 与 `target.task_id`，两者必须一致；list 没有 task_id。共享 ledger 的 `request_id` 仍为全局身份：不能因 plugin 或 store 不同而复用。Task 私有 `task_request_commits` 用 `(plugin_id, store_id, request_id)` 作复合主键，provider version/digest 是恢复 pin，不进入主键。

Task 的字段、默认值、合法范围、状态、版本冲突、no-op、查询、游标、事件以及 180 天结果保留规则详见 [`SPEC-SOURCE.md`](SPEC-SOURCE.md)。实施时不可自行放宽：

- 状态为 `open / completed / deleted`；`deleted` 是软删除终态。修改已有 Task 的四项命令都必须携带 `expected_version`。
- update 只接受 `title / notes / priority / project_ref / source_ref / due_at / estimate_minutes / tags`；缺省字段表示不变，可空字段显式 `null` 表示清空。未知字段和系统字段都应拒绝。
- 先校验并发版本，再判定业务 no-op。版本匹配且无变化时返回 `changed=false`，不得增加 version/updated_at，且不得生成 event intent。
- 同 request 重放返回首次提交的结果快照，不以当前 Task 拼装旧结果。相同 request_id、不同 fingerprint 拒绝；请求 fingerprint 使用 Core SDK 的 JCS 方案，且不得另写 Task 私有 canonicalizer。
- 正式事件仅为 `task.created / task.updated / task.completed / task.reopened / task.deleted`，payload 只含 `resource_refs.task_id`，不得含 Task 正文。
- `get/list/preview` 不 claim、不创建 Task DB、不清理过期结果、不产生业务事件。preview 不生成正式 Task ID。

## Core 0.3.1 对接合同

Core 0.3.1 是 Task 的前置依赖。当前批准的 SDK 入口为：

```python
from yushuos_sdk.canonical import fingerprint_for_scheme

fingerprint_for_scheme(request, scheme)  # scheme: legacy-v1 | jcs-operation-v1
StateStore.claim(request)
StateStore.operation_context(request_id)
StateStore.reconcile_confirmed(request, result, context, emissions)
```

`fingerprint_for_scheme` 对 `jcs-operation-v1` 按 RFC 8785/JCS UTF-8 bytes 对通过 Schema 校验、尚未经过 Task trim/default/时间归一化的 `{capability, fields, target}` 计算 SHA-256。运行元数据和 request_id 不进入 fingerprint；intent、Core project、provider 身份另外固定核验。Core safe binding 与 `operation_context(request_id)` 另外保存原 intent。旧 `Request.fingerprint()` 继续为 `legacy-v1`。claim 从 Core 的 operation-context binding 中读取 scheme，不能自行猜版本。

Core v3 manifest 允许声明 `operation_support: local_commit_v1`；该协议供 v3 的 `read_only` 与 `internal_write` 使用，拒绝 `external_write`。普通 legacy v3 context 保持 schema_version 1；local-commit context 使用 schema_version 2，并带 Core 绑定的 `intent`、`operation_support` 与 `fingerprint_scheme`。`StateStore.operation_context()` 仅返回安全元数据，不含路径、resources 或业务正文。

runner 使用既有 `json-stdio-v2` envelope，并处理 `action=invoke / replay / recover` 三条路径。replay 只读 Task 提交证明；recover 只恢复已提交操作，只能补交收据和事件，不能再次执行业务写入。recover 使用原 provider version/digest、run/root/causation/depth；恢复前仍通过 Core 当前权限与资源门禁。已提交结果通过 `StateStore.reconcile_confirmed(request, result, context, emissions)` 原子追加核验 receipt 与 outbox，保留原未知收据和已有冲突人工裁决。

## Task DB schema v1 冻结门槛

并行写实现前，由 Storage owner 冻结表、约束、索引、迁移、Repository 接口和 schema fixtures。其他工作流以变更请求提出修改，不直接改共享 schema。v1 仅有四张表：

| 表 | 固定职责 |
|---|---|
| `tasks` | Task 全部业务字段及内部 `store_id`；Task ID 唯一，version 大于零，status 有限枚举。 |
| `task_tags` | `task_id / tag / position`；外键、`(task_id, tag)` 唯一，保持首次录入顺序。 |
| `task_request_commits` | `(plugin_id, store_id, request_id)` 复合主键；指纹方案、fingerprint、operation、Core project、provider pins、task_id、原操作状态、changed/error code、结果快照、提交时间、正文到期时间、event intents 和恢复状态。 |
| `schema_meta` | 当前 schema_version 与绑定的 store_id。 |

索引覆盖 `created_at DESC, id DESC` 排序游标以及 status、project、due、updated、priority、tag、过期结果清理。SQLite 事务通过 `BEGIN IMMEDIATE` 执行并发版本检查、Task 变更、标签写入和 commit proof；忙等待有界。迁移必须可回滚并保留旧库恢复能力；遇到未知的新 schema_version 时拒绝写入。`event_recovery_state` 只允许 `none`（无事件意图）、`pending`（业务已提交，Core outbox 尚未确认）和 `ledger_recorded`（receipt/outbox 已持久化）；它不表示消费者已收到事件。

## 写入、恢复与 Stage 4 门槛

标准写路径固定为：

```text
Core gate
→ SDK claim 原请求
→ 单个 Task SQLite 事务：事实变更 + commit proof + 原结果快照 + event intents
→ Core ledger 事务：最小 receipt + outbox
→ 更新 Task 本地 event_recovery_state
→ 返回原结果
```

Stage 4 的黄金链路必须完整演示：

```text
task.create → claim → Task commit → commit proof → receipt/outbox
→ Core event → 进程崩溃后的显式恢复 → 原快照 replay
```

**Stage 4 未通过前，不扩展 update/complete/reopen/delete/list 等其他业务能力。**验收至少故障注入“Task commit 前”“Task commit 后、ledger 前”“ledger 后、响应前”三个窗口；事件导入后、标记 imported 前也要验证稳定 event ID 去重。无 commit proof 的请求保持 unknown 和锁，Host 确认原进程停止后才允许按原身份 `history resolve`；provider pin 不符或已存在冲突人工裁决时停止恢复；`abandoned` 保留锁。

结果正文自首次提交起保留 180 天。到期后的原业务操作仍是 succeeded/committed，但响应 `data` 只能包含 `operation_status=committed`、`result_state=expired`、`code=task.result_expired`、`task_id`、`original_request_id`；不能伪造 task 或 changed。Core 仍可补交原 receipt/outbox。只在下一次真实且授权的写调用清除已过期快照；查询、preview、replay 不触发清理。请求身份、fingerprint、原状态、task_id、changed 和事件恢复元数据长期保留。

## 八条工作流与文件所有权

| 工作流 | Owner / 独占写入范围 | 前置依赖 | 完成条件 |
|---|---|---|---|
| A — Core 0.3.1 | Core owner；manifest、SDK、runner、receipt/outbox、provenance | Stage 0 baseline | v2 与未启用新协议的 v3 保持兼容；新协议门禁与核验测试通过。 |
| B — Contract/Schema | Contract owner；权威 Schema、Task manifest、capability/event/permission fixtures | A 的 SDK/manifest 接口冻结 | 七个 capability 的请求、结果、preview、错误及恢复状态有可执行 fixtures。 |
| C — Storage | Storage owner；Task DB migration、索引、Repository、schema fixtures | B 冻结 | 并发版本、提交证明、回滚、标签和正文清理的存储合同冻结。 |
| D — Write Path | Write owner；Task 领域规则与 `plugin/` runner 的 invoke 分支 | A/B/C；先与 F/G 通过 Stage 4 | 黄金链路通过后再实现其余写能力；所有 no-op 无 event。 |
| E — Query | Query owner；get/list、过滤和 keyset cursor | C schema 冻结；集成门槛为 Stage 4 | 删除可见性、过滤边界、无快照承诺及万条数据查询有证据。 |
| F — Recovery | Recovery owner；replay/recover、expired terminal、event marker | A/B/C；Stage 4 与 D 联合 | 四类崩溃窗口、过期、provider mismatch 和 abandoned 均不重做业务。 |
| G — Tests/Release | Integration owner；独立故障注入、权限/并发/隐私、三 OS 构建安装验收 | Stage 0 起持续验证 | 完整 CI 与逐项证据报告；报告仅记录实际运行结果。 |
| H — Docs/Reference | Docs owner；README、双语作者指南、fixtures 使用说明和参考资产 | 契约先冻结；Stage 9 实测后定稿教程 | 示例命令真实运行，参考资产继承合同且不复制 Core 基础设施。 |

manifest、公共 schema、Task migration 及 Task 公共 model 各只有一个指定 owner。改动这些文件先提出变更，再由 owner 更新权威文件及对应 fixtures。文档和 fixture 归 H owner；集成报告归 G owner。重复的机械任务优先用 GPT-6-luna 最高推理档完成，涉及 Core/公共契约/故障恢复语义的审查由集成负责人完成。

## 阶段顺序与发布边界

1. **Stage 0** 冻结 Core `d7e5ce4` baseline；主 Agent 已核验该基线的 138 项测试通过。
2. **Stage 1** 完成 Core 0.3.1 nullable Schema、local-commit protocol、版本化 fingerprint、replay/recover、`reconcile_confirmed` 与安全 provenance。
3. **Stage 2** 冻结 Task Contract v3 manifest、authoritative schemas、permissions、capabilities、events 与 fixtures。
4. **Stage 3** 冻结 Task DB schema v1、migration、索引及 Repository 接口。
5. **Stage 4** 完成 create 黄金链路、Core event、崩溃恢复和原结果 replay；此为扩展能力的硬门槛。
6. **Stage 5–8** 按门槛扩展写操作、Query、恢复矩阵及并发/权限/隐私测试。
7. **Stage 9** 三 OS runner、锁定包 ZIP、解包、Core install-plugin 和隔离环境卸载检查。
8. **Stage 10** 基于真实执行定稿中英 README、插件使用教程、Business Plugin Author Guide 与验证报告，冻结 Reference Business Plugin 0.1。

Task 独立发行 ZIP 只包含 `plugin/` 下的锁定插件文件。Core 的 `lock-plugin` 与 `install-plugin` 目前都接收普通目录；安装流程先安全解包 ZIP，再运行 `yushuos --config-root <root> install-plugin --path <extracted-plugin-dir>`。Core CLI 没有 `uninstall-plugin` 命令。卸载只在隔离配置根中验证：先将 `yushuos.task` 加入 Core `user_disabled`，确认 effective disable，再删除经 Core 安装布局解析出的精确 `<config-root>/plugins/yushuos.task/<version>` 代码目录，保留 plugin data 和共享 ledger，并核对 `catalog`/`doctor` 无插件且无 manifest errors。不得把此验收写成 Core 提供卸载 CLI。

插件通过 Core 的 `lock-plugin` 创建 `plugin.lock.json`，Core 再验证锁文件并把该版本安装到不可变目录。Task 不发布到 PyPI；无外部业务账号、真实外部写入或默认启用的 automation。当前 Core 尚未发布 0.3.1 时，CI 必须从显式 `CORE_SOURCE` 仓库及完整 commit SHA 安装 Core；只有 Core owner 发布候选提交后，才能把实际 SHA 回填到仓库变量。不得使用虚构版本号、未锁定分支或假测试结果。

## 交付和验证报告

最终发布包含 Core 0.3.1 变更、Task 0.1.0 locked ZIP、迁移、契约/事件说明、单元与集成测试、隔离 Demo、双语使用说明、Business Plugin Author Guide 和逐项验证报告。长期资产为 `contracts/fixtures/`（跨业务插件协议 fixtures）、`reference-plugin/`（source/schema 冻结并通过黄金链路后生成的骨架）和本目录的作者指南。

最终报告按验收项逐项填写：运行环境/commit、实际命令、实际通过数、失败/跳过项、ZIP 哈希与真实安装路径、故障注入窗口结果、权限和隐私检查。未执行的项目标记“未运行”，不得写成通过；性能项给出真实测量方法与结果，不用不稳定耗时断言替代正确性。
