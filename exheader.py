"""
3DS NCCH Extended Header (exheader.bin) reader/writer.

The exheader is exactly 0x800 (2048) bytes, stored little-endian.

Byte layout (derived from packed C++ struct in exheader.h):

  SCI (System Control Info) — offset 0x000, size 0x200
    0x000  8   title (char[8])
    0x008  5   reserved1
    0x00D  1   sciFlags
    0x00E  2   remasterVersion
    0x010  4   textCodeSetInfo.address
    0x014  4   textCodeSetInfo.physicalRegionSize
    0x018  4   textCodeSetInfo.size
    0x01C  4   stackSize
    0x020  4   readOnlyCodeSetInfo.address
    0x024  4   readOnlyCodeSetInfo.physicalRegionSize
    0x028  4   readOnlyCodeSetInfo.size
    0x02C  4   reserved2
    0x030  4   dataCodeSetInfo.address
    0x034  4   dataCodeSetInfo.physicalRegionSize
    0x038  4   dataCodeSetInfo.size
    0x03C  4   bssSize
    0x040  384 dependencyModules (48 x uint64)
    0x1C0  8   systemInfo.saveDataSize
    0x1C8  8   systemInfo.jumpId
    0x1D0  48  systemInfo.reserved

  ACI1 — offset 0x200, size 0x200
    0x200  0x170  arm11systemCaps
    0x370  0x70   arm11kernelCaps (28 x uint32 descriptors)
    0x3E0  0x10   arm11kernelCaps reserved
    0x3F0  0x10   arm9accessControl

  accessDesc — offset 0x400, size 0x100
  ncchHdr    — offset 0x500, size 0x100

  ACI2 — offset 0x600, size 0x200
    (same layout as ACI1)
"""

import struct
import sys
from pathlib import Path

EXHEADER_SIZE = 0x800

# SCI field offsets
_TEXT_ADDR       = 0x010
_TEXT_PHYS_SIZE  = 0x014
_TEXT_SIZE       = 0x018
_RODATA_ADDR     = 0x020
_DATA_ADDR       = 0x030
_DATA_PHYS_SIZE  = 0x034
_DATA_SIZE       = 0x038
_BSS_SIZE        = 0x03C

# ACI1 kernel capability descriptors start
_ACI1_KERN_CAPS  = 0x370
_NUM_DESCRIPTORS = 28

# SVC that must be enabled: ControlProcessMemory
_SVC_CONTROL_PROCESS_MEMORY = 0x70


class Exheader:
    def __init__(self, path: Path):
        self.path = path
        raw = path.read_bytes()
        if len(raw) != EXHEADER_SIZE:
            raise RuntimeError(
                f'Exheader: Invalid file size {len(raw):#x}, expected {EXHEADER_SIZE:#x}'
            )
        self.data = bytearray(raw)

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    def _ru32(self, offset: int) -> int:
        return struct.unpack_from('<I', self.data, offset)[0]

    def _wu32(self, offset: int, value: int) -> None:
        struct.pack_into('<I', self.data, offset, value & 0xFFFFFFFF)

    # ------------------------------------------------------------------
    # Named accessors used by patchmaker
    # ------------------------------------------------------------------

    @property
    def title(self) -> str:
        return self.data[0:8].decode('ascii', errors='replace').rstrip('\x00')

    @property
    def text_address(self) -> int:
        return self._ru32(_TEXT_ADDR)

    @property
    def text_phys_size(self) -> int:
        return self._ru32(_TEXT_PHYS_SIZE)

    @property
    def text_size(self) -> int:
        return self._ru32(_TEXT_SIZE)

    @property
    def rodata_address(self) -> int:
        return self._ru32(_RODATA_ADDR)

    @property
    def data_address(self) -> int:
        return self._ru32(_DATA_ADDR)

    @property
    def data_phys_size(self) -> int:
        return self._ru32(_DATA_PHYS_SIZE)

    @property
    def data_size(self) -> int:
        return self._ru32(_DATA_SIZE)

    @property
    def bss_size(self) -> int:
        return self._ru32(_BSS_SIZE)

    # ------------------------------------------------------------------
    # fixExheader logic (ported from patchmaker.cpp:305-389)
    # ------------------------------------------------------------------

    def apply_patch(self, new_code_size: int, new_text_code_size: int) -> bool:
        """
        Update exheader fields and enable SVC 0x70.
        Returns False if the kernel caps table overflows (> 28 entries).
        """
        # 1. text.size = text.physicalRegionSize << 12
        self._wu32(_TEXT_SIZE, new_text_code_size)

        bss = self._ru32(_BSS_SIZE)
        data_phys = self._ru32(_DATA_PHYS_SIZE)

        # 2. Expand data section to cover aligned BSS + new code
        data_phys += _align_up(bss, 0x1000) >> 12
        data_phys += _align_up(new_code_size, 0x1000) >> 12
        self._wu32(_DATA_PHYS_SIZE, data_phys)
        self._wu32(_DATA_SIZE, data_phys << 12)

        # 3. Clear BSS size
        self._wu32(_BSS_SIZE, 0)

        # 4. Update ARM11 kernel capabilities in ACI1
        return self._enable_svc(_SVC_CONTROL_PROCESS_MEMORY)

    def _enable_svc(self, svc_id: int) -> bool:
        """Parse existing SVC descriptors, enable svc_id, repack."""
        svcs = [False] * 0x100
        other_caps: list[int] = []

        for i in range(_NUM_DESCRIPTORS):
            cap = self._ru32(_ACI1_KERN_CAPS + i * 4)
            if (cap & 0xF8000000) == 0xF0000000:
                # SVC descriptor
                mask = cap & 0x00FFFFFF
                table_index = (cap & 0x03000000) >> 24
                for bit in range(24):
                    if mask & (1 << bit):
                        svc = table_index * 24 + bit
                        if svc < 0x100:
                            svcs[svc] = True
            elif cap != 0xFFFFFFFF:
                other_caps.append(cap)

        svcs[svc_id] = True

        # Build compact SVC descriptor list
        caps: list[int] = []
        for table_index in range(8):
            new_cap = 0xF0000000 | (table_index << 24)
            for bit in range(24):
                if svcs[table_index * 24 + bit]:
                    new_cap |= (1 << bit)
            if new_cap & 0x00FFFFFF:
                caps.append(new_cap)

        caps.extend(other_caps)

        if len(caps) > _NUM_DESCRIPTORS:
            print('Setting ARM11 kernel caps failed: too many descriptors', file=sys.stderr)
            return False

        # Write descriptors; fill remaining slots with 0xFFFFFFFF
        for i, cap in enumerate(caps):
            self._wu32(_ACI1_KERN_CAPS + i * 4, cap)
        for i in range(len(caps), _NUM_DESCRIPTORS):
            self._wu32(_ACI1_KERN_CAPS + i * 4, 0xFFFFFFFF)

        return True

    def save(self) -> None:
        self.path.write_bytes(self.data)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _align_up(value: int, align: int) -> int:
    return (value + align - 1) & ~(align - 1)
