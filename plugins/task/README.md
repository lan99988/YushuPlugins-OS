# YushuOS Task Plugin

`yushuos.task` is the first standalone YushuOS business plugin. Version 0.2.0 provides nine capabilities: create, get, list, update, complete, reopen, soft-delete, cancel, and archive tasks. It is also the reference implementation for Core's Contract v3 `local_commit_v1` plugin protocol. The frozen behavior is in [Implementation Ready](docs/IMPLEMENTATION-READY.zh-CN.md), the public model and storage details are in [Contract and Storage](docs/CONTRACT-AND-STORAGE.md), and future plugin work follows the [Business Plugin Author Guide](docs/BUSINESS-PLUGIN-AUTHOR-GUIDE.en.md).

Cancel changes open tasks to cancelled. Archive sets `archived_at` while retaining status; reopen clears the archive and returns completed/cancelled/archived tasks to open. Lists exclude deleted and archived tasks by default; use `include_deleted` and `include_archived` explicitly. Existing schema v1 databases remain readable and receive a backup before their first write migrates them to v2. See the [0.2.0 specification](docs/TASK-0.2.0.md) for transition and recovery details.

## Compatibility and data boundaries

Task requires Python 3.11+ and YushuOS Core `>=0.3.1,<0.4`. Use the pinned Core source revision `a198be8eb463581b8d18440fb46558c35fe62f5f`; the Core compatibility workflow and its results are linked in [Verification](VERIFICATION.md). The Task ZIP is a locked Core plugin package. The Python wheel is for development and dependency checks; installing the wheel does not install the plugin into Core.

Task business data lives in Core's private plugin data directory, separate from the shared Core operation ledger. Core receipts, locks, and events hold bounded metadata and task references, not task titles or notes. `project_ref` is a business classification, not an authorization boundary. A deleted task is soft-deleted and terminal. Result bodies become logically expired after 180 days; the next real, authorized Task write physically clears expired bodies. Reads, previews, replay, and recovery do not purge them.

## Build and install

From the Task source checkout, install the pinned Core source in a Python virtual environment. Python 3.11 or later and `pip` are sufficient; `uv` is optional.

```powershell
git clone https://github.com/lan99988/YushuCore-OS.git .\yushuos-core
git -C .\yushuos-core checkout a198be8eb463581b8d18440fb46558c35fe62f5f
python -m venv .venv-core
.\.venv-core\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .\yushuos-core
python .\scripts\build_plugin.py --core-path .\yushuos-core
```

On bash, activate the equivalent environment with:

```bash
git clone https://github.com/lan99988/YushuCore-OS.git ./yushuos-core
git -C ./yushuos-core checkout a198be8eb463581b8d18440fb46558c35fe62f5f
python3 -m venv .venv-core
source .venv-core/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ./yushuos-core
python ./scripts/build_plugin.py --core-path ./yushuos-core
```

The ZIP contains only the verified `plugin/` tree. Extract it to a normal directory, then install that directory through Core. `install-plugin` installs an immutable version; it does not select the version or grant permissions.

```powershell
python .\examples\setup_core.py --config-root .\.task-core --store-id personal
Expand-Archive -LiteralPath .\dist\yushuos-task-0.2.0.zip -DestinationPath .\task-unpacked
yushuos --config-root .\.task-core install-plugin --path .\task-unpacked\plugin
yushuos --config-root .\.task-core catalog --details
```

For bash, activate the venv from the build section, then run the equivalent commands:

```bash
python ./examples/setup_core.py --config-root ./.task-core --store-id personal
python -m zipfile -e ./dist/yushuos-task-0.2.0.zip ./task-unpacked
yushuos --config-root ./.task-core install-plugin --path ./task-unpacked/plugin
yushuos --config-root ./.task-core catalog --details
```

The setup helper selects Task 0.2.0, binds `task_store`, and initializes an empty shared ledger without a seed request or Task database. It grants `task.read` and `task.write` by default; `--allow-delete` explicitly adds `task.delete`.

Run the full real-CLI walkthrough against the locked plugin directory. It creates a temporary Core root, installs the plugin, executes create/list/update/complete/reopen/cancel/archive/reopen, checks status, and replays create with the original request. The temporary root is deleted when the walkthrough finishes.

```powershell
python .\examples\demo.py --plugin-path .\plugin --yushuos yushuos
```

The same walkthrough on bash is:

```bash
python ./examples/demo.py --plugin-path ./plugin --yushuos yushuos
```

For one capability in an existing Core root, the helper also supports direct invocations. This example saves the original create request for possible recovery:

```powershell
python .\examples\demo.py --config-root .\.task-core --store-id personal --capability task.create --fields '{"title":"Prepare release"}' --save-request .\create-request.json
python .\examples\demo.py --config-root .\.task-core --store-id personal --capability task.list --fields '{}'
$taskRequestId = (Get-Content .\create-request.json -Raw | ConvertFrom-Json).request_id
yushuos --config-root .\.task-core status --request-id $taskRequestId
yushuos --config-root .\.task-core resume --file .\create-request.json --host-mode execute
```

Reads require `task.read`; creation requires `task.write`; update/complete/reopen require both `task.read` and `task.write`; delete requires `task.read` and `task.delete`. Updates and state transitions require the current `expected_version`; see [Contract and Storage](docs/CONTRACT-AND-STORAGE.md) for exact fields. Core's write preview returns validated planned fields and target with `write_performed: false`, without running the Task plugin or creating a Task ID/database. Execute a write only with Core's explicit execute mode. Recovery must use the original request file and identity, never a new request ID. Saved request files contain input fields, so keep them private.

## Disable and remove installed code

Core does not provide an `uninstall-plugin` command. First add `yushuos.task` to the `user_disabled` list in the relevant `config.yaml`, then inspect `catalog --details` and `doctor` to confirm the disable state. Resolve and verify the exact installed version directory, normally `<config-root>/plugins/yushuos.task/0.2.0`, before removing only that code directory. Keep `<config-root>/plugin-data/yushuos.task` and the shared operation ledger: they contain user data and Core-owned recovery history.

## Release verification

The standalone ZIP, wheel, clean-environment CLI smoke, local test results, and the distinction between Task and Core CI are recorded in [VERIFICATION.md](VERIFICATION.md). Task does not connect external accounts, schedule work, send notifications, or provide a plugin-specific CLI that bypasses Core.

## License

MIT; see [LICENSE](LICENSE).
