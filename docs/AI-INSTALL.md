# AI 安装和调用指南（Draft）

安装器只读取受信 manifest v3，检查 Core 0.3.1 与协议 `json-stdio-v2`，再验证 provider id、能力唯一性和合同文件存在。不得从合同读取或保存任何凭据；用户秘密须经宿主 secret store 单独注入，日志只能记录 secret name。

宿主先读取能力 input schema 并进行本地校验，再通过统一 Core 执行入口调用。写操作请求须绑定 invocation id、私有 store_id、权限决定及 context schema 2；本地事务的 action 使用 invoke/replay/recover。调用方只有在收到真实 committed proof 后才能向用户报告写入完成。未知结果必须展示待核对状态并通过 recover 查询；不能重放不幂等的 provider 操作。

严格按 capability 的字段传参，不补猜截止日期、金额、健康测量值或缺失证据。错误码和 retryable 决定下一步：invalid_input 修正参数，permission_denied 请求宿主授权，version_conflict 重新读取，outcome_unknown 核对提交 proof。列表传显式 store scope 和分页游标。App能力必须使用 `feishu.*`/`ima.*` namespaced 名称。

Reviewer checklist：manifest v3、Core版本、JSON Schema、权限、事务标记、资源引用和真实 provider 回执均匹配本仓库冻结合同；所有未经证实的行为保持未完成状态。

