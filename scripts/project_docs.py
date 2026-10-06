"""Generate mechanical per-provider docs and fixtures from reviewed contracts."""
import json
from pathlib import Path
from business_runtime.contracts import REGISTRY
from tests.test_domain_lifecycles import SAMPLES

ROOT = Path(__file__).resolve().parents[1]


def generate():
    sections = ["# 能力规格 / Capability specifications\n",
                "契约以各目录 JSON 和锁定 manifest 为准。此表不表示已完成 live App 验收。\n"]
    for slug, caps in REGISTRY.items():
        folder = ROOT / "plugins" / slug
        test = folder / "tests" / ("test_" + slug + "_installed.py")
        test.parent.mkdir(parents=True, exist_ok=True)
        test.write_text('"""Independent provider installation regression."""\n'
                        'from tests import test_domain_lifecycles as checks\n\n'
                        f'def test_{slug}_installed(tmp_path):\n'
                        f'    checks.test_independent_core_installed_domain_roundtrip_and_invalid_input(tmp_path, {slug!r})\n',
                        encoding="utf-8", newline="\n")
        cap, fields, kind = SAMPLES[slug]
        fixture = {"version": 1, "plugin": "yushuos." + slug, "seed": {"capability": cap, "intent": "command",
                    "fields": fields, "target": {"store_id": "personal"}}, "expected_resource_kind": kind}
        (ROOT / "contracts" / slug / "fixtures.json").write_text(json.dumps(fixture, indent=2, ensure_ascii=False) + "\n",
                                                                encoding="utf-8", newline="\n")
        rows = ["| Capability | Effect | Mode | Required fields |", "|---|---|---|---|"]
        for name, definition in caps.items():
            rows.append(f"| `{name}` | {definition['effect']} | {definition['execution_mode']} | "
                        + ", ".join(f"`{x}`" for x in definition["inputs"].get("required", [])) + " |")
        guide = f"# {slug}\n\n独立插件 `yushuos.{slug}`，0.1.0，Core 0.3.1 / Python 3.11+。\n\n"
        guide += "解压所选 ZIP，运行 `yushuos install-plugin --path <staging>/plugin`；然后显式选择版本、绑定资源、授予权限。"
        guide += f"绑定 `{slug}_store`，target 为 `{{\"store_id\":\"personal\"}}`；权限 `{slug}.read/write`。\n\n"
        guide += "读取 query，变更 command。host_required 由宿主提供判断与证据。unknown 保留 request_id 并先 Core resume。"
        guide += "业务与 proof 同事务，Core 收据另存。停用使用 user_disabled；移除包保留私有数据。\n\n"
        guide += f"根目录运行 `python -m pytest plugins/{slug}/tests -q`。包锁不是沙箱或签名。详见 [安装](../../docs/INSTALL.md)。\n\n"
        (folder / "README.md").write_text(guide + "\n".join(rows) + "\n", encoding="utf-8", newline="\n")
        sections += [f"\n## {slug}\n", "\n".join(rows) + "\n"]
    for slug in ("task", "feishu", "ima"):
        values = json.loads((ROOT / "contracts" / slug / "capabilities.json").read_text(encoding="utf-8"))
        sections += [f"\n## {slug}\n", "| Capability | Effect | Mode |\n|---|---|---|\n"]
        for cap in values["capabilities"]:
            sections.append(f"| `{cap['name']}` | {cap['effect']} | {cap.get('execution_mode', 'standalone')} |\n")
    (ROOT / "docs/PLUGIN-SPECS.md").write_text("\n".join(sections), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    generate()
