"""Build NSIS installers for Python applications.
"""
import io
import logging
import ntpath
import operator
import os
from pathlib import Path
import re
import shutil
from subprocess import call
import sys
import fnmatch
import zipfile

if os.name == 'nt':
    import winreg
else:
    winreg = None

from .configreader import get_installer_builder_args
from .commands import prepare_bin_directory, find_exe
from .copymodules import copy_modules
from .nsiswriter import NSISFileWriter
from .wheels import WheelGetter
from .util import download, get_cache_dir, normalize_path, ensure_valid_icon

__version__ = '2.8'

pjoin = os.path.join
logger = logging.getLogger(__name__)

_PKGDIR = os.path.abspath(os.path.dirname(__file__))
DEFAULT_PY_VERSION = '3.6.3'
DEFAULT_BUILD_DIR = pjoin('build', 'nsis')
DEFAULT_ICON = pjoin(_PKGDIR, 'glossyorb.ico')
if os.name == 'nt' and sys.maxsize == (2**63)-1:
    DEFAULT_BITNESS = 64
else:
    DEFAULT_BITNESS = 32

def find_makensis_win():
    """Locate makensis.exe on Windows by querying the registry"""
    try:
        nsis_install_dir = winreg.QueryValue(winreg.HKEY_LOCAL_MACHINE, 'SOFTWARE\\NSIS')
    except OSError:
        nsis_install_dir = winreg.QueryValue(winreg.HKEY_LOCAL_MACHINE, 'SOFTWARE\\Wow6432Node\\NSIS')

    return pjoin(nsis_install_dir, 'makensis.exe')

class InputError(ValueError):
    def __init__(self, param, value, expected):
        self.param = param
        self.value = value
        self.expected = expected

    def __str__(self):
        return "{e.value!r} is not valid for {e.param}, expected {e.expected}".format(e=self)


def split_entry_point(ep: str):
    """Like ep.split(':'), but with extra checks and helpful errors"""
    module, _, func = ep.partition(':')
    if all([s.isidentifier() for s in module.split('.')]) and func.isidentifier():
        return module, func

    raise InputError(
        'entry point', ep, "'mod:func', so 'from mod import func' works"
    )


class InstallerBuilder(object):
    """Controls building an installer. This includes three main steps:

    1. Arranging the necessary files in the build directory.
    2. Filling out the template NSI file to control NSIS.
    3. Running ``makensis`` to build the installer.

    :param str appname: Application name
    :param str version: Application version
    :param dict shortcuts: Dictionary keyed by shortcut name, containing
            dictionaries whose keys match the fields of :ref:`shortcut_config`
            in the config file
    :param str publisher: Publisher name
    :param str icon: Path to an icon for the application
    :param list packages: List of strings for importable packages to include
    :param dict commands: Dictionary keyed by command name, containing dicts
            defining the commands, as in the config file.
    :param list pypi_wheel_reqs: Package specifications to fetch from PyPI as wheels
    :param extra_wheel_sources: Directory paths to find wheels in.
    :type extra_wheel_sources: list of Path objects
    :param local_wheels: Glob paths matching wheel files to include
    :type local_wheels: list of str
    :param list extra_files: List of 2-tuples (file, destination) of files to include
    :param list exclude: Paths of files to exclude that would otherwise be included
    :param str py_version: Full version of Python to bundle
    :param int py_bitness: Bitness of bundled Python (32 or 64)
    :param str py_format: (deprecated) 'bundled'. Use Pynsist 1.x for
            'installer' option.
    :param bool inc_msvcrt: True to include the Microsoft C runtime with 'bundled'
            Python.
    :param str build_dir: Directory to run the build in
    :param str installer_name: Filename of the installer to produce
    :param str nsi_template: Path to a template NSI file to use
    """
    def __init__(self, appname, version, shortcuts, *, publisher=None,
                icon=DEFAULT_ICON, packages=None, extra_files=None,
                py_version=DEFAULT_PY_VERSION, py_bitness=DEFAULT_BITNESS,
                py_format='bundled', inc_msvcrt=True, build_dir=DEFAULT_BUILD_DIR,
                installer_name=None, nsi_template=None,
                exclude=None, pypi_wheel_reqs=None, extra_wheel_sources=None,
                local_wheels=None, commands=None, license_file=None):
        self.appname = appname
        self.version = version
        self.publisher = publisher
        self.shortcuts = shortcuts
        self.icon = icon
        self.packages = packages or []
        self.exclude = [normalize_path(p) for p in (exclude or [])]
        self.extra_files = extra_files or []
        self.pypi_wheel_reqs = pypi_wheel_reqs or []
        self.extra_wheel_sources = extra_wheel_sources or []
        self.local_wheels = local_wheels or []
        self.commands = commands or {}
        self.license_file = license_file

        # Python options
        self.py_version = py_version
        if not self._py_version_pattern.match(py_version):
            if not os.environ.get('PYNSIST_PY_PRERELEASE'):
                raise InputError('py_version', py_version,
                                 "a full Python version like '3.4.0'")
        if self.py_version_tuple < (3, 5):
            raise InputError('py_version', py_version,
                             "Python >= 3.5.0 (use Pynsist 1.x for older Python.")
        self.py_bitness = py_bitness
        if py_bitness not in {32, 64}:
            raise InputError('py_bitness', py_bitness, "32 or 64")
        self.py_major_version = self.py_qualifier = '.'.join(self.py_version.split('.')[:2])
        if self.py_bitness == 32:
            self.py_qualifier += '-32'

        if py_format == 'installer':
            raise InputError('py_format', py_format, "'bundled' (use Pynsist 1.x for 'installer')")
        elif py_format != 'bundled':
            raise InputError('py_format', py_format, "'bundled'")

        self.inc_msvcrt = inc_msvcrt

        # Build details
        self.build_dir = build_dir
        self.installer_name = installer_name or self.make_installer_name()
        self.nsi_template = nsi_template
        if self.nsi_template is None:
            if self.inc_msvcrt:
                self.nsi_template = 'pyapp_msvcrt.nsi'
            else:
                self.nsi_template = 'pyapp.nsi'

        self.nsi_file = pjoin(self.build_dir, 'installer.nsi')

        # To be filled later
        self.install_files = []
        self.install_dirs = []
        self.msvcrt_files = []

    _py_version_pattern = re.compile(r'\d\.\d+\.\d+$')

    @property
    def py_version_tuple(self):
        parts = self.py_version.split('.')
        return int(parts[0]), int(parts[1])

    def make_installer_name(self):
        """Generate the filename of the installer exe

        e.g. My_App_1.0.exe
        """
        s = self.appname + '_' + self.version + '.exe'
        return s.replace(' ', '_')

    def _python_download_url_filename(self):
        version = self.py_version
        bitness = self.py_bitness
        filename = 'python-{}-embed-{}.zip'.format(version,
                                   'amd64' if bitness==64 else 'win32')

        version_minus_prerelease = re.sub(r'(a|b|rc)\d+$', '', self.py_version)
        return 'https://www.python.org/ftp/python/{0}/{1}'.format(
                version_minus_prerelease, filename), filename

    def fetch_python_embeddable(self):
        """Fetch the embeddable Windows build for the specified Python version

        It will be unpacked into the build directory.

        In addition, any ``*._pth`` files found therein will have the pkgs path
        appended to them.
        """
        url, filename = self._python_download_url_filename()
        cache_file = get_cache_dir(ensure_existence=True) / filename

        # Check if cache file needs (re)downloading: missing, too small, or corrupted
        need_download = True
        if cache_file.is_file():
            if cache_file.stat().st_size < 1000:
                logger.warning('Cached Python zip is too small, re-downloading...')
            else:
                try:
                    with zipfile.ZipFile(str(cache_file)) as z:
                        z.testzip()  # Verify integrity
                    need_download = False
                except (zipfile.BadZipFile, Exception):
                    logger.warning('Cached Python zip is corrupted, re-downloading...')

        if need_download:
            try:
                cache_file.unlink()
            except FileNotFoundError:
                pass
            logger.info('Downloading embeddable Python build...')
            logger.info('Getting %s', url)
            download(url, cache_file)

        logger.info('Unpacking Python...')
        python_dir = pjoin(self.build_dir, 'Python')

        with zipfile.ZipFile(str(cache_file)) as z:
            z.extractall(python_dir)

        # Manipulate any *._pth files so the default paths AND pkgs directory
        # ends up in sys.path. Please see:
        # https://docs.python.org/3/using/windows.html#finding-modules
        # for more information.
        pth_files = [f for f in os.listdir(python_dir)
                     if os.path.isfile(pjoin(python_dir, f))
                     and f.endswith('._pth')]
        for pth in pth_files:
            with open(pjoin(python_dir, pth), 'a+b') as f:
                f.write(b'\r\n..\\pkgs\r\nimport site\r\n')

        self.install_dirs.append(('Python', '$INSTDIR'))

    def prepare_msvcrt(self):
        arch = 'x64' if self.py_bitness == 64 else 'x86'
        src = pjoin(_PKGDIR, 'msvcrt', arch)
        dst = pjoin(self.build_dir, 'msvcrt')
        self.msvcrt_files = sorted(os.listdir(src))

        shutil.copytree(src, dst)

    SCRIPT_TEMPLATE = """#!python{qualifier}
import sys, os
import site
scriptdir, script = os.path.split(os.path.abspath(__file__))
installdir = scriptdir  # for compatibility with commands
pkgdir = os.path.join(scriptdir, 'pkgs')
sys.path.insert(0, pkgdir)
# Ensure .pth files in pkgdir are handled properly
site.addsitedir(pkgdir)
os.environ['PYTHONPATH'] = pkgdir + os.pathsep + os.environ.get('PYTHONPATH', '')

# APPDATA should always be set, but in case it isn't, try user home
# If none of APPDATA, HOME, USERPROFILE or HOMEPATH are set, this will fail.
appdata = os.environ.get('APPDATA', None) or os.path.expanduser('~')

if 'pythonw' in sys.executable:
    # Running with no console - send all stdstream output to a file.
    kw = {{'errors': 'replace'}} if (sys.version_info[0] >= 3) else {{}}
    sys.stdout = sys.stderr = open(os.path.join(appdata, script+'.log'), 'w', **kw)
else:
    # In a console. But if the console was started just for this program, it
    # will close as soon as we exit, so write the traceback to a file as well.
    def excepthook(etype, value, tb):
        "Write unhandled exceptions to a file and to stderr."
        import traceback
        traceback.print_exception(etype, value, tb)
        with open(os.path.join(appdata, script+'.log'), 'w') as f:
            traceback.print_exception(etype, value, tb, file=f)
    sys.excepthook = excepthook

{extra_preamble}

if __name__ == '__main__':
    from {module} import {func}
    {func}()
"""

    LAUNCHER_SCRIPT_TEMPLATE = """# -*- coding: utf-8 -*-
import sys, os
import site
# With distlib launcher, sys.argv[0] is the launcher exe path (e.g. TOOL.exe)
# sys.executable is the Python interpreter (e.g. ...\\Python\\pythonw.exe)
scriptdir, script = os.path.split(os.path.abspath(sys.argv[0]))
# Strip .exe extension so log file is named 'TOOL.log' rather than 'TOOL.exe.log'
if script.lower().endswith('.exe'):
    script = script[:-4]
installdir = os.path.dirname(os.path.dirname(sys.executable))  # install root
pkgdir = os.path.join(installdir, 'pkgs')
sys.path.insert(0, pkgdir)
# Ensure .pth files in pkgdir are handled properly
site.addsitedir(pkgdir)
os.environ['PYTHONPATH'] = pkgdir + os.pathsep + os.environ.get('PYTHONPATH', '')

# Allowing .dll files in Python directory to be found
os.environ['PATH'] += ';' + os.path.dirname(sys.executable)

# APPDATA should always be set, but in case it isn't, try user home
# If none of APPDATA, HOME, USERPROFILE or HOMEPATH are set, this will fail.
appdata = os.environ.get('APPDATA', None) or os.path.expanduser('~')

if 'pythonw' in sys.executable:
    # Running with no console - send all stdstream output to a file.
    kw = {{'errors': 'replace'}} if (sys.version_info[0] >= 3) else {{}}
    sys.stdout = sys.stderr = open(os.path.join(appdata, script+'.log'), 'w', **kw)
else:
    # In a console. But if the console was started just for this program, it
    # will close as soon as we exit, so write the traceback to a file as well.
    def excepthook(etype, value, tb):
        "Write unhandled exceptions to a file and to stderr."
        import traceback
        traceback.print_exception(etype, value, tb)
        with open(os.path.join(appdata, script+'.log'), 'w') as f:
            traceback.print_exception(etype, value, tb, file=f)
    sys.excepthook = excepthook

{extra_preamble}

if __name__ == '__main__':
    from {module} import {func}
    {func}()
"""

    def write_script(self, entrypt, target, extra_preamble=''):
        """Write a launcher script from a 'module:function' entry point

        py_version and py_bitness are used to write an appropriate shebang line
        for the PEP 397 Windows launcher.
        """
        module, func = split_entry_point(entrypt)
        with open(target, 'w') as f:
            f.write(self.SCRIPT_TEMPLATE.format(qualifier=self.py_qualifier,
                    module=module, func=func, extra_preamble=extra_preamble))

        pkg = module.split('.')[0]
        if pkg not in self.packages:
            self.packages.append(pkg)

    def prepare_icons(self):
        """Validate and convert icon files to valid ICO format.

        If an icon file is a PNG image, it will be automatically converted to
        ICO format (PNG-compressed ICO, supported on Windows Vista+).
        This handles both the main installer icon and all shortcut icons.
        The main installer icon is also copied to the build directory so NSIS
        can find it when building the installer.
        """
        # Validate main installer icon
        try:
            self.icon = ensure_valid_icon(self.icon)
        except ValueError as e:
            logger.error(str(e))
            raise

        # Validate all shortcut icons
        for scname, sc in self.shortcuts.items():
            if 'icon' in sc:
                try:
                    sc['icon'] = ensure_valid_icon(sc['icon'])
                except ValueError as e:
                    logger.error(str(e))
                    raise

        # Copy main installer icon to build directory for NSIS
        icon_basename = os.path.basename(self.icon)
        dest_icon = pjoin(self.build_dir, icon_basename)
        if os.path.abspath(self.icon) != os.path.abspath(dest_icon):
            shutil.copy2(self.icon, self.build_dir)
        # Add to install_files (avoid duplicates - prepare_shortcuts may also add it)
        icon_entry = (icon_basename, '$INSTDIR')
        if icon_entry not in self.install_files:
            self.install_files.append(icon_entry)

    def prepare_shortcuts(self):
        """Prepare shortcut files in the build directory.

        If entry_point is specified, generate a standalone .exe launcher using
        distlib's launcher mechanism (same as commands). If script is specified,
        copy to the build directory. Prepare target and parameters for these
        shortcuts.

        Also copies shortcut icons.
        """
        files = set()
        for scname, sc in self.shortcuts.items():
            if not sc.get('target'):
                if sc.get('entry_point'):
                    script_base = scname.replace(' ', '_')
                    exe_name = script_base + '.exe'
                    exe_path = pjoin(self.build_dir, exe_name)
                    console = sc['console']

                    # 1. Get the base launcher exe from distlib
                    with open(find_exe(self.py_bitness, console=console), 'rb') as f:
                        launcher_b = f.read()

                    # 2. Shebang: Python executable to run with
                    # Use a relative path so the launcher finds Python relative
                    # to its own location (the launcher exe is at $INSTDIR root,
                    # same level as the Python/ directory).
                    # Note: <launcher_dir> is NOT a token the distlib launcher
                    # resolves — it's just a documentation placeholder. Relative
                    # paths like "Python\pythonw.exe" are resolved by the launcher
                    # relative to the launcher exe's directory.
                    if console:
                        shebang = b"#!Python\\python.exe\r\n"
                    else:
                        shebang = b"#!Python\\pythonw.exe\r\n"

                    # 3. The script to run, inside a zip file
                    specified_preamble = sc.get('extra_preamble', None)
                    if isinstance(specified_preamble, str):
                        # Filename
                        extra_preamble = io.open(specified_preamble, encoding='utf-8')
                    elif specified_preamble is None:
                        extra_preamble = io.StringIO()  # Empty
                    else:
                        # Passed a StringIO or similar object
                        extra_preamble = specified_preamble

                    module, func = split_entry_point(sc['entry_point'])
                    script = self.LAUNCHER_SCRIPT_TEMPLATE.format(
                        module=module, func=func,
                        extra_preamble=extra_preamble.read().rstrip(),
                    )

                    zip_bio = io.BytesIO()
                    with zipfile.ZipFile(zip_bio, 'w') as zf:
                        zf.writestr('__main__.py', script.encode('utf-8'))

                    # Put the pieces together
                    with open(exe_path, 'wb') as f:
                        f.write(launcher_b)
                        f.write(shebang)
                        f.write(zip_bio.getvalue())

                    sc['target'] = '$INSTDIR\\' + exe_name
                    sc['parameters'] = ''
                    files.add(exe_name)

                    pkg = module.split('.')[0]
                    if pkg not in self.packages:
                        self.packages.append(pkg)
                else:
                    shutil.copy2(sc['script'], self.build_dir)
                    sc['target'] = '$INSTDIR\\Python\\python{}.exe'.format(
                        '' if sc['console'] else 'w')
                    sc['script'] = os.path.basename(sc['script'])
                    sc['parameters'] = '"%s"' % ntpath.join('$INSTDIR', sc['script'])
                    files.add(sc['script'])

            shutil.copy2(sc['icon'], self.build_dir)
            sc['icon'] = os.path.basename(sc['icon'])
            files.add(sc['icon'])
        self.install_files.extend([(f, '$INSTDIR') for f in files])

    def copy_license(self):
        """
        If a license file has been specified, ensure it's copied into the
        install directory and added to the install_files list.
        """
        if self.license_file:
            shutil.copy2(self.license_file, self.build_dir)
            license_file_name = os.path.basename(self.license_file)
            self.install_files.append((license_file_name, '$INSTDIR'))


    def prepare_packages(self):
        """Move requested packages into the build directory.

        If a pynsist_pkgs directory exists, it is copied into the build
        directory as pkgs/ . Any packages not already there are found on
        sys.path and copied in.
        """
        logger.info("Copying packages into build directory...")
        build_pkg_dir = pjoin(self.build_dir, 'pkgs')

        # 1. Manually prepared packages
        if os.path.isdir('pynsist_pkgs'):
            shutil.copytree('pynsist_pkgs', build_pkg_dir)
        else:
            os.mkdir(build_pkg_dir)

        # 2. Wheels specified in pypi_wheel_reqs or in paths of local_wheels
        wg = WheelGetter(self.pypi_wheel_reqs, self.local_wheels, build_pkg_dir,
                         py_version=self.py_version, bitness=self.py_bitness,
                         extra_sources=self.extra_wheel_sources,
                         exclude=self.exclude)
        wg.get_all()

        # 3. Copy importable modules
        copy_modules(self.packages, build_pkg_dir,
                     py_version=self.py_version, exclude=self.exclude)

    def prepare_commands(self):
        for cmd in self.commands.values():
            split_entry_point(cmd['entry_point'])  # Check entry point format
        command_dir = Path(self.build_dir) / 'bin'
        command_dir.mkdir()
        prepare_bin_directory(command_dir, self.commands, bitness=self.py_bitness)
        self.install_dirs.append((command_dir.name, '$INSTDIR'))
        self.extra_files.append((pjoin(_PKGDIR, '_system_path.py'), '$INSTDIR'))

    def copytree_ignore_callback(self, directory, files):
        """This is being called back by our shutil.copytree call to implement the
        'exclude' feature.
        """
        ignored = set()

        # Filter by file names relative to the build directory
        directory = os.path.normpath(directory)
        files = [os.path.join(directory, fname) for fname in files]

        # Execute all patterns
        for pattern in self.exclude:
            ignored.update([
                os.path.basename(fname)
                for fname in fnmatch.filter(files, pattern)
            ])
        return ignored

    def copy_extra_files(self):
        """Copy a list of files into the build directory, and add them to
        install_files or install_dirs as appropriate.
        """
        # Create installer.nsi, so that a data file with the same name will
        # automatically be renamed installer.1.nsi. All the other files needed
        # in the build directory should already be in place.
        Path(self.nsi_file).touch()

        for file, destination in self.extra_files:
            file = file.rstrip('/\\')
            in_build_dir = Path(self.build_dir, os.path.basename(file))

            # Find an unused name in the build directory,
            # similar to the source filename, e.g. foo.1.txt, foo.2.txt, ...
            stem, suffix = in_build_dir.stem, in_build_dir.suffix
            n = 1
            while in_build_dir.exists():
                name = '{}.{}{}'.format(stem, n, suffix)
                in_build_dir = in_build_dir.with_name(name)
                n += 1

            if destination:
                # Normalize destination paths to Windows-style
                destination = destination.replace('/', '\\')
            else:
                destination = '$INSTDIR'

            if os.path.isdir(file):
                if self.exclude:
                    shutil.copytree(file, str(in_build_dir),
                                    ignore=self.copytree_ignore_callback)
                else:
                    # Don't use our exclude callback if we don't need to,
                    # as it slows things down.
                    shutil.copytree(file, str(in_build_dir))
                self.install_dirs.append((in_build_dir.name, destination))
            else:
                shutil.copy2(file, str(in_build_dir))
                self.install_files.append((in_build_dir.name, destination))

    def write_nsi(self):
        """Write the NSI file to define the NSIS installer.

        Most of the details of this are in the template and the
        :class:`nsist.nsiswriter.NSISFileWriter` class.
        """
        nsis_writer = NSISFileWriter(self.nsi_template, installerbuilder=self)

        logger.info('Writing NSI file to %s', self.nsi_file)
        # Sort by destination directory, so we can group them effectively
        self.install_files.sort(key=operator.itemgetter(1))
        nsis_writer.write(self.nsi_file)

    def run_nsis(self):
        """Runs makensis using the specified .nsi file

        Returns the exit code.
        """
        makensis = shutil.which('makensis')
        if (makensis is None) and os.name == 'nt':
            try:
                makensis = find_makensis_win()
            except OSError:
                pass

        if makensis is None:
            print("makensis was not found. Install NSIS and try again.")
            print("http://nsis.sourceforge.net/Download")
            return 1

        logger.info('\n~~~ Running makensis ~~~')
        return call([makensis, self.nsi_file])

    def run(self, makensis=True):
        """Run all the steps to build an installer.
        """
        try:  # Start with a clean build directory
            shutil.rmtree(self.build_dir)
        except FileNotFoundError:
            pass
        os.makedirs(self.build_dir)

        self.fetch_python_embeddable()
        if self.inc_msvcrt:
            self.prepare_msvcrt()

        # Validate/convert icons before preparing shortcuts
        self.prepare_icons()

        self.prepare_shortcuts()

        self.copy_license()

        if self.commands:
            self.prepare_commands()

        # Packages
        self.prepare_packages()

        # Extra files
        self.copy_extra_files()

        self.write_nsi()

        if makensis:
            exitcode = self.run_nsis()

            if not exitcode:
                logger.info('Installer written to %s', pjoin(self.build_dir, self.installer_name))
            return exitcode
        return 0

def main(argv=None):
    """Make an installer from the command line.

    This parses command line arguments and a config file, and calls
    :func:`all_steps` with the extracted information.
    """
    logger.setLevel(logging.INFO)
    logger.handlers = [logging.StreamHandler()]

    import argparse
    argp = argparse.ArgumentParser(prog='pynsist')
    argp.add_argument('config_file')
    argp.add_argument('--no-makensis', action='store_true',
        help='Prepare files and folders, stop before calling makensis. For debugging.'
    )
    options = argp.parse_args(argv)

    dirname, config_file = os.path.split(options.config_file)
    if dirname:
        os.chdir(dirname)

    from . import configreader
    try:
        cfg = configreader.read_and_validate(config_file)
        args = get_installer_builder_args(cfg)
    except configreader.InvalidConfig as e:
        logger.error('Error parsing configuration file:')
        logger.error(str(e))
        sys.exit(1)

    try:
        ec = InstallerBuilder(**args).run(makensis=(not options.no_makensis))
    except InputError as e:
        logger.error("Error in config values:")
        logger.error(str(e))
        sys.exit(1)

    return ec
