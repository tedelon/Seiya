# Changelog

## V1.0.0 (2026-06-28)

Seiya V1.0.0 — first public release. Enhanced fork of [pynsist](https://github.com/takluyver/pynsist) for building Windows installers of Python applications.

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
