"""
Batch decompile: capture stdout correctly to suppress debug noise.
"""
import os
import sys
from io import StringIO

PYC_DIR = r"E:\GCRPA\pyc_files"
OUT_DIR = r"E:\GCRPA\decompiled"


def decompile_one(pyc_path, py_path):
    """Decompile a single .pyc, capturing ALL output cleanly."""
    out_buf = StringIO()
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = StringIO()  # suppress debug spam
    sys.stderr = StringIO()  # suppress errors

    try:
        import uncompyle6
        uncompyle6.decompile_file(pyc_path, out_buf)
    except Exception:
        pass
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    result = out_buf.getvalue()
    if result.strip():
        os.makedirs(os.path.dirname(py_path), exist_ok=True)
        with open(py_path, 'w', encoding='utf-8') as f:
            f.write(result)
        return True
    return False


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    todo = []
    for root, dirs, files in os.walk(PYC_DIR):
        for f in files:
            if f.endswith('.pyc'):
                pyc_path = os.path.join(root, f)
                rel = os.path.relpath(pyc_path, PYC_DIR)
                py_path = os.path.join(OUT_DIR, rel[:-4] + '.py')
                if not os.path.exists(py_path):
                    todo.append((pyc_path, py_path, rel))

    # Put important files first
    priority = [x for x in todo if 'GCRPA' in x[2] or 'pyi' in x[2].lower()]
    rest = [x for x in todo if x not in priority]
    ordered = priority + rest

    print(f"Remaining: {len(todo)} files")
    ok = 0
    fail = 0

    for pyc_path, py_path, rel in ordered:
        if decompile_one(pyc_path, py_path):
            ok += 1
        else:
            fail += 1

        if (ok + fail) % 100 == 0:
            print(f"  {ok + fail}/{len(todo)} ({ok} ok, {fail} fail)")

    print(f"\nDone: {ok} succeeded, {fail} failed")

    gcrpa_py = os.path.join(OUT_DIR, 'GCRPA.py')
    if os.path.exists(gcrpa_py):
        with open(gcrpa_py, 'r', encoding='utf-8') as f:
            n = len(f.readlines())
        print(f"GCRPA.py: {n} lines")


if __name__ == '__main__':
    main()
