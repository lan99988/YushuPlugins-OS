# AI 宿主接入 / AI host integration

先按[安装指南](INSTALL.md)安装固定 Core 和所选插件，再配置版本、资源范围和最小权限。安装插件或宿主入口不会构成用户对业务写入或外部 App 操作的授权。

## Codex 与 WorkBuddy

固定 Core 已 deploy、verify 并 activate 后，使用 Core 自带的 <code>install-host</code> 安装托管 Skill/Rule。PowerShell：

~~~powershell
$CoreHome = Join-Path $HOME '.yushuos'
yushuos --config-root $CoreHome install-host --host codex --host-config-root (Join-Path $HOME '.codex')
yushuos --config-root $CoreHome install-host --host workbuddy --host-config-root (Join-Path $HOME '.workbuddy')
~~~

macOS/Linux：

~~~bash
core_home="$HOME/.yushuos"
yushuos --config-root "$core_home" install-host --host codex --host-config-root "$HOME/.codex"
yushuos --config-root "$core_home" install-host --host workbuddy --host-config-root "$HOME/.workbuddy"
~~~

这两个命令只安装 Core 调用说明入口，不安装业务插件、不连接 App、不授予权限。对应入口可用相同参数运行 <code>uninstall-host</code>；Core 仅删除仍与安装记录匹配、且未被用户修改的托管文件。其他 AI 宿主用通用 CLI/JSON 流程接入；本 Suite 没有其他宿主的自动安装器。

## 请求、预览与执行

1. 先读取 <code>doctor</code> 与 <code>catalog --details</code>，选择 catalog 当前显示 available 的能力，并核对 Schema、intent、execution mode、权限和 resource scopes。
2. 宿主把用户意图转换成契约规定的 JSON。读请求使用 <code>query</code>，变更使用 <code>command</code>；target 必须与已绑定资源完全一致。
3. 写请求先用 <code>invoke --mode preview</code>。只有用户明确批准该次写入且宿主执行策略授权时，才用 <code>invoke --mode execute --host-mode execute</code>。配置中的 grant 不代替逐次授权。
4. 只把 <code>succeeded</code> 当作明确成功。<code>preview</code> 没有提交；<code>unknown</code> 表示结果待核验，不能换新 ID 重试。
5. 对本地插件的未知写入，保留原 request ID、完整原请求及 Core 配置，先查询 <code>status --request-id &lt;id&gt;</code>，再依据 Core 收据与插件 proof 使用 <code>resume --file &lt;原请求.json&gt; --host-mode execute</code>。只有用户明确授权恢复时才执行。
6. <code>host_required</code> 判断与证据由宿主提供。不得捏造引用，也不能把 confidence 描述成科学概率。
7. 跨插件流程通过 Core 显式调用，先确认目标步骤成功，再标记来源；这些步骤不构成事务。

Capture 示例仅使用虚构字段：

~~~json
{
  "request_id": "demo-capture-001",
  "capability": "capture.create",
  "intent": "command",
  "fields": {"content": "Example", "source": "manual"},
  "target": {"store_id": "personal"}
}
~~~

~~~bash
yushuos --config-root "$HOME/.yushuos" invoke --file request.json --mode preview
~~~

获准执行时使用相同文件和 ID，并传入 <code>--mode execute --host-mode execute</code>。写入之后可查询状态；只有状态仍为 unknown 且需要恢复时才运行 resume。若 status 已为 succeeded，不要再次执行或恢复：

~~~bash
yushuos --config-root "$HOME/.yushuos" status --request-id demo-capture-001
# 仅当 status 仍为 unknown 且需要恢复时运行
yushuos --config-root "$HOME/.yushuos" resume --file request.json --host-mode execute
~~~

Core receipt/outbox 与插件业务 SQLite 分属不同数据库，不能作为跨库 ACID 事务描述。Core 0.3.1 的本地 <code>resume</code> 必须找到同一请求的回执并核验原 provider 与插件 proof。

## App 外部写入的 unknown

Feishu/IMA 的外部写入不能沿用本地插件的通用恢复说明。Core 0.3.1 没有 App 私有 receipt 的通用访问路径；Core <code>resume</code> 可能返回 unavailable，不能据此推断 App 成功或失败。保留原 request ID、原始完整 V3 envelope、binding 和 provider 身份，再按 [App connector 恢复说明](APP-CONNECTORS.md)调用对应 adapter 的私有 receipt 核验流程。核验前不重新 submit、不换 ID；缺少 receipt 证据时继续报告 unknown/unavailable。

当前 Feishu/IMA live-read/live-write 状态是 <code>not_verified</code>。模拟 transport 的成功只证明模拟路径有效，不能报告为真实账号或线上 App 成功。插件安装也不会自动取得账号授权。

## English

Install the pinned Core and selected plugins using the [installation guide](INSTALL.en.md). Configure versions, resource scopes, and narrow grants explicitly. Installing a plugin or host entry does not authorize business writes or external App actions.

For Codex and WorkBuddy, run Core's <code>install-host</code> after the pinned Core release has been deployed, verified, and activated:

~~~powershell
$CoreHome = Join-Path $HOME '.yushuos'
yushuos --config-root $CoreHome install-host --host codex --host-config-root (Join-Path $HOME '.codex')
yushuos --config-root $CoreHome install-host --host workbuddy --host-config-root (Join-Path $HOME '.workbuddy')
~~~

~~~bash
core_home="$HOME/.yushuos"
yushuos --config-root "$core_home" install-host --host codex --host-config-root "$HOME/.codex"
yushuos --config-root "$core_home" install-host --host workbuddy --host-config-root "$HOME/.workbuddy"
~~~

These commands install only Core-managed Skill/Rule instructions. They do not install business plugins, connect Apps, or grant permissions. Run <code>uninstall-host</code> with the same arguments to remove an entry; Core removes only an unmodified file matching its installation record. Other hosts use the generic CLI/JSON flow and have no automatic installer in this Suite.

Read <code>doctor</code> and <code>catalog --details</code> before routing. Use only capabilities that are currently available and match their declared schema, intent, mode, scopes, and grants. Preview writes first. Execute only after the user explicitly approves that write and the host authorizes execution. A configured grant does not replace per-action approval.

Treat only <code>succeeded</code> as confirmed success. A preview has not committed. Preserve the original request ID. For an unknown local plugin write, check <code>status --request-id &lt;id&gt;</code> and reconcile the Core receipt with the plugin proof before an authorized <code>resume --file &lt;original-request.json&gt; --host-mode execute</code>. The Core receipt/outbox and plugin business database are separate stores, with no cross-database ACID transaction. A host-required decision must come from the host with real evidence.

For an App external write with <code>unknown</code>, Core 0.3.1's generic <code>resume</code> may return unavailable because the adapter receipt is private. That status does not establish whether the App write succeeded. Preserve the original request ID, full V3 envelope, binding, and provider identity; follow the [App connector recovery guide](APP-CONNECTORS.md) to verify the adapter's private receipt. Do not submit again under a new ID. If receipt evidence is missing, keep reporting unknown/unavailable. Feishu/IMA live access remains <code>not_verified</code>; a mock success is not evidence of a live result.
