"""
Final complete extraction: properly handle all PyInstaller entry types.
"""
import os
import sys
import marshal
import types
import struct

EXE = r"E:\GCRPA\GCRPA.exe"
OUT_PYC = r"E:\GCRPA\pyc_files"

PY37_MAGIC = b'\x42\x0d\x0d\x0a'

CODE_TYPE = type((lambda: 0).__code__)


def make_pyc(code_obj):
    marshalled = marshal.dumps(code_obj)
    return PY37_MAGIC + b'\x00\x00\x00\x00' + b'\x00\x00\x00\x00' + marshalled


def save_pyc(code_obj, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(make_pyc(code_obj))
    return os.path.getsize(path)


def try_load_code(data):
    """Try to find and load a code object from binary data."""
    if isinstance(data, CODE_TYPE):
        return data

    if not isinstance(data, bytes):
        return None

    # Try raw first (GCRPA format: direct marshal)
    for offset in range(0, min(24, len(data))):
        try:
            obj = marshal.loads(data[offset:])
            if isinstance(obj, CODE_TYPE):
                return obj
        except:
            continue
    return None


def main():
    from PyInstaller.archive.readers import CArchiveReader

    archive = CArchiveReader(EXE)
    toc = archive.toc

    print(f"TOC entries: {len(toc)}")
    os.makedirs(OUT_PYC, exist_ok=True)

    total_pyc = 0

    # --- Phase 1: Extract all TOC code entries ---
    print("\n=== Phase 1: Extract TOC code entries ===")
    for name in toc:
        data = archive.extract(name)
        if data is None:
            continue

        # Skip DLL/PDY/dist-info
        if name.endswith('.dll') or name.endswith('.pyd'):
            continue
        if '.dist-info' in name or '.egg-info' in name:
            continue

        code = try_load_code(data)
        if code is not None:
            safe_name = name.replace('\\', '/')
            if not safe_name.endswith('.pyc'):
                safe_name += '.pyc'
            path = os.path.join(OUT_PYC, safe_name)
            size = save_pyc(code, path)
            total_pyc += 1
            if name in ('GCRPA',) or name.startswith('pyi'):
                print(f"  {name} -> {safe_name} ({size} bytes) [{code.co_filename}]")

    print(f"TOC code entries extracted: {total_pyc}")

    # --- Phase 2: Extract PYZ entries ---
    print("\n=== Phase 2: Extract PYZ entries ===")
    pyz_count = 0
    if 'PYZ-00.pyz' in toc:
        pyz_archive = archive.open_embedded_archive('PYZ-00.pyz')
        pyz_toc = pyz_archive.toc
        print(f"PYZ entries: {len(pyz_toc)}")
        pyz_dir = os.path.join(OUT_PYC, 'pyz_stdlib')
        for name in pyz_toc:
            try:
                data = pyz_archive.extract(name)
                code = try_load_code(data)
                if code is not None:
                    path = os.path.join(pyz_dir, name + '.pyc')
                    save_pyc(code, path)
                    pyz_count += 1
            except:
                pass
        print(f"PYZ extracted: {pyz_count}")
        total_pyc += pyz_count

    # --- Phase 3: Extract resource files ---
    print("\n=== Phase 3: Extract resources ===")
    res_count = 0
    for name in toc:
        if 'res/' in name.lower() or name.endswith('.txt') or name.endswith('.xls'):
            data = archive.extract(name)
            if data and isinstance(data, bytes):
                path = os.path.join(OUT_PYC, name.replace('\\', '/'))
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, 'wb') as f:
                    f.write(data)
                res_count += 1
                print(f"  {name} ({len(data)} bytes)")
    print(f"Resources extracted: {res_count}")

    # --- Final summary ---
    print(f"\n{'=' * 50}")
    print(f"Total .pyc files: {total_pyc}")

    # Show key files
    print("\nKey application files:")
    for root, dirs, files in os.walk(OUT_PYC):
        for f in sorted(files):
            full = os.path.join(root, f)
            rel = os.path.relpath(full, OUT_PYC)
            if f.endswith('.pyc') and ('GCRPA' in f or 'pyi' in f.lower()):
                print(f"  {rel} ({os.path.getsize(full)} bytes)")
    # Check GCRPA
    gcrpa = os.path.join(OUT_PYC, 'GCRPA.pyc')
    if os.path.exists(gcrpa):
        print(f"\n  *** MAIN MODULE: GCRPA.pyc ({os.path.getsize(gcrpa)} bytes) ***")

    # Show resources
    print("\nResources:")
    res_dir = os.path.join(OUT_PYC, 'res')
    if os.path.exists(res_dir):
        for f in os.listdir(res_dir):
            print(f"  res/{f}")

    return total_pyc


if __name__ == '__main__':
    main()
