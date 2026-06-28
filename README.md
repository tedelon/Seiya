# Seiya

**Enhanced Python Windows Installer Builder**

> **Fork Notice**: Seiya is a derivative work based on [pynsist](https://github.com/takluyver/pynsist) by Thomas Kluyver, distributed under the MIT License. Original copyright © 2014 Thomas Kluyver. Seiya modifications copyright © 2026 Seiya Contributors. See [LICENSE](LICENSE) for full copyright and license text. Seiya is **not** affiliated with or endorsed by the original pynsist project — it is an independent enhanced fork. The `pynsist` command is preserved solely for backward compatibility with existing user config files.

Seiya is an enhanced fork of [pynsist](https://github.com/takluyver/pynsist) that builds Windows installers for Python applications. It bundles Python itself, so you can distribute your application to users who don't have Python installed.

## Key Enhancements

| Feature | Description |
|---------|-------------|
| **`.exe` Launchers** | Generates standalone `.exe` launchers using the distlib launcher mechanism (replaces `.launch.pyw` scripts) |
| **Auto Dependency Resolution** | Only list direct dependencies — transitive dependencies are automatically resolved via `importlib.metadata` |
| **Custom Logo Support** | Supports PNG, JPG, ICO, BMP, GIF, WEBP image formats as application logos (auto-converted to ICO) |
| **Platform Filtering** | Automatically excludes macOS/Linux-only dependencies (e.g. `pyobjc-*`) via PEP 508 markers |
| **Metadata Preservation** | Copies `.dist-info` directories so `importlib.metadata` works at runtime |
| **Backward Compatible** | Existing `pynsist` config files and commands work unchanged |

## Quick Start

### 1. Install Seiya

```bash
pip install seiya
# Or with image format support (JPG, BMP, GIF, WEBP):
pip install "seiya[image]"
```

### 2. Install NSIS

Download and install [NSIS](http://nsis.sourceforge.net/Download).

### 3. Write a Config File

Create `installer.cfg`:

```ini
[Application]
name=MyApp
version=1.0
entry_point=myapp:main
logo=mylogo.png

[Python]
version=3.12.10
bitness=64
format=bundled

[Include]
packages=flask
  webview
```

### 4. Build the Installer

```bash
seiya installer.cfg
# Or equivalently:
python -m seiya installer.cfg
# Backward compatible:
pynsist installer.cfg
```

This generates `build/nsis/MyApp_1.0.exe` — a complete Windows installer that bundles Python and all dependencies.

## Custom Logo

Seiya supports multiple image formats as application logos. The `logo` option (alias for `icon`) automatically converts images to ICO format:

```ini
[Application]
name=MyApp
version=1.0
entry_point=myapp:main
logo=mylogo.png        # PNG (recommended, supports transparency)
# or
logo=mylogo.jpg        # JPG (requires Pillow)
# or
logo=mylogo.ico        # ICO (used directly, no conversion)
```

| Format | Extension | Pillow Required | Notes |
|--------|-----------|-----------------|-------|
| ICO | `.ico` | No | Used directly, no conversion |
| PNG | `.png` | No (recommended) | Auto-converted to multi-size ICO |
| JPEG | `.jpg/.jpeg` | Yes | Requires Pillow |
| BMP | `.bmp` | Yes | Requires Pillow |
| GIF | `.gif` | Yes | Requires Pillow |
| WEBP | `.webp` | Yes | Requires Pillow |

Install Pillow for full format support:

```bash
pip install Pillow
```

## Auto Dependency Resolution

Only list your **direct dependencies** in the config file. Seiya automatically resolves all transitive dependencies:

```ini
[Include]
packages=flask
  webview
```

Seiya will automatically include:
- `flask` → `jinja2`, `werkzeug`, `click`, `itsdangerous`, `markupsafe`, `blinker`
- `webview` → `bottle`, `proxy_tools`, `pythonnet`, `clr_loader`, `cffi`, `typing_extensions`
- `cffi` → `pycparser`
- And all their `.dist-info` metadata directories

Platform-specific dependencies (e.g. `pyobjc-*` on macOS) are automatically filtered out via PEP 508 markers.

## Configuration Reference

### [Application] Section

| Option | Required | Description |
|--------|----------|-------------|
| `name` | Yes | Application name |
| `version` | Yes | Application version |
| `publisher` | No | Publisher name |
| `entry_point` | No | Entry point as `module:function` |
| `script` | No | Launch script path (alternative to `entry_point`) |
| `logo` | No | Custom logo — supports PNG/JPG/ICO/BMP/GIF/WEBP |
| `icon` | No | Alias for `logo` (`logo` takes precedence) |
| `console` | No | `true` to show console window (default: `false`) |
| `license_file` | No | License file path |
| `extra_preamble` | No | Extra Python code to run before entry point |

### [Python] Section

| Option | Required | Description |
|--------|----------|-------------|
| `version` | No | Python version (recommend `3.12.10`) |
| `bitness` | No | `32` or `64` (default: `64`) |
| `format` | No | Must be `bundled` |
| `include_msvcrt` | No | Include MSVC runtime (default: `true`) |

### [Include] Section

| Option | Required | Description |
|--------|----------|-------------|
| `packages` | No | Direct dependencies (transitive deps auto-resolved) |
| `pypi_wheels` | No | PyPI wheel specs to download |
| `local_wheels` | No | Local wheel file paths |
| `files` | No | Extra files as `source>destination` |
| `exclude` | No | File patterns to exclude |

### [Build] Section

| Option | Required | Description |
|--------|----------|-------------|
| `directory` | No | Build directory (default: `build/nsis`) |
| `installer_name` | No | Installer filename |
| `nsi_template` | No | Custom NSI template path |

### [Shortcut *name*] Section (Optional)

Defines additional shortcuts. Same options as [Application].

### [Command *name*] Section (Optional)

Defines command-line tools added to PATH.

| Option | Required | Description |
|--------|----------|-------------|
| `entry_point` | Yes | Entry point as `module:function` |
| `console` | No | Default: `true` |
| `extra_preamble` | No | Extra Python code file |

## How It Works

### .exe Launcher Structure

Each generated `.exe` launcher consists of three parts:

```
[distlib launcher exe] + [shebang] + [zip(__main__.py)]
```

1. **Launcher EXE** — Base launcher from distlib (`t64.exe` for console, `w64.exe` for GUI)
2. **Shebang** — `#!Python\pythonw.exe\r\n` (relative path to Python interpreter)
3. **ZIP** — Contains `__main__.py` that sets up `sys.path` and calls the entry function

### Dependency Resolution Flow

```
User lists: packages=[flask, webview]
        ↓
resolve_dependencies() reads importlib.metadata
        ↓
Recursively resolves 'requires' field
        ↓
PEP 508 markers filter platform-specific deps
        ↓
copy_modules() + _copy_dist_info()
```

### Logo Conversion Flow

```
logo=mylogo.png
        ↓
ensure_valid_icon() detects format
        ↓
├── ICO: used directly
├── PNG: png_to_ico() (Pillow or wrapper)
└── JPG/other: _convert_with_pillow() (requires Pillow)
        ↓
Generates <name>_converted.ico (original file unchanged)
```

## Examples

The `examples/` directory contains sample projects:

- **console/** — Console application with custom logos (PNG/JPG/ICO test configs)
- **pyqt5/** — PyQt5 GUI application
- **pywebview/** — pywebview simple browser
- **pygame/** — Pygame example
- **streamlit/** — Streamlit app packaging

Run an example:

```bash
cd examples/console
seiya installer_logo_png.cfg
```

## Build Output Structure

After running `seiya installer.cfg`, the `build/nsis/` directory contains:

```
build/nsis/
├── MyApp.exe                        # Generated .exe launcher
├── MyApp_1.0.exe                    # NSIS installer package
├── installer.nsi                    # NSIS script
├── <logo>_converted.ico             # Converted ICO (if source was PNG/JPG)
├── Python/                          # Embedded Python
│   ├── python.exe
│   ├── pythonw.exe
│   └── ...
├── pkgs/                            # All Python packages
│   ├── flask/
│   ├── jinja2/                      # Auto-resolved transitive dep
│   ├── flask-3.0.0.dist-info/       # Metadata directory
│   └── ...
└── msvcrt/                          # MSVC runtime (optional)
```

## Installation

### From PyPI

```bash
pip install seiya
pip install "seiya[image]"     # With JPG/BMP/GIF/WEBP support
```

### From Source

```bash
git clone https://github.com/seiya-builder/seiya.git
cd seiya
pip install -e .
```

### Requirements

- Python 3.8+
- NSIS (for building installers)
- Optional: Pillow (for JPG/BMP/GIF/WEBP logo support)

## Compatibility

Seiya is fully backward compatible with pynsist:

- ✅ All config file formats (`[Application]`, `[Python]`, `[Include]`, `[Build]`, `[Shortcut]`, `[Command]`)
- ✅ `entry_point` and `script` launch methods
- ✅ `pypi_wheels` and `local_wheels` dependency acquisition
- ✅ Custom NSI templates
- ✅ Extra files inclusion
- ✅ License files
- ✅ Multiple shortcuts
- ✅ Command-line tools (`[Command]` sections)
- ✅ The `pynsist` command still works as an alias

**New features added by Seiya:**
- 🆕 `seiya` command (in addition to `pynsist`)
- 🆕 `logo=` config option (alias for `icon=`, supports more formats)
- 🆕 PNG/JPG/BMP/GIF/WEBP auto-conversion to ICO
- 🆕 Auto transitive dependency resolution
- 🆕 Platform-specific dependency filtering
- 🆕 `.dist-info` metadata auto-copy
- 🆕 `.exe` launchers (replaces `.launch.pyw`)

## FAQ

### "invalid icon file" error

This error occurs with original pynsist when using PNG/JPG images. Seiya automatically converts image formats to ICO, so this error should not occur. Install Pillow for full format support: `pip install Pillow`.

### JPG conversion fails

JPG conversion requires Pillow. Install it: `pip install Pillow`.

### `ModuleNotFoundError` at runtime

Check that all direct dependencies are listed in `packages=`. Transitive dependencies should be auto-resolved; if not, verify the package metadata is intact in your Python environment.

### `PackageNotFoundError: No package metadata was found for xxx`

Seiya auto-copies `.dist-info` directories. If this error persists, verify the package is properly installed in your build environment.

### Build directory locked (`FileExistsError`)

Stop any processes using the build directory and remove it:

```powershell
Get-Process | Where-Object { $_.Name -match "AppName|pythonw" } | Stop-Process -Force
Remove-Item -Recurse -Force "build/nsis"
```

## License

MIT License — see [LICENSE](LICENSE).

- Original pynsist code: Copyright © 2014 Thomas Kluyver
- Seiya modifications: Copyright © 2026 Seiya Contributors

This project reuses code from the following third-party projects under their respective licenses:
- [pynsist](https://github.com/takluyver/pynsist) (MIT) by Thomas Kluyver
- [distlib](https://bitbucket.org/pypa/distlib) (Python Software Foundation License) — provides launcher EXE binaries
- `nsist/_system_path.py` (BSD license) — see file header for details
- `nsist/glossyorb.ico` (CC Attribution 3.0) — by Mysitemyway.com

## Acknowledgments

Seiya is based on [pynsist](https://github.com/takluyver/pynsist) by Thomas Kluyver. Thanks to the pynsist contributors and the distlib project for the launcher mechanism.

## Disclaimer

Seiya is an independent project and is **not** affiliated with, endorsed by, or sponsored by the original pynsist project or its author. The `pynsist` command-line entry point is provided solely for backward compatibility with existing user configuration files, and does not imply any relationship with the original project. All trademarks belong to their respective owners.
