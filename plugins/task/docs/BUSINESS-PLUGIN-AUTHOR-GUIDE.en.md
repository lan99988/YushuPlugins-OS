# Business Plugin Author Guide

Use this guide before changing or creating a YushuOS business plugin. Read the approved domain specification and the Core 0.3.1 plugin contract first. For Task-specific behavior, [`SPEC-SOURCE.md`](SPEC-SOURCE.md) is authoritative; this guide defines the reusable implementation process. A completion claim must be traceable to a diff, executable fixture, test output, or install artifact.

## Build sequence

### 1. Fix the domain boundary

List domain facts, capabilities, states, errors, permissions, references, events, retention rules, and explicit exclusions. Core owns orchestration, permission and resource gates, request claims, receipts, outbox, workflows, scheduling, and event delivery. A domain plugin owns its business facts and any external system calls. Mark external writes separately and define how their results are read back. Keep the Core request `project_ref` separate from a domain field with the same name.

**Done when:** every write has an identified facts owner, permission, and recovery readback; no capability silently crosses into another domain.

### 2. Freeze the contract

Freeze capability IDs, intents, effects, input and output schemas, execution mode, permissions, resource scopes, declared events, error codes, preview shape, result shape, and request identity before implementing storage. Treat the schema as the single source of truth and validate the manifest, fixtures, and runtime models against it. Include valid and rejected requests, results, event references, previews, no-ops, and expired results in ordinary JSON fixtures. Fixtures contain synthetic data only.

For Core Contract v3, use `json-stdio-v2`. Parse the envelope through `PluginContext.from_envelope()`. For a local business write, use the Core-bound fingerprint profile and claim API; never recalculate an old request with a different scheme.

**Done when:** every public field has an owner, nullability, default, limit, and rejection behavior; every capability and emitted event is declared.

### 3. Freeze domain storage

Define business facts, idempotency proofs, result snapshots, and recovery metadata separately. Specify each table's key, foreign keys, checks, indexes, transaction boundary, migration path, unknown-version behavior, delete behavior, and retention period. One storage owner controls shared migrations; other workstreams submit requested changes.

Use Task's schema as an example of a committed local fact and proof, not as a universal business schema. A new domain must justify its own tables against its own invariants.

**Done when:** concurrent writes have a deterministic outcome, migrations are transactional, unknown schema versions fail closed, and the plugin has no second Core operation ledger.

### 4. Prove one golden write path

Implement one representative write before adding the rest. The order is: Core gates → claim the original request ID → commit the business fact, proof, result snapshot, and event intents in one domain transaction → write the Core receipt and outbox → update any private recovery marker → return the result. Use the real Core runner, not only direct domain calls.

For `local_commit_v1`, exercise all three actions:

- `invoke` performs the first authorized write and stores its proof in the same domain transaction.
- `recover` runs only after explicit `resume --host-mode execute`. It verifies the original provider and proof, then calls `StateStore.reconcile_confirmed(request, result, context, emissions)`. If no proof exists, keep the result unknown and do not repeat the business write.
- `replay` follows current Core capability, permission, resource, and provider checks. It reads the original saved result without a business write or any storage mutation.

**Done when:** the real runner produces one business fact, one original result snapshot, a Core receipt and outbox event, and then recovers or replays that same result after a process crash without a duplicate write.

### 5. Add the remaining capabilities

Only after the golden write path passes, implement the other writes and queries. Require an explicit expected version for every mutation. Check the version before deciding whether a request is a no-op. Keep mutable-field allowlists, state transitions, deleted-record behavior, and time interpretation explicit. Queries and previews use read-only paths and never claim a write request or create write-side effects.

**Done when:** each write covers success, stale version, no-op, permission denial, and out-of-scope resource; `changed=false` does not change timestamps or versions and creates no event intent. Query tests cover inclusion and exclusion rules, and cursors bind a fixed ordering position to their filters.

### 6. Verify recovery, authorization, and privacy

Inject process failures before the domain commit, after the domain commit but before the Core ledger commit, after the Core ledger commit but before the response, and after event import but before its imported marker. Cover provider drift, missing proofs, conflicting human resolutions, result expiration, revoked permissions, out-of-scope stores, concurrent version conflicts, and unknown database versions.

Search the Core ledger, outbox, automation history, logs, exception paths, and test artifacts for business text. Core stores request identity, operation status, provider provenance, and safe resource references; the business body stays in the domain store. Use isolated temporary directories and no real external accounts.

For `local_commit_v1`, replay is read-only. A crash after the Core receipt/outbox commit but before a private Task marker can leave that marker pending; the Core ledger is authoritative for the receipt and event. Do not write or purge from replay to repair a marker.

Task result bodies expire logically 180 days after commit. After expiry, query, preview, replay, and recovery do not return the body. The next genuine authorized write physically clears expired result bodies; if no later write occurs, expired bytes may remain in the database file. Expiry does not turn a committed business operation into a failure. Return `succeeded` with `operation_status: committed`, `result_state: expired`, `code: task.result_expired`, `task_id`, and `original_request_id`, with no `task` or `changed` field.

**Done when:** every crash window has a stable expected result; missing proof never triggers a blind retry; revoked access cannot recover or replay through a Core path; logs and Core-owned storage contain no business body.

### 7. Package and install the plugin

Keep the distributable package under the repository's separate `plugin/` directory. Run Core 0.3.1 `lock-plugin` on that directory before creating a ZIP. Exclude `.git`, tests, temporary databases, wheels, development dependencies, and machine-specific paths. For ZIP installation, validate and extract the archive safely, then pass the extracted plugin directory to Core 0.3.1 `install-plugin`.

Core 0.3.1 has no `uninstall-plugin` command. Disable the plugin in Core configuration first; in an isolated test configuration, remove only the exact installed `plugins/<id>/<version>` code directory resolved from the Core layout. Keep plugin data and the shared ledger. Verify catalog and `doctor` after removal.

**Done when:** the ZIP and lock file are reviewable, an actual runner starts on each supported OS, installs do not overwrite a version, and isolated removal touches only the selected plugin code version.

### 8. Publish evidence with the docs

Document the actual capabilities, parameters, permissions, results, and install flow. Verify every command against Core 0.3.1 `--help` or an actual run before documenting it. The validation report records the commit or source revision, OS, Python version, exact command, result, and remaining work. Label anything not executed as “not run.”

**Done when:** a clean temporary Core root can complete the documented flow and every pass/fail statement points to run evidence.

## Core 0.3.1 local-commit API

The exact Core interface is documented in `yushuos-core 0.3.1` at `docs/en/PLUGINS.md` and `docs/zh-CN/PLUGINS.md`. The public profile APIs used by a v3 plugin are:

- `yushuos_sdk.canonical.fingerprint_for_scheme(request, scheme)` supports `legacy-v1` and `jcs-operation-v1`. The latter hashes RFC 8785 JCS for `{capability, fields, target}`. Core binds `intent` and the top-level `project_ref` separately.
- `StateStore.claim(request)` uses the fingerprint scheme already bound by Core.
- `StateStore.operation_context(request_id) -> dict | None` returns safe provider, project, intent, trace, event declaration, profile, and scheme metadata; it does not return paths, resource bindings, or request bodies.
- `StateStore.record_with_events(result, context, emissions)` commits the normal receipt and allow-listed outbox events in one Core-ledger transaction.
- `StateStore.reconcile_confirmed(request, result, context, emissions) -> bool` appends a recovery confirmation and its outbox events atomically. It preserves the original uncertain receipt and rejects abandoned or conflicting human resolutions.

Declare top-level `operation_support: local_commit_v1` only on a contract v3 manifest. It enables `invoke`, `replay`, and `recover` for `internal_write` capabilities; `read_only` capabilities may coexist, while `external_write` is not supported by this profile. Profile context uses `schema_version: 2` and adds `intent`, `operation_support`, and `fingerprint_scheme`. Core preserves the v1 context and legacy `Request.fingerprint()` behavior for plugins without this profile.

## Authoritative references

- Task behavior and frozen retention rules: [`SPEC-SOURCE.md`](SPEC-SOURCE.md).
- Task's authoritative schema, adapter, domain model, and storage implementation: [`contracts.py`](../src/yushuos_task/contracts.py), [`plugin.py`](../src/yushuos_task/plugin.py), [`domain.py`](../src/yushuos_task/domain.py), and [`storage.py`](../src/yushuos_task/storage.py).
- The Core 0.3.1 `local_commit_v1` implementation contract: `yushuos-core 0.3.1/docs/en/PLUGINS.md` and `yushuos-core 0.3.1/docs/zh-CN/PLUGINS.md`.
