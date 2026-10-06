# 实施计划（Draft）

1. 冻结20个插件的 manifest/capability/event/resource contract，验证唯一性与能力库存。
2. 实现 Core 0.3.1 json-stdio-v2 host adapter：严格 Result、schema validation、权限上下文及稳定错误。
3. 为本地插件接入 local_commit_v1 与 context schema 2；在每个业务 SQLite txn 同步存 commit proof，并实现 invoke/replay/recover。
4. 按 provider 实现 Feishu/IMA 能力和真实回执；不得将外部服务写入伪装为本地 ACID。
5. 对每插件逐项验证字段、转换、权限、scope、分页、重复请求、冲突、提交恢复和失败回滚。

依赖次序：合同→Core host→本地存储及 proof→外部 provider→集成验收。完成条件是规格与实现一致、错误/未知结果诚实表达、私有数据 scope 不串库、inventory测试通过。当前阶段只冻结合同，不声明插件运行时或端到端验收完成。

