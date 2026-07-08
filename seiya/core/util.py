import os
import struct
import logging
from pathlib import Path
import requests
import sys

logger = logging.getLogger(__name__)


def download(url, target):
    """Download a file using requests.
    
    This is like urllib.request.urlretrieve, but requests validates SSL
    certificates by default.
    """
    if isinstance(target, Path):
        target = str(target)

    from . import __version__
    headers = {'user-agent': 'Pynsist/'+__version__}
    r = requests.get(url, headers=headers, stream=True)
    r.raise_for_status()
    with open(target, 'wb') as f:
        for chunk in r.iter_content(chunk_size=1024): 
            if chunk:
                f.write(chunk)

CACHE_ENV_VAR = 'PYNSIST_CACHE_DIR'

def get_cache_dir(ensure_existence=False):
    specified = os.environ.get(CACHE_ENV_VAR, None)

    if specified:
        p = Path(specified)
    elif os.name == 'posix' and sys.platform != 'darwin':
        # Linux, Unix, AIX, etc.
        # use ~/.cache if empty OR not set
        xdg = os.environ.get("XDG_CACHE_HOME", None) or (os.path.expanduser('~/.cache'))
        p = Path(xdg, 'pynsist')

    elif sys.platform == 'darwin':
        p = Path(os.path.expanduser('~'), 'Library/Caches/pynsist')

    else:
        # Windows (hopefully)
        local = os.environ.get('LOCALAPPDATA', None) or (os.path.expanduser('~\\AppData\\Local'))
        if local.startswith('~'):
            logger.warning("Could not find cache directory. Please set any of "
                           "these environment variables: "
                           "LOCALAPPDATA, HOME, USERPROFILE or HOMEPATH")
        p = Path(local, 'pynsist')

    if ensure_existence:
        p.mkdir(parents=True, exist_ok=True)

    return p


def normalize_path(path):
    """Normalize paths to contain "/" only"""
    return os.path.normpath(path).replace('\\', '/')


def is_valid_ico(path):
    """Check if a file is a valid ICO format file.

    A valid ICO file starts with: reserved(2 bytes=0) + type(2 bytes=1) + count(2 bytes>0)
    """
    try:
        with open(path, 'rb') as f:
            header = f.read(6)
        if len(header) < 6:
            return False
        reserved, type_, count = struct.unpack('<HHH', header)
        return reserved == 0 and type_ == 1 and count > 0
    except (IOError, OSError):
        return False


def is_png(path):
    """Check if a file is a PNG image (by magic bytes)."""
    try:
        with open(path, 'rb') as f:
            magic = f.read(8)
        # PNG magic bytes: 89 50 4E 47 0D 0A 1A 0A
        return magic == b'\x89PNG\r\n\x1a\n'
    except (IOError, OSError):
        return False


def is_jpeg(path):
    """Check if a file is a JPEG image (by magic bytes)."""
    try:
        with open(path, 'rb') as f:
            magic = f.read(3)
        # JPEG SOI marker: FF D8 FF
        return magic[:3] == b'\xff\xd8\xff'
    except (IOError, OSError):
        return False


def _read_png_dimensions(path):
    """Read width and height from a PNG file header.

    PNG IHDR chunk starts at byte 16, containing 4-byte width + 4-byte height (big-endian).
    Returns (width, height) or (0, 0) if parsing fails.
    """
    try:
        with open(path, 'rb') as f:
            f.seek(16)  # Skip signature(8) + length(4) + type(4)
            width = struct.unpack('>I', f.read(4))[0]
            height = struct.unpack('>I', f.read(4))[0]
        return width, height
    except (IOError, OSError, struct.error):
        return 0, 0


def _has_pillow():
    """Check if Pillow is available for image conversion."""
    try:
        from PIL import Image
        return True
    except ImportError:
        return False


def _convert_with_pillow(src_path, ico_path, sizes=None):
    """Convert any image format to ICO using Pillow.

    :param str src_path: Source image (PNG, JPG, etc.)
    :param str ico_path: Destination ICO file path
    :param list sizes: List of (width, height) tuples for ICO sizes.
                       Defaults to common Windows icon sizes.
    :returns: True if conversion succeeded, False otherwise.
    """
    try:
        from PIL import Image
        if sizes is None:
            sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
        img = Image.open(src_path)
        # Convert to RGBA to support transparency
        if img.mode != 'RGBA':
            img = img.convert('RGBA')
        img.save(ico_path, format='ICO', sizes=sizes)
        logger.info('Converted %s to ICO format %s using Pillow', src_path, ico_path)
        return True
    except Exception as e:
        logger.error('Pillow conversion failed: %s', e)
        return False


def png_to_ico(png_path, ico_path):
    """Convert a PNG file to ICO format.

    Uses Pillow if available (for proper multi-size ICO with alpha channel).
    Falls back to embedding PNG data directly in ICO container (PNG-compressed
    ICO, supported on Windows Vista+) when Pillow is not available.

    :param str png_path: Path to the source PNG file.
    :param str ico_path: Path to write the resulting ICO file.
    :returns: True if conversion succeeded, False otherwise.
    """
    # Prefer Pillow for better quality and multi-size support
    if _has_pillow():
        return _convert_with_pillow(png_path, ico_path)

    # Fallback: wrap PNG data in ICO container without re-encoding
    try:
        with open(png_path, 'rb') as f:
            png_data = f.read()

        width, height = _read_png_dimensions(png_path)
        # ICO stores dimensions as a single byte (0 means 256)
        w_byte = 0 if width >= 256 else width
        h_byte = 0 if height >= 256 else height

        png_size = len(png_data)
        # ICO header (6 bytes) + 1 directory entry (16 bytes) = 22 bytes offset
        data_offset = 22

        # Build ICO file:
        # Header: reserved(2)=0, type(2)=1, count(2)=1
        header = struct.pack('<HHH', 0, 1, 1)
        # Directory entry:
        #   width(1), height(1), colors(1)=0, reserved(1)=0,
        #   planes(2)=1, bitcount(2)=32, size(4)=png_size, offset(4)=data_offset
        entry = struct.pack('<BBBBHHII', w_byte, h_byte, 0, 0, 1, 32, png_size, data_offset)

        with open(ico_path, 'wb') as f:
            f.write(header)
            f.write(entry)
            f.write(png_data)

        logger.info('Converted PNG icon %s to ICO format %s', png_path, ico_path)
        return True
    except (IOError, OSError) as e:
        logger.error('Failed to convert PNG to ICO: %s', e)
        return False


def jpeg_to_ico(jpg_path, ico_path):
    """Convert a JPEG file to ICO format.

    Requires Pillow to decode JPEG data. If Pillow is not available,
    returns False with an error message.

    :param str jpg_path: Path to the source JPEG file.
    :param str ico_path: Path to write the resulting ICO file.
    :returns: True if conversion succeeded, False otherwise.
    """
    if not _has_pillow():
        logger.error('Cannot convert JPEG to ICO: Pillow is required. '
                     'Install it with: pip install Pillow')
        return False
    return _convert_with_pillow(jpg_path, ico_path)


def image_to_ico(src_path, ico_path):
    """Convert any supported image format to ICO format.

    Supports PNG, JPEG, and ICO formats. Uses Pillow for format conversion
    when available; falls back to PNG wrapper for PNG files without Pillow.

    :param str src_path: Path to the source image file.
    :param str ico_path: Path to write the resulting ICO file.
    :returns: True if conversion succeeded, False otherwise.
    """
    if is_png(src_path):
        return png_to_ico(src_path, ico_path)
    elif is_jpeg(src_path):
        return jpeg_to_ico(src_path, ico_path)
    elif _has_pillow():
        # Try Pillow for any other image format (BMP, GIF, WEBP, etc.)
        return _convert_with_pillow(src_path, ico_path)
    else:
        logger.error('Cannot convert %s: unsupported format and Pillow is not available',
                     src_path)
        return False


def ensure_valid_icon(icon_path):
    """Ensure an icon file is in valid ICO format.

    If the file is a PNG or JPEG image, it will be automatically converted to
    ICO format. The converted file is written to a temporary location next to
    the original; the original file is NOT modified.

    If the file is already a valid ICO, it is left unchanged.

    :param str icon_path: Path to the icon file (may be PNG, JPG, or ICO).
    :returns: Path to a valid ICO file (may be a temp file if conversion occurred).
    :raises ValueError: If the file cannot be converted to ICO.
    """
    if not os.path.isfile(icon_path):
        raise ValueError('Icon file not found: %s' % icon_path)

    if is_valid_ico(icon_path):
        return icon_path

    # Determine conversion target path (next to original, with _converted.ico suffix)
    base, _ = os.path.splitext(icon_path)
    tmp_path = base + '_converted.ico'

    if is_png(icon_path):
        logger.info('Icon %s is PNG format, converting to ICO...', icon_path)
        if png_to_ico(icon_path, tmp_path):
            return tmp_path
        raise ValueError('Failed to convert PNG icon to ICO: %s' % icon_path)

    if is_jpeg(icon_path):
        logger.info('Icon %s is JPEG format, converting to ICO...', icon_path)
        if jpeg_to_ico(icon_path, tmp_path):
            return tmp_path
        raise ValueError(
            'Failed to convert JPEG icon to ICO: %s. '
            'Pillow is required for JPEG conversion.' % icon_path
        )

    # Try Pillow for any other image format (BMP, GIF, WEBP, TIFF, etc.)
    if _has_pillow():
        logger.info('Icon %s is an unknown image format, trying Pillow conversion...', icon_path)
        if _convert_with_pillow(icon_path, tmp_path):
            return tmp_path
        raise ValueError('Failed to convert icon to ICO: %s' % icon_path)

    raise ValueError(
        'Icon file %s is not a valid ICO, PNG, or JPEG format. '
        'Install Pillow for additional format support: pip install Pillow' % icon_path
    )
