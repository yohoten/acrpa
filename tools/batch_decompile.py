"""
Batch decompile all remaining .pyc files.
Suppresses verbose output, handles errors gracefully.
"""
import os
import sys
import warnings

PYC_DIR = r"E:\GCRPA\pyc_files"
OUT_DIR = r"E:\GCRPA\decompiled"


def decompile_one(pyc_path, py_path):
    """Decompile a single .pyc file, return (success, error_msg)."""
    from io import StringIO

    output = StringIO()
    try:
        # Suppress stderr during decompilation
        import uncompyle6
        old_stderr = sys.stderr
        sys.stderr = StringIO()
        try:
            uncompyle6.decompile_file(pyc_path, output)
        finally:
            sys.stderr = old_stderr

        result = output.getvalue()
        if result.strip():
            os.makedirs(os.path.dirname(py_path), exist_ok=True)
            with open(py_path, 'w', encoding='utf-8') as f:
                f.write(result)
            return True, None
        else:
            return False, "Empty output"
    except Exception as e:
        return False, str(e)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    # Collect all .pyc files
    all_pyc = []
    for root, dirs, files in os.walk(PYC_DIR):
        for f in files:
            if f.endswith('.pyc'):
                all_pyc.append(os.path.join(root, f))

    # Skip files already decompiled
    remaining = []
    already = 0
    for pyc in all_pyc:
        rel = os.path.relpath(pyc, PYC_DIR)
        py_name = rel[:-4] + '.py'
        py_path = os.path.join(OUT_DIR, py_name)
        if os.path.exists(py_path):
            already += 1
        else:
            remaining.append((pyc, py_path, rel))

    print(f"Total .pyc: {len(all_pyc)}")
    print(f"Already decompiled: {already}")
    print(f"Remaining: {len(remaining)}")

    success = 0
    failed = 0

    for idx, (pyc_path, py_path, rel) in enumerate(remaining):
        ok, err = decompile_one(pyc_path, py_path)
        if ok:
            success += 1
        else:
            failed += 1
            # Save error
            err_path = py_path + '.err'
            with open(err_path, 'w') as ef:
                ef.write(f"Decompile error: {err}\n")

        if (success + failed) % 50 == 0 and (success + failed) > 0:
            print(f"  Progress: {success + failed}/{len(remaining)} ({success} ok, {failed} fail)")

    print(f"\nBatch complete: {success} succeeded, {failed} failed")

    # Print GCRPA.py stats
    gcrpa_path = os.path.join(OUT_DIR, 'GCRPA.py')
    if os.path.exists(gcrpa_path):
        with open(gcrpa_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        print(f"\nGCRPA.py: {len(lines)} lines, {os.path.getsize(gcrpa_path)} bytes")
        # Check for key functions
        funcs = [l.strip() for l in lines if l.startswith('def ')]
        print(f"Functions found: {len(funcs)}")
        for func in funcs:
            print(f"  {func}")


if __name__ == '__main__':
    main()
