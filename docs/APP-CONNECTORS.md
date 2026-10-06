# App Connectors MVP

Feishu 和 IMA 分别独立源目录与发布包；导出入口为 adapter.CAPABILITIES、MANIFEST、descriptor()、handle_envelope()、main()。protocol.py各App独立携带，不依赖公共business_runtime或兄弟插件。

Core基线0.3.1 SHA a198be8eb463581b8d18440fb46558c35fe62f5f；manifest v3、json-stdio-v2、schema 1上下文。禁止operation_support=local_commit_v1与external_write组合。真实Core清单SHA锁加载、PluginRunner子进程配置传递已有模拟测试；真实账号未做新增授权或写入，状态not_verified。

| App | 实现边界 | unavailable边界 |
| --- | --- | --- |
| Feishu | task create/get/list/update/complete；calendar.event create/get/list/update；message.send text；resource.bindings.get | 未配置CLI/用户binding、未授权、未验收、白名单外能力 |
| IMA | note create/append/get/search/list；notebook.list；kb get/list/search；knowledge.search | note delete/overwrite、kb.associate未实现；未配置凭据、未授权、未验收、白名单外能力 |

能力名只用feishu.*与ima.*，不竞争task.*或knowledge.*领域provider。契约input为request.fields，output为Result.data，Core状态succeeded/failed/unavailable/unknown/preview；write preview明确not_submitted。资源引用始终使用实际Task GUID、calendar event_id、message_id、note_id/folder_id/knowledge_base_id；不伪造统一document对象或版本。

宿主仓库外binding定义configured、account_ref、verified_capabilities、authorized_capabilities、grants及私有credentials。资源scope从SDK context.resources读取，不从自然语言推断。Core manifest.resource_scopes只对request.target门控，adapter另严格比对fields与允许scope，Task get/update/complete再read核验远端tasklists归属。全局IMA操作核验account_ref。只有所有门控通过才claim和调用transport。

远端协议external_receipt_v1：request_id+指纹，持久预先claim，确认provider资源ID后持久结果；缺证据unknown，recover/replay只读本地收据证明，不提交远端。崩溃残留claim与收据持久失败均不会触发重复调用。SQLite、Core ledger、provider不具备跨库/远端原子性。Core 0.3.1原生恢复仅面向内部local_commit_v1，外部恢复直接调用原信封CLI；unknown若缺收据不能定案。

模拟验证使用父仓库venv运行 `python -m pytest tests/test_app_connectors.py`。测试fake HTTP/CLI，无真实网络写入；源参考只读个人系统白名单代码，未复制配置/凭据/数据。统一框架负责生成最终插件清单、Descriptor、锁和ZIP；不得把任何用户binding或receipt SQLite加入发行包。
