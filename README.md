# Magikoopa (Python CLI)

A 3DS game code injection and patching tool. Compiles custom ARM code, injects it into a game's `code.bin`, applies hooks, and updates `exheader.bin` accordingly.

This is a pure-Python CLI rewrite of the original C++/Qt application. No GUI, no Qt dependency — just Python 3.10+ and the ARM devkitPro toolchain.

All files here were generated entirely by an LLM. The only manually applied patch was changing `text.size = text.physicalRegionSize << 12` to the new code size, as the aligned variant crashed on Luma + 3DS for [msr-remote-connector](https://github.com/randovania/msr-remote-connector)

You can find the original project at https://github.com/RicBent/Magikoopa

## Requirements

- Python 3.10 or newer
- [devkitPro](https://devkitpro.org/wiki/Getting_Started) with the following components installed:
  - **devkitARM** — provides `arm-none-eabi-gcc`, `arm-none-eabi-as`, `make`, etc.
  - **libctru** — Nintendo 3DS C standard library (both Makefiles link against `-lctru`)
  - **3DS portlibs** — needed by the newcode Makefile (`armv6k` and `3ds` portlibs)
- The following environment variables must be set (devkitPro's installer usually does this):
  - `DEVKITPRO` — path to the devkitPro installation (e.g. `/opt/devkitpro`)
  - `DEVKITARM` — path to devkitARM (e.g. `/opt/devkitpro/devkitARM`)
  - `PORTLIBS_PATH` — path to portlibs root (e.g. `/opt/devkitpro/portlibs`)
- A patch project directory set up from `PatchTemplate/` (see below)
- Install dependencies from the `requirements.txt`

On Windows, devkitPro is installed via devkitProUpdater and sets the environment variables automatically. On Linux/macOS, use the dkp-pacman package manager and install `3ds-dev` which pulls in devkitARM, libctru, and the portlibs in one step.
See [devkitPro Getting Started](https://devkitpro.org/wiki/Getting_Started)

## Usage

```
python magikoopa.py insert [working_dir]
python magikoopa.py clean  [working_dir]
```

`working_dir` is the path to your patch project. It defaults to the current directory if omitted.

### Commands

| Command | Description |
|---------|-------------|
| `insert` | Compile custom code and inject it into the game binary |
| `clean` | Clean all build artifacts (newcode + loader) |

### Examples

```sh
# Inject from the current directory
python magikoopa.py insert

# Inject from a specific project path
python magikoopa.py insert /path/to/my-nsmb2-hack

# Clean build artifacts
python magikoopa.py clean /path/to/my-nsmb2-hack
```

## Project directory structure

The working directory must contain:

```
my-hack/
├── Makefile              # Builds your custom code → newcode.bin, newcode.sym
├── code.bin              # Original game code binary (unmodified)
├── exheader.bin          # Original game exheader (unmodified)
├── source/               # Your custom C/C++/ASM source files
│   └── *.hks             # Hook definition files (optional here)
├── hooks/                # Additional hook definition files (optional)
│   └── *.hks
└── loader/
    ├── Makefile          # Builds the loader bootstrap → loader.bin, loader.sym
    └── source/
        ├── loader.c      # Loader entry point (uses SVC to set RWX on newcode)
        ├── svc.s         # ARM SVC stubs
        └── hooks.hks     # Loader hook definitions
```

Use `PatchTemplate/` from the repository root as your starting point.

## Which code.bin and exheader.bin are modified?

This is important to understand:

### First run

On the very first `insert`, Magikoopa checks whether `bak/code.bin` and `bak/exheader.bin` exist. Since they don't, it **copies the current `code.bin` and `exheader.bin` into `bak/`** as the originals. These backups are never overwritten again.

```
my-hack/
├── code.bin        ← gets patched (overwritten with injected version)
├── exheader.bin    ← gets patched (overwritten with modified version)
└── bak/
    ├── code.bin    ← created now — the untouched original
    └── exheader.bin← created now — the untouched original
```

### Every subsequent run

At the start of each `insert`, Magikoopa **restores `code.bin` and `exheader.bin` from `bak/`** before doing anything. This means:

- The tool always patches from the **original, unmodified** game files, not from the last run's output.
- Running `insert` twice in a row produces the same result both times.
- The `bak/` copies are the source of truth. **Never delete them** unless you want to re-establish new originals from whatever `code.bin`/`exheader.bin` are currently in the project root.

Memory layout (loader offset, new code offset) is also computed from `bak/exheader.bin`, not the patched one.

### Re-establishing the originals

If you want to start fresh with a different base `code.bin` (e.g. a different game version):

1. Delete `bak/code.bin` and `bak/exheader.bin`.
2. Place the new originals as `code.bin` and `exheader.bin` in the project root.
3. Run `insert` — the new files will be backed up and used as the new originals.

## Hook file format (.hks)

Hook files use a simple YAML-like format. Each entry starts with a name followed by a colon (no indentation), and its properties are indented below it. Lines starting with `#` are comments.

```yaml
# Replace a game function with a branch to custom code (via symbol name)
MyBranchHook:
    type: branch
    link: true           # required: true = BL, false = B
    addr: 0x00430988
    func: MyCustomFunction

# Branch to a raw address instead of a symbol
MyBranchHookRaw:
    type: branch
    link: false
    addr: 0x00430988
    dest: 0x00431000

# Non-destructive hook: saves registers, calls your function, restores them
MySoftHook:
    type: softbranch
    opcode: post         # pre | post | ignore (default: ignore — skips original instruction)
    addr: 0x00431000
    func: MyHookFunction

# Overwrite bytes at an address with raw hex data
MyPatch:
    type: patch
    addr: 0x00432000
    data: 0xE3A00001     # MOV r0, #1

# Write a symbol's address as a 32-bit value (for patching function pointers)
MySymPatch:
    type: symbol
    addr: 0x00433000
    sym: MyCallbackFunction
```

### Hook types

| Type | Aliases | Description |
|------|---------|-------------|
| `branch` | — | Overwrites one ARM instruction with B or BL to your function. Requires `link: true` or `link: false`. Use `func:` for a symbol name or `dest:` for a raw address. |
| `softbranch` | `soft_branch` | Saves all registers, calls your function, restores them, optionally runs the original instruction before (`pre`) or after (`post`), then returns. Default `opcode` is `ignore` (original instruction is skipped). Use `func:` for a symbol name or `dest:` for a raw address. |
| `patch` | — | Writes raw hex bytes (`data:`) or copies data from a symbol address (`src:` + `len:`) |
| `symbol` | `symptr`, `sym_ptr` | Writes a symbol's 32-bit address to a location (for patching function pointers) |

Hook files are loaded from `source/` and `hooks/` in the project root (for newcode hooks) and from `loader/source/` and `loader/hooks/` (for loader hooks).

## Optional: copy output files automatically

Create a file named `<project-folder-name>.mkproj.user` in the project root (INI format) to have the patched files copied to another location after each successful `insert`:

```ini
[CopyPaths]
Code=C:\path\to\game\romfs\code.bin
Exheader=C:\path\to\game\exheader.bin
```

Both keys are optional. If a key is absent or empty, no copy is performed for that file.
