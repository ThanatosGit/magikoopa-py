import subprocess
import sys
from pathlib import Path


def run_make(working_dir: Path, code_addr: int, data_addr: int | None = None) -> int:
    """
    Run 'make CODEADDR=0x... [DATAADDR=0x...]' in working_dir.
    Streams output to stdout/stderr, prefixing detected warnings and errors.
    Returns the make exit code.
    """
    args = ['make', f'CODEADDR=0x{code_addr:08X}']
    if data_addr is not None:
        args.append(f'DATAADDR=0x{data_addr:08X}')
    return _run(args, working_dir)


def run_clean(working_dir: Path) -> int:
    """Run 'make clean' in working_dir. Returns the exit code."""
    return _run(['make', 'clean'], working_dir)


def _run(args: list[str], cwd: Path) -> int:
    try:
        proc = subprocess.Popen(
            args,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except FileNotFoundError:
        print(f'[ERROR] Could not launch {args[0]!r}. Is it on your PATH?', file=sys.stderr)
        return 1

    for line in proc.stdout:  # type: ignore[union-attr]
        line = line.rstrip('\n')
        lower = line.lower()
        if 'error' in lower or 'undefined reference to' in lower:
            print(f'[ERROR] {line}')
        elif 'warning' in lower:
            print(f'[WARNING] {line}')
        else:
            print(line)

    return proc.wait()
