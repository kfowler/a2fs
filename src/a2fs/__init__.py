"""
Apple DOS 3.3 FUSE Filesystem
A FUSE filesystem implementation for mounting Apple DOS 3.3 disk images.
"""

from .dos33fs import AppleDOS33FS, mount

__version__ = "0.1.0"
__all__ = ["AppleDOS33FS", "mount"]
