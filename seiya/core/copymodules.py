import importlib
import importlib.abc
import importlib.machinery
import importlib.metadata
import os
import re
import shutil
import sys
import tempfile
import zipfile, zipimport
import fnmatch
import logging
from functools import partial

from .util import normalize_path

pjoin = os.path.join
logger = logging.getLogger(__name__)

PY2 = sys.version_info[0] == 2
running_python  = '.'.join(str(x) for x in sys.version_info[:2])

class ExtensionModuleMismatch(ImportError):
    pass

extensionmod_errmsg = """Found an extension module that will not be usable on %s:
%s
Put Windows packages in pynsist_pkgs/ to avoid this."""

def check_ext_mod(path, target_python):
    """If path is an extension module, check that it matches target platform.
    
    It should be for Windows and we should be running on the same version
    of Python that we're targeting. Raises ExtensionModuleMismatch if not.
    
    Does nothing if path is not an extension module.
    """
    if path.endswith('.so'):
        raise ExtensionModuleMismatch(extensionmod_errmsg % ('Windows', path))
    elif path.endswith('.pyd') and not target_python.startswith(running_python):
        # TODO: From Python 3.2, extension modules can restrict themselves
        # to a stable ABI. Can we detect this?
        raise ExtensionModuleMismatch(extensionmod_errmsg % ('Python '+target_python, path))

def check_package_for_ext_mods(path, target_python):
    """Walk the directory path, calling :func:`check_ext_mod` on each file.
    """
    for dirpath, dirnames, filenames in os.walk(path):
        for filename in filenames:
            check_ext_mod(os.path.join(path, dirpath, filename), target_python)

def copy_zipmodule(loader, modname, target):
    """Copy a module or package out of a zip file to the target directory."""
    file = loader.get_filename(modname)
    assert file.startswith(loader.archive)
    path_in_zip = file[len(loader.archive+'/'):]
    zf = zipfile.ZipFile(loader.archive)

    # If the packages are in a subdirectory, extracting them recreates the
    # directory structure from the zip file. So extract to a temp dir first,
    # and then copy the modules to target.
    tempdir = tempfile.mkdtemp()
    if loader.is_package(modname):
        # Extract everything in a folder
        pkgdir, basename = os.path.split(path_in_zip)
        assert basename.startswith('__init__')
        pkgfiles = [f for f in zf.namelist() if f.startswith(pkgdir)]
        zf.extractall(tempdir, pkgfiles)
        shutil.copytree(pjoin(tempdir, pkgdir), pjoin(target, modname))
    else:
        # Extract a single file
        zf.extract(path_in_zip, tempdir)
        shutil.copy2(pjoin(tempdir, path_in_zip), target)

    shutil.rmtree(tempdir)

def copytree_ignore_callback(excludes, pkgdir, modname, directory, files):
    """This is being called back by our shutil.copytree call to implement the
    'exclude' feature.
    """
    ignored = set()

    # Filter by file names relative to the build directory
    reldir = os.path.relpath(directory, pkgdir)
    target = os.path.join('pkgs', modname, reldir)
    files = [normalize_path(os.path.join(target, fname)) for fname in files]
    # Execute all patterns
    for pattern in excludes + ['*.pyc']:
        ignored.update([
            os.path.basename(fname)
            for fname in fnmatch.filter(files, pattern)
        ])

    return ignored


class ModuleCopier:
    """Finds and copies importable Python modules and packages.

    This is the Python >3.3 version and uses the `importlib` package to
    locate modules.
    """
    def __init__(self, py_version, path=None):
        self.py_version = py_version
        self.path = path if (path is not None) else ([''] + sys.path)

    def copy(self, modname, target, exclude):
        """Copy the importable module 'modname' to the directory 'target'.

        modname should be a top-level import, i.e. without any dots.
        Packages are always copied whole.

        This can currently copy regular filesystem files and directories,
        and extract modules and packages from appropriately structured zip
        files.
        """
        spec = importlib.machinery.PathFinder.find_spec(modname, self.path)
        if spec is None:
            raise ImportError('Could not find %r' % modname)
        loader = spec.loader
        if loader is None:
            # Namespace package (no __init__.py) — copy the entire directory.
            # This handles packages like pywin32's 'win32' and 'win32comext'
            # that contain .pyd/.py files but lack an __init__.py.
            search_locations = getattr(spec, 'submodule_search_locations', None) or []
            for location in search_locations:
                if os.path.isdir(location):
                    dest = os.path.join(target, modname)
                    if os.path.exists(dest):
                        return
                    if exclude:
                        shutil.copytree(
                            location, dest,
                            ignore=partial(copytree_ignore_callback, exclude, location, modname)
                        )
                    else:
                        shutil.copytree(
                            location, dest,
                            ignore=shutil.ignore_patterns('*.pyc', '__pycache__')
                        )
                    return
            raise ImportError('Cannot bundle namespace package %r (directory not found)' % modname)

        pkg = loader.is_package(modname)

        if isinstance(loader, importlib.machinery.ExtensionFileLoader):
            check_ext_mod(loader.path, self.py_version)
            shutil.copy2(loader.path, target)

        elif isinstance(loader, importlib.abc.FileLoader):
            file = loader.get_filename(modname)
            if pkg:
                pkgdir, basename = os.path.split(file)
                assert basename.startswith('__init__')
                check_package_for_ext_mods(pkgdir, self.py_version)
                dest = os.path.join(target, modname)
                if exclude:
                    shutil.copytree(
                        pkgdir, dest,
                        ignore=partial(copytree_ignore_callback, exclude, pkgdir, modname)
                    )
                else:
                    # Don't use our exclude callback if we don't need to,
                    # as it slows things down.
                    shutil.copytree(
                        pkgdir, dest,
                        ignore=shutil.ignore_patterns('*.pyc')
                    )
            else:
                shutil.copy2(file, target)

        elif isinstance(loader, zipimport.zipimporter):
            copy_zipmodule(loader, modname, target)


def _eval_marker(marker, target_sys_platform='win32', target_py_version=(3, 12)):
    """Minimal PEP 508 marker evaluator for common cases.

    Returns True if the requirement should be included on the target platform,
    False otherwise. Handles the marker types commonly found in package
    metadata: extra, sys_platform, platform_system, os_name, python_version.
    Unknown markers default to True (include) to be safe.
    """
    marker = marker.strip()
    if not marker:
        return True

    # Skip optional dependencies (extra == "...")
    if 'extra' in marker:
        return False

    # Check sys_platform (e.g., 'sys_platform == "darwin"')
    m = re.search(r'sys_platform\s*==\s*["\']([^"\']+)["\']', marker)
    if m:
        return m.group(1) == target_sys_platform

    # Check platform_system (e.g., 'platform_system == "Windows"')
    m = re.search(r'platform_system\s*==\s*["\']([^"\']+)["\']', marker)
    if m:
        return m.group(1) == 'Windows'

    # Check os_name (e.g., 'os_name == "nt"')
    m = re.search(r'os_name\s*==\s*["\']([^"\']+)["\']', marker)
    if m:
        return m.group(1) == 'nt'

    # Check python_version (e.g., 'python_version < "3.9"')
    m = re.search(r'python_version\s*([<>=!~]+)\s*["\'](\d+)\.(\d+)["\']', marker)
    if m:
        op, major, minor = m.group(1), int(m.group(2)), int(m.group(3))
        target = target_py_version
        req_ver = (major, minor)
        if op == '<':
            return target < req_ver
        elif op == '<=':
            return target <= req_ver
        elif op == '>':
            return target > req_ver
        elif op == '>=':
            return target >= req_ver
        elif op == '==':
            return target == req_ver
        elif op == '!=':
            return target != req_ver

    # Unknown marker - include to be safe
    return True


def resolve_dependencies(modnames, py_version=None):
    """Resolve all transitive dependencies of the given module names.

    Uses importlib.metadata to find dependencies declared in package metadata.
    This way, the user only needs to list direct dependencies (e.g. flask,
    webview, comtypes) and all sub-dependencies (jinja2, werkzeug, bottle,
    proxy_tools, typing_extensions, etc.) are automatically included.

    Platform-specific dependencies (e.g. pyobjc-* on macOS) are filtered out
    so only Windows-compatible dependencies are included.
    """
    # Parse target Python version for marker evaluation
    target_py_version = None
    if py_version:
        try:
            parts = py_version.split('.')
            target_py_version = (int(parts[0]), int(parts[1]))
        except (ValueError, IndexError):
            pass
    if target_py_version is None:
        target_py_version = (3, 12)

    # Build mapping: top-level import name -> distribution name
    # importlib.metadata works with distribution names, not import names
    all_distributions = {d.metadata['Name'].lower(): d for d in importlib.metadata.distributions()}

    # Map import names to distribution names via top_level files or heuristics
    import_to_dist = {}
    for dist in importlib.metadata.distributions():
        name = dist.metadata['Name']
        if not name:
            continue
        # Try to find top-level modules from the distribution
        try:
            tops = dist.read_text('top_level.txt')
            if tops:
                for top in tops.strip().splitlines():
                    # top_level.txt may contain paths like 'win32\lib\afxres';
                    # take only the top-level component.
                    top = top.strip().replace('\\', '/').split('/')[0]
                    if not top:
                        continue
                    import_to_dist[top.lower().replace('-', '_')] = name.lower()
            else:
                # Fallback: assume import name == dist name (with - → _)
                import_to_dist[name.lower().replace('-', '_')] = name.lower()
        except Exception:
            import_to_dist[name.lower().replace('-', '_')] = name.lower()

    resolved = set()
    # Map each resolved module to its distribution name (for .dist-info copying)
    mod_to_distname = {}
    queue = list(modnames)

    while queue:
        modname = queue.pop(0)
        modkey = modname.lower().replace('-', '_')
        if modkey in resolved:
            continue
        resolved.add(modkey)

        # Find the distribution for this import name
        distname = import_to_dist.get(modkey, modkey)
        dist = all_distributions.get(distname)
        if dist is None:
            # Try exact name
            dist = all_distributions.get(modkey)
        if dist is None:
            logger.debug('No metadata found for %r, skipping dependency resolution', modname)
            continue

        dist_name_lower = dist.metadata['Name'].lower()
        mod_to_distname[modkey] = dist_name_lower

        # Also add all top-level modules of this distribution to the resolved set.
        # This handles cases like pythonnet where 'clr.py' is a separate top-level
        # module belonging to the pythonnet distribution.
        try:
            tops = dist.read_text('top_level.txt')
            if tops:
                for top in tops.strip().splitlines():
                    # top_level.txt may contain paths like 'win32\lib\afxres';
                    # take only the top-level component.
                    top = top.strip().replace('\\', '/').split('/')[0]
                    if not top:
                        continue
                    topkey = top.lower().replace('-', '_')
                    if topkey not in resolved:
                        # Add to queue so its dependencies are also resolved
                        queue.append(top)
                        mod_to_distname[topkey] = dist_name_lower
        except Exception:
            pass

        # Get requires() and resolve each dependency
        try:
            requires = dist.requires or []
        except Exception:
            requires = []

        for req in requires:
            # Parse requirement string: "flask>=2.0" → "flask"
            # Handle markers like '; sys_platform == "darwin"' or '; extra == "test"'
            if ';' in req:
                req_part, marker = req.split(';', 1)
                if not _eval_marker(marker, target_py_version=target_py_version):
                    continue
                req = req_part
            # Handle old-style "package (>=version)" format
            if '(' in req:
                req = req.split('(')[0].strip()
            # Extract package name (before any version specifier)
            depname = req.strip().split('>')[0].split('<')[0].split('=')[0].split('!')[0].split('~')[0].strip()
            if not depname:
                continue
            depkey = depname.lower().replace('-', '_')
            if depkey not in resolved:
                queue.append(depname)

    # Convert back to import-style names (use _ for - in package names)
    result = []
    for name in resolved:
        # Normalize: distribution names use -, import names use _
        result.append(name.replace('-', '_'))

    logger.info('Resolved dependencies: %s', result)
    logger.info('Module to distribution mapping: %s', mod_to_distname)
    return result, mod_to_distname


def _copy_dist_info(target, mod_to_distname):
    """Copy .dist-info metadata directories for resolved packages.

    Many packages call importlib.metadata.version() or similar at runtime,
    which requires the .dist-info metadata to be present.
    """
    # Get unique distribution names
    distnames = set(mod_to_distname.values())
    if not distnames:
        return

    # Find all .dist-info directories in site-packages
    for dist in importlib.metadata.distributions():
        dist_name_lower = dist.metadata['Name'].lower()
        if dist_name_lower not in distnames:
            continue
        # dist._path is the path to the .dist-info directory
        dist_info_path = dist._path
        if not dist_info_path or not os.path.isdir(str(dist_info_path)):
            continue
        dest = pjoin(target, os.path.basename(str(dist_info_path)))
        if os.path.exists(dest):
            continue
        try:
            shutil.copytree(str(dist_info_path), dest)
            logger.debug('Copied metadata: %s', os.path.basename(str(dist_info_path)))
        except Exception as e:
            logger.warning('Failed to copy metadata for %s: %s', dist_name_lower, e)


def copy_modules(modnames, target, py_version, path=None, exclude=None):
    """Copy the specified importable modules to the target directory.

    By default, it finds modules in :data:`sys.path` - this can be overridden
    by passing the path parameter.

    Transitive dependencies are automatically resolved using package metadata,
    so the user only needs to list direct dependencies.
    """
    # Auto-resolve transitive dependencies
    modnames, mod_to_distname = resolve_dependencies(modnames, py_version=py_version)

    mc = ModuleCopier(py_version, path)
    files_in_target_noext = [os.path.splitext(f)[0] for f in os.listdir(target)]

    for modname in modnames:
        if modname in files_in_target_noext:
            # Already there, no need to copy it.
            continue
        try:
            mc.copy(modname, target, exclude)
        except ImportError as e:
            # Some packages (e.g. pywin32) declare many top-level modules in
            # top_level.txt that are not importable in the build environment
            # (pyd files needing post-install, optional sub-systems like mapi,
            # exchange, pythonwin, etc.). Skip these with a warning instead of
            # aborting the entire build.
            logger.warning('Could not copy module %r: %s', modname, e)

    # Copy .dist-info metadata directories so importlib.metadata works at runtime
    _copy_dist_info(target, mod_to_distname)

    if not modnames:
        # NSIS abhors an empty folder, so give it a file to find.
        with open(os.path.join(target, 'placeholder'), 'w') as f:
            f.write('This file only exists so NSIS finds something in this directory.')
