"""
Core orchestrator — equivalent to patchmaker.cpp.

NOTE from original source:
  TODO: Do not put loader data segment after newcode.
  Currently a non-zero loader data segment size will break RWX permissions
  of newcode hooks and exheader sizes.
"""

import configparser
import shutil
import sys
from pathlib import Path

from compiler import run_make
from exheader import Exheader
from hooklinker import HookLinker
from sym_table import SymTable

BASE_ADDR = 0x00100000

REQUIRED_FILES = [
    'Makefile',
    'loader/Makefile',
    'code.bin',
    'exheader.bin',
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_directory(work_dir: Path) -> list[str]:
    """Return a list of missing required files (empty = valid)."""
    return [f for f in REQUIRED_FILES if not (work_dir / f).exists()]


def make_insert(work_dir: Path) -> bool:
    """Full compile-and-inject pipeline. Returns True on success."""
    missing = validate_directory(work_dir)
    if missing:
        print('The working directory is invalid. The following files are missing:', file=sys.stderr)
        for f in missing:
            print(f'  - /{f}', file=sys.stderr)
        return False

    _check_backup(work_dir)
    _restore_from_backup(work_dir)

    # ------------------------------------------------------------------
    # Compute memory layout from the backup exheader
    # ------------------------------------------------------------------
    exh_bak = Exheader(work_dir / 'bak' / 'exheader.bin')
    loader_offset = _align_up(exh_bak.text_size + BASE_ADDR, 0x10)
    loader_max_size = exh_bak.rodata_address - loader_offset
    new_code_offset = (
        exh_bak.data_address
        + (exh_bak.data_phys_size << 12)
        + _align_up(exh_bak.bss_size, 0x1000)
    )

    print(f'Game Name:           {exh_bak.title}')
    print(f'Loader Offset:       {loader_offset:08X}')
    print(f'Loader maximum Size: {loader_max_size:08X}')
    print(f'New Code Offset:     {new_code_offset:08X}')

    # ------------------------------------------------------------------
    # Phase 1: Compile newcode
    # ------------------------------------------------------------------
    print('\nRunning Make...')
    rc = run_make(work_dir, new_code_offset)
    if rc != 0:
        print('Compilation Failed', file=sys.stderr)
        return False

    # ------------------------------------------------------------------
    # Phase 2: Load newcode symbols and hooks
    # ------------------------------------------------------------------
    sym_table = SymTable()
    sym_table.load(work_dir / 'newcode.sym')

    hook_linker = HookLinker()
    hook_linker.sym_table = sym_table
    hook_linker.load_hooks(work_dir / 'source')
    hook_linker.load_hooks(work_dir / 'hooks')

    # ------------------------------------------------------------------
    # Phase 3: Generate newcodeinfo.h for the loader
    # ------------------------------------------------------------------
    newcode_bin = work_dir / 'newcode.bin'
    newcode_size = newcode_bin.stat().st_size
    aligned_newcode_size = _align_up(newcode_size, 0x10) + hook_linker.extra_data_size()
    loader_data_offset = new_code_offset + _align_up(newcode_size, 0x10)

    header_text = (
        '#ifndef NEWCODEINFO_H\n'
        '#define NEWCODEINFO_H\n'
        '\n'
        f'#define NEWCODE_OFFSET 0x{new_code_offset:08X}\n'
        f'#define NEWCODE_SIZE 0x{aligned_newcode_size:08X}\n'
        '\n'
        '#endif // NEWCODEINFO_H\n'
    )
    (work_dir / 'loader' / 'source' / 'newcodeinfo.h').write_text(header_text, encoding='utf-8')
    print(f'Hook size: {hook_linker.extra_data_size():08X}')

    # ------------------------------------------------------------------
    # Phase 4: Compile loader
    # ------------------------------------------------------------------
    print('\nRunning Make (Loader)...')
    rc = run_make(work_dir / 'loader', loader_offset, loader_data_offset)
    if rc != 0:
        print('Compilation Failed (Loader)', file=sys.stderr)
        return False

    # ------------------------------------------------------------------
    # Phase 5: Load loader symbols and hooks
    # ------------------------------------------------------------------
    loader_sym_table = SymTable()
    loader_sym_table.load(work_dir / 'loader' / 'loader.sym')

    loader_hook_linker = HookLinker()
    loader_hook_linker.sym_table = loader_sym_table
    loader_hook_linker.load_hooks(work_dir / 'loader' / 'source')
    loader_hook_linker.load_hooks(work_dir / 'loader' / 'hooks')

    # Validate loader size
    loader_text_end_sym = loader_sym_table.get('__text_end')
    loader_text_start_sym = loader_sym_table.get('__text_start')
    if loader_text_end_sym == 0xFFFFFFFF or loader_text_start_sym == 0xFFFFFFFF:
        print('Parsing Loader sections failed', file=sys.stderr)
        return False

    loader_insert_size = (
        loader_text_end_sym - loader_text_start_sym + loader_hook_linker.extra_data_size()
    )
    if loader_insert_size > loader_max_size:
        print(f'Loader text size ({loader_insert_size:#x}) exceeds maximum ({loader_max_size:#x})',
              file=sys.stderr)
        return False

    # ------------------------------------------------------------------
    # Phase 6: Binary insertion
    # ------------------------------------------------------------------
    print('\nInserting...')
    ok = _insert(
        work_dir,
        loader_offset,
        new_code_offset,
        loader_data_offset,
        loader_sym_table,
        loader_hook_linker,
        hook_linker,
    )
    if not ok:
        return False

    # ------------------------------------------------------------------
    # Phase 7: Fix exheader
    # ------------------------------------------------------------------
    print('Fixing Exheader...')
    loader_data_end_sym = loader_sym_table.get('__data_end')
    new_code_size = loader_data_end_sym + hook_linker.extra_data_size() - new_code_offset
    new_text_code_size = loader_text_end_sym + loader_hook_linker.extra_data_size() - 0x100000
    exh = Exheader(work_dir / 'exheader.bin')
    if not exh.apply_patch(new_code_size, new_text_code_size):
        return False
    exh.save()

    # ------------------------------------------------------------------
    # Phase 8: Post-hook (copy output files)
    # ------------------------------------------------------------------
    _post_hook(work_dir)

    print('\nAll done')
    return True


def make_clean(work_dir: Path) -> bool:
    """Clean build artifacts in both the project root and loader directories."""
    missing = validate_directory(work_dir)
    if missing:
        print('The working directory is invalid. The following files are missing:', file=sys.stderr)
        for f in missing:
            print(f'  - /{f}', file=sys.stderr)
        return False

    from compiler import run_clean
    print('Making Clean...')
    rc1 = run_clean(work_dir)
    rc2 = run_clean(work_dir / 'loader')
    return rc1 == 0 and rc2 == 0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _insert(
    work_dir: Path,
    loader_offset: int,
    new_code_offset: int,
    loader_data_offset: int,
    loader_sym_table: SymTable,
    loader_hook_linker: HookLinker,
    hook_linker: HookLinker,
) -> bool:
    loader_text_end  = loader_sym_table.get('__text_end')
    loader_data_start = loader_sym_table.get('__data_start')
    loader_data_end   = loader_sym_table.get('__data_end')

    for sym, name in [
        (loader_text_end,  '__text_end'),
        (loader_data_start, '__data_start'),
        (loader_data_end,   '__data_end'),
    ]:
        if sym == 0xFFFFFFFF:
            print(f'Parsing Loader sections failed: symbol {name!r} not found', file=sys.stderr)
            return False

    code_data     = bytearray((work_dir / 'code.bin').read_bytes())
    loader_data_b = (work_dir / 'loader' / 'loader.bin').read_bytes()
    newcode_data  = (work_dir / 'newcode.bin').read_bytes()

    old_code_size = len(code_data)
    hook_data_end = loader_data_end + hook_linker.extra_data_size()
    new_size = _align_up(hook_data_end, 0x1000) - BASE_ADDR

    # Resize (extend) code.bin
    if new_size > len(code_data):
        code_data.extend(b'\x00' * (new_size - len(code_data)))

    # Insert Loader Text
    loader_text_size = loader_text_end - loader_offset
    lt_off = loader_offset - BASE_ADDR
    code_data[lt_off:lt_off + loader_text_size] = loader_data_b[:loader_text_size]

    # Clear BSS section (old code end → new code start)
    bss_start = old_code_size
    bss_end   = new_code_offset - BASE_ADDR
    if bss_end > bss_start:
        code_data[bss_start:bss_end] = b'\x00' * (bss_end - bss_start)

    # Insert NewCode
    nc_off = new_code_offset - BASE_ADDR
    code_data[nc_off:nc_off + len(newcode_data)] = newcode_data

    # Clear padding between newcode and loader data
    pad_start = nc_off + len(newcode_data)
    pad_end   = loader_data_start - BASE_ADDR
    if pad_end > pad_start:
        code_data[pad_start:pad_end] = b'\x00' * (pad_end - pad_start)

    # Insert Loader Data
    ld_size     = loader_data_end - loader_data_start
    ld_file_off = loader_data_start - loader_offset
    ld_code_off = loader_data_start - BASE_ADDR
    code_data[ld_code_off:ld_code_off + ld_size] = loader_data_b[ld_file_off:ld_file_off + ld_size]

    # Pad out the rest of the page
    next_page = _align_up(ld_code_off + ld_size, 0x1000)
    code_data[ld_code_off + ld_size:next_page] = b'\x00' * (next_page - ld_code_off - ld_size)

    # Debug info (mirrors the qDebug output in the C++ version)
    print(f'  Loader Text Start: {loader_offset:08X}')
    print(f'  Loader Text End:   {loader_text_end:08X}')
    print(f'  Loader Text Size:  {loader_text_size:08X}')
    print(f'  Loader Data Start: {loader_data_start:08X}')
    print(f'  Loader Data End:   {loader_data_end:08X}')
    print(f'  Loader Data Size:  {ld_size:08X}')
    print(f'  New Code Start:    {new_code_offset:08X}')
    print(f'  New Code End:      {new_code_offset + len(newcode_data):08X}')
    print(f'  New Code Size:     {len(newcode_data):08X}')

    # Apply hooks
    hook_linker.set_extra_data_ptr(loader_data_end)
    loader_hook_linker.set_extra_data_ptr(loader_text_end)

    hook_linker.apply_to(code_data)
    loader_hook_linker.apply_to(code_data)

    # Zero rest of last page after hook data
    hde_off = hook_data_end - BASE_ADDR
    code_data[hde_off:] = b'\x00' * (len(code_data) - hde_off)

    (work_dir / 'code.bin').write_bytes(code_data)
    return True


def _check_backup(work_dir: Path) -> None:
    bak_dir = work_dir / 'bak'
    bak_dir.mkdir(exist_ok=True)
    for fname in ('code.bin', 'exheader.bin'):
        src = work_dir / fname
        dst = bak_dir / fname
        if not dst.exists() and src.exists():
            shutil.copy2(src, dst)


def _restore_from_backup(work_dir: Path) -> None:
    bak_dir = work_dir / 'bak'
    for fname in ('code.bin', 'exheader.bin'):
        src = bak_dir / fname
        dst = work_dir / fname
        if src.exists():
            if dst.exists():
                dst.unlink()
            shutil.copy2(src, dst)


def _post_hook(work_dir: Path) -> None:
    proj_name = work_dir.name
    user_file = work_dir / f'{proj_name}.mkproj.user'
    if not user_file.exists():
        return

    cfg = configparser.ConfigParser()
    cfg.read(user_file, encoding='utf-8')

    code_dst = cfg.get('CopyPaths', 'Code', fallback='').strip()
    exh_dst  = cfg.get('CopyPaths', 'Exheader', fallback='').strip()

    if code_dst:
        dst = Path(code_dst)
        if dst.exists():
            dst.unlink()
        shutil.copy2(work_dir / 'code.bin', dst)
        print(f'Copied code.bin -> {dst}')

    if exh_dst:
        dst = Path(exh_dst)
        if dst.exists():
            dst.unlink()
        shutil.copy2(work_dir / 'exheader.bin', dst)
        print(f'Copied exheader.bin -> {dst}')


def _align_up(value: int, align: int) -> int:
    return (value + align - 1) & ~(align - 1)
