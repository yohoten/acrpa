"""
Batch decompile using subprocess to avoid debug output pollution.
"""
import os
import sys
import subprocess
import glob

PYC_DIR = r"E:\GCRPA\pyc_files"
OUT_DIR = r"E:\GCRPA\decompiled"


def decompile_subprocess(pyc_path, py_path):
    """Decompile using subprocess to capture all output cleanly."""
    os.makedirs(os.path.dirname(py_path), exist_ok=True)

    script = f"""
import sys
import os
sys.path.insert(0, r'{OUT_DIR}')
try:
    import uncompyle6
    with open(r'{py_path}', 'w', encoding='utf-8') as out:
        uncompyle6.decompile_file(r'{pyc_path}', out)
except Exception as e:
    with open(r'{py_path}.err', 'w') as ef:
        ef.write(str(e))
    sys.exit(1)
"""
    result = subprocess.run(
        [sys.executable, '-c', script],
        capture_output=True, text=True, timeout=30
    )
    return result.returncode == 0


def main():
    # Collect .pyc files not yet decompiled
    todo = []
    for root, dirs, files in os.walk(PYC_DIR):
        for f in files:
            if f.endswith('.pyc'):
                pyc_path = os.path.join(root, f)
                rel = os.path.relpath(pyc_path, PYC_DIR)
                py_path = os.path.join(OUT_DIR, rel[:-4] + '.py')
                if not os.path.exists(py_path):
                    todo.append((pyc_path, py_path, rel))

    print(f"Todo: {len(todo)} files")

    ok = 0
    fail = 0

    # Process GCRPA.py first if present
    gcrpa = [(p, o, r) for p, o, r in todo if 'GCRPA' in r]
    others = [(p, o, r) for p, o, r in todo if 'GCRPA' not in r]

    ordered = gcrpa + others

    for pyc_path, py_path, rel in ordered:
        if decompile_subprocess(pyc_path, py_path):
            ok += 1
        else:
            fail += 1

        if (ok + fail) % 100 == 0:
            print(f"  Progress: {ok + fail}/{len(todo)} ({ok} ok, {fail} fail)")

    print(f"\nDone: {ok} succeeded, {fail} failed")

    # Show GCRPA stats
    gcrpa_py = os.path.join(OUT_DIR, 'GCRPA.py')
    if os.path.exists(gcrpa_py):
        with open(gcrpa_py, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        print(f"\nGCRPA.py: {len(lines)} lines")


if __name__ == '__main__':
    main()
