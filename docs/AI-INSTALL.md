# AI 宿主接入 / AI host integration

先阅读 INSTALL.md，安装固定 Core 及所选插件，显式完成版本、资源和权限配置。
AI 不能把安装视为用户授权业务写入。

## Codex / WorkBuddy 原生入口

Core 已部署、verify、activate 后执行：

```powershell
$coreHome = Join-Path $env:USERPROFILE '.yushuos'
yushuos --config-root $coreHome install-host --host codex --host-config-root (Join-Path $env:USERPROFILE '.codex')
yushuos --config-root $coreHome install-host --host workbuddy --host-config-root (Join-Path $env:USERPROFILE '.workbuddy')
```

```bash
core_home="$HOME/.yushuos"
yushuos --config-root "$core_home" install-host --host codex --host-config-root "$HOME/.codex"
yushuos --config-root "$core_home" install-host --host workbuddy --host-config-root "$HOME/.workbuddy"
```

这两个命令安装 Core 托管 Skill/Rule 调用说明，不安装业务插件，不开通 App，不给权限。
卸载对应入口使用相同参数的 uninstall-host；仅删除未被用户改动的托管文件。
插件 SKILL.md 可按宿主规则作为补充说明。其他 AI 没有专用安装器，按通用 CLI 手动接入。

## 通用调用协议

1. 读取 doctor 和 catalog --details。仅选择当前可用能力，核对 inputs、intents、execution_mode、permissions、resource_scopes。
2. 宿主负责自然语言理解，将请求转为 JSON。query 读取，command 变更，target 必须对应已绑定资源。
3. 先运行 invoke --mode preview --file request.json；获准执行时使用 --mode execute --host-mode execute。
4. 解析 stdout 的一个 Result JSON；只有 succeeded 是确定成功，preview 没有业务副作用，unknown 不得换 ID 重新提交。
5. 本地写保留原 request_id 和完整请求，status 查询后 resume 原请求；无 proof 时等待人工核验。
6. host_required 的判断与证据由宿主明确提供。不能捏造引用，不能把 confidence 当科学概率。
7. 跨插件经 Core 查询/调用，显式准备输入，确认目标成功后再标记来源。Workflow 不是事务。
8. App unknown 的恢复遵循 APP-CONNECTORS.md；Core 0.3.1 缺少共享收据时不能通用自动 resume。

Capture 示例（仅虚构数据）：

```json
{"request_id":"demo-capture-001","capability":"capture.create","intent":"command","fields":{"content":"Example","source":"manual"},"target":{"store_id":"personal"}}
```

```bash
yushuos --config-root "$HOME/.yushuos" invoke --file request.json --mode preview
yushuos --config-root "$HOME/.yushuos" invoke --file request.json --mode execute --host-mode execute
yushuos --config-root "$HOME/.yushuos" status --request-id demo-capture-001
yushuos --config-root "$HOME/.yushuos" resume --file request.json --host-mode execute
```

上述 execute 仅在操作者已授予 capture.write 且任务请求被授权时使用。
不得读取另一插件 SQLite，不绕过 Core 直接运行 adapter，不把凭据传进模型提示或版本库。

## English

Install the pinned Core and selected ZIPs; configure versions, resources and grants explicitly.
Use Core install-host/uninstall-host for Codex and WorkBuddy. Other hosts use the same CLI/JSON flow manually.
Discover actual schemas in catalog --details; the host handles language and host_required judgement.
Preview first, execute authorized actions, preserve request IDs, and recover local commits from proofs.
Cross-plugin steps are explicit and do not form a transaction. App unknown writes require the private receipt verification documented in APP-CONNECTORS.md, never blind retry.
