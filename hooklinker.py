from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from hooks import (
    BranchHook,
    Hook,
    HookError,
    HookInfo,
    PatchHook,
    SoftBranchHook,
    SymbolAddrPatchHook,
)

if TYPE_CHECKING:
    from pathlib import Path

    from sym_table import SymTable


class HookLinker:
    def __init__(self):
        self.hooks: list[Hook] = []
        self.sym_table: SymTable | None = None
        self._extra_data_ptr: int = 0

    def set_extra_data_ptr(self, ptr: int) -> None:
        self._extra_data_ptr = ptr

    def extra_data_size(self) -> int:
        return sum(h.extra_data_size() for h in self.hooks)

    def load_hooks(self, path: Path, subdirs: bool = False) -> None:
        """Load all .hks files from a directory (non-recursive by default)."""
        if not path.exists():
            return
        if path.is_file():
            self._load_hooks_from_file(path)
            return
        if subdirs:
            hks_files = sorted(path.rglob('*.hks'))
        else:
            hks_files = sorted(path.glob('*.hks'))
        for hks_file in hks_files:
            self._load_hooks_from_file(hks_file)

    def _load_hooks_from_file(self, path: Path) -> None:
        if not path.exists():
            return
        text = path.read_text(encoding='utf-8', errors='replace')

        entries: list[HookInfo] = []
        current: HookInfo | None = None
        line_nbr = 0

        for raw_line in text.splitlines():
            line_nbr += 1
            # Strip comments
            hash_idx = raw_line.find('#')
            if hash_idx >= 0:
                raw_line = raw_line[:hash_idx]

            line = raw_line

            # New entry: non-indented line that contains ':'
            if not line.startswith((' ', '\t')) and ':' in line:
                name = line[:line.index(':')].strip()
                current = HookInfo(name, str(path), line_nbr)
                entries.append(current)

            # Key-value pair: indented line that contains ':'
            if current is not None and ':' in line:
                if not line.startswith(('\t', ' ')):
                    continue  # This is the entry header, already handled above
                line = line.lstrip('\t ')
                idx = line.index(':')
                label = line[:idx].strip()
                value = line[idx + 1:].strip()
                current.values[label] = value

        for info in entries:
            try:
                hook = self._hook_from_info(info)
                if hook is not None:
                    self.hooks.append(hook)
            except HookError as e:
                print(
                    f'{e.hook_info.path}:{e.hook_info.line}: error: Hook: {e.msg}',
                    file=sys.stderr,
                )
                raise SystemExit(1)

    def _hook_from_info(self, info: HookInfo) -> Hook | None:
        if not info.has('type'):
            raise HookError(info, 'No type given')
        hook_type = info.get('type').lower()
        if hook_type == 'branch':
            return BranchHook(self, info)
        elif hook_type in ('softbranch', 'soft_branch'):
            return SoftBranchHook(self, info)
        elif hook_type == 'patch':
            return PatchHook(self, info)
        elif hook_type in ('symbol', 'symptr', 'sym_ptr'):
            return SymbolAddrPatchHook(self, info)
        else:
            raise HookError(info, f'Invalid type "{info.get("type")}"')

    def apply_to(self, data: bytearray) -> None:
        ptr = self._extra_data_ptr
        for hook in self.hooks:
            hook.write_data(data, ptr)
            ptr += hook.extra_data_size()

    def clear(self) -> None:
        self.hooks.clear()
