from dataclasses import dataclass
from pathlib import Path


@dataclass
class SymTableEntry:
    offset: int
    size: int
    was_mangled: bool


class SymTable:
    def __init__(self):
        self.symbols: dict[str, SymTableEntry] = {}

    def load(self, path: Path) -> None:
        if not path.exists():
            return
        for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
            line = line.replace('\t', ' ')
            segs = line.split()
            if len(segs) < 3:
                continue
            try:
                offset = int(segs[0], 16)
            except ValueError:
                continue
            try:
                size = int(segs[-2], 16)
            except ValueError:
                continue
            name = segs[-1]
            if name.startswith('_Z'):
                demangled = _demangle(name)
                if demangled is not None:
                    # Store under the demangled name (hook files use this form)
                    self.symbols[demangled] = SymTableEntry(offset, size, True)
                # Always also store under the raw mangled name as a fallback,
                # so hook files can reference either form regardless of whether
                # a demangler is available.
                self.symbols[name] = SymTableEntry(offset, size, False)
            else:
                self.symbols[name] = SymTableEntry(offset, size, False)

    def get(self, name: str) -> int:
        """Return the offset for a symbol, or 0xFFFFFFFF if not found."""
        entry = self.symbols.get(name)
        if entry is None:
            return 0xFFFFFFFF
        return entry.offset

    def has(self, name: str) -> bool:
        return name in self.symbols

    def clear(self) -> None:
        self.symbols.clear()


def _demangle(name: str) -> str | None:
    """Attempt to demangle a C++ mangled name. Returns None on failure."""
    try:
        from cpp_demangle import demangle
        return demangle(name)
    except ImportError:
        pass
    except Exception:
        pass
    # Fallback: try arm-none-eabi-c++filt
    try:
        import subprocess
        result = subprocess.run(
            ['arm-none-eabi-c++filt', name],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            demangled = result.stdout.strip()
            if demangled != name:
                return demangled
    except Exception:
        pass
    return None
