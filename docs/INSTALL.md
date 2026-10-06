# 安装指南

## 前置条件

- Python 3.11 或更新版本、Git。
- 本地安装固定版本的 YushuOS Core。插件套件不发布到 PyPI，不能用 `pip install yushuos-plugin-suite` 安装。
- 具备网络连接以检出 Core 固定提交。Core 与插件均以当前用户身份运行；插件包锁用于发现文件变化，不是操作系统沙箱或发布者签名。

## 1. 安装固定 Core

```powershell
git clone https://github.com/lan99988/YushuCore-OS.git yushuos-core
Set-Location yushuos-core
git checkout a198be8eb463581b8d18440fb46558c35fe62f5f
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
yushuos --help
```

macOS/Linux 激活命令为 `source .venv/bin/activate`。用独立且权限收紧的目录存放 Core 配置和数据；不要把凭据或个人业务数据库放进代码仓库。若已有 Core 安装，确认版本为 0.3.1 且来源 SHA 匹配，不要覆盖其本地配置。

## 2. 构建套件 ZIP

从插件仓库根目录（需 Python 3.11+）执行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .[dev]
python -m scripts.build_suite --output dist
```

生成器会在 `dist/` 写入独立插件 ZIP、`suite-manifest.json` 和 `SHA256SUMS`。每个 ZIP 有自己的 `plugin.yaml`、runner、业务运行时/adapter 和 `plugin.lock.json`。Suite 不在 PyPI；发布状态与验收证据见 RELEASE.md；App 的 mock 与 live 状态分别记录。

校验 SHA-256（PowerShell）：

```powershell
Get-Content dist\SHA256SUMS | ForEach-Object {
  $parts = $_ -split '\s+', 2
  $actual = (Get-FileHash (Join-Path dist $parts[1]) -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($actual -ne $parts[0]) { throw "SHA256 mismatch: $($parts[1])" }
}
```

ZIP 内是插件包目录。解压所选 ZIP 到专用 staging 目录，再运行 Core 的真实插件安装命令：

```powershell
Expand-Archive dist\yushuos-task-0.2.0.zip .\staging\task
yushuos --config-root "$env:USERPROFILE\.yushuos" install-plugin --path .\staging\task\plugin
```

本地领域 ZIP 的名称形如 `yushuos-capture-0.1.0.zip`；task ZIP 为 `yushuos-task-0.2.0.zip`。按 `suite-manifest.json` 选择插件，不必安装整个套件。不要猜测 App 代理的 live 能力或把 pending 验证状态当成成功。

## 3. 配置和检查

Core CLI 支持 `install-plugin`，用于验证并复制不可变插件版本；它不会自动选择或启用插件。Core 配置通过 `plugins.versions` 选择 plugin ID 对应的版本，通过 `plugins.disabled` / `user_disabled` 控制禁用，并通过 `permissions.grants` 配置权限授予；具体 schema 以固定 Core 的配置模板和 `docs/zh-CN/USAGE.md` 为准。项目配置也可选择插件版本，但不得扩大全局权限。安装后检查：

```powershell
yushuos --config-root "$env:USERPROFILE\.yushuos" doctor
yushuos --config-root "$env:USERPROFILE\.yushuos" catalog --details
```

只有 catalog 显示的能力、依赖、资源范围和权限状态与预期一致时，才从宿主发出请求。读取用 `query`，变更用 `command`；host-required 需要宿主给出证据和判断，Core 不自带 AI。默认预览并不等于已提交。

## 4. Codex、WorkBuddy 与其他 AI 宿主

支持宿主的托管 Skill/Rule 安装命令和约束见 [Core AI 安装说明](https://github.com/lan99988/YushuCore-OS/blob/a198be8eb463581b8d18440fb46558c35fe62f5f/docs/zh-CN/AI-INSTALL.md)。对没有专用 installer 的宿主，可手动复制插件 ZIP 中的 `SKILL.md` 到宿主自己的 Skill/Rule 目录；Skill 只作为说明入口，必须将请求交给统一 Core CLI 和 catalog，不能直接调用其他插件或读取私有数据库。本仓库没有另外提供自动 Skill/Rule 安装器。

## 卸载与数据保留

Core 0.3.1 的 `install-plugin` 提供版本安装；当前 CLI 没有对应的插件卸载子命令。禁用或从配置中移除所选版本不会清理 `<config-root>/plugin-data/` 下的插件私有数据。保留配置和数据目录的备份；清除数据属于单独的数据管理操作，不是卸载包的副作用。
