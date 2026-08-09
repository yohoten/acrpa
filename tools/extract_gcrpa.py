"""
GCRPA.exe PyInstaller 提取脚本
直接从 EXE 中解析 PyInstaller CArchive 格式并提取所有文件
"""
import os
import zlib
import struct
import marshal
import sys

PYINSTALLER_MAGIC = b'MEI\x0C\x0B\x0A\x0B\x0E'

OUTPUT_DIR = r"E:\GCRPA\extracted"
PYZ_DIR = r"E:\GCRPA\pyz_output"
EXE_PATH = r"F:\（8）Desktop\Files\Script_plugin\GCRPA.exe"


def find_carchive_offset(data):
    """找到 CArchive 的起始偏移（在 PE 文件末尾的 overlay 中）"""
    # PyInstaller 将 CArchive 附加到 EXE 末尾
    # 在文件末尾搜索 magic marker
    for i in range(len(data) - 100, -1, -1):
        if data[i:i+8] == PYINSTALLER_MAGIC:
            return i
    return None


def parse_toc(data, offset):
    """解析 CArchive 的 Table of Contents"""
    pos = offset + 8  # skip magic
    # Cookie
    cookie_size = struct.unpack('!i', data[pos:pos+4])[0]
    pos += 4
    toc_length = struct.unpack('!i', data[pos:pos+4])[0]
    pos += 4
    toc_pos = struct.unpack('!i', data[pos:pos+4])[0]
    pos += 4
    pyver = struct.unpack('!i', data[pos:pos+4])[0]
    pos += 4
    # Python library version info
    python_lib_offset = pos

    # TOC starts at offset + toc_pos
    toc_start = offset + toc_pos
    pos = toc_start
    entries = []
    while pos < offset + toc_length:
        # Each entry: (entry_size, path_len, path, data...)
        entry_size = struct.unpack('!i', data[pos:pos+4])[0]
        pos += 4
        if entry_size == 0:
            break
        path_len = struct.unpack('!i', data[pos:pos+4])[0]
        pos += 4
        path = data[pos:pos+path_len].rstrip(b'\x00').decode('utf-8', errors='replace')
        pos += path_len
        # entry_size - 4(path_len) - path_len
        data_len = entry_size - 4 - path_len
        entry_data = data[pos:pos+data_len]
        pos += data_len
        entries.append((path, entry_data))
    return entries


def extract_carchive(data, output_dir):
    """从 CArchive 中提取所有文件"""
    offset = find_carchive_offset(data)
    if offset is None:
        print("ERROR: 未找到 PyInstaller CArchive marker!")
        return False

    print(f"找到 CArchive 在偏移: 0x{offset:X} ({offset})")

    entries = parse_toc(data, offset)
    print(f"解析到 {len(entries)} 个条目")

    for path, entry_data in entries:
        out_path = os.path.join(output_dir, path)
        out_dir = os.path.dirname(out_path)
        os.makedirs(out_dir, exist_ok=True)

        # 检查是否被 zlib 压缩 (entry 以 'x\x9c' 或 'x\xda' 等 zlib header 开头)
        if entry_data[:2] == b'\x78\x9c' or entry_data[:2] == b'\x78\xda' or entry_data[:2] == b'\x78\x01':
            try:
                entry_data = zlib.decompress(entry_data)
            except Exception:
                pass

        try:
            with open(out_path, 'wb') as f:
                f.write(entry_data)
        except Exception as e:
            print(f"  写入失败: {path}: {e}")

    print(f"提取完成，输出目录: {output_dir}")
    return True


def extract_pyz(pyz_path, output_dir):
    """解压 PYZ 归档（PYZ 本质是包含 pyc 的 zip 文件）"""
    import zipfile
    os.makedirs(output_dir, exist_ok=True)
    count = 0
    try:
        with zipfile.ZipFile(pyz_path, 'r') as zf:
            for name in zf.namelist():
                zf.extract(name, output_dir)
                count += 1
        print(f"PYZ 解压完成: {count} 个文件 -> {output_dir}")
        return True
    except zipfile.BadZipFile:
        print(f"PYZ 文件不是标准 ZIP 格式: {pyz_path}")
        return False
    except Exception as e:
        print(f"PYZ 解压错误: {e}")
        return False


def main():
    print(f"读取 EXE: {EXE_PATH}")
    print(f"文件大小: {os.path.getsize(EXE_PATH) / 1024 / 1024:.1f} MB")

    with open(EXE_PATH, 'rb') as f:
        data = f.read()

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Step 1: 提取 CArchive
    print("\n=== Step 1: 提取 PyInstaller CArchive ===")
    if not extract_carchive(data, OUTPUT_DIR):
        print("CArchive 提取失败")
        sys.exit(1)

    # Step 2: 查找并解压 PYZ 文件
    print("\n=== Step 2: 解压 PYZ 归档 ===")
    pyz_files = []
    for root, dirs, files in os.walk(OUTPUT_DIR):
        for f in files:
            if f.endswith('.pyz') or f.startswith('PYZ'):
                pyz_files.append(os.path.join(root, f))

    for pyz in pyz_files:
        print(f"解压: {pyz}")
        extract_pyz(pyz, PYZ_DIR)

    print("\n=== 提取完成 ===")
    print(f"CArchive 文件: {OUTPUT_DIR}")
    print(f"PYZ 文件: {PYZ_DIR}")

    # 列出主要提取的文件
    print("\n--- 提取的顶层文件 ---")
    for root, dirs, files in os.walk(OUTPUT_DIR):
        level = root.replace(OUTPUT_DIR, '').count(os.sep)
        indent = '  ' * level
        print(f"{indent}[{os.path.basename(root)}/]")
        if level < 2:
            for f in files[:20]:
                size = os.path.getsize(os.path.join(root, f))
                print(f"{indent}  {f} ({size:,} bytes)")
        break

    print("\n--- PYZ 内容 (前30个) ---")
    for root, dirs, files in os.walk(PYZ_DIR):
        for f in files[:30]:
            print(f"  {f}")
        break


if __name__ == '__main__':
    main()
