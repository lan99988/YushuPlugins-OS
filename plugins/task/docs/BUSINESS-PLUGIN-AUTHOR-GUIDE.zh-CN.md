# Business Plugin Author Guide

本文是给后续 AI 实施 YushuOS 业务插件的操作规程。开始任何业务插件改动前先阅读本文和 [`IMPLEMENTATION-READY.zh-CN.md`](IMPLEMENTATION-READY.zh-CN.md)；若任务涉及 Task 0.1.0 的业务行为，再逐条阅读 [`SPEC-SOURCE.md`](SPEC-SOURCE.md)。完成条件必须能从 diff、可执行 fixtures、测试输出或安装产物中核对。

## 实施步骤

### 1. 先定领域边界

读取批准的领域规格和 Core Contract。把领域事实、能力、状态、错误、权限、引用、事件、数据保留期限和不做事项列在一处。把 Core 的编排、权限门禁、claim、receipt、outbox、workflow、事件总线和调度视为已有公共设施。把对外系统调用单独标为外部写入，并说明它如何读回核验。

**完成条件：** 产品能力没有隐含地跨入其他领域；每一条写路径的事实所有者、权限和失败后读取方法都有明确答案；Core 顶层 `project_ref` 与领域业务 `project_ref` 没有混用。

### 2. 冻结请求/结果合同

先冻结 capability id、intent/effect、inputs/outputs、execution mode、permissions、resource scopes、event declaration、error codes、preview shape、result shape 与 request identity。输入 Schema 是唯一权威来源；manifest、验证 fixtures 和运行时模型由它生成或校验，不维护两套手写副本。

Contract v3 使用 `json-stdio-v2`。按需调用 `PluginContext.from_envelope()` 校验 Core 绑定上下文。对 `local_commit_v1`，按冻结 Core API 使用版本化 `fingerprint_for_scheme`、claim、operation context 与 `reconcile_confirmed`；旧插件的 legacy fingerprint 不得被新算法重算。

将每个公共合同边界做成普通 JSON fixtures：合法请求/结果、拒绝样例、事件引用、预览、no-op 和结果过期。Fixture 只保存测试数据，不存真实账号、路径、token、业务私密正文或时间相关的预期值。

**完成条件：** 合同中的每个字段都有 owner、是否可空、默认值、范围和拒绝行为；Core 可消费的 manifest 能从权威 schema 校验；没有未声明能力或 event。

### 3. 冻结领域存储

明确哪些数据是业务事实，哪些是幂等证明、历史结果快照与恢复元数据。为所有表写清主键、外键、检查约束、索引、事务边界、迁移策略、未知新版本处理、删除语义和留存期。一个 Storage owner 独占公共 migration/schema 的修改，其余工作流提交变更请求。

Task 的 v1 schema 与字段、索引和约束以 [`IMPLEMENTATION-READY.zh-CN.md`](IMPLEMENTATION-READY.zh-CN.md) 为准。后续领域插件可有不同事实模型；复制 Task 的表结构前必须说明它符合该领域的业务语义。

**完成条件：** 两进程对同一版本写入有确定结果；migration 可在临时库中升级、失败回滚；未知 schema 版本 fail closed；没有第二个 operation ledger。

### 4. 先跑通 Stage 4 黄金链路

先只实现一条代表性的成功写能力。顺序固定为：Core gates → 原 request ID claim → 单一领域库事务提交事实与提交证明 → Core ledger 中 receipt/outbox → 本地恢复标记 → 返回结果。用真实 Core runner 执行，而不只直接调用领域函数。

对 `local_commit_v1` 必须分别执行 `invoke`、只读 `replay` 和显式 `recover`。recover 首先核对原 provider/version/digest、可信 context、请求身份和私有 commit proof；提交证明存在时只补交原结果；证明不存在时保持 unknown 和锁，等待 Host 核验原进程已停止。人工核验记录或 abandoned 决策不能被插件覆盖。

**完成条件：** 一次端到端执行从原 request ID 得到 Task 事实、原结果 snapshot、Core receipt、outbox 和 event；进程崩溃后只补交，不重复执行业务；原请求重放得到首次结果快照。通过此门槛前停止扩展其他 capability。

### 5. 扩展其他能力并保留 no-op 语义

同一领域写入模式通过后，再添加其余命令和查询。所有修改命令采用显式乐观版本；先比 expected_version，再判断 no-op。状态迁移、可变字段白名单、已删除事实行为和时间语义全部按合同实现。查询与 preview 使用只读路径，且不能 claim 或触发写入副作用。

**完成条件：** 每个写能力同时覆盖成功、旧版本冲突、无变化、权限拒绝和目标资源不匹配；`changed=false` 没有数据时间/版本变化，也没有 event intent。查询边界有包含/排除验证，cursor 只能编码固定排序位置及过滤绑定。

### 6. 验证恢复窗口、权限和数据最小化

对至少四个提交时点做进程故障注入：业务 commit 前、业务 commit 后/Core ledger 前、Core ledger 提交后/响应前、事件导入后/imported marker 前。加入 provider pin 不符、恢复没有证明、人工冲突裁决、正文到期、权限撤销、store 越界、并发冲突与数据库未知版本。

在 `operations.sqlite3`、outbox、自动化历史、日志、异常消息和测试工件中搜索业务正文。只允许 Core 持有请求身份、操作结论、provider provenance 和安全资源引用；业务正文留在领域库。测试都使用隔离临时目录，不使用真实外部账号。

**完成条件：** 每个故障窗口有稳定预期结果；无提交证明的业务不被盲目重放；失败不会泄露字段正文；无权限时无法通过直接 invoke、workflow 或恢复绕过门禁。

### 7. 锁包、运行 CLI 并核对卸载

业务包放入仓库独立的 `plugin/` 发布目录；在该目录运行 Core `lock-plugin`，再打 ZIP。不要把 `.git`、测试、临时数据库、wheel、开发依赖或本机路径放入插件目录。Core 0.3.1 `install-plugin` 接受普通目录，因此 ZIP 安装前先做路径安全校验并解包，再把该目录交给 CLI。

当前 Core 没有 `uninstall-plugin` 命令。卸载流程必须先在 Core 配置中停用 plugin，再只删除经 Core 布局解析的精确 `plugins/<id>/<version>` 代码目录；保留 plugin-data 与共享 ledger。隔离验收要确认 catalog 中不再可执行，并检查 `doctor`。不得向用户宣称存在未实现的卸载 CLI。

**完成条件：** ZIP 可被审阅、锁文件校验通过、三 OS 能启动实际 runner；安装版本不可覆盖；隔离卸载仅影响精确插件代码版本，不触碰用户数据与 Core 状态。

### 8. 交付有证据的文档

文档命令只写在相应 CLI `--help`、实现或真实命令核验之后。README 说明用途、权限和安装路径；能力指南说明每个 capability 的参数与结果；Business Plugin Author Guide 说明跨插件合同。最终验证报告逐项记录 commit、OS/Python、实际命令、结果和遗留项；未执行项目写“未运行”。

**完成条件：** 文档步骤在干净临时配置根中实际完成；打包内容与锁定清单一致；报告能追溯每个通过结论的运行证据。

## 固定约束

- 公共 manifest/schema/migration 各有且仅有一名 owner；其他工作流通过变更请求集成。
- Core 全局 request ID 唯一；按 Core SDK fingerprint scheme 做身份比对，不自造 canonical JSON。
- Core receipt/outbox 只保留最小收据和资源引用；Task 正文不离开领域存储。
- no-op 不改事实、不增版本、不发事件；已删除事实遵守合同终态。
- Core 与领域库分开提交，恢复靠提交证明和 Core reconciliation，不能声称跨数据库 ACID。
- 未通过黄金链路前不并行扩业务功能；schema 冻结后不能直接修改公共结构。
- 重复性 fixture、格式迁移和机械文档工作优先由 GPT-6-luna 最高推理档完成；公共协议、故障恢复和最终集成仍由集成负责人审查。
- 所有执行测试使用隔离临时数据；没有实际运行的命令或验收不得写为通过。
