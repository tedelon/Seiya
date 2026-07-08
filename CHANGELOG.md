# Changelog

## V1.0.2 (2026-07-08)

Bugfix release — fixes dependency resolution crash with packages that declare non-importable top-level modules.

### Bug Fixes

- **`ImportError: Could not find 'mapi'`** — Packages like `pywin32` declare many top-level modules in `top_level.txt` (e.g. `mapi`, `exchange`, `pythonwin`, `adsi`) that are not importable in the build environment (pyd files requiring post-install, optional Windows sub-systems). The `copy_modules()` function now skips these with a warning instead of aborting the build.

## V1.0.1 (2026-07-08)

Critical bugfix release — fixes `.pyw` files being generated instead of `.exe` launchers in clean environments.

### Bug Fixes

- **Missing `requests-download` dependency** — V1.0.0 omitted `requests-download` from `pyproject.toml`, causing `import` failure in clean environments (`ModuleNotFoundError: No module named 'requests_download'`). Now explicitly listed as a dependency.
- **`nsist` package name conflict** — V1.0.0 used `nsist` as the internal package name, colliding with the original pynsist. If users installed original pynsist as a workaround, the original `nsist` package overwrote seiya's enhanced version, reverting to `.pyw` script generation. Resolved by merging all code into `seiya/core/` subpackage — seiya is now fully self-contained with zero dependency on a `nsist` package.
- **Broken shebang in `commands.py`** — `prepare_bin_directory()` used `#!<launcher_dir>\\..\\Python\\python.exe`, but `<launcher_dir>` is not a token the distlib launcher resolves. Fixed to `#!..\\Python\\python.exe` (true relative path).
- **`pynsist` entry point conflict** — V1.0.0 registered `pynsist = "nsist:main"`, conflicting with original pynsist. Changed to `pynsist = "seiya:main"`.

### Changes

- Merged `nsist/` → `seiya/core/` subpackage (all modules, templates, and `msvcrt/` DLLs)
- `seiya/__init__.py` now imports from `seiya.core` instead of `nsist`
- `pyproject.toml`: `packages = ["seiya", "seiya.core"]`, `package-data` targets `seiya.core`
- Version updated to 1.0.1

## V1.0.0 (2026-06-28)

Seiya V1.0.0 — first public release. Enhanced fork of [pynsist](https://github.com/takluyver/pynsist) for building Windows installers of Python applications.

### Attribution & License

Seiya is a derivative work based on pynsist by Thomas Kluyver
(https://github.com/takluyver/pynsist), distributed under the MIT License.

- Original pynsist code: Copyright © 2014 Thomas Kluyver
- Seiya modifications: Copyright © 2026 Seiya Contributors

Seiya is **not** affiliated with, endorsed by, or sponsored by the original
pynsist project or its author. The `pynsist` command-line entry point is
preserved solely for backward compatibility with existing user config files.

Third-party assets reused under their respective licenses:
- `seiya/core/_system_path.py` — BSD license (see file header)
- `seiya/core/glossyorb.ico` — CC Attribution 3.0 (Mysitemyway.com)
- distlib launcher binaries — Python Software Foundation License

### Highlights

- **Standalone `.exe` launchers** — Replaces the old `.launch.pyw` script approach with native `.exe` launchers built on the distlib launcher mechanism (`[launcher exe] + [shebang] + [zip(__main__.py)]`)
- **Auto transitive dependency resolution** — Only list direct dependencies in `packages=`; transitive dependencies are resolved automatically via `importlib.metadata`
- **Custom logo support** — New `logo=` config option accepting PNG, JPG, ICO, BMP, GIF, WEBP formats (auto-converted to ICO)
- **Platform filtering** — PEP 508 markers automatically exclude macOS/Linux-only dependencies (e.g. `pyobjc-*`)
- **Metadata preservation** — `.dist-info` directories are copied so `importlib.metadata` works at runtime
- **Backward compatible** — Existing `pynsist` config files and the `pynsist` command work unchanged

### New Commands

| Command | Description |
|---------|-------------|
| `seiya installer.cfg` | Build installer using Seiya |
| `python -m seiya installer.cfg` | Equivalent module invocation |
| `pynsist installer.cfg` | Backward-compatible alias |

### New Config Options

| Section | Option | Description |
|---------|--------|-------------|
| `[Application]` | `logo` | Custom logo (PNG/JPG/ICO/BMP/GIF/WEBP), alias for `icon` |
| `[Shortcut *]` | `logo` | Per-shortcut logo, same format support |

### Technical Details

- **Shebang**: `#!Python\pythonw.exe\r\n` (relative path for portable installation)
- **Embedded Python**: Tested with Python 3.12.10 (amd64)
- **Icon conversion**: PNG wrapped to ICO without Pillow; multi-size ICO (16/32/48/64/128/256px) with Pillow
- **Dependency resolution**: Recursive `requires` field traversal with PEP 508 marker evaluation

### Acknowledgments

Based on pynsist by Thomas Kluyver. Thanks to the distlib project for the launcher mechanism.
