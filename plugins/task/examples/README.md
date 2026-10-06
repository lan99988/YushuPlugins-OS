# Task 插件示例

这些示例面向 Python 3.11+ 与 YushuOS Core 0.3.1。请从 Task 仓库根目录运行，并先按 Core 固定提交安装 Core CLI/SDK；示例不依赖 Task Python wheel，runner 会从锁定插件包的 `plugin/src` 加载业务代码。

```powershell
git clone https://github.com/lan99988/YushuCore-OS.git .\yushuos-core
git -C .\yushuos-core checkout 3d784c23034b8cd590327da36f6fa8d41f0dd362
python -m venv .venv-core
.\.venv-core\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .\yushuos-core
```

## 一次跑完隔离演示

在仓库根目录运行：

```powershell
python examples/demo.py --plugin-path .\plugin --yushuos yushuos
```

`plugin/` 必须包含有效的 `plugin.yaml` 和 Core 生成的 `plugin.lock.json`。脚本创建新的临时 Core 根，通过 Core CLI 执行 `install-plugin`，然后通过 Core CLI `invoke` 展示 create、list、update、complete、status 和相同 request 的 replay。演示结束时 Core 临时根及其 Task 数据会一并清理。

完整演示不使用用户的默认 Core 配置，也不授予 `task.delete`。业务操作都经过 Core 的正式 CLI 和插件 runner。

## 手动初始化隔离 Core 配置

以下命令在仓库根目录运行；示例路径应当不存在，初始化脚本遇到已有 `config.yaml` 或 ledger 时会拒绝覆盖：

```powershell
python examples/setup_core.py --config-root .\.task-demo-core --store-id personal
yushuos --config-root .\.task-demo-core install-plugin --path .\plugin
python examples/demo.py --config-root .\.task-demo-core --store-id personal --capability task.list --fields '{"limit":20}' --yushuos yushuos
```

`setup_core.py` 固定选择 `yushuos.task@0.1.0`，绑定 `task_store` 到指定的 `store_id`，并用 Core SDK 初始化空共享操作台账。默认权限只有 `task.read` 和 `task.write`；只有显式传入 `--allow-delete` 才增加 `task.delete`。setup 不安装插件，也不会创建 Task 私有 SQLite 数据库；该数据库只会在首次获准的 Task 写操作时创建。

省略 `--config-root` 时，setup 会创建一个新的系统临时目录并保留它，便于用户后续使用；路径会打印在终端。若要授权删除，初始化时运行：

```powershell
python examples/setup_core.py --config-root .\.task-demo-delete --store-id personal --allow-delete
```

## 单次 Core 调用

`demo.py` 也保留单次 capability 调用模式，但必须显式给出已有配置根和 store：

```powershell
python examples/demo.py --config-root .\.task-demo-core --store-id personal --capability task.list --fields '{"limit":20}' --yushuos yushuos
```

写入 capability 使用 Core 的 `execute` 与 `host-mode execute`，因此受 Core 的 grant、资源绑定、请求收据和恢复机制约束。可传 `--preview` 让 Core 只返回预览；Core 的预览 gate 不启动 Task runner，也不创建 Task 数据库或 Task ID。`--save-request PATH` 可将原请求以排他创建方式写入新文件，避免覆盖已有请求文件。
