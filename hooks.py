from __future__ import annotations

import struct
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hooklinker import HookLinker

BASE_ADDR = 0x00100000


# ---------------------------------------------------------------------------
# ARM opcode helpers
# ---------------------------------------------------------------------------

def make_branch_opcode(src: int, dest: int, link: bool) -> int:
    """Generate an ARM B or BL instruction (little-endian 32-bit)."""
    ret = 0xEA000000
    if link:
        ret |= 0x01000000
    offset = (dest // 4) - (src // 4) - 2
    ret |= offset & 0x00FFFFFF
    return ret & 0xFFFFFFFF


def offset_opcode(opcode: int, org_position: int, new_position: int) -> int:
    """Fix a position-dependent opcode (B/BL) when relocated to a new address."""
    nybble14 = (opcode >> 24) & 0xF
    if 0xA <= nybble14 <= 0xB:
        # Reconstruct the destination from the original encoding
        old_offset = opcode & 0x00FFFFFF
        # Sign-extend the 24-bit value
        if old_offset & 0x800000:
            old_offset -= 0x1000000
        old_offset = (old_offset + 2) * 4
        dest = org_position + old_offset
        new_offset = (dest // 4) - (new_position // 4) - 2
        return (opcode & 0xFF000000) | (new_offset & 0x00FFFFFF)
    return opcode


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class HookError(Exception):
    def __init__(self, info: HookInfo, msg: str):
        self.hook_info = info
        self.msg = msg
        super().__init__(msg)


# ---------------------------------------------------------------------------
# HookInfo  (also imported by hooklinker)
# ---------------------------------------------------------------------------

class HookInfo:
    def __init__(self, name: str, path: str, line: int):
        self.name = name
        self.path = path
        self.line = line
        self.values: dict[str, str] = {}

    def has(self, key: str) -> bool:
        return key in self.values

    def get(self, key: str) -> str:
        return self.values.get(key, '')

    def get_bool(self, key: str) -> bool:
        return self.values.get(key, '').lower() == 'true'

    def get_uint(self, key: str) -> int | None:
        val = self.values.get(key, '').strip()
        if not val:
            return None
        try:
            if val.lower().startswith('0x'):
                return int(val, 16)
            return int(val, 10)
        except ValueError:
            return None


# ---------------------------------------------------------------------------
# Base hook
# ---------------------------------------------------------------------------

class Hook:
    def __init__(self, parent: HookLinker, info: HookInfo):
        self._parent = parent
        self._info = info
        if not info.has('addr'):
            raise HookError(info, 'No address given')
        addr = info.get_uint('addr')
        if addr is None or addr < BASE_ADDR:
            raise HookError(info, f'Invalid address "{info.get("addr")}"')
        self.address = addr

    def write_data(self, data: bytearray, extra_data_ptr: int) -> None:
        pass

    def extra_data_size(self) -> int:
        return 0

    # Helpers
    def _file_off(self, addr: int) -> int:
        return addr - BASE_ADDR

    def _read32(self, data: bytearray, addr: int) -> int:
        return struct.unpack_from('<I', data, self._file_off(addr))[0]

    def _write32(self, data: bytearray, addr: int, value: int) -> None:
        struct.pack_into('<I', data, self._file_off(addr), value & 0xFFFFFFFF)


# ---------------------------------------------------------------------------
# BranchHook  (type: branch)
# ---------------------------------------------------------------------------

class BranchHook(Hook):
    def __init__(self, parent: HookLinker, info: HookInfo):
        super().__init__(parent, info)
        if not info.has('link'):
            raise HookError(info, 'Invalid branch link type')
        self._link = info.get_bool('link')
        if info.has('func'):
            sym = parent.sym_table
            if sym is None:
                raise HookError(info, 'Invalid SymTable')
            name = info.get('func')
            if not sym.has(name):
                raise HookError(info, f'Function name "{name}" not found')
            self._destination = sym.get(name)
        elif info.has('dest'):
            dest = info.get_uint('dest')
            if dest is None:
                raise HookError(info, f'Invalid branch destination "{info.get("dest")}"')
            self._destination = dest
        else:
            raise HookError(info, 'No branch destination given')

    def write_data(self, data: bytearray, extra_data_ptr: int) -> None:
        self._write32(data, self.address,
                      make_branch_opcode(self.address, self._destination, self._link))


# ---------------------------------------------------------------------------
# SoftBranchHook  (type: softbranch / soft_branch)
# ---------------------------------------------------------------------------

class SoftBranchHook(Hook):
    _PUSH = 0xE92D5FFF  # push {r0-r12, r14}
    _POP  = 0xE8BD5FFF  # pop  {r0-r12, r14}

    def __init__(self, parent: HookLinker, info: HookInfo):
        super().__init__(parent, info)
        if info.has('func'):
            sym = parent.sym_table
            if sym is None:
                raise HookError(info, 'Invalid SymTable')
            name = info.get('func')
            if not sym.has(name):
                raise HookError(info, f'Function name "{name}" not found')
            self._destination = sym.get(name)
        elif info.has('dest'):
            dest = info.get_uint('dest')
            if dest is None:
                raise HookError(info, f'Invalid branch destination "{info.get("dest")}"')
            self._destination = dest
        else:
            raise HookError(info, 'No branch destination given')

        opcode_str = info.get('opcode').lower() if info.has('opcode') else 'ignore'
        if opcode_str == 'pre':
            self._opcode_pos = 'pre'
        elif opcode_str == 'post':
            self._opcode_pos = 'post'
        elif opcode_str == 'ignore':
            self._opcode_pos = 'ignore'
        else:
            raise HookError(info, f'Invalid softHook opcode position "{info.get("opcode")}"')

    def extra_data_size(self) -> int:
        return 5 * 4  # always 20 bytes reserved (matches C++)

    def write_data(self, data: bytearray, extra_data_ptr: int) -> None:
        # Read original instruction at hook address
        orig_opcode = self._read32(data, self.address)
        # Overwrite it with a branch to the trampoline
        self._write32(data, self.address, make_branch_opcode(self.address, extra_data_ptr, False))

        # Build trampoline at extra_data_ptr
        ptr = extra_data_ptr
        if self._opcode_pos == 'pre':
            self._write32(data, ptr, offset_opcode(orig_opcode, self.address, ptr))
            ptr += 4
        self._write32(data, ptr, self._PUSH)
        ptr += 4
        self._write32(data, ptr, make_branch_opcode(ptr, self._destination, True))
        ptr += 4
        self._write32(data, ptr, self._POP)
        ptr += 4
        if self._opcode_pos == 'post':
            self._write32(data, ptr, offset_opcode(orig_opcode, self.address, ptr))
            ptr += 4
        self._write32(data, ptr, make_branch_opcode(ptr, self.address + 4, False))


# ---------------------------------------------------------------------------
# PatchHook  (type: patch)
# ---------------------------------------------------------------------------

class PatchHook(Hook):
    def __init__(self, parent: HookLinker, info: HookInfo):
        super().__init__(parent, info)
        if info.has('data'):
            self._from_bin = False
            hex_str = info.get('data').lower()
            if hex_str.startswith('0x'):
                hex_str = hex_str[2:]
            hex_str = hex_str.replace(' ', '').replace('\t', '')
            self._patch_data = bytes.fromhex(hex_str)
        elif info.has('src') and info.has('len'):
            self._from_bin = True
            sym = parent.sym_table
            if sym is None:
                raise HookError(info, 'Invalid SymTable')
            src_name = info.get('src')
            if not sym.has(src_name):
                raise HookError(info, 'Invalid src symbol')
            self._src = sym.get(src_name)
            length = info.get_uint('len')
            if length is None:
                raise HookError(info, 'Invalid length')
            self._len = length
        else:
            raise HookError(info, 'No patch data given')

    def write_data(self, data: bytearray, extra_data_ptr: int) -> None:
        if not self._from_bin:
            off = self._file_off(self.address)
            data[off:off + len(self._patch_data)] = self._patch_data
        else:
            src_off = self._file_off(self._src)
            chunk = bytes(data[src_off:src_off + self._len])
            dst_off = self._file_off(self.address)
            data[dst_off:dst_off + self._len] = chunk


# ---------------------------------------------------------------------------
# SymbolAddrPatchHook  (type: symbol / symptr / sym_ptr)
# ---------------------------------------------------------------------------

class SymbolAddrPatchHook(Hook):
    def __init__(self, parent: HookLinker, info: HookInfo):
        super().__init__(parent, info)
        if not info.has('sym'):
            raise HookError(info, 'No symbol given')
        sym = parent.sym_table
        if sym is None:
            raise HookError(info, 'Invalid SymTable')
        sym_name = info.get('sym')
        if not sym.has(sym_name):
            raise HookError(info, f'Symbol name "{sym_name}" not found')
        self._destination = sym.get(sym_name)

    def write_data(self, data: bytearray, extra_data_ptr: int) -> None:
        self._write32(data, self.address, self._destination)
