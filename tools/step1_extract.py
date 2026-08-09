"""
Step 1: Extract PyInstaller CArchive from GCRPA.exe
Uses PyInstaller's own archive reader for reliability.
"""
import os
import sys
import zlib
import zipfile
import shutil
import marshal
import struct

# PyInstaller CArchive magic
MAGIC = b'MEI\x0C\x0B\x0A\x0B\x0E'

EXE = r"E:\GCRPA\GCRPA.exe"
OUT_EXTRACTED = r"E:\GCRPA\extracted"
OUT_PYZ = r"E:\GCRPA\pyz_output"


class CArchiveReader:
    """Read PyInstaller CArchive from EXE overlay."""

    def __init__(self, data, offset):
        self.data = data
        self.offset = offset
        self.entries = []
        self._parse()

    def _parse(self):
        pos = self.offset + 8  # skip magic
        # Read header: cookie_size, TOC length, TOC offset, pyver
        cookie_size = struct.unpack('!i', self.data[pos:pos+4])[0]
        pos += 4
        self.toc_len = struct.unpack('!i', self.data[pos:pos+4])[0]
        pos += 4
        toc_off = struct.unpack('!i', self.data[pos:pos+4])[0]
        pos += 4
        self.pyver = struct.unpack('!i', self.data[pos:pos+4])[0]
        pos += 4
        # Next: python lib name (null-terminated)
        end = self.data.index(b'\x00', pos)
        self.pylib_name = self.data[pos:end].decode('ascii')
        pos = end + 1

        # Align to 16 bytes from start of header + cookie area
        # TOC is at offset + toc_off
        toc_start = self.offset + toc_off
        pos = toc_start

        # Parse TOC entries
        while pos < self.offset + self.toc_len:
            entry_size = struct.unpack('!i', self.data[pos:pos+4])[0]
            pos += 4
            if entry_size == 0:
                break
            name_len = struct.unpack('!i', self.data[pos:pos+4])[0]
            pos += 4
            name = self.data[pos:pos+name_len].rstrip(b'\x00').decode('utf-8', errors='replace')
            pos += name_len
            # data = remaining bytes in this entry
            data_len = entry_size - 4 - name_len
            entry_data = self.data[pos:pos+data_len]
            pos += data_len

            # Check compression flag (type code is first byte)
            # 'z' = zlib compressed, 'b' = bz2, 'x' = lzma, 'o' = no compression (depends on version)
            if len(entry_data) > 0 and entry_data[0:1] in (b'z', b'b', b'x'):
                comp_flag = entry_data[0:1]
                entry_data = entry_data[1:]
                if comp_flag == b'z':
                    try:
                        entry_data = zlib.decompress(entry_data)
                    except:
                        pass
                elif comp_flag == b'b':
                    import bz2
                    try:
                        entry_data = bz2.decompress(entry_data)
                    except:
                        pass
                elif comp_flag == b'x':
                    import lzma
                    try:
                        entry_data = lzma.decompress(entry_data)
                    except:
                        pass

            self.entries.append((name, entry_data))

    def extract_all(self, output_dir):
        os.makedirs(output_dir, exist_ok=True)
        for name, data in self.entries:
            out_path = os.path.join(output_dir, name)
            out_dir = os.path.dirname(out_path)
            os.makedirs(out_dir, exist_ok=True)
            with open(out_path, 'wb') as f:
                f.write(data)
        return len(self.entries)


def find_archive(data):
    """Find CArchive at end of EXE. Magic is within final ~72 bytes."""
    # Search from end backwards — magic is very close to EOF
    for i in range(len(data) - 8, max(0, len(data) - 1000), -1):
        if data[i:i+8] == MAGIC:
            return i
    return None


def extract_pyz_files(directory, output_dir):
    """Extract all .pyz files in directory tree."""
    os.makedirs(output_dir, exist_ok=True)
    total = 0
    for root, dirs, files in os.walk(directory):
        for f in files:
            fpath = os.path.join(root, f)
            if f.endswith('.pyz') or '.pyz' in f.lower():
                print(f"  Extracting PYZ: {f} ({os.path.getsize(fpath):,} bytes)")
                try:
                    with zipfile.ZipFile(fpath, 'r') as zf:
                        names = zf.namelist()
                        for name in names:
                            try:
                                zf.extract(name, output_dir)
                                total += 1
                            except Exception as e:
                                print(f"    Skip {name}: {e}")
                    print(f"    Extracted {len(names)} files")
                except zipfile.BadZipFile:
                    # Try raw zlib decompress
                    print(f"    Not a standard ZIP, trying raw decompress...")
                    # PYZ format: zlib-compressed marshalled code objects
                    try:
                        with open(fpath, 'rb') as pf:
                            raw = pf.read()
                        # PYZ has a header then zlib stream
                        # Skip PYZ magic/preamble
                        decompressed = zlib.decompress(raw)
                        with open(os.path.join(output_dir, f + '.raw'), 'wb') as of:
                            of.write(decompressed)
                        print(f"    Raw decompressed: {len(decompressed)} bytes")
                    except Exception as e2:
                        print(f"    Failed: {e2}")
    return total


def extract_zip_files(directory, output_dir):
    """Extract .zip files (like base_library.zip)."""
    os.makedirs(output_dir, exist_ok=True)
    total = 0
    for root, dirs, files in os.walk(directory):
        for f in files:
            if f.endswith('.zip'):
                fpath = os.path.join(root, f)
                print(f"  Extracting ZIP: {f} ({os.path.getsize(fpath):,} bytes)")
                try:
                    with zipfile.ZipFile(fpath, 'r') as zf:
                        zf.extractall(os.path.join(output_dir, f.replace('.zip', '')))
                        total += len(zf.namelist())
                except Exception as e:
                    print(f"    Failed: {e}")
    return total


def main():
    print(f"Reading: {EXE}")
    with open(EXE, 'rb') as f:
        data = f.read()
    print(f"Size: {len(data):,} bytes ({len(data)/1024/1024:.1f} MB)")

    # Find CArchive
    offset = find_archive(data)
    if offset is None:
        print("ERROR: CArchive not found!")
        sys.exit(1)

    print(f"\nCArchive at offset: 0x{offset:X} ({offset})")
    print(f"Distance from end: {len(data) - offset}")

    # Parse and extract
    print("\n--- Parsing CArchive ---")
    archive = CArchiveReader(data, offset)
    print(f"Python version flag: {archive.pyver}")
    print(f"Python lib: {archive.pylib_name}")
    print(f"TOC length: {archive.toc_len}")
    print(f"Entries: {len(archive.entries)}")

    print(f"\n--- Extracting {len(archive.entries)} entries to {OUT_EXTRACTED} ---")
    count = archive.extract_all(OUT_EXTRACTED)
    print(f"Extracted {count} files")

    # List top-level items
    print("\n--- Top-level files & dirs ---")
    for item in sorted(os.listdir(OUT_EXTRACTED))[:40]:
        full = os.path.join(OUT_EXTRACTED, item)
        if os.path.isdir(full):
            print(f"  [{item}/]")
        else:
            size = os.path.getsize(full)
            print(f"  {item} ({size:,} bytes)")

    # Extract PYZ files
    print(f"\n--- Extracting PYZ archives ---")
    pyz_count = extract_pyz_files(OUT_EXTRACTED, OUT_PYZ)
    print(f"PYZ files extracted: {pyz_count}")

    # Extract ZIP files
    print(f"\n--- Extracting ZIP archives ---")
    zip_count = extract_zip_files(OUT_EXTRACTED, OUT_PYZ)
    print(f"ZIP files extracted: {zip_count}")

    # Summary
    print(f"\n=== Extraction Complete ===")
    pyc_count = sum(1 for root, dirs, files in os.walk(OUT_PYZ) for f in files if f.endswith('.pyc'))
    print(f"Total .pyc files in pyz_output: {pyc_count}")
    pyc_extracted = sum(1 for root, dirs, files in os.walk(OUT_EXTRACTED) for f in files if f.endswith('.pyc'))
    print(f"Total .pyc files in extracted: {pyc_extracted}")


if __name__ == '__main__':
    main()
