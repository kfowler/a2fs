#!/usr/bin/env python3
"""
Apple DOS 3.3 Filesystem Implementation
Read-only FUSE filesystem for Apple DOS 3.3 disk images (.dsk, .do formats)
"""

import os
import sys
import errno
import struct
import ctypes.util

# Monkeypatch find_library to support fuse-t on macOS
_original_find_library = ctypes.util.find_library

def _find_library(name):
    if name == 'fuse':
        # Check for fuse-t
        if os.path.exists('/usr/local/lib/libfuse-t.dylib'):
            return '/usr/local/lib/libfuse-t.dylib'
    return _original_find_library(name)

ctypes.util.find_library = _find_library

from fuse import FUSE, FuseOSError, Operations


class AppleDOS33FS(Operations):
    """FUSE filesystem for Apple DOS 3.3 disk images"""

    def __init__(self, dsk_path):
        self.dsk_path = dsk_path
        self.fd = open(dsk_path, 'rb')
        self.files = {}  # filename -> {type, len, ts_track, ts_sector}
        self._file_cache = {}  # Cache file data for performance
        self._parse_catalog()

    def _read_sector(self, track, sector):
        """Read a 256-byte sector from the disk image"""
        if track < 0 or track >= 35:
            raise ValueError(f"Invalid track: {track}")
        if sector < 0 or sector >= 16:
            raise ValueError(f"Invalid sector: {sector}")

        offset = (track * 16 + sector) * 256
        self.fd.seek(offset)
        data = self.fd.read(256)

        if len(data) != 256:
            raise IOError(f"Failed to read full sector T{track}S{sector}: got {len(data)} bytes")

        return data

    def _parse_catalog(self):
        """Parse the DOS 3.3 catalog to build file directory"""
        # VTOC is at T17, S0
        vtoc = self._read_sector(17, 0)

        # Validate VTOC and get catalog location
        catalog_track = vtoc[1]
        catalog_sector = vtoc[2]

        # Sanity check: catalog track/sector must be valid
        if catalog_track == 0 or catalog_track >= 35 or catalog_sector >= 16:
            # VTOC appears corrupted or this is not a standard DOS 3.3 disk
            # Fall back to standard catalog location
            catalog_track = 17
            catalog_sector = 15

        current_track = catalog_track
        current_sector = catalog_sector

        while current_track != 0:
            sector_data = self._read_sector(current_track, current_sector)

            next_track = sector_data[1]
            next_sector = sector_data[2]

            # Each catalog sector contains up to 7 file entries
            for i in range(7):
                offset = 11 + (i * 35)
                entry = sector_data[offset:offset+35]

                ts_track = entry[0]
                if ts_track == 0 or ts_track == 0xFF:
                    continue

                # Extract filename (30 bytes, high bit set)
                filename_raw = entry[3:33]
                filename = "".join([chr(b & 0x7F) for b in filename_raw if b != 0]).strip()

                file_type = entry[2] & 0x7F
                file_len = struct.unpack('<H', entry[33:35])[0]  # Length in sectors

                # Handle duplicate filenames if necessary (DOS 3.3 allows them)
                # For now, last one wins
                self.files[filename] = {
                    'type': file_type,
                    'len_sectors': file_len,
                    'ts_track': ts_track,
                    'ts_sector': entry[1]
                }

            current_track = next_track
            current_sector = next_sector

    def _read_file_data(self, filename):
        """Read complete file data by following T/S list chain"""
        if filename not in self.files:
            return b''

        # Check cache first
        if filename in self._file_cache:
            return self._file_cache[filename]

        file_entry = self.files[filename]
        data = bytearray()

        ts_track = file_entry['ts_track']
        ts_sector = file_entry['ts_sector']

        # Follow T/S list chain
        while ts_track != 0:
            ts_list = self._read_sector(ts_track, ts_sector)

            # Each TS list sector has up to 122 track/sector pairs
            for i in range(122):
                offset = 12 + (i * 2)
                track = ts_list[offset]
                sector = ts_list[offset + 1]

                if track == 0:
                    # End of data in this T/S list
                    break

                data.extend(self._read_sector(track, sector))

            # Next T/S list sector in chain
            ts_track = ts_list[1]
            ts_sector = ts_list[2]

        result = bytes(data)
        self._file_cache[filename] = result
        return result

    # FUSE Operations
    # ===============

    def getattr(self, path, fh=None):
        """Get file/directory attributes"""
        if path == '/':
            return dict(st_mode=(0o40755), st_nlink=2)

        filename = path[1:]  # strip leading /
        if filename in self.files:
            # Return actual file size (sectors * 256)
            # Note: Some file types have size metadata in their headers,
            # but for simplicity we report the full sector allocation
            st_size = self.files[filename]['len_sectors'] * 256
            return dict(st_mode=(0o100444), st_nlink=1, st_size=st_size)

        raise FuseOSError(errno.ENOENT)

    def readdir(self, path, fh):
        """List directory contents"""
        return ['.', '..'] + list(self.files.keys())

    def read(self, path, length, offset, fh):
        """Read data from file"""
        filename = path[1:]
        if filename not in self.files:
            raise FuseOSError(errno.ENOENT)

        # Read file data (cached after first read)
        data = self._read_file_data(filename)
        return data[offset:offset + length]

    def destroy(self, path):
        """Clean up resources when unmounting"""
        if self.fd:
            self.fd.close()


def mount(image_path: str, mount_point: str, foreground: bool = True):
    """Mount an Apple DOS 3.3 disk image"""
    if not os.path.exists(mount_point):
        os.makedirs(mount_point)

    filesystem = AppleDOS33FS(image_path)
    FUSE(filesystem, mount_point, nothreads=True, foreground=foreground)


def main():
    """Command-line entry point"""
    if len(sys.argv) != 3:
        print("Usage: %s <dsk_file> <mountpoint>" % sys.argv[0])
        sys.exit(1)

    dsk_path = sys.argv[1]
    mountpoint = sys.argv[2]

    mount(dsk_path, mountpoint, foreground=True)


if __name__ == '__main__':
    main()
