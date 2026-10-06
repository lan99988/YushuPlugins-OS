from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "yushuos_task"
DESTINATION = ROOT / "plugin" / "src" / "yushuos_task"
EXCLUDED = {"__pycache__", ".pytest_cache"}


def source_files(root: Path) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and not (set(path.relative_to(root).parts) & EXCLUDED) and path.suffix != ".pyc"
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="把 Task 权威源码复制到 plugin/src/yushuos_task")
    parser.add_argument("--check", action="store_true", help="只检查发行源码副本是否一致")
    args = parser.parse_args()
    expected = source_files(SOURCE)
    if args.check:
        actual = source_files(DESTINATION) if DESTINATION.exists() else set()
        if actual != expected:
            print(f"发行源码不一致；缺失={sorted(expected - actual)}，多余={sorted(actual - expected)}")
            return 1
        for relative in sorted(expected):
            if (SOURCE / Path(relative)).read_bytes() != (DESTINATION / Path(relative)).read_bytes():
                print(f"发行源码内容不一致：{relative}")
                return 1
        print("plugin/src/yushuos_task 与 src/yushuos_task 完全一致")
        return 0

    DESTINATION.mkdir(parents=True, exist_ok=True)
    for relative in sorted(expected):
        target = DESTINATION / Path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SOURCE / Path(relative), target)
    actual = source_files(DESTINATION)
    if actual != expected:
        print(f"发行目录存在未跟踪文件；多余={sorted(actual - expected)}")
        return 1
    print("已同步 plugin/src/yushuos_task")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
