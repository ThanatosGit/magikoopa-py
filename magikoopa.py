#!/usr/bin/env python3
"""
Magikoopa — 3DS game code injection/patching tool (CLI)

Usage:
  python magikoopa.py insert [working_dir]
  python magikoopa.py clean  [working_dir]

  working_dir defaults to the current directory if not given.
"""

import argparse
import sys
from pathlib import Path


def cmd_insert(args: argparse.Namespace) -> int:
    from patchmaker import make_insert
    work_dir = Path(args.working_dir).resolve()
    success = make_insert(work_dir)
    return 0 if success else 1


def cmd_clean(args: argparse.Namespace) -> int:
    from patchmaker import make_clean
    work_dir = Path(args.working_dir).resolve()
    success = make_clean(work_dir)
    return 0 if success else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='magikoopa',
        description='3DS game code injection and patching tool',
    )
    subparsers = parser.add_subparsers(dest='command', metavar='command')
    subparsers.required = True

    # insert
    p_insert = subparsers.add_parser(
        'insert',
        help='Compile custom code and inject it into the game binary',
    )
    p_insert.add_argument(
        'working_dir',
        nargs='?',
        default='.',
        help='Patch project directory (default: current directory)',
    )
    p_insert.set_defaults(func=cmd_insert)

    # clean
    p_clean = subparsers.add_parser(
        'clean',
        help='Clean build artifacts',
    )
    p_clean.add_argument(
        'working_dir',
        nargs='?',
        default='.',
        help='Patch project directory (default: current directory)',
    )
    p_clean.set_defaults(func=cmd_clean)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print('\nInterrupted.', file=sys.stderr)
        return 1
    except Exception as exc:
        print(f'Fatal error: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
