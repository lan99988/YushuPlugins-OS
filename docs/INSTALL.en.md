# Install and make a first call

This guide checks out the pinned Core, installs Core and Suite development dependencies into one Suite virtual environment, initializes a private Core home, deploys and activates Core, then installs one plugin. You need Git, Python 3.11+, and network access.

## 1. Check out Core and the Suite

Run these commands from a working directory you choose. The Suite is not published on PyPI. Keep the same shell session and stay in the Suite root for the following snippets. After opening a new terminal, return to that directory and redefine CoreHome/core_home.

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

On macOS/Linux, use the same clone and pin commands, then activate and install dependencies as follows:

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

Both paths install the pinned Core commit and the Suite's <code>.[dev]</code> extra into the Suite repository's <code>.venv</code>. Installing only the Suite would leave the Core CLI out of this environment.

## 2. Initialize and deploy Core

The default Core home is <code>~/.yushuos</code>. Copy the template only when <code>config.yaml</code> does not exist; the commands preserve an existing configuration. Core 0.3.1 has no <code>init</code> command. Keep configuration and the operation ledger outside the repositories.

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

<code>deploy --preview</code> copies the pinned Core as an inactive candidate, <code>verify</code> checks its file hashes, and <code>activate</code> switches the active version only after verification succeeds. At this point, use <code>doctor</code> to confirm the active release is verified. The shared ledger is configured and initialized in step 4; until then, <code>ledger_bound</code> is expected to be false. Do not put configuration, credentials, or business data in the code repositories.

## 3. Build or select a plugin ZIP

If approved Suite release assets are available, choose one ZIP from that release's <code>suite-manifest.json</code> and verify it with the matching <code>SHA256SUMS</code>. Otherwise build 20 independent ZIPs from the current Suite checkout:

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

On macOS, verify with <code>(cd dist && shasum -a 256 -c SHA256SUMS)</code>. The manifest lists plugin ID, version, ZIP filename, SHA-256, and validation status. Install only the plugins you need. The MVP's 20 mock packages have been built and the independent package-install path has been verified. This does not mean the five cross-platform CI jobs passed, or that live Feishu/IMA App reads and writes are verified; those remain <code>not_verified</code>. Building local artifacts does not publish a release.

The following example installs Capture. Extract the ZIP to a fresh staging directory; the plugin directory is inside its <code>plugin</code> subdirectory. Core's <code>install-plugin</code> verifies the package lock and copies an immutable version:

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

The Task ZIP is named <code>yushuos-task-0.2.0.zip</code>; use the manifest for other domain and Feishu/IMA package versions and names. Installation verifies and copies a package. It does not select or enable it, grant business permissions, or connect a live App. Core 0.3.1 has no <code>enable</code> or <code>uninstall-plugin</code> subcommand.

## 4. Configure, inspect, and invoke

Edit the private <code>config.yaml</code> and merge these keys into the matching template sections. When a section already exists, merge its entries instead of appending a duplicate top-level section:

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

<code>plugins.versions</code> selects the installed version; <code>bindings.resources</code> binds Capture's <code>capture_store</code> scope to the example <code>personal</code> store; <code>permissions.grants</code> grants declared capabilities. Grant only the scope you need. A write grant is Core policy and does not replace the user's authorization for each execution. The resource binding must match the request's <code>target.store_id</code>. Configure other plugins using the capabilities and scopes shown in their catalog entries. Core 0.3.1 has no ledger-initialization CLI; after saving the config, create the ledger schema with the pinned SDK's supported <code>StateStore.connect(write=True)</code> before the first local write.

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

Use a capability that the catalog declares and currently reports as available. This example creates a fictional local Capture item; save it as <code>request.json</code>:

~~~json
{
  "request_id": "demo-capture-001",
  "capability": "capture.create",
  "intent": "command",
  "fields": {"content": "Example capture item", "source": "manual"},
  "target": {"store_id": "personal"}
}
~~~

~~~powershell
yushuos --config-root $CoreHome invoke --file request.json --mode preview
~~~

~~~bash
yushuos --config-root "$core_home" invoke --file request.json --mode preview
~~~

The preview should return <code>preview</code> with <code>write_performed</code> set to false. Review the fields, target, permissions, and expected change. Only after the user explicitly approves this write should the host execute the same request:

~~~powershell
yushuos --config-root $CoreHome invoke --file request.json --mode execute --host-mode execute
yushuos --config-root $CoreHome status --request-id demo-capture-001
~~~

~~~bash
yushuos --config-root "$core_home" invoke --file request.json --mode execute --host-mode execute
yushuos --config-root "$core_home" status --request-id demo-capture-001
~~~

Only <code>succeeded</code> confirms success. If a local write returns <code>unknown</code>, query <code>status</code> with the original ID, inspect the plugin proof, then—after authorization—run <code>resume --file request.json --host-mode execute</code> with the same full request. Do not switch IDs or blindly replay. The plugin's SQLite database and Core receipt/outbox live in separate databases; they are not one ACID transaction.

For a Feishu/IMA external write that returns <code>unknown</code>, do not treat Core's generic <code>resume</code> as verification. Core 0.3.1 may not have a shared receipt for the original request. Follow the [App connector recovery guide](APP-CONNECTORS.md) to verify the adapter's durable private receipt. Without the original full envelope and a receipt that proves the result, keep the state <code>unknown</code>/<code>unavailable</code> and stop automation. Live App access remains <code>not_verified</code>; a mock success is not a live success.

## 5. Connect a host, disable, and remove

After the active Core release passes hash verification, install the managed Codex Skill or WorkBuddy Rule entry point:

~~~powershell
yushuos --config-root $CoreHome install-host --host codex --host-config-root (Join-Path $HOME '.codex')
yushuos --config-root $CoreHome install-host --host workbuddy --host-config-root (Join-Path $HOME '.workbuddy')
~~~

~~~bash
yushuos --config-root "$core_home" install-host --host codex --host-config-root "$HOME/.codex"
yushuos --config-root "$core_home" install-host --host workbuddy --host-config-root "$HOME/.workbuddy"
~~~

These commands install Core-managed Skill/Rule instructions only. They do not install business plugins, connect Apps, or grant permissions. Inspect the host configuration directory first; the installer refuses to overwrite an unmanaged file. Other AI hosts use the generic CLI/JSON protocol and have no additional automatic installer.

To disable a plugin, add its ID, such as <code>yushuos.capture</code>, to <code>user_disabled</code> (or <code>plugins.disabled</code>) in the Core config. Core has no <code>enable</code> subcommand. Before manually removing a package, disable it and back up the config. Remove <code>&lt;Core home&gt;/plugins/yushuos.capture/0.1.0</code> and its selection from <code>plugins.versions</code>. The package and data paths are separate: Capture's private data remains under <code>&lt;Core home&gt;/plugin-data/yushuos.capture/</code>. Keep that data directory; any business-data cleanup is a separate decision by its owner.
