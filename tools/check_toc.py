"""Check TOC entry structure and find app modules."""
from PyInstaller.archive.readers import CArchiveReader

a = CArchiveReader(r'E:\GCRPA\GCRPA.exe')
toc = a.toc

categories = {'dll': 0, 'pyd': 0, 'dist_info': 0, 'pyz': 0, 'zip': 0, 'dir': 0, 'pyc': 0, 'other': 0}
other_names = []

for name in toc:
    if name.endswith('.dll') or '.dll' in name:
        categories['dll'] += 1
    elif name.endswith('.pyd'):
        categories['pyd'] += 1
    elif '.dist-info' in name or '.egg-info' in name:
        categories['dist_info'] += 1
    elif name.endswith('.pyz'):
        categories['pyz'] += 1
    elif name.endswith('.zip'):
        categories['zip'] += 1
    elif '/' in name or '\\' in name:
        categories['dir'] += 1
    elif name.endswith('.pyc'):
        categories['pyc'] += 1
    else:
        categories['other'] += 1
        other_names.append(name)

print("Categories:")
for k, v in categories.items():
    print(f"  {k}: {v}")

print(f"\nOther entries (total {len(other_names)}):")
for n in other_names[:80]:
    print(f"  {n}")
if len(other_names) > 80:
    print(f"  ... and {len(other_names)-80} more")

# Check raw data of a few entries
print("\n--- Raw data samples ---")
for name in other_names[:3]:
    data = a.extract(name)
    if data is not None:
        print(f"\n  '{name}': type={type(data).__name__}, len={len(data)}")
        # Check if it looks like marshalled code
        import marshal
        try:
            obj = marshal.loads(data)
            print(f"    marshal type: {type(obj).__name__}")
        except:
            print(f"    Not marshallable. First bytes: {data[:20].hex()}")

# Also check some dir entries
print("\n--- Dir entries check ---")
dir_names = [n for n in toc if '/' in n or '\\' in n]
print(f"Total dir-like entries: {len(dir_names)}")
for n in dir_names[:20]:
    print(f"  {n}")
