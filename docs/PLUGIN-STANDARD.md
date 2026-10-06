# 插件标准 / Plugin standard

Core 固定 0.3.1（sources.lock.json），manifest contract v3，json-stdio-v2。Result 顶层为 status/request_id/message/resource/data/error；状态为 succeeded/failed/unavailable/unknown/preview/needs_clarification。stdout 只输出协议 JSON，诊断写 stderr。

能力声明 inputs/outputs/effect/intents/execution_mode/permissions/resource_scopes。Schema 使用 Core 支持的子集，不依赖 $ref、oneOf、format。读取 query，变更 command；额外输入拒绝。输出计算字段按已声明 Schema 扩展。

通用引用是 {provider,kind,id,store_id}。Core 事件只传 type 和 resource_refs，后者包含允许的标量 URI/id；正文保存在所属插件。project_ref 是分类和请求身份的一部分，不自动授予资源权限。

17 个本地插件：target.store_id 对应 <slug>_store，权限为 <slug>.read/write。实体包含 id/kind/store_id/version/created_at/updated_at/archived/fields。写返回 entity/changed/operation_status/result_state，列表返回 items/next_cursor，默认50最多100，cursor绑定资源和筛选条件。Task 保留独立 task schema 与 task_store 权限。更新要求 expected_version，冲突不覆写。

本地写使用 local_commit_v1、PluginContext schema 2。写摘要采用 Core jcs-operation-v1，读取兼容 legacy-v1。提交身份绑定版本、provider digest、项目、scope、能力、intent及请求摘要；同版本修改代码也不能恢复旧操作。

业务、proof、原结果及事件意图在同一私有 SQLite 文件/connection/transaction 中提交。Core 收据与 outbox 在另一 Core SQLite 事务内确认，两份数据库没有跨库 ACID。recover/replay 只核验原 proof 并补交确认，不重做业务。没有 proof 或外部响应不明时保持 unknown，不换 ID 盲重试。Workflow 不提供跨插件事务或自动补偿。

通用快照180天后返回 committed/expired 元数据，仍保留 proof；通用存储当前没有物理清理任务。Task 依自身策略清理。代码降级不逆向迁移数据库；Task0.1→0.2先备份，切回旧代码不是完整数据回滚。

App schema 1 外部协议独立于 local_commit_v1。命名为 feishu.* / ima.*。private binding 明确账号、资源、能力授权、验收与 grants。receipt 保留原结果；没有确定回执的超时/中断保持 unknown，不能重放写入。代码/mock验收不等于 live 验收。Core 缺失共享 operation receipt 时不会自动 resume App 写，需按各App文档读取原信封的私有收据；没有证明就人工核验。

宿主负责自然语言和 AI 判断。host_required 自动化进入 host_pending；插件校验宿主显式判断与证据，不调用隐藏模型。confidence 是注明来源的判断，不是科学校准概率。Finance 不含 OCR/投资建议，Body 不诊断，Creation.publication 只记录事实，Planner.apply 不代表日历已同步。

协作经 Core 显式读取、准备输入及确认引用；不读取其他插件数据库，不重建业务 scheduler/event bus。质量证据绑定代码树、契约和检查规则，任何必需关卡失败均不能发布。
