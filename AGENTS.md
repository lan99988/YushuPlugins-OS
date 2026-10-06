# YushuOS plugin suite
Use Chinese for reports. Core is pinned at 0.3.1; do not edit Core.
Plugins own their data and communicate through Core. No cross-plugin database reads.
Manifest v3, json-stdio-v2, local_commit_v1 for local writes. Do not invent cross-file ACID.
No credentials, local configurations, private data or real App writes.
Use isolated task worktrees, TDD and tests. Only edit assigned paths.
No placeholder-success handlers. No skipping tests to pass.
Do not commit or publish unless your task specifically assigns that responsibility.
