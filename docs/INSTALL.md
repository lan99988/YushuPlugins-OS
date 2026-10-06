# 安装与首次调用

本指南从源码开始，依次检出固定 Core、在 Suite 的同一个虚拟环境安装 Core 与开发依赖、初始化私有 Core 目录、部署并激活 Core，再安装一个插件。需要 Git、Python 3.11+ 和网络访问。

## 1. 检出 Core 与 Suite

在一个你选择的工作目录运行以下命令。Suite 当前不发布到 PyPI。 后续片段假设你保持同一个 shell 会话并位于 Suite 根目录；重新打开终端后，先回到该目录并重新设置 CoreHome/core_home。

~~~powershell
git clone https://github.com/lan99988/YushuCore-OS.git yushuos-core
git -C yushuos-core checkout a198be8eb463581b8d18440fb46558c35fe62f5f
git clone https://github.com/lan99988/YushuPlugins-OS.git yushuos-plugins
Set-Location yushuos-plugins
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e "..\yushuos-core" -e ".[dev]"
yushuos --help
~~~

macOS/Linux 使用相同检出步骤，虚拟环境激活与依赖安装如下：

~~~bash
git clone https://github.com/lan99988/YushuCore-OS.git yushuos-core
git -C yushuos-core checkout a198be8eb463581b8d18440fb46558c35fe62f5f
git clone https://github.com/lan99988/YushuPlugins-OS.git yushuos-plugins
cd yushuos-plugins
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ../yushuos-core -e '.[dev]'
yushuos --help
~~~

两条路线都在 Suite 仓库的 <code>.venv</code> 中安装固定 Core commit 与 Suite 的 <code>.[dev]</code>。不要只安装 Suite，否则该环境不会包含 pinned Core CLI。

## 2. 初始化并部署 Core

默认 Core home 是当前用户的 <code>~/.yushuos</code>。只在 <code>config.yaml</code> 不存在时复制模板；已有配置保持原样。Core 没有 <code>init</code> 子命令。配置与台账包含本机状态，应放在仓库外。

~~~powershell
$CoreHome = Join-Path $HOME '.yushuos'
New-Item -ItemType Directory -Force $CoreHome | Out-Null
$CoreConfig = Join-Path $CoreHome 'config.yaml'
if (-not (Test-Path -LiteralPath $CoreConfig)) {
  Copy-Item -LiteralPath ..\yushuos-core\templates\core.yaml.template -Destination $CoreConfig
}

yushuos --config-root $CoreHome deploy --source ..\yushuos-core --version 0.3.1 --preview
yushuos --config-root $CoreHome verify --version 0.3.1
yushuos --config-root $CoreHome activate --version 0.3.1
yushuos --config-root $CoreHome doctor
~~~

~~~bash
core_home="$HOME/.yushuos"
mkdir -p "$core_home"
core_config="$core_home/config.yaml"
if [ ! -e "$core_config" ]; then
  cp ../yushuos-core/templates/core.yaml.template "$core_config"
fi

yushuos --config-root "$core_home" deploy --source ../yushuos-core --version 0.3.1 --preview
yushuos --config-root "$core_home" verify --version 0.3.1
yushuos --config-root "$core_home" activate --version 0.3.1
yushuos --config-root "$core_home" doctor
~~~

Core 的 <code>deploy --preview</code> 复制固定 Core 文件为未激活候选版本；<code>verify</code> 校验文件哈希；只有验证成功后才用 <code>activate</code> 切换活动版本。这里先用 <code>doctor</code> 确认活动版本的 <code>release_status</code> 为 verified。共享台账会在第 4 步把路径写入私有配置并初始化；初始化前 <code>ledger_bound</code> 尚未开启是预期状态。不要把配置、凭据或业务数据提交到代码仓库。

## 3. 构建或选择插件 ZIP

如已有获准发布的 Suite 资产，可按其 <code>suite-manifest.json</code> 选择一个 ZIP，并按同一资产中的 <code>SHA256SUMS</code> 校验。没有可用发布资产时，从当前 Suite checkout 构建 20 个独立 ZIP：

~~~powershell
python -m scripts.build_suite --output dist
Get-Content .\dist\SHA256SUMS | ForEach-Object {
  $parts = $_ -split '\s+', 2
  $actual = (Get-FileHash -LiteralPath (Join-Path .\dist $parts[1]) -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($actual -ne $parts[0]) { throw "SHA256 mismatch: $($parts[1])" }
}
Get-Content .\dist\suite-manifest.json
~~~

~~~bash
python -m scripts.build_suite --output dist
(cd dist && sha256sum --check SHA256SUMS)
cat dist/suite-manifest.json
~~~

macOS 可用 <code>(cd dist && shasum -a 256 -c SHA256SUMS)</code> 校验。清单列出 plugin ID、版本、ZIP 文件名、SHA-256 和验证状态；只安装需要的插件。MVP 的 20 个模拟包已构建，独立包安装路径已验证。该证据不表示五项跨平台 CI 已通过，也不表示 Feishu/IMA 的真实 App 读写已验收；两者仍为 <code>not_verified</code>。构建出来的本地资产也不自动成为正式发布。

以下用 Capture 演示。ZIP 解压后插件目录在 <code>plugin</code> 子目录，Core 的 <code>install-plugin</code> 会检查锁并复制不可变版本：

~~~powershell
$Stage = '.\staging\capture-0.1.0'
New-Item -ItemType Directory -Force $Stage | Out-Null
Expand-Archive -LiteralPath .\dist\yushuos-capture-0.1.0.zip -DestinationPath $Stage
yushuos --config-root $CoreHome install-plugin --path "$Stage\plugin"
~~~

~~~bash
mkdir -p staging/capture-0.1.0
python -m zipfile -e dist/yushuos-capture-0.1.0.zip staging/capture-0.1.0
yushuos --config-root "$core_home" install-plugin --path staging/capture-0.1.0/plugin
~~~

Task 包名是 <code>yushuos-task-0.2.0.zip</code>；本地领域插件和 Feishu/IMA 包的版本及名称以清单为准。安装只验证并复制包，不会选择/启用插件、授予业务权限或连接真实 App。Core 0.3.1 没有 <code>enable</code> 或 <code>uninstall-plugin</code> 命令。

## 4. 配置、检查并调用

编辑私有 <code>config.yaml</code>，把以下键合并到模板中对应区块；若区块已存在，应合并条目而不是追加重复的顶层区块：

~~~yaml
plugins:
  versions:
    yushuos.capture: "0.1.0"
bindings:
  resources:
    capture_store: personal
permissions:
  grants:
    - capture.read
    - capture.write
state:
  ledger_path: state/operations.sqlite3
~~~

<code>plugins.versions</code> 选择已经安装的版本，<code>bindings.resources</code> 把 Capture 的 scope <code>capture_store</code> 绑定到本示例的 <code>personal</code> store，<code>permissions.grants</code> 明确授予声明的能力。按你需要授予最小范围；写权限 grant 只是 Core 配置门禁，不能代替每次执行前的用户授权。资源绑定值须与请求 <code>target.store_id</code> 一致。每个插件和每个环境按 catalog 所示 capability 配置自己的范围与权限。Core 0.3.1 没有台账初始化 CLI；保存配置后，用固定 SDK 支持的 <code>StateStore.connect(write=True)</code> 创建台账 schema，首次执行本地写入前必须完成。

~~~powershell
$Ledger = Join-Path $CoreHome 'state\operations.sqlite3'
python -c "import sys; from yushuos_sdk.state import StateStore; exec('with StateStore(sys.argv[1]).connect(write=True):\n    pass')" $Ledger
yushuos --config-root $CoreHome doctor
yushuos --config-root $CoreHome catalog --details
~~~

~~~bash
python -c 'import sys; from yushuos_sdk.state import StateStore; exec("with StateStore(sys.argv[1]).connect(write=True):\n    pass")' "$core_home/state/operations.sqlite3"
yushuos --config-root "$core_home" doctor
yushuos --config-root "$core_home" catalog --details
~~~

只使用 catalog 声明且当前可用的能力。下面的例子会创建虚构的本地 Capture 条目；将 JSON 保存为 <code>request.json</code>：

~~~json
{
  "request_id": "demo-capture-001",
  "capability": "capture.create",
  "intent": "command",
  "fields": {"content": "示例捕获条目", "source": "manual"},
  "target": {"store_id": "personal"}
}
~~~

~~~powershell
yushuos --config-root $CoreHome invoke --file request.json --mode preview
~~~

~~~bash
yushuos --config-root "$core_home" invoke --file request.json --mode preview
~~~

预览返回 <code>preview</code> 且 <code>write_performed</code> 为 false。只有在检查字段、目标、权限和预期变化，并且用户明确批准这次写入后，才由宿主用同一请求执行：

~~~powershell
yushuos --config-root $CoreHome invoke --file request.json --mode execute --host-mode execute
yushuos --config-root $CoreHome status --request-id demo-capture-001
~~~

~~~bash
yushuos --config-root "$core_home" invoke --file request.json --mode execute --host-mode execute
yushuos --config-root "$core_home" status --request-id demo-capture-001
~~~

只有结果为 <code>succeeded</code> 才确认成功。若本地写入返回 <code>unknown</code>，先用原 ID 查 <code>status</code> 并核对插件 proof，再在获准后对同一完整请求运行 <code>resume --file request.json --host-mode execute</code>。不要改用新 ID 或盲目重放。插件自己的 SQLite 与 Core receipt/outbox 分处不同数据库，不构成跨库 ACID 事务。

Feishu/IMA 外部写入返回 <code>unknown</code> 时，不要把 Core 的通用 <code>resume</code> 当作已核验。Core 0.3.1 不一定有共享原请求收据；必须按 [App connector 恢复说明](APP-CONNECTORS.md)核对 adapter 的持久私有 receipt。没有原完整信封或能证明结果的 receipt，就保留 <code>unknown</code>/<code>unavailable</code>，停止自动操作。实时 App 当前 <code>not_verified</code>，模拟成功不能报告为线上成功。

## 5. 接入宿主、停用与移除

Core 活动版本通过哈希验证后，可安装 Codex Skill 或 WorkBuddy Rule 调用入口：

~~~powershell
yushuos --config-root $CoreHome install-host --host codex --host-config-root (Join-Path $HOME '.codex')
yushuos --config-root $CoreHome install-host --host workbuddy --host-config-root (Join-Path $HOME '.workbuddy')
~~~

~~~bash
yushuos --config-root "$core_home" install-host --host codex --host-config-root "$HOME/.codex"
yushuos --config-root "$core_home" install-host --host workbuddy --host-config-root "$HOME/.workbuddy"
~~~

这些命令只安装受 Core 管理的 Skill/Rule 说明入口，不安装业务插件、不连接 App、不授予权限。安装前检查宿主配置目录；安装器会拒绝覆盖非托管文件。其他 AI 宿主按通用 CLI/JSON 协议接入，没有额外自动安装器。

停用插件时，在 Core 配置的 <code>user_disabled</code> 列表（或 <code>plugins.disabled</code>）加入 plugin ID，例如 <code>yushuos.capture</code>；不要寻找 <code>enable</code> 命令。手工移除包前先停用并备份，然后删除 <code>&lt;Core home&gt;/plugins/yushuos.capture/0.1.0</code>，并从 <code>plugins.versions</code> 移除该版本选择。包目录与业务数据分离：Capture 数据留在 <code>&lt;Core home&gt;/plugin-data/yushuos.capture/</code>。不要删除该数据目录；清理业务数据须单独备份并由数据所有者明确决定。
