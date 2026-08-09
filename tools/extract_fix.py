"""
Fix and complete the extraction: handle GCRPA entry and PYZ code objects.
"""
import os
import sys
import marshal
import struct
import zipfile
import io

EXE = r"E:\GCRPA\GCRPA.exe"
OUT_PYC = r"E:\GCRPA\pyc_files"

PY37_MAGIC = b'\x42\x0d\x0d\x0a'


def make_pyc(code_obj):
    """Create proper .pyc bytes from a code object."""
    # Python 3.7 .pyc format: magic(4) + timestamp(4) + source_size(4) + marshalled_code
    marshalled = marshal.dumps(code_obj)
    return PY37_MAGIC + b'\x00\x00\x00\x00' + b'\x00\x00\x00\x00' + marshalled


def try_load_code(data):
    """Try to load a code object from PyInstaller-wrapped data."""
    # Try different offsets to skip PyInstaller header
    for offset in [20, 16, 12, 8, 4, 0]:
        try:
            obj = marshal.loads(data[offset:])
            if isinstance(obj, type(lambda: 0).__code__.__class__):
                return obj
        except:
            continue
    return None


def main():
    from PyInstaller.archive.readers import CArchiveReader

    archive = CArchiveReader(EXE)
    toc = archive.toc

    os.makedirs(OUT_PYC, exist_ok=True)

    # --- Fix 1: Extract GCRPA and other failed entries ---
    print("=== Fixing failed TOC entries ===")

    failed_names = [
        'GCRPA', 'pyiboot01_bootstrap', 'pyi_rth_subprocess',
        'pyi_rth_pkgutil', 'pyi_rth_inspect', 'pyi_rth__tkinter'
    ]

    for name in failed_names:
        data = archive.extract(name)
        if data is None:
            print(f"  {name}: data is None")
            continue

        print(f"  {name}: data type={type(data).__name__}, len={len(data)}")

        # Check what type we got
        if isinstance(data, type(lambda: 0).__code__.__class__):
            # It's already a code object!
            code = data
            pyc_path = os.path.join(OUT_PYC, name + '.pyc')
            with open(pyc_path, 'wb') as f:
                f.write(make_pyc(code))
            print(f"    -> Saved as code object, {os.path.getsize(pyc_path)} bytes")
            continue

        # Try to load code from bytes
        code = try_load_code(data)
        if code:
            pyc_path = os.path.join(OUT_PYC, name + '.pyc')
            with open(pyc_path, 'wb') as f:
                f.write(make_pyc(code))
            print(f"    -> Extracted at offset, {os.path.getsize(pyc_path)} bytes")
        else:
            print(f"    -> Failed. First 32 bytes hex: {data[:32].hex()}")
            # Dump raw data for inspection
            dump_path = os.path.join(OUT_PYC, name + '.dump')
            with open(dump_path, 'wb') as f:
                f.write(data)
            print(f"    -> Raw data saved to {dump_path}")

    # --- Fix 2: Extract PYZ entries (code objects, not bytes) ---
    print("\n=== Extracting PYZ entries ===")

    if 'PYZ-00.pyz' in toc:
        pyz_archive = archive.open_embedded_archive('PYZ-00.pyz')
        pyz_toc = pyz_archive.toc
        print(f"PYZ entries: {len(pyz_toc)}")

        pyz_dir = os.path.join(OUT_PYC, 'pyz_stdlib')
        os.makedirs(pyz_dir, exist_ok=True)

        extracted = 0
        for name in pyz_toc:
            try:
                data = pyz_archive.extract(name)
                if data is None:
                    continue

                if isinstance(data, type(lambda: 0).__code__.__class__):
                    # Direct code object
                    code = data
                elif isinstance(data, bytes):
                    # Try to load code
                    code = try_load_code(data)
                    if code is None:
                        continue
                else:
                    continue

                pyc_path = os.path.join(pyz_dir, name + '.pyc')
                with open(pyc_path, 'wb') as f:
                    f.write(make_pyc(code))
                extracted += 1

            except Exception as e:
                pass

        print(f"PYZ modules extracted: {extracted}")

    # --- Fix 3: Also check base_library.zip contents ---
    print("\n=== Summary ===")
    total = 0
    for root, dirs, files in os.walk(OUT_PYC):
        for f in files:
            if f.endswith('.pyc'):
                total += 1
                if total <= 5:
                    path = os.path.join(root, f)
                    print(f"  {os.path.relpath(path, OUT_PYC)} ({os.path.getsize(path)} bytes)")
    print(f"Total .pyc files: {total}")

    # Highlight the main module
    gcrpa_path = os.path.join(OUT_PYC, 'GCRPA.pyc')
    if os.path.exists(gcrpa_path):
        print(f"\n*** GCRPA.pyc EXISTS: {os.path.getsize(gcrpa_path)} bytes ***")
    else:
        print("\n*** GCRPA.pyc NOT FOUND ***")


if __name__ == '__main__':
    main()
