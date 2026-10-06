# YushuOS Plugins

YushuOS Plugins is a local-first suite of independently installed personal-management plugins. The repository defines 17 local domain plugins, the Task 0.2.0 plugin, and independent Feishu/IMA connector adapters. Plugins use the Core manifest, capability catalog, and JSON invocation entry point. Core is pinned to [YushuCore-OS commit `a198be8eb463581b8d18440fb46558c35fe62f5f`](https://github.com/lan99988/YushuCore-OS/tree/a198be8eb463581b8d18440fb46558c35fe62f5f).

## Scope

| Plugin | slug / plugin ID | Capability summary from the current definitions |
|---|---|---|
| Capture | `capture` / `yushuos.capture` | Capture CRUD, classification, processing, archive/restore |
| Idea | `idea` / `yushuos.idea` | Idea CRUD, conversion and discard |
| Bug | `bug` / `yushuos.bug` | Issue CRUD, severity, resolution/reopen/close and task link |
| Finance | `finance` / `yushuos.finance` | Statements, monthly snapshots, close and summary |
| Relationship | `relationship` / `yushuos.relationship` | People, interactions and recent interaction |
| Habit | `habit` / `yushuos.habit` | Habit CRUD, check-in/undo, pause/resume and history |
| Body | `body` / `yushuos.body` | State, sleep and training records; recovery summary |
| Life admin | `lifeadmin` / `yushuos.lifeadmin` | Life-admin CRUD, completion and renewal |
| Decision | `decision` / `yushuos.decision` | Decision CRUD, choice, outcome and host review |
| Planner | `planner` / `yushuos.planner` | Plans, deterministic generation/preview, apply and replan |
| Deep work | `deepwork` / `yushuos.deepwork` | Focus session start/pause/resume/finish/cancel/interrupt |
| Competition | `competition` / `yushuos.competition` | Competition CRUD, milestone/progress and host review |
| Knowledge | `knowledge` / `yushuos.knowledge` | Knowledge items, concepts, links, insights and search |
| Creation | `creation` / `yushuos.creation` | Creation items, stage changes and publication records |
| Review | `review` / `yushuos.review` | Structured review generate/read/finalize/compare and host interpretation |
| Personal model | `personal_model` / `yushuos.personal_model` | Hypothesis CRUD, evidence attachment and host evaluation |
| Cognition | `cognition` / `yushuos.cognition` | Pattern CRUD, analysis and review (host supplies judgement) |
| Task | `task` / `yushuos.task` | `task.create/get/list/update/complete/reopen/delete/cancel/archive` |
| Feishu | `feishu` / `yushuos.feishu` | Proxy adapter in development; no connected account or verified live access implied |
| IMA | `ima` / `yushuos.ima` | Proxy adapter in development; no connected account or verified live access implied |

The 17 local domain plugins define 155 capabilities. `business_runtime/contracts.py`, the Task contract, and each App adapter manifest are authoritative. The personal-model slug is `personal_model` and its plugin ID is `yushuos.personal_model`; its capabilities use the `personal-model.*` prefix because Core capability prefixes cannot contain underscores.

## Install

The suite is not published on PyPI. Install the pinned Core first, then choose plugin ZIPs from a generated suite. The build outputs 20 independent ZIPs under `dist/`, plus `SHA256SUMS` and `suite-manifest.json`. See the [installation guide](docs/INSTALL.en.md). The application is still in development; treat ZIPs as installable release artifacts only when the build runs and validates successfully on the current checkout.

```powershell
git clone https://github.com/lan99988/YushuCore-OS.git yushuos-core
python -m pip install -e .\yushuos-core
```

The [installation guide](docs/INSTALL.en.md) pins the exact Core commit and shows suite build steps. Installing a plugin copies and verifies a package version; the operator must still configure Core's version selection, host permissions, and resource bindings. Uninstalling a plugin package preserves its private data.

## Execution boundaries

- Core catalogs and routes capabilities, validates schemas and permissions, and maintains its receipt/outbox. The AI host interprets requests and supplies host judgement. A `host_required` capability does not call an AI built into Core.
- Use `query` for reads and `command` for mutations. Preview, authorized execution, and live App writes are separate states. Installing a plugin grants no business permission and performs no App write.
- Each domain plugin owns a private SQLite database. Plugins never read another plugin's database. Cross-domain reads and collaboration go through Core via `integration/flows.py`.
- A local write commits business data and its proof in one transaction in that plugin's SQLite database. Core SDK receipts/outbox use another database; there is no cross-database ACID transaction.
- Result statuses are `succeeded`, `failed`, `unavailable`, `unknown`, and `preview`. Standard top-level fields are `status`, `request_id`, `message`, `resource`, `data`, and `error`.
- Keep the original `request_id`. For `unknown`, query or recover through Core before taking further action; never retry with a new ID. See the [AI invocation guide](docs/AI-INSTALL.en.md).
- Skills/Rules are host entry points. Manual Codex and WorkBuddy integration is documented in [Core's AI install guide](https://github.com/lan99988/YushuCore-OS/blob/a198be8eb463581b8d18440fb46558c35fe62f5f/docs/en/AI-INSTALL.md); this repository does not claim an automatic host installer.

## Development and CI

```bash
python -m pip install -e ".[dev]"
python -m pytest tests -q
python -m pytest plugins/task/tests -q
python -m scripts.build_suite --output dist
```

The full CI matrix runs on Linux, Windows, and macOS with the Core Git SHA pinned. It runs Ruff, root and Task tests, builds the suite, and checks package locks/manifests. App live-read/live-write has not been verified; do not report an unrun CI as passing. See the [plugin standard](docs/PLUGIN-STANDARD.md), [capability specifications](docs/PLUGIN-SPECS.md), and [release guide](docs/RELEASE.md).
