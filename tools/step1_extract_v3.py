"""
Step 1: Extract all files from PyInstaller EXE using PyInstaller API.
"""
import os
import sys
import zipfile
import zlib

EXE = r"E:\GCRPA\GCRPA.exe"
OUT_EXTRACTED = r"E:\GCRPA\extracted"
OUT_PYZ = r"E:\GCRPA\pyz_output"


def extract_all():
    from PyInstaller.archive.readers import CArchiveReader

    archive = CArchiveReader(EXE)
    toc = archive.toc
    print(f"Total TOC entries: {len(toc)}")

    os.makedirs(OUT_EXTRACTED, exist_ok=True)

    # Count types
    pyc_count = 0
    dll_count = 0
    pyz_count = 0
    zip_count = 0
    other_count = 0

    extracted = 0
    errors = 0

    for name in toc:
        try:
            data = archive.extract(name)
            if data is None:
                continue

            out_path = os.path.join(OUT_EXTRACTED, name)
            out_dir = os.path.dirname(out_path)
            os.makedirs(out_dir, exist_ok=True)

            with open(out_path, 'wb') as f:
                f.write(data)
            extracted += 1

            # Categorize
            if name.endswith('.pyc'):
                pyc_count += 1
            elif name.endswith('.dll') or name.endswith('.pyd'):
                dll_count += 1
            elif name.endswith('.pyz'):
                pyz_count += 1
            elif name.endswith('.zip'):
                zip_count += 1
            else:
                other_count += 1

        except Exception as e:
            errors += 1
            if errors <= 5:
                print(f"  Error extracting '{name}': {e}")

    print(f"\nExtracted: {extracted} files")
    print(f"  .pyc: {pyc_count}")
    print(f"  .dll/.pyd: {dll_count}")
    print(f"  .pyz: {pyz_count}")
    print(f"  .zip: {zip_count}")
    print(f"  other: {other_count}")
    print(f"  errors: {errors}")

    return archive


def extract_embedded_archives(archive):
    """Handle PYZ and ZIP files embedded in the archive."""
    from PyInstaller.archive.readers import CArchiveReader

    total_pyc = 0

    for name in archive.toc:
        if name.endswith('.pyz') or name.endswith('.zip'):
            print(f"\nProcessing embedded archive: {name}")
            try:
                embedded = archive.open_embedded_archive(name)
                # embedded is another CArchiveReader
                if hasattr(embedded, 'toc'):
                    toc = embedded.toc
                    print(f"  Entries: {len(toc)}")
                    sub_dir = os.path.join(OUT_PYZ, os.path.splitext(name)[0])
                    os.makedirs(sub_dir, exist_ok=True)
                    for sub_name in toc:
                        try:
                            sub_data = embedded.extract(sub_name)
                            if sub_data:
                                out_path = os.path.join(sub_dir, sub_name)
                                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                                with open(out_path, 'wb') as f:
                                    f.write(sub_data)
                                if sub_name.endswith('.pyc'):
                                    total_pyc += 1
                        except Exception as e:
                            pass
                    print(f"  Extracted to: {sub_dir}")
            except Exception as e:
                print(f"  Failed to open embedded archive '{name}': {e}")

    print(f"\nTotal .pyc from embedded archives: {total_pyc}")


def main():
    print(f"=== Step 1: Extract GCRPA.exe ===")
    print(f"EXE: {EXE}")

    archive = extract_all()

    print(f"\n=== Step 2: Extract embedded PYZ/ZIP archives ===")
    extract_embedded_archives(archive)

    # Final count
    all_pyc = 0
    for root, dirs, files in os.walk(OUT_EXTRACTED):
        for f in files:
            if f.endswith('.pyc'):
                all_pyc += 1
    for root, dirs, files in os.walk(OUT_PYZ):
        for f in files:
            if f.endswith('.pyc'):
                all_pyc += 1

    print(f"\n=== Done ===")
    print(f"Extracted files: {OUT_EXTRACTED}")
    print(f"PYZ expanded: {OUT_PYZ}")
    print(f"Total .pyc files found: {all_pyc}")


if __name__ == '__main__':
    main()
