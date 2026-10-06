from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from yushuos_task.contracts import render_manifest  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="从权威 Contract Schema 生成 plugin/plugin.yaml")
    parser.add_argument("--check", action="store_true", help="只检查生成文件是否与权威定义一致")
    args = parser.parse_args()
    target = ROOT / "plugin" / "plugin.yaml"
    rendered = render_manifest()
    if args.check:
        if not target.is_file() or target.read_text(encoding="utf-8") != rendered:
            print("plugin/plugin.yaml 与 src/yushuos_task/contracts.py 不一致")
            return 1
        print("plugin/plugin.yaml 与权威 Contract Schema 一致")
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"已生成 {target.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
