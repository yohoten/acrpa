"""
Rebuild all .pyc files with correct 16-byte Python 3.7 header.
Then decompile them all to .py source files.
"""
import os
import sys
import marshal
import types
import struct

EXE = r"E:\GCRPA\GCRPA.exe"
OUT_PYC = r"E:\GCRPA\pyc_files"
OUT_DECOMPILED = r"E:\GCRPA\decompiled"

CODE_TYPE = type((lambda: 0).__code__)

# Correct Python 3.7 .pyc header: magic(4) + flags(4) + timestamp(4) + source_size(4) = 16 bytes
PY37_MAGIC = b'\x42\x0d\x0d\x0a'
PY37_HEADER = PY37_MAGIC + b'\x00\x00\x00\x00' + b'\x00\x00\x00\x00' + b'\x00\x00\x00\x00'


def make_pyc(code_obj):
    return PY37_HEADER + marshal.dumps(code_obj)


def save_pyc(code_obj, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(make_pyc(code_obj))
    return os.path.getsize(path)


def try_load_code(data):
    if isinstance(data, CODE_TYPE):
        return data
    if not isinstance(data, bytes):
        return None
    for offset in range(0, min(24, len(data))):
        try:
            obj = marshal.loads(data[offset:])
            if isinstance(obj, CODE_TYPE):
                return obj
        except:
            continue
    return None


def rebuild_all_pyc():
    """Rebuild all .pyc files from the EXE."""
    from PyInstaller.archive.readers import CArchiveReader

    archive = CArchiveReader(EXE)
    toc = archive.toc

    os.makedirs(OUT_PYC, exist_ok=True)

    total = 0
    all_paths = []

    # Phase 1: TOC code entries
    print("=== Phase 1: TOC code entries ===")
    for name in toc:
        data = archive.extract(name)
        if data is None:
            continue
        code = try_load_code(data)
        if code is not None:
            safe = name.replace('\\', '/')
            if not safe.endswith('.pyc'):
                safe += '.pyc'
            path = os.path.join(OUT_PYC, safe)
            save_pyc(code, path)
            total += 1
            all_paths.append(path)
            print(f"  {name} -> {safe} [{code.co_filename}]")

    print(f"TOC code entries: {total}")

    # Phase 2: PYZ entries
    print("\n=== Phase 2: PYZ entries ===")
    pyz_count = 0
    if 'PYZ-00.pyz' in toc:
        pyz_archive = archive.open_embedded_archive('PYZ-00.pyz')
        pyz_dir = os.path.join(OUT_PYC, 'pyz_stdlib')
        for name in pyz_archive.toc:
            try:
                data = pyz_archive.extract(name)
                code = try_load_code(data)
                if code is not None:
                    path = os.path.join(pyz_dir, name + '.pyc')
                    save_pyc(code, path)
                    pyz_count += 1
                    all_paths.append(path)
            except:
                pass
        print(f"PYZ entries: {pyz_count}")
        total += pyz_count

    # Phase 3: base_library .pyc files (from zip, just fix headers)
    print("\n=== Phase 3: Fix base_library headers ===")
    bl_dir = os.path.join(OUT_PYC, 'base_library')
    bl_count = 0
    for root, dirs, files in os.walk(bl_dir):
        for f in files:
            if f.endswith('.pyc'):
                path = os.path.join(root, f)
                try:
                    with open(path, 'rb') as fh:
                        data = fh.read()
                    # These already have a 12-byte or wrong header
                    # Try to fix: find the marshal code object
                    code = try_load_code(data)
                    if code is not None:
                        save_pyc(code, path)
                        bl_count += 1
                except:
                    pass
    print(f"Fixed base_library: {bl_count}")
    total += bl_count

    print(f"\nTotal .pyc files: {total}")
    return all_paths, total


def decompile_all():
    """Decompile all .pyc files to .py using uncompyle6."""
    import subprocess

    os.makedirs(OUT_DECOMPILED, exist_ok=True)

    success = 0
    failed = 0
    total = 0

    for root, dirs, files in os.walk(OUT_PYC):
        for f in files:
            if not f.endswith('.pyc'):
                continue
            total += 1
            pyc_path = os.path.join(root, f)
            rel_path = os.path.relpath(pyc_path, OUT_PYC)
            py_name = rel_path[:-4]  # remove .pyc
            py_path = os.path.join(OUT_DECOMPILED, py_name + '.py')

            os.makedirs(os.path.dirname(py_path), exist_ok=True)

            try:
                # Use uncompyle6 API directly for better control
                import uncompyle6
                from io import StringIO

                output = StringIO()
                uncompyle6.decompile_file(pyc_path, output)
                code = output.getvalue()

                if code.strip():
                    with open(py_path, 'w', encoding='utf-8') as out:
                        out.write(code)
                    success += 1
                else:
                    failed += 1

            except Exception as e:
                failed += 1
                # Save error info
                err_path = py_path + '.err'
                with open(err_path, 'w') as err:
                    err.write(f"Decompile error: {e}\n")
                if failed <= 10:
                    print(f"  FAILED: {rel_path} -> {e}")

            if total % 100 == 0:
                print(f"  Progress: {total} files, {success} ok, {failed} failed")

    print(f"\nDecompilation complete: {success}/{total} succeeded, {failed} failed")
    return success, failed


def main():
    print("=" * 60)
    print("GCRPA Full Rebuild & Decompile Pipeline")
    print("=" * 60)

    # Step 1: Rebuild all .pyc with correct headers
    print("\n>>> Rebuilding .pyc files with correct headers...")
    paths, pyc_total = rebuild_all_pyc()

    # Step 2: Decompile
    print("\n>>> Decompiling .pyc to .py...")
    ok, fail = decompile_all()

    # Summary
    print(f"\n{'=' * 60}")
    print(f"SUMMARY")
    print(f"{'=' * 60}")
    print(f"Total .pyc files: {pyc_total}")
    print(f"Decompiled: {ok} succeeded, {fail} failed")

    # Show key output files
    print(f"\nOutput directories:")
    print(f"  .pyc files: {OUT_PYC}")
    print(f"  .py files:  {OUT_DECOMPILED}")

    # Check GCRPA.py
    main_py = os.path.join(OUT_DECOMPILED, 'GCRPA.py')
    if os.path.exists(main_py):
        with open(main_py, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        print(f"\n  *** GCRPA.py: {len(lines)} lines, {os.path.getsize(main_py)} bytes ***")
        print(f"  First 30 lines:")
        for line in lines[:30]:
            print(f"    {line.rstrip()}")


if __name__ == '__main__':
    main()
