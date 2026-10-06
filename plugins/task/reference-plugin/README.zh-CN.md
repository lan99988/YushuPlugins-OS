# 业务插件参考骨架

本目录说明 YushuOS 业务插件的通用起步方式。可运行的 Task 0.2.0 插件仍是参考实现；这里不复制它的领域 Schema，也不复制 Core 台账代码。

## 兼容版本

目标版本为 Core 0.3.1（`yushuos-core >=0.3.1,<0.4`）、Python 3.11 或更高版本、Contract v3 和 `json-stdio-v2` runner。可恢复的本地写入使用 `operation_support: local_commit_v1`。

开始前阅读 [`../docs/BUSINESS-PLUGIN-AUTHOR-GUIDE.zh-CN.md`](../docs/BUSINESS-PLUGIN-AUTHOR-GUIDE.zh-CN.md)。Task 的业务行为以 [`../docs/SPEC-SOURCE.md`](../docs/SPEC-SOURCE.md) 为准，可运行的权威示例在 [`contracts.py`](../src/yushuos_task/contracts.py)、[`plugin.py`](../src/yushuos_task/plugin.py)、[`domain.py`](../src/yushuos_task/domain.py) 和 [`storage.py`](../src/yushuos_task/storage.py)。复用适用于自己领域的流程与不变量；复制 Task 表结构前先说明它为何适合新领域。

## 建议目录

```text
plugin/
  plugin.yaml             # 从权威合同生成或校验
  run.py                  # 单条 JSON stdio 信封入口
  plugin.lock.json        # 由 Core 0.3.1 lock-plugin 生成
  src/
    <package>/            # 经校验并由 Core 安装、运行的包
      contracts.py        # capability Schema 和 fixtures
      domain.py            # 纯验证与状态迁移
      storage.py           # 领域数据库、提交证明、快照、migration
      plugin.py            # Core 信封适配器
tests/
  fixtures/
  unit/
  integration/
```

Core 独占操作台账。业务插件只拥有领域数据库与本地提交证明；不得再建一套 claim/receipt/outbox/event 台账。将打包依赖和业务数据放在 `plugin/` 发布目录之外。

## 本地写入协议

每个 `internal_write` capability 按以下顺序执行：

1. 由 Core 校验当前 capability、权限、资源范围、provider pin 和请求 Schema。
2. 使用 `PluginContext.from_envelope()` 校验信封，并构造唯一的 SDK `Request`。
3. 使用 Core 已绑定的 `fingerprint_for_scheme(request, scheme)` profile，并在领域写入前调用 `StateStore.claim(request)`。
4. 在一个领域数据库事务内提交业务事实、幂等证明、原始结果快照和安全事件意图。
5. 对已知的正常结果调用 `StateStore.record_with_events(result, context, emissions)`；领域提交已经存在但 Core 收据/outbox 无法确认时返回 `unknown`。
6. 结果通过输出合同后再返回。

`local_commit_v1` 的三个 runner action 分工明确：

| action | 合同 |
| --- | --- |
| `invoke` | 首次获准写入。claim 原 request ID，再原子写入领域事实和证明。 |
| `recover` | 只由显式 `resume --host-mode execute` 启动。读取原提交证明；存在时调用 `StateStore.reconcile_confirmed(request, result, context, emissions)`。不存在时返回 unknown，不能重做领域写入。 |
| `replay` | Core 重新校验当前权限、资源、能力和原 provider 后才调用。读取原结果，不写业务数据、marker 或清理过期正文。 |

Core 在 operation context 中绑定原 provider ID/version/digest、request intent、project 和 trace。recover/replay 必须复用该身份。其他 request、operation、store、project、provider version 或 digest 的证明都不能复用。

版本化请求指纹 `jcs-operation-v1` 对 `{capability, fields, target}` 的 RFC 8785 JCS 求哈希；`intent` 和顶层 `project_ref` 由 Core 单独绑定。`StateStore.claim(request)` 从 Core 绑定读取方案；不得根据摘要猜算法或静默回退到 legacy 指纹。

## 收据、事件与私有正文

Core 收据只保存最小操作状态和请求身份；Core outbox 事件只携带已声明事件类型和安全资源引用。业务字段、结果快照与恢复证明留在领域数据库。事件意图只包含声明的类型和安全引用。

`changed=false` 不创建事件意图。查询和预览不 claim、不创建业务数据库、不修改 version/timestamp、不清理过期结果正文，也不发事件。Core 会在启动 runner 前处理本地写入预览。

Task 结果快照在提交 180 天后逻辑过期。查询、预览、replay 和 recover 均不得返回过期正文。下一次真实获准写入才会物理清除过期快照；若此后没有写入，过期字节仍可能留在数据库文件中。过期结果仍报告为 `succeeded`，返回 `operation_status: committed`、`result_state: expired`、`code: task.result_expired`、`task_id` 和 `original_request_id`，省略 `task` 与 `changed`。

Task event recovery marker 是私有同步元数据，不是业务结果的权威来源。若进程在 Core 收据/outbox 提交后、更新该 marker 前停止，marker 可能暂留 `pending`；Core 台账才是收据和事件的权威来源。保持 replay 只读，不在 replay 中 purge 或修复 marker。

## 合同和验证清单

- 只声明已经实现的 capability、明确权限、资源范围和事件类型。
- JSON nullable 类型必须明确，例如 `type: [string, "null"]`；YAML 空值不是类型名称。
- update 使用字段白名单；每个修改命令都要求 `expected_version`。
- normal、preview 和 expired 结果形状要能区分；过期输出不得伪造缺失的业务数据。
- 测试请求身份冲突、no-op、并发版本冲突、provider drift、权限撤销、资源越界、各崩溃窗口和 Core 台账隐私。
- 在隔离临时配置中通过真实 Core 0.3.1 runner 验证，并在发布报告中保留准确命令与结果。

Core API 签名与 runner 语义以 `yushuos-core 0.3.1/docs/en/PLUGINS.md` 和 `yushuos-core 0.3.1/docs/zh-CN/PLUGINS.md` 为准；Core 升级后重新核验。
