#!/usr/bin/env python3
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

class AppleDOSFS(Operations):
    def __init__(self, dsk_path):
        self.dsk_path = dsk_path
        self.fd = open(dsk_path, 'rb')
        self.files = {} # filename -> {type, len, ts_track, ts_sector}
        self._parse_catalog()

    def _read_sector(self, track, sector):
        offset = (track * 16 + sector) * 256
        self.fd.seek(offset)
        return self.fd.read(256)

    def _parse_catalog(self):
        # VTOC is at T17, S0
        vtoc = self._read_sector(17, 0)
        catalog_track = vtoc[1]
        catalog_sector = vtoc[2]

        current_track = catalog_track
        current_sector = catalog_sector

        while current_track != 0:
            sector_data = self._read_sector(current_track, current_sector)
            
            next_track = sector_data[1]
            next_sector = sector_data[2]

            for i in range(7):
                offset = 11 + (i * 35)
                entry = sector_data[offset:offset+35]
                
                ts_track = entry[0]
                if ts_track == 0 or ts_track == 0xFF:
                    continue

                filename_raw = entry[3:33]
                filename = "".join([chr(b & 0x7F) for b in filename_raw if b != 0]).strip()
                
                file_type = entry[2] & 0x7F
                file_len = struct.unpack('<H', entry[33:35])[0] # Length in sectors

                # Handle duplicate filenames if necessary (DOS 3.3 allows them, standard FS doesn't)
                # For now, last one wins or we could append a suffix.
                self.files[filename] = {
                    'type': file_type,
                    'len_sectors': file_len,
                    'ts_track': ts_track,
                    'ts_sector': entry[1]
                }

            current_track = next_track
            current_sector = next_sector

    def _read_file_data(self, filename):
        if filename not in self.files:
            return b''
        
        file_entry = self.files[filename]
        data = bytearray()
        
        ts_track = file_entry['ts_track']
        ts_sector = file_entry['ts_sector']
        
        while ts_track != 0:
            ts_list = self._read_sector(ts_track, ts_sector)
            
            # Each TS list sector has up to 122 pairs
            for i in range(122):
                offset = 12 + (i * 2)
                track = ts_list[offset]
                sector = ts_list[offset + 1]
                
                if track == 0:
                    # Empty or sparse? DOS 3.3 files are usually contiguous but can have holes?
                    # Actually 0/0 usually means end of data in this list if we haven't reached size
                    # But let's just read what's there.
                    # If it's a random access text file it might have holes.
                    # For simplicity, we'll treat T0 as a null byte block or skip?
                    # In DOS 3.3, T0/S0 is not a valid data sector.
                    break 
                
                data.extend(self._read_sector(track, sector))
            
            ts_track = ts_list[1]
            ts_sector = ts_list[2]
            
        return bytes(data)

    # Filesystem methods
    # ==================

    def getattr(self, path, fh=None):
        if path == '/':
            return dict(st_mode=(0o40755), st_nlink=2)
        
        filename = path[1:] # strip leading /
        if filename in self.files:
            # We don't know exact byte size easily without reading, 
            # but we have sector count. 
            # Let's read it to be accurate or estimate.
            # Reading entire file on getattr might be slow for large files, 
            # but these are max 140KB.
            # Optimization: Cache the data?
            # For now, let's just return sector_count * 256 or read it.
            # To be correct for `cat`, we should probably provide the real size.
            # However, binary files have a length in the header (first 2 or 4 bytes).
            # Text files end with 0x00.
            # Let's just return sector * 256 for now, user might see trailing nulls.
            st_size = self.files[filename]['len_sectors'] * 256
            return dict(st_mode=(0o100444), st_nlink=1, st_size=st_size)
        
        raise FuseOSError(errno.ENOENT)

    def readdir(self, path, fh):
        return ['.', '..'] + list(self.files.keys())

    def read(self, path, length, offset, fh):
        filename = path[1:]
        if filename not in self.files:
            raise FuseOSError(errno.ENOENT)
        
        # Simple implementation: read whole file, return slice
        # Inefficient for huge files, fine for Apple II
        data = self._read_file_data(filename)
        return data[offset:offset + length]

def main():
    if len(sys.argv) != 3:
        print("Usage: %s <dsk_file> <mountpoint>" % sys.argv[0])
        sys.exit(1)

    dsk_path = sys.argv[1]
    mountpoint = sys.argv[2]
    
    if not os.path.exists(mountpoint):
        os.makedirs(mountpoint)

    FUSE(AppleDOSFS(dsk_path), mountpoint, nothreads=True, foreground=True)

if __name__ == '__main__':
    main()
