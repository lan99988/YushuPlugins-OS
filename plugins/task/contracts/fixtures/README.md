# Contract v3 fixtures

通用 fixtures 固定 Core `json-stdio-v2` 调用 envelope、标准结果和 local-commit context 的协议形状。它们使用虚构的 `fixture.*` ID，不承载真实路径。

`invoke-envelope.json` 对应普通 v3 invoke envelope；`invoke-result.json` 是与请求 ID 对应的标准 succeeded result；`local-commit-context.json` 展示 Core 0.3.1 `operation_support: local_commit_v1` 绑定的 context schema_version 2。它不代表任何业务数据已提交。

`task-create-fields.json`、`task-write-result-available.json` 和 `task-write-result-expired.json` 固定 Task Contract v3 的业务输入与两种写结果分支；测试会使用 inline manifest schema 和分支 validator 验证它们。它们不替代 Core 0.3.1 runner 集成测试。

Core 0.3.1 未进入实际 pinned Core CI 前，通用协议文件只作为协议样例，不能被报告为已通过集成验收。
