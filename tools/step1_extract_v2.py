"""
Step 1: Extract PyInstaller EXE using PyInstaller's own API.
"""
import os
import sys

EXE = r"E:\GCRPA\GCRPA.exe"
OUT_EXTRACTED = r"E:\GCRPA\extracted"

def main():
    print(f"Reading: {EXE}")

    # Use PyInstaller's CArchiveReader
    from PyInstaller.archive.readers import CArchiveReader

    archive = CArchiveReader(EXE)

    print(f"Archive type: {type(archive)}")
    print(f"Archive contents:")

    os.makedirs(OUT_EXTRACTED, exist_ok=True)

    count = 0
    # CArchiveReader is iterable — each item is a CArchiveEntry or similar
    for item in archive:
        # Explore what attributes the item has
        print(f"  Item type: {type(item).__name__}, dir: {[a for a in dir(item) if not a.startswith('_')]}")
        break  # Just show first for now

    print(f"\nDone")


if __name__ == '__main__':
    main()
