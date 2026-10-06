# ima 独立连接插件

版本 0.1.0，目标 YushuOS Core >=0.3.1,<0.4（验收基线 SHA a198be8eb463581b8d18440fb46558c35fe62f5f）。Python >=3.11，依赖 yushuos-core、jsonschema、PyYAML（只在生成清单时需要）。协议 JSONL / json-stdio-v2，stdout 仅输出六字段 Core Result。

## 安装

统一生成框架将此目录的 adapter.py、definition.py、protocol.py 放入独立发布包，run.py 调用 adapter.main；导出 MANIFEST、CAPABILITIES 和 descriptor() 供框架生成 plugin.yaml、App Descriptor 和 SHA256 lock。该包不依赖兄弟 App 插件或个人系统目录。安装与版本选择使用 Core 标准插件流程；Core type=app 的 active_pointer 及已存在共享 ledger 仍需要宿主绑定。

先在仓库外建立用户 binding JSON，用 Core plugins.config["yushuos.ima"].binding_file 指向绝对路径。模板只含假值：

```json
{
  "app": "ima",
  "configured": true,
  "account_ref": "account-alias",
  "verified_capabilities": [],
  "authorized_capabilities": [],
  "grants": [],
  "credentials": {}
}
```

逐项验收后才登记 verified_capabilities；只有已有明确授权才登记 authorized_capabilities 和 grants。发行版清单已实现能力的 verified/authorized=true 表示代码与模拟测试支持，供 Core 注册；未实现能力仍为 false。实际账户仍由仓库外 binding 的 configured、能力名单、grants，再与 Core grants 和资源 scope 共同门控，默认名单为空，真实 App 验收状态 not_verified。不得将模拟测试等同真实账户验收。秘密只能来自环境变量或仓库外用户 binding，禁止写入清单、Descriptor、ZIP、测试样例和日志。

不可变发布包包含 configure.py；先准备仓库外现存 Core 配置和共享台账，再运行 `python C:/FAKE/release/configure.py --core-root C:/FAKE/core --binding-file C:/FAKE/account-binding.json`。工具安装锁定包、生成 native active_pointer 并接合 binding_file；不创建共享台账、不增补任何资源绑定或权限。省略 binding-file 时只生成未配置的空名单模板。

## 调用

`python adapter.py` 从 stdin 读取完整 Core V3 信封，一行一请求。必须有 SDK schema 1 context；context身份、外层capability/effect、query/command intent、mode/host_mode 均会校验。读取使用 query，写入使用 command 且 mode=execute、host_mode=execute。preview 返回 not_submitted，不创建收据或调用远端。

请求 resource ID 放 fields。Core manifest.resource_scopes 绑定 request.target，同名 target 可镜像 context.resources 的绑定值；独立CLI允许 target={}，adapter 仍以 fields 对比 context.resources 严格检查 scope。Core正常配置建议每scope绑定一个字符串；独立入口也接受允许ID数组。Core只门控target，fields检查由适配器执行。未配置、未授权、未验收、未实现一律 unavailable；不要绕过入口手工调用任意endpoint。

外部写使用 external_receipt_v1 本插件协议。插件私有 data_path/external-receipts.sqlite3 保存 request_id + 指纹 + 预先claim + 可验证provider资源ID收据。指纹覆盖完整请求、账户别名、provider digest、版本、资源绑定。claim落盘先于远端调用；同ID同指纹只读收据，不同指纹 request_conflict；并发只有一个调用。超时、缺少回执、提交后中断或落盘失败保持 unknown。recover/replay 只读本地持久收据证据，无法证明结果则 unknown，绝不重新提交。没有声称本地SQLite与远端或Core ledger之间原子提交。

Core 0.3.1 PluginRunner 的 recover/replay 入口仅支持 local_commit_v1 内部写；外部写恢复需直接以原完整信封、原binding/data_path、action=recover 调用本CLI，不冒充Core原生外部恢复。不能修改或清空收据来盲重试。

## 实际边界

IMA 使用固定https://ima.qq.com/openapi/路径和POST JSON；环境变量 IMA_OPENAPI_CLIENTID、IMA_OPENAPI_APIKEY 优先，Core runner会清理环境变量，故Core调用通常使用仓库外 binding.credentials.client_id/api_key。实际映射：note.create→note/v1/import_doc（folder_id）；note.append→append_doc；note.get→get_doc_content；note.search→search_note；note.list→list_note；notebook.list→list_notebook；kb.get/list/search→get_knowledge_base/get_knowledge_list/search_knowledge_base；knowledge.search→search_knowledge。不存在 ima.document API。note.delete/overwrite、kb.associate 明确not_implemented；图像、问答等未列能力unsupported_capability。账户全局搜索/目录操作要求account scope匹配binding.account_ref；note、notebook、kb分别绑定允许ID。search_note按start/end生成续页offset，其余分页必须有已核验next_cursor；未公开续页游标返回pagination_unverified，绝不伪造。页大小1–20。Provider未公开可依赖的写幂等键，故未知结果尤其不能重做。仅返回白名单ID/title/content，临时URL/headers不输出。

离线测试覆盖真实CLI/HTTP传输层fake、分页、限流、scope拒绝、脱敏、预先claim、重复请求、不同指纹、并发、超时、中断与收据失败。源接口参考只读白名单 app_plugins/ 和 personal_system/adapters/，未复制个人配置、数据或凭据。
