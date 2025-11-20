"""
Apple DOS 3.3 Filesystem Implementation
Supports reading Apple DOS 3.3 disk images (.dsk, .do formats)
"""

import os
import stat
import errno
from typing import Dict, List, Optional, Tuple
from refuse.high import FUSE, FuseOSError, Operations


class AppleDOS33Disk:
    """Apple DOS 3.3 disk image reader"""

    # DOS 3.3 constants
    SECTOR_SIZE = 256
    SECTORS_PER_TRACK = 16
    TRACKS = 35
    VTOC_TRACK = 17
    VTOC_SECTOR = 0
    CATALOG_TRACK = 17
    CATALOG_SECTOR = 15

    # File types
    FILE_TYPES = {
        0x00: 'T',  # Text
        0x01: 'I',  # Integer BASIC
        0x02: 'A',  # Applesoft BASIC
        0x04: 'B',  # Binary
        0x08: 'S',  # S type
        0x10: 'R',  # Relocatable
        0x20: 'a',  # A type
        0x40: 'b',  # B type
    }

    def __init__(self, image_path: str):
        self.image_path = image_path
        self.image_data = self._load_image()
        self.files = self._read_catalog()

    def _load_image(self) -> bytes:
        """Load the disk image into memory"""
        with open(self.image_path, 'rb') as f:
            data = f.read()
        expected_size = self.SECTOR_SIZE * self.SECTORS_PER_TRACK * self.TRACKS
        if len(data) != expected_size:
            raise ValueError(f"Invalid disk image size: {len(data)} (expected {expected_size})")
        return data

    def _get_sector(self, track: int, sector: int) -> bytes:
        """Read a sector from the disk image"""
        if track < 0 or track >= self.TRACKS:
            raise ValueError(f"Invalid track: {track}")
        if sector < 0 or sector >= self.SECTORS_PER_TRACK:
            raise ValueError(f"Invalid sector: {sector}")

        offset = (track * self.SECTORS_PER_TRACK + sector) * self.SECTOR_SIZE
        return self.image_data[offset:offset + self.SECTOR_SIZE]

    def _read_catalog(self) -> Dict[str, dict]:
        """Read the file catalog from the disk"""
        files = {}

        # Start with first catalog sector
        track = self.CATALOG_TRACK
        sector = self.CATALOG_SECTOR

        while track != 0:
            sector_data = self._get_sector(track, sector)

            # Get next catalog sector link
            next_track = sector_data[1]
            next_sector = sector_data[2]

            # Process file entries (7 entries per sector, starting at offset 0x0B)
            for i in range(7):
                offset = 0x0B + (i * 0x23)
                entry = sector_data[offset:offset + 0x23]

                # Check if entry is used (track number != 0)
                if entry[0] == 0:
                    continue

                # Parse file entry
                file_track = entry[0]
                file_sector = entry[1]
                file_type = entry[2]

                # Extract filename (30 characters max, high bit set for each char)
                filename_bytes = entry[3:33]
                filename = ''.join(chr(b & 0x7F) for b in filename_bytes).strip()

                if not filename:
                    continue

                # Get file length in sectors
                length = entry[33] | (entry[34] << 8)

                files[filename] = {
                    'type': self.FILE_TYPES.get(file_type, '?'),
                    'track': file_track,
                    'sector': file_sector,
                    'length': length,
                    'type_code': file_type
                }

            # Move to next catalog sector
            if next_track == 0:
                break
            track = next_track
            sector = next_sector

        return files

    def _read_tslist(self, track: int, sector: int) -> List[Tuple[int, int]]:
        """Read a Track/Sector list and return list of data sectors"""
        sectors = []

        while track != 0:
            ts_data = self._get_sector(track, sector)

            # Next T/S list sector
            next_track = ts_data[1]
            next_sector = ts_data[2]

            # Sector offset (for this T/S list)
            # sector_offset = ts_data[5] | (ts_data[6] << 8)

            # Read T/S pairs (starting at offset 0x0C, up to 122 pairs)
            for i in range(122):
                pair_offset = 0x0C + (i * 2)
                t = ts_data[pair_offset]
                s = ts_data[pair_offset + 1]

                if t == 0:
                    break

                sectors.append((t, s))

            # Move to next T/S list sector if exists
            if next_track == 0:
                break
            track = next_track
            sector = next_sector

        return sectors

    def read_file(self, filename: str) -> bytes:
        """Read a file's contents from the disk"""
        if filename not in self.files:
            raise FileNotFoundError(f"File not found: {filename}")

        file_info = self.files[filename]
        track = file_info['track']
        sector = file_info['sector']

        # Read the T/S list
        sectors = self._read_tslist(track, sector)

        # Read all data sectors
        data = b''
        for t, s in sectors:
            data += self._get_sector(t, s)

        return data

    def get_files(self) -> Dict[str, dict]:
        """Get the dictionary of files on the disk"""
        return self.files


class AppleDOS33FS(Operations):
    """FUSE filesystem for Apple DOS 3.3 disk images"""

    def __init__(self, image_path: str):
        self.image_path = image_path
        self.disk = AppleDOS33Disk(image_path)
        self.fd_counter = 0
        self.open_files: Dict[int, bytes] = {}

    def getattr(self, path: str, fh=None):
        """Get file attributes"""
        if path == '/':
            return {
                'st_mode': stat.S_IFDIR | 0o755,
                'st_nlink': 2,
                'st_size': 0,
                'st_ctime': os.path.getctime(self.image_path),
                'st_mtime': os.path.getmtime(self.image_path),
                'st_atime': os.path.getatime(self.image_path),
            }

        # Remove leading slash
        filename = path[1:]

        if filename not in self.disk.get_files():
            raise FuseOSError(errno.ENOENT)

        file_info = self.disk.get_files()[filename]
        file_size = file_info['length'] * AppleDOS33Disk.SECTOR_SIZE

        return {
            'st_mode': stat.S_IFREG | 0o644,
            'st_nlink': 1,
            'st_size': file_size,
            'st_ctime': os.path.getctime(self.image_path),
            'st_mtime': os.path.getmtime(self.image_path),
            'st_atime': os.path.getatime(self.image_path),
        }

    def readdir(self, path: str, fh):
        """Read directory contents"""
        if path != '/':
            raise FuseOSError(errno.ENOENT)

        entries = ['.', '..']
        entries.extend(self.disk.get_files().keys())
        return entries

    def open(self, path: str, flags):
        """Open a file"""
        filename = path[1:]

        if filename not in self.disk.get_files():
            raise FuseOSError(errno.ENOENT)

        # Only allow read-only access
        if flags & (os.O_WRONLY | os.O_RDWR):
            raise FuseOSError(errno.EROFS)

        # Read file data and cache it
        file_data = self.disk.read_file(filename)
        self.fd_counter += 1
        self.open_files[self.fd_counter] = file_data

        return self.fd_counter

    def read(self, path: str, size: int, offset: int, fh):
        """Read from an open file"""
        if fh not in self.open_files:
            raise FuseOSError(errno.EBADF)

        data = self.open_files[fh]
        return data[offset:offset + size]

    def release(self, path: str, fh):
        """Close an open file"""
        if fh in self.open_files:
            del self.open_files[fh]
        return 0

    def statfs(self, path: str):
        """Get filesystem statistics"""
        return {
            'f_bsize': AppleDOS33Disk.SECTOR_SIZE,
            'f_blocks': AppleDOS33Disk.TRACKS * AppleDOS33Disk.SECTORS_PER_TRACK,
            'f_bavail': 0,
            'f_bfree': 0,
            'f_files': len(self.disk.get_files()),
            'f_ffree': 0,
        }


def mount(image_path: str, mount_point: str, foreground: bool = False):
    """Mount an Apple DOS 3.3 disk image"""
    filesystem = AppleDOS33FS(image_path)
    FUSE(filesystem, mount_point, foreground=foreground, ro=True, nothreads=True)


if __name__ == '__main__':
    import sys

    if len(sys.argv) < 3:
        print("Usage: python -m a2fs.dos33fs <disk_image> <mount_point>")
        sys.exit(1)

    mount(sys.argv[1], sys.argv[2], foreground=True)
