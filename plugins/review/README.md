# review

独立插件 `yushuos.review`，0.1.0，Core 0.3.1 / Python 3.11+。

解压所选 ZIP，运行 `yushuos install-plugin --path <staging>/plugin`；然后显式选择版本、绑定资源、授予权限。绑定 `review_store`，target 为 `{"store_id":"personal"}`；权限 `review.read/write`。

读取 query，变更 command。host_required 由宿主提供判断与证据。unknown 保留 request_id 并先 Core resume。业务与 proof 同事务，Core 收据另存。停用使用 user_disabled；移除包保留私有数据。

根目录运行 `python -m pytest plugins/review/tests -q`。包锁不是沙箱或签名。详见 [安装](../../docs/INSTALL.md)。

| Capability | Effect | Mode | Required fields |
|---|---|---|---|
| `review.generate` | internal_write | standalone | `period_start`, `period_end`, `sources`, `missing_sources` |
| `review.get` | read_only | standalone | `id` |
| `review.list` | read_only | standalone |  |
| `review.finalize` | internal_write | standalone | `id`, `expected_version` |
| `review.compare` | read_only | standalone | `id`, `other_id` |
| `review.interpret` | internal_write | host_required | `id`, `expected_version`, `reason`, `judgement`, `evidence_refs` |
