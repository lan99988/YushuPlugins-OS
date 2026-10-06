# Plugin Standard（Draft，待与运行代码接合）

## 版本与传输

目标 Core `0.3.1`；本文件为运行代码接合前草案，不能作为已验证冻结规格。适用于 Core `0.3.1`、manifest v3、`json-stdio-v2`。每个插件由 `contracts/<slug>/capabilities.json` 声明唯一 provider id `yushuos.<slug>`，并同时提供 `events.json` 与 `resources.json`。UTF-8 JSONL 一行一请求、一行一响应；stdout 只允许协议帧，诊断写 stderr。拒绝未知 capability、未知字段、缺字段、非法类型和超限值。

响应严格为 `Result`：`{status,request_id,message,resource,data,error}`，且只允许这六个键。status 为 `ok|error|partial`；成功时 error=null，错误时包含稳定 code、retryable、details；无主资源时 resource=null。不得以成功状态返回空壳或未提交写入。

## 描述符和数据

能力描述符必须声明 `name,input,output,effect,intents,execution_mode,permissions,transaction,errors`。JSON Schema 属性必须具体、`additionalProperties:false`；禁止 `request:{}` 和无约束业务对象。实体统一 `{id,kind,version,created_at,updated_at,archived,fields}`，领域字段只在 fields 内。每次读写显式提供私有 `store_id`；引用统一 `{provider,kind,id,store_id}`。秘密不得进入合同、事件、日志。

本地写入 data 为 `{entity,changed,operation_status:"committed",result_state:"available"}`；单实体读取为 `{entity}`；列表为 `{items,next_cursor}`。列表 limit 默认50、范围1..100，cursor不透明。计算/提案返回 typed result，不能以空对象冒充成功。

## 本地提交

本地写使用 `local_commit_v1`，上下文 schema 2，action 为 `invoke|replay|recover`。插件在同一个业务 SQLite 事务中提交领域变更和 commit proof；proof 固定含 operation id、请求摘要、资源引用、结果摘要、提交状态。提交前异常整体回滚。SDK 只根据已知 proof 调和；不得仅凭重试推断提交。多个文件之间不保证 ACID。replay 幂等；recover 依据持久 proof 定案，否则返回 `outcome_unknown`，不得盲目重做。

## Provider 与能力

飞书/IMA 写走 provider adapter，使用 provider 幂等键和真实回执；超时且无确定回执返回 `outcome_unknown`。能力名必须分别以 `feishu.`、`ima.` 开头，禁止复用旧 task/knowledge capability 名称。Task 依导入 Task v1.0；默认待处理，不猜截止日期，完成不删除，删除必须显式调用，飞书原生 Task 是权威来源。

Finance 金额以十进制字符串输入并以 Decimal 精确计算；MVP 不含 OCR 和投资估值。Capture.route 只记录宿主分类；mark_processed 必须有 ref 或 ignored reason。Review 为独立结构化记录，解释由 host 提供。Personal model/cognition 评价是 `host_required`，只用显式证据，概率必须校准，严禁编造证据。CRUD 独立运行。Planner 不调用 AI，先满足硬约束，再按 priority urgent/high/normal/low、due_at、稳定 id 排序，并支持 energy config。

稳定错误码：`invalid_input`、`not_found`、`version_conflict`、`permission_denied`、`storage_error`、`provider_error`、`outcome_unknown`、`unsupported_capability`。

