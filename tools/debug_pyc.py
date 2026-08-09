"""Debug .pyc header format."""
import marshal
import py_compile, tempfile, os
import importlib.util

# Check our GCRPA.pyc
with open(r'E:\GCRPA\pyc_files\GCRPA.pyc', 'rb') as f:
    data = f.read()

print(f"Our GCRPA.pyc: {len(data)} bytes")
print(f"Header+data: magic={data[:4].hex()} flags={data[4:8].hex()} ts={data[8:12].hex()}")

for offset in [12, 16, 8]:
    try:
        code = marshal.loads(data[offset:])
        print(f"  offset {offset}: OK - {code.co_name}")
    except Exception as e:
        print(f"  offset {offset}: {e}")

# Create reference .pyc to see correct format
print(f"\nPython magic: {importlib.util.MAGIC_NUMBER.hex()}")

tmp = tempfile.NamedTemporaryFile(suffix='.py', delete=False)
tmp.write(b'x = 1\n')
tmp.close()
pyc_path = py_compile.compile(tmp.name, doraise=True)
with open(pyc_path, 'rb') as f:
    ref = f.read()
print(f"Reference .pyc: {len(ref)} bytes")
print(f"  magic: {ref[:4].hex()}")
print(f"  bytes 4-7 (flags): {ref[4:8].hex()}")
print(f"  bytes 8-11: {ref[8:12].hex()}")
print(f"  bytes 12-15: {ref[12:16].hex()}")

for offset in [12, 16]:
    try:
        code = marshal.loads(ref[offset:])
        print(f"  offset {offset}: OK - {code.co_name}")
    except Exception as e:
        print(f"  offset {offset}: {e}")

os.unlink(tmp.name)
os.unlink(pyc_path)
