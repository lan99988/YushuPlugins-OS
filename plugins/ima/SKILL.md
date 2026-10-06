---
name: app-ima
description: ima 独立白名单 JSON 连接插件；必须使用用户已绑定发行版本和完整 Core 上下文。
---

# ima 连接插件

先读取安装目录 README.md 和宿主已绑定清单。仅调用已实现、已授权、已验收能力；发行版默认不可执行真实App操作。

使用 run.py JSONL 完整 json-stdio-v2 信封，读取 intent=query；仅用户明确要求保存、更新或发送时使用 command 和 execute/execute。资源ID必须同时符合用户绑定scope。秘密不打印、不拷贝入请求或发布包。

复用稳定 request_id。unknown 后以原上下文 action=recover 只读持久收据；不能换ID、删收据或直接调用API重做。外部写不使用local_commit_v1。没有真实App权限时报告not_verified，不索取新权限，不执行写入验收。
