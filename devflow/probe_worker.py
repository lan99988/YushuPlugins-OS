"""Ordinary, cancellable CLI capability probe; never invoke an agent turn."""
import argparse
import json
import sys
from pathlib import Path

from .capabilities import probe_cli
from .runtime import CommandRunner


def main(argv=None):
    parser = argparse.ArgumentParser(description='CLI capability protocol worker')
    parser.add_argument('repo')
    args = parser.parse_args(argv)
    runner = CommandRunner()
    # The watch's parent CommandRunner owns the process tree. Help/version and
    # app-server must inherit its process group/job instead of escaping it.
    runner.contained = True
    try:
        capabilities = probe_cli(runner, Path(args.repo).resolve(), contained=True)
        print(json.dumps(capabilities, ensure_ascii=False))
        return 0
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
