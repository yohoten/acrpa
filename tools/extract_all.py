"""
Complete extraction: CArchive -> .pyc files with proper headers.
Handles PyInstaller's custom wrapping format.
"""
import os
import sys
import marshal
import struct
import zipfile

EXE = r"E:\GCRPA\GCRPA.exe"
OUT_EXTRACTED = r"E:\GCRPA\extracted"
OUT_PYC = r"E:\GCRPA\pyc_files"
OUT_DECOMPILED = r"E:\GCRPA\decompiled"

# Python 3.7 .pyc header: magic + timestamp + source size
# magic: 420d 0d0a (little-endian)
PY37_MAGIC = b'\x42\x0d\x0d\x0a'


def save_as_pyc(data, output_path):
    """Convert PyInstaller-wrapped data to proper .pyc file."""
    # PyInstaller wraps: [16-byte pyi header][4-byte data length][code object...]
    # The 16-byte header includes the .pyc header at the start
    # Format: pyc_magic(4) + timestamp(4) + "pyi" marker(4) + pyi_data_len(4) + code_length(4) + code_data

    if len(data) < 20:
        print(f"  Data too short: {len(data)} bytes")
        return False

    # Check for PyInstaller wrapper
    if data[:2] == PY37_MAGIC[:2]:
        # Has PyInstaller wrap
        # The marshaled code starts after the 16-byte header + 4-byte length
        # Actually, PyInstaller stores: [16-byte header][4-byte uncompressed_len][marshalled code]
        pyi_header = data[:16]
        code_data = data[20:]  # skip 16-byte header + 4-byte data_len

        # Try to marshal.loads from various offsets
        for offset in [0, 4, 8, 12, 16, 20]:
            try:
                code = marshal.loads(data[offset:])
                if isinstance(code, type(lambda: 0).__code__.__class__):
                    # Found a code object!
                    pyc_data = PY37_MAGIC + b'\x00\x00\x00\x00' + b'\x00\x00\x00\x00' + marshal.dumps(code)
                    with open(output_path, 'wb') as f:
                        f.write(pyc_data)
                    return True
            except:
                continue

        # Try with the wrapped data directly
        try:
            code = marshal.loads(code_data)
            if isinstance(code, type(lambda: 0).__code__.__class__):
                pyc_data = PY37_MAGIC + b'\x00\x00\x00\x00' + b'\x00\x00\x00\x00' + marshal.dumps(code)
                with open(output_path, 'wb') as f:
                    f.write(pyc_data)
                return True
        except:
            pass
    else:
        # Direct marshalled code
        try:
            code = marshal.loads(data)
            if isinstance(code, type(lambda: 0).__code__.__class__):
                pyc_data = PY37_MAGIC + b'\x00\x00\x00\x00' + b'\x00\x00\x00\x00' + marshal.dumps(code)
                with open(output_path, 'wb') as f:
                    f.write(pyc_data)
                return True
        except:
            pass

    # If all fails, just try to save the raw data with a pyc header
    try:
        # Strip the pyi header then try marshal
        for trim in [16, 20, 24]:
            try:
                code = marshal.loads(data[trim:])
                pyc_data = PY37_MAGIC + b'\x00\x00\x00\x00' + b'\x00\x00\x00\x00' + marshal.dumps(code)
                with open(output_path, 'wb') as f:
                    f.write(pyc_data)
                return True
            except:
                continue
    except:
        pass

    return False


def extract_toc_modules(archive, output_dir):
    """Extract all Python modules from CArchive TOC."""
    toc = archive.toc
    os.makedirs(output_dir, exist_ok=True)

    extracted = 0
    failed = []

    for name in toc:
        data = archive.extract(name)
        if data is None:
            continue

        # Skip DLLs, PYD files
        if name.endswith('.dll') or name.endswith('.pyd'):
            path = os.path.join(output_dir, name.replace('/', os.sep))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'wb') as f:
                f.write(data)
            continue

        # Check if it's Python code data
        is_code = False
        # PyInstaller modules start with 42 0d (Python 3.7 magic)
        if data[:2] == b'\x42\x0d' or data[:2] == b'\x42\x0D':
            is_code = True
        # Or it's a top-level module name
        elif name in ('struct',) or name.startswith('pyi') or name == 'GCRPA':
            is_code = True

        if is_code:
            safe_name = name.replace('/', os.sep).replace('\\', os.sep)
            if not safe_name.endswith('.pyc'):
                safe_name += '.pyc'
            path = os.path.join(output_dir, safe_name)
            os.makedirs(os.path.dirname(path), exist_ok=True)

            if save_as_pyc(data, path):
                extracted += 1
            else:
                failed.append(name)
        else:
            # Save other files (dist-info, etc.)
            path = os.path.join(output_dir, name.replace('/', os.sep))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'wb') as f:
                f.write(data)

    print(f"TOC Python modules extracted: {extracted}")
    if failed:
        print(f"Failed entries ({len(failed)}):")
        for f in failed:
            print(f"  - {f}")

    return extracted


def extract_pyz_properly(exe_path, output_dir):
    """Extract PYZ archive — PYZ is essentially a marshalled code archive."""
    from PyInstaller.archive.readers import CArchiveReader
    archive = CArchiveReader(exe_path)

    os.makedirs(output_dir, exist_ok=True)
    extracted = 0

    # Find PYZ entry
    if 'PYZ-00.pyz' in archive.toc:
        try:
            pyz_archive = archive.open_embedded_archive('PYZ-00.pyz')
            pyz_toc = pyz_archive.toc
            print(f"PYZ entries: {len(pyz_toc)}")

            for name in pyz_toc:
                data = pyz_archive.extract(name)
                if data is None:
                    continue

                safe_name = name + '.pyc'
                path = os.path.join(output_dir, safe_name)
                os.makedirs(os.path.dirname(path), exist_ok=True)

                if save_as_pyc(data, path):
                    extracted += 1
        except Exception as e:
            print(f"PYZ extraction error: {e}")
    else:
        print("PYZ-00.pyz not found in archive")

    print(f"PYZ modules extracted: {extracted}")
    return extracted


def decompile_pyc(pyc_path, output_path):
    """Decompile a .pyc file to .py using decompyle3."""
    import subprocess
    try:
        result = subprocess.run(
            ['python', '-m', 'uncompyle6', '-o', output_path, pyc_path],
            capture_output=True, text=True, timeout=30
        )
        return True
    except Exception as e:
        pass

    # Try decompyle3
    try:
        result = subprocess.run(
            ['python', '-m', 'decompyle3', '-o', output_path, pyc_path],
            capture_output=True, text=True, timeout=30
        )
        return True
    except Exception as e:
        pass

    return False


def main():
    from PyInstaller.archive.readers import CArchiveReader

    print("=" * 60)
    print("GCRPA.exe Full Extraction Pipeline")
    print("=" * 60)

    archive = CArchiveReader(EXE)
    print(f"\nTOC entries: {len(archive.toc)}")

    # Step 1: Extract all TOC entries, converting code to .pyc
    print("\n--- Step 1: Extract TOC entries ---")
    toc_pyc = extract_toc_modules(archive, OUT_PYC)

    # Step 2: Extract PYZ entries
    print("\n--- Step 2: Extract PYZ entries ---")
    pyz_pyc = extract_pyz_properly(EXE, OUT_PYC)

    # Step 3: Also try extracting base_library.zip as raw zip
    print("\n--- Step 3: Extract base_library.zip ---")
    if 'base_library.zip' in archive.toc:
        data = archive.extract('base_library.zip')
        zip_path = os.path.join(OUT_EXTRACTED, 'base_library.zip')
        with open(zip_path, 'wb') as f:
            f.write(data)
        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                zip_out = os.path.join(OUT_PYC, 'base_library')
                zf.extractall(zip_out)
                print(f"  Extracted to {zip_out}")
        except Exception as e:
            print(f"  Not a valid ZIP: {e}")

    # Summary
    total_pyc = sum(1 for r, d, fs in os.walk(OUT_PYC) for f in fs if f.endswith('.pyc'))
    print(f"\n{'=' * 60}")
    print(f"Total .pyc files: {total_pyc}")
    print(f"Output: {OUT_PYC}")

    # List key files
    print("\n--- Key .pyc files ---")
    for root, dirs, files in os.walk(OUT_PYC):
        for f in sorted(files)[:30]:
            full = os.path.join(root, f)
            rel = os.path.relpath(full, OUT_PYC)
            size = os.path.getsize(full)
            print(f"  {rel} ({size:,} bytes)")

    return total_pyc


if __name__ == '__main__':
    main()
