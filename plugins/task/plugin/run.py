"""Portable Core runner bootstrap for the staged plugin package."""

from pathlib import Path
import sys


PLUGIN_ROOT = Path(__file__).resolve().parent
PLUGIN_SOURCE = PLUGIN_ROOT / "src"
if str(PLUGIN_SOURCE) not in sys.path:
    sys.path.insert(0, str(PLUGIN_SOURCE))

from yushuos_task.plugin import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
