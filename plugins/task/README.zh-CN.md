# YushuOS Task 插件

`yushuos.task` 是 YushuOS 的首个独立业务插件。0.2.0 提供九项能力：创建、读取、列表、修改、完成、重开、软删除、取消和归档任务。它也是 Core Contract v3 `local_commit_v1` 插件协议的参考实现。冻结行为见[实施基线](docs/IMPLEMENTATION-READY.zh-CN.md)，公共模型与存储说明见[合同与存储参考](docs/CONTRACT-AND-STORAGE.md)，后续插件按[业务插件作者指南](docs/BUSINESS-PLUGIN-AUTHOR-GUIDE.zh-CN.md)开发。

取消将 open 改为 cancelled；归档设置 archived_at 并保留状态；重开可将 completed/cancelled/归档任务恢复为 open 并清除归档。列表默认排除删除与归档，分别使用 include_deleted 和 include_archived 显式包含。旧 schema v1 可继续只读访问，首次获准写入前备份并迁移到 v2。完整规则见 [0.2.0 扩展规格](docs/TASK-0.2.0.md)。

## 兼容性与数据边界

Task 要求 Python 3.11+ 和 YushuOS Core `>=0.3.1,<0.4`。请使用 Core 源码固定提交 `3d784c23034b8cd590327da36f6fa8d41f0dd362`；Core 兼容性工作流及结果见[验证报告](VERIFICATION.md)。Task ZIP 是经锁定、供 Core 安装的插件包。Python wheel 供开发与依赖检查使用；安装 wheel 不会把插件安装进 Core。

Task 业务数据保存在 Core 私有插件数据目录，与 Core 共享操作台账分开。Core 收据、锁和事件只保存有界元数据及 Task 引用，不保存标题或备注。`project_ref` 仅用于业务分类，不构成权限边界。删除是软删除且为终态。结果正文在 180 天后逻辑过期；下一次真实且获准的 Task 写入才会物理清除过期正文。读取、预览、重放和恢复都不会清理正文。

## 构建与安装

在 Task 源码仓库中安装固定版本的 Core。以下命令适用于 Windows PowerShell；需要 Python 3.11+ 和 pip。

```powershell
git clone https://github.com/lan99988/YushuCore-OS.git .\yushuos-core
git -C .\yushuos-core checkout 3d784c23034b8cd590327da36f6fa8d41f0dd362
python -m venv .venv-core
.\.venv-core\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .\yushuos-core
python .\scripts\build_plugin.py --core-path .\yushuos-core
```

在 bash 中使用以下激活方式：

```bash
git clone https://github.com/lan99988/YushuCore-OS.git ./yushuos-core
git -C ./yushuos-core checkout 3d784c23034b8cd590327da36f6fa8d41f0dd362
python3 -m venv .venv-core
source .venv-core/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ./yushuos-core
python ./scripts/build_plugin.py --core-path ./yushuos-core
```

生成的 ZIP 只包含经过 Core 校验的 `plugin/` 目录。先将 ZIP 解压到普通目录，再通过 Core 安装。`install-plugin` 安装不可变版本，但不会自动选中版本或授权权限。

```powershell
python .\examples\setup_core.py --config-root .\.task-core --store-id personal
Expand-Archive -LiteralPath .\dist\yushuos-task-0.2.0.zip -DestinationPath .\task-unpacked
yushuos --config-root .\.task-core install-plugin --path .\task-unpacked\plugin
yushuos --config-root .\.task-core catalog --details
```

bash 中激活前述 venv 后运行：

```bash
python ./examples/setup_core.py --config-root ./.task-core --store-id personal
python -m zipfile -e ./dist/yushuos-task-0.2.0.zip ./task-unpacked
yushuos --config-root ./.task-core install-plugin --path ./task-unpacked/plugin
yushuos --config-root ./.task-core catalog --details
```

初始化助手选择 Task 0.2.0，绑定 `task_store`，并创建空共享台账；无需 seed request，也不会创建 Task 数据库。默认授权 `task.read` 和 `task.write`；只有显式加上 `--allow-delete` 才会增加 `task.delete`。

运行锁定插件目录的完整真实 CLI 演示：它在临时 Core 根中安装插件，执行 create/list/update/complete/reopen/cancel/archive/reopen，检查 status，再以原请求重放 create。演示结束时会删除临时根。

```powershell
python .\examples\demo.py --plugin-path .\plugin --yushuos yushuos
```

bash 下运行同一演示：

```bash
python ./examples/demo.py --plugin-path ./plugin --yushuos yushuos
```

在既有 Core 配置根中，也可以通过 helper 调用单项 capability。下面示例保存原 create 请求以便后续核对或恢复：

```powershell
python .\examples\demo.py --config-root .\.task-core --store-id personal --capability task.create --fields '{"title":"Prepare release"}' --save-request .\create-request.json
python .\examples\demo.py --config-root .\.task-core --store-id personal --capability task.list --fields '{}'
$taskRequestId = (Get-Content .\create-request.json -Raw | ConvertFrom-Json).request_id
yushuos --config-root .\.task-core status --request-id $taskRequestId
yushuos --config-root .\.task-core resume --file .\create-request.json --host-mode execute
```

读取需要 `task.read`；创建需要 `task.write`；修改/完成/重开/取消/归档需要 `task.read` 与 `task.write`；删除需要 `task.read` 与 `task.delete`。修改和状态切换都必须带当前 `expected_version`，每项能力的精确字段见[合同与存储参考](docs/CONTRACT-AND-STORAGE.md)。Core 写入预览会返回经验证的计划字段、目标和 `write_performed: false`，不会运行 Task 插件、生成 Task ID 或创建数据库。只有 Core 显式执行模式才会提交写操作。恢复必须使用原请求文件和 request ID，不能换新 ID。保存的请求文件含有输入字段，请妥善保管。

## 停用并移除安装代码

Core 当前没有 `uninstall-plugin` 命令。先将 `yushuos.task` 加入对应 `config.yaml` 的 `user_disabled` 列表，再检查 `catalog --details` 和 `doctor` 确认已停用。解析并核对精确安装版本目录后，只删除该代码目录；默认位置为 `<config-root>/plugins/yushuos.task/0.2.0`。保留 `<config-root>/plugin-data/yushuos.task` 和共享操作台账，它们保存用户数据及 Core 管理的恢复记录。

## 发行验证

独立 ZIP、wheel、干净环境 CLI smoke、本地测试结果，以及 Task CI 与 Core CI 的区别记录在[验证报告](VERIFICATION.md)。Task 不连接外部账号、不排程、不发送通知，也没有绕过 Core 的插件专属 CLI。

## 许可证

MIT，见 [LICENSE](LICENSE)。
