# Installation guide

## Prerequisites

- Python 3.11+ and Git.
- A local installation of the pinned YushuOS Core. The suite is not published on PyPI, so `pip install yushuos-plugin-suite` is not supported.
- Network access to check out the pinned Core commit. Core and plugins run as the current user. Package locks detect file drift; they are not an OS sandbox or publisher signature.

## 1. Install the pinned Core

```powershell
git clone https://github.com/lan99988/YushuCore-OS.git yushuos-core
Set-Location yushuos-core
git checkout a198be8eb463581b8d18440fb46558c35fe62f5f
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
yushuos --help
```

On macOS/Linux, activate with `source .venv/bin/activate`. Keep Core configuration and data in a dedicated, access-controlled directory. Do not put credentials or personal databases in the code repository. For an existing Core installation, verify version 0.3.1 and the pinned SHA; do not overwrite local configuration.

## 2. Build suite ZIPs

From the plugin repository root with Python 3.11+:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m scripts.build_suite --output dist
```

The builder writes independent plugin ZIPs, `suite-manifest.json`, and `SHA256SUMS` under `dist/`. Each ZIP contains its own `plugin.yaml`, runner, business runtime/adapter, and `plugin.lock.json`. The suite is not on PyPI. If the current checkout lacks source files required by the builder or the build fails, that checkout has not produced the complete installable 20-package suite. The application remains in development; do not describe target artifacts as released before a successful build and installation.

Verify SHA-256 on PowerShell:

```powershell
Get-Content dist\SHA256SUMS | ForEach-Object {
  $parts = $_ -split '\s+', 2
  $actual = (Get-FileHash (Join-Path dist $parts[1]) -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($actual -ne $parts[0]) { throw "SHA256 mismatch: $($parts[1])" }
}
```

Extract a selected ZIP to a staging directory and use Core's actual plugin installation command:

```powershell
Expand-Archive dist\yushuos-task-0.2.0.zip .\staging\task
yushuos --config-root "$env:USERPROFILE\.yushuos" install-plugin --path .\staging\task\plugin
```

Local domain ZIP names look like `yushuos-capture-0.1.0.zip`; the Task ZIP is `yushuos-task-0.2.0.zip`. Choose plugins from `suite-manifest.json`. Do not assume an App proxy has live capabilities or treat pending validation as success.

## 3. Configure and inspect

Core's `install-plugin` command verifies and copies an immutable plugin version; it does not select or enable it. Core configuration uses `plugins.versions` to select a version by plugin ID, `plugins.disabled` / `user_disabled` to disable plugins, and `permissions.grants` for permission grants. Follow the pinned Core configuration template and `docs/en/USAGE.md` for the exact schema. Project configuration may select versions but must not expand global permissions. Then inspect:

```powershell
yushuos --config-root "$env:USERPROFILE\.yushuos" doctor
yushuos --config-root "$env:USERPROFILE\.yushuos" catalog --details
```

Only invoke capabilities whose readiness, dependencies, scopes, and permissions match your setup. Use `query` for reads and `command` for changes. Host-required capabilities need evidence and judgement from the host; Core does not include an AI model. Preview does not mean a write was committed.

## 4. Codex, WorkBuddy, and other AI hosts

Managed Skill/Rule installation for supported hosts is documented in [Core's AI install guide](https://github.com/lan99988/YushuCore-OS/blob/a198be8eb463581b8d18440fb46558c35fe62f5f/docs/en/AI-INSTALL.md). For a host without a dedicated installer, copy the `SKILL.md` from a plugin ZIP into the host's own Skill/Rule directory manually. The Skill is an instruction entry point: it must route requests through the shared Core CLI and catalog, never call another plugin directly or read its private database. This repository does not provide an additional automatic Skill/Rule installer.

## Uninstall and retained data

Core 0.3.1 provides version installation through `install-plugin`; its current CLI has no matching plugin uninstall subcommand. Disabling a plugin or removing its selected version from configuration does not delete private plugin data under `<config-root>/plugin-data/`. Back up configuration and data. Data removal is a separate data-management action, not a side effect of uninstalling a package.
