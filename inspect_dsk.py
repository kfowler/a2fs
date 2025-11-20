import struct
import sys

def read_sector(f, track, sector):
    offset = (track * 16 + sector) * 256
    f.seek(offset)
    return f.read(256)

def parse_dsk(filename):
    with open(filename, 'rb') as f:
        # Read VTOC (Track 17, Sector 0)
        vtoc = read_sector(f, 17, 0)
        
        catalog_track = vtoc[1]
        catalog_sector = vtoc[2]
        
        print(f"VTOC found. First catalog sector: T{catalog_track}, S{catalog_sector}")
        
        files = []
        
        current_track = catalog_track
        current_sector = catalog_sector
        
        while current_track != 0:
            sector_data = read_sector(f, current_track, current_sector)
            
            # Next catalog sector
            next_track = sector_data[1]
            next_sector = sector_data[2]
            
            # Parse file entries
            for i in range(7):
                offset = 11 + (i * 35)
                entry = sector_data[offset:offset+35]
                
                track_list_track = entry[0]
                if track_list_track == 0 or track_list_track == 0xFF:
                    continue # Deleted or empty
                
                filename_raw = entry[3:33]
                # High bit is set on characters
                filename_str = "".join([chr(b & 0x7F) for b in filename_raw if b != 0]).strip()
                
                file_type = entry[2] & 0x7F
                file_len = struct.unpack('<H', entry[33:35])[0]
                
                print(f"File: {filename_str}, Type: {file_type:02X}, Len: {file_len}")
                files.append(filename_str)
            
            current_track = next_track
            current_sector = next_sector

    return files

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 inspect_dsk.py <dsk_file>")
        sys.exit(1)
    
    parse_dsk(sys.argv[1])
