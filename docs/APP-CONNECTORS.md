# App Connectors MVP

Feishu 和 IMA 分别独立源目录与发布包；导出入口为 adapter.CAPABILITIES、MANIFEST、descriptor()、handle_envelope()、main()。protocol.py、definition.py 和 configure.py 各 App 独立携带，不依赖公共business_runtime或兄弟插件。

Core基线0.3.1 SHA a198be8eb463581b8d18440fb46558c35fe62f5f；manifest v3、json-stdio-v2、schema 1上下文。禁止operation_support=local_commit_v1与external_write组合。真实Core清单SHA锁加载、PluginRunner子进程配置传递已有模拟测试；真实账号未做新增授权或写入，状态not_verified。

| App | 实现边界 | unavailable边界 |
| --- | --- | --- |
| Feishu | task create/get/list/update/complete；calendar.event create/get/list/update；message.send text；resource.bindings.get | 未配置CLI/用户binding、未授权、未验收、白名单外能力 |
| IMA | note create/append/get/search/list；notebook.list；kb get/list/search；knowledge.search | note delete/overwrite、kb.associate未实现；未配置凭据、未授权、未验收、白名单外能力 |

能力名只用feishu.*与ima.*，不竞争task.*或knowledge.*领域provider。契约input为request.fields，output为Result.data，Core状态succeeded/failed/unavailable/unknown/preview；write preview明确not_submitted。资源引用始终使用实际Task GUID、calendar event_id、message_id、note_id/folder_id/knowledge_base_id；不伪造统一document对象或版本。

宿主仓库外binding定义configured、account_ref、verified_capabilities、authorized_capabilities、grants及私有credentials。资源scope从SDK context.resources读取，不从自然语言推断。Core manifest.resource_scopes只对request.target门控，adapter另严格比对fields与允许scope，Task get/update/complete再read核验远端tasklists归属。全局IMA操作核验account_ref。只有所有门控通过才claim和调用transport。

发行清单的 implemented 能力使用代码/模拟验收 verified=true、authorized=true，使 Core 能注册其实现；未实现能力保持 false、unavailable。这不登记任何真实账户权限。账户的 binding capability membership、binding grants、Core grants 与 scope 必须同时满足。`configure.py --core-root C:/FAKE/core --binding-file C:/FAKE/account-binding.json` 只接合已存在的仓库外 Core、台账与锁定发布包，生成 native active_pointer/config_file/ledger_path/python_executable 和 manifest SHA256；不改权限和资源，不输出私有凭据，不发起 App 调用。未提供 binding-file 时生成 configured=false 的空权限模板。

远端协议external_receipt_v1：request_id+指纹，持久预先claim，确认provider资源ID后持久结果；缺证据unknown，recover/replay只读本地收据证明，不提交远端。崩溃残留claim与收据持久失败均不会触发重复调用。SQLite、Core ledger、provider不具备跨库/远端原子性。Core 0.3.1原生恢复仅面向内部local_commit_v1，外部恢复直接调用原信封CLI；unknown若缺收据不能定案。

真实 CoreRuntime 模拟链路已验证 read、write preview、Core 无 grant 拒绝、账户未授权/未验收拒绝、target/fields 越范围拒绝，以及 fake external write 成功后的同 request_id 私有收据重放。模拟远端成功、私有收据确认落盘后退出子进程，Core invoke 返回 unknown；重启后 Core.resume 返回 unavailable（共享台账无原请求收据），不会伪造 Core confirmation。显式 App recover CLI 读取已持久私有收据返回 succeeded，fake 远端调用次数仍为 1，Core 台账保持原状态。

核验 CLI 必须使用宿主在私有目录保存的原完整 V3 信封，只修改 action，保留原 request、binding、data_path、provider_digest 与 scope。下面全部为 FAKE 路径，不是新增写入验收：

```powershell
$recoveryEnvelope = Get-Content -LiteralPath C:/FAKE/private/original-envelope.json -Raw | ConvertFrom-Json
$recoveryEnvelope.action = 'recover'
$recoveryEnvelope | ConvertTo-Json -Depth 50 -Compress | & C:/FAKE/python.exe C:/FAKE/release/adapter.py
```

没有原信封或没有可证明的私有收据时继续报告 unknown/unavailable；不能改 ID、删收据、重新 submit，不能把 App 的本地收据核验冒充 Core 原生 resume。

模拟验证使用父仓库venv运行 `python -m pytest tests/test_app_connectors.py tests/test_app_connectors_core.py`。测试fake HTTP/CLI和真正 CoreRuntime/原生指针加载，无真实网络写入；源参考只读个人系统白名单代码，未复制配置/凭据/数据。统一框架负责生成最终插件清单、Descriptor、锁和ZIP；不得把任何用户binding或receipt SQLite加入发行包。
