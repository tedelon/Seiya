"""Seiya - Enhanced Python Windows Installer Builder

Seiya is an enhanced fork of pynsist that builds Windows installers for Python
applications. It bundles Python itself, so you can distribute your application
to people who don't have Python installed.

Attribution:
  This is a derivative work based on pynsist by Thomas Kluyver
  (https://github.com/takluyver/pynsist), distributed under the MIT License.
  Original copyright (c) 2014 Thomas Kluyver.
  Seiya modifications copyright (c) 2026 Seiya Contributors.
  Seiya is not affiliated with or endorsed by the original pynsist project.

Key enhancements over pynsist:
  - Generates standalone .exe launchers (using distlib launcher mechanism)
  - Auto-resolves transitive dependencies (only list direct dependencies)
  - Supports custom logos in PNG, JPG, ICO, BMP, GIF, WEBP formats
  - Filters platform-specific dependencies (excludes macOS/Linux only packages)
  - Copies .dist-info metadata for runtime importlib.metadata support

Compatibility:
  - Fully compatible with existing pynsist config files
  - The 'pynsist' command still works as an alias (backward compatibility only)
  - New 'seiya' command provides the same functionality

Usage:
    seiya installer.cfg
    python -m seiya installer.cfg
    pynsist installer.cfg  (backward compatible)
"""
from seiya.core import (
    InstallerBuilder,
    InputError,
    split_entry_point,
    main as _nsist_main,
    __version__ as _nsist_version,
)

__version__ = '1.0.2'

# Re-export public API for programmatic use
InstallerBuilder = InstallerBuilder
InputError = InputError
split_entry_point = split_entry_point


def main(argv=None):
    """Command-line entry point for Seiya.

    Behaves identically to pynsist's main(), accepting the same arguments:
        seiya <config_file> [--no-makensis]

    This ensures full backward compatibility while providing a distinct
    command name to avoid conflicts with the original pynsist package.
    """
    return _nsist_main(argv)


if __name__ == '__main__':
    main()
