# YushuOS Plugins

YushuOS Plugins is a local-first suite of independently installed personal-management plugins. The repository contains 17 domain plugins, Task 0.2.0, and independent Feishu/IMA connector adapters. Plugins use Core manifests, the capability catalog, and one JSON invocation interface. Core is pinned to [YushuOS Core commit a198be8eb463581b8d18440fb46558c35fe62f5f](https://github.com/lan99988/YushuCore-OS/tree/a198be8eb463581b8d18440fb46558c35fe62f5f).

## Scope

| Plugin | slug / plugin ID | Capability summary from current definitions |
|---|---|---|
| Capture | <code>capture</code> / <code>yushuos.capture</code> | Capture CRUD, classification, processing, archive/restore |
| Idea | <code>idea</code> / <code>yushuos.idea</code> | Idea CRUD, conversion and discard |
| Bug | <code>bug</code> / <code>yushuos.bug</code> | Issue CRUD, severity, resolution/reopen/close and task links |
| Finance | <code>finance</code> / <code>yushuos.finance</code> | Transactions, monthly snapshots, close and summary |
| Relationship | <code>relationship</code> / <code>yushuos.relationship</code> | People, interactions and recent interaction |
| Habit | <code>habit</code> / <code>yushuos.habit</code> | Habit CRUD, check-in/undo, pause/resume and history |
| Body | <code>body</code> / <code>yushuos.body</code> | State, sleep and training records; recovery summary |
| Life admin | <code>lifeadmin</code> / <code>yushuos.lifeadmin</code> | Life-admin CRUD, completion and renewal |
| Decision | <code>decision</code> / <code>yushuos.decision</code> | Decision CRUD, choice, outcome and host review |
| Planner | <code>planner</code> / <code>yushuos.planner</code> | Plans, deterministic generation/preview, apply and replan |
| Deep work | <code>deepwork</code> / <code>yushuos.deepwork</code> | Focus session start/pause/resume/finish/cancel/interrupt |
| Competition | <code>competition</code> / <code>yushuos.competition</code> | Competition CRUD, milestone/progress and host review |
| Knowledge | <code>knowledge</code> / <code>yushuos.knowledge</code> | Knowledge items, concepts, links, insights and search |
| Creation | <code>creation</code> / <code>yushuos.creation</code> | Creation items, stage changes and publication records |
| Review | <code>review</code> / <code>yushuos.review</code> | Structured review generate/read/finalize/compare and host interpretation |
| Personal model | <code>personal_model</code> / <code>yushuos.personal_model</code> | Hypothesis CRUD, evidence attachment and host evaluation |
| Cognition | <code>cognition</code> / <code>yushuos.cognition</code> | Pattern CRUD and analysis/review (host supplies judgement) |
| Task | <code>task</code> / <code>yushuos.task</code> | <code>task.create/get/list/update/complete/reopen/delete/cancel/archive</code> |
| Feishu | <code>feishu</code> / <code>yushuos.feishu</code> | Calendar/task/message adapter; mock integration tested, live access unverified |
| IMA | <code>ima</code> / <code>yushuos.ima</code> | Note/notebook/knowledge-base reads and search, note creation/appending; mock integration tested, live access unverified |

The 17 local domain plugins define 155 capabilities. <code>business_runtime/contracts.py</code>, the Task contract, and each App adapter manifest are authoritative. The personal-model slug is <code>personal_model</code> and its plugin ID is <code>yushuos.personal_model</code>; its capabilities use the <code>personal-model.*</code> prefix to satisfy Core's capability-prefix parser.

For a first run, try Capture for local capture and search, Task for creating and completing a to-do, or Planner to preview a plan. Review the preview before granting and executing a write.

## Install

The suite is not published on PyPI. These commands install the pinned Core source and Suite development dependencies into the same virtual environment. The [installation guide](docs/INSTALL.en.md) continues with Core setup, ZIP selection/installation, private configuration, and recovery.

~~~powershell
git clone https://github.com/lan99988/YushuCore-OS.git yushuos-core
git -C yushuos-core checkout a198be8eb463581b8d18440fb46558c35fe62f5f
git clone https://github.com/lan99988/YushuPlugins-OS.git yushuos-plugins
Set-Location yushuos-plugins
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e "..\yushuos-core" -e ".[dev]"
~~~

~~~bash
git clone https://github.com/lan99988/YushuCore-OS.git yushuos-core
git -C yushuos-core checkout a198be8eb463581b8d18440fb46558c35fe62f5f
git clone https://github.com/lan99988/YushuPlugins-OS.git yushuos-plugins
cd yushuos-plugins
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ../yushuos-core -e '.[dev]'
~~~

The current MVP's 20 mock packages have been built and independently installed. The five-platform CI matrix is still in progress, so it is not yet a green result. Feishu/IMA live App reads and writes remain <code>not_verified</code>. A package installation copies and verifies an immutable plugin version; Core still needs explicit version selection, resource bindings, and grants. Core 0.3.1 has no <code>init</code>, <code>enable</code>, or <code>uninstall-plugin</code> command. Removing a package does not remove its private data.

## Execution boundaries

- Core catalogs and routes capabilities, validates schemas and permissions, and maintains its receipt/outbox. The host interprets language and supplies host judgement. A <code>host_required</code> capability does not call an AI built into Core.
- Use <code>query</code> for reads and <code>command</code> for changes. Preview, authorized execution, and live App writes are separate states. Installing a plugin grants no business permission and performs no App write.
- Each domain plugin owns a private SQLite database. Plugins do not read one another's databases. Cross-domain reads and collaboration go through Core via <code>integration/flows.py</code>.
- A local write commits business data and proof in the plugin's SQLite transaction. Core SDK receipts/outbox use a separate database; there is no cross-database ACID transaction.
- Results include <code>succeeded</code>, <code>failed</code>, <code>unavailable</code>, <code>unknown</code>, and <code>preview</code>. Only <code>succeeded</code> confirms success. A preview has not committed; <code>unknown</code> requires verification.
- Keep the original <code>request_id</code>. Recover a local plugin's unknown result from the Core receipt and plugin proof. An App write with <code>unknown</code> requires checking the adapter's private receipt; Core 0.3.1's generic <code>resume</code> cannot confirm an App result. See the [AI invocation guide](docs/AI-INSTALL.md) and [App connector guide](docs/APP-CONNECTORS.md).
- Use Core's <code>install-host</code> for managed Codex Skill and WorkBuddy Rule entry points. It does not install business plugins, connect an App, or grant permissions. See [Core's AI installation guide](https://github.com/lan99988/YushuCore-OS/blob/a198be8eb463581b8d18440fb46558c35fe62f5f/docs/en/AI-INSTALL.md).

## Development and CI

~~~bash
python -m pip install -e ".[dev]"
python -m pytest tests -q
python -m pytest plugins/task/tests -q
python -m scripts.build_suite --output dist
~~~

The five CI jobs cover Linux with Python 3.11/3.12/3.13, Windows with Python 3.12, and macOS with Python 3.12, using the pinned Core SHA. They are still in progress; do not report them as passed until all results are confirmed. App live reads and writes are unverified. See the [plugin standard](docs/PLUGIN-STANDARD.md), [capability specifications](docs/PLUGIN-SPECS.md), and [release guide](docs/RELEASE.md).
