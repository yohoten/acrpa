# -*- coding: utf-8 -*-
"""Dump PE header / exports / imports of a DLL without third-party deps.

Usage:
    python tools/_inspect_pe.py <path-to-dll> [--strings]
"""
import re
import struct
import sys


def main(path, do_strings=False):
    with open(path, "rb") as fh:
        data = fh.read()

    print("file        :", path)
    print("size        :", len(data), "bytes (%.2f MB)" % (len(data) / 1048576.0))
    if data[:2] != b"MZ":
        print("not a PE file")
        return

    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    if data[e_lfanew:e_lfanew + 4] != b"PE\0\0":
        print("bad PE signature")
        return

    machine = struct.unpack_from("<H", data, e_lfanew + 4)[0]
    nsec = struct.unpack_from("<H", data, e_lfanew + 6)[0]
    opt_size = struct.unpack_from("<H", data, e_lfanew + 20)[0]
    chars = struct.unpack_from("<H", data, e_lfanew + 22)[0]
    opt_off = e_lfanew + 24
    magic = struct.unpack_from("<H", data, opt_off)[0]
    is64 = magic == 0x20B
    arch = {0x14C: "x86(32)", 0x8664: "x64", 0xAA64: "arm64"}.get(machine, hex(machine))
    print("machine     :", arch)
    print("magic       :", hex(magic), "(PE32+)" if is64 else "")
    print("characteri  :", hex(chars), "DLL" if chars & 0x2000 else "")

    dd_off = opt_off + (112 if is64 else 96)
    dirs = []
    for i in range(16):
        rva, size = struct.unpack_from("<II", data, dd_off + i * 8)
        dirs.append((rva, size))

    print("CLR header  :", "yes -> .NET / mixed-mode" if dirs[14][0] else "none -> native")

    sh_off = opt_off + opt_size
    secs = []
    for i in range(nsec):
        o = sh_off + i * 40
        name = data[o:o + 8].rstrip(b"\0").decode("latin1", "replace")
        vsize, va, rsize, roff = struct.unpack_from("<IIII", data, o + 8)
        secs.append((name, va, max(vsize, rsize), roff))

    def rva2off(rva):
        for _name, va, vsz, roff in secs:
            if va <= rva < va + vsz:
                return roff + (rva - va)
        return None

    def cstr(off):
        if off is None:
            return "<bad-rva>"
        end = data.find(b"\0", off)
        return data[off:end].decode("latin1", "replace")

    # ── exports ──
    exp_rva = dirs[0][0]
    if exp_rva:
        off = rva2off(exp_rva)
        dllname_rva, _base, nfunc, nnames = struct.unpack_from("<IIII", data, off + 12)
        an, ao = struct.unpack_from("<II", data, off + 32)
        print("\n[dll name]   :", cstr(rva2off(dllname_rva)))
        print("[exports]    : total=%d named=%d" % (nfunc, nnames))
        names = []
        for i in range(nnames):
            nrva = struct.unpack_from("<I", data, rva2off(an) + i * 4)[0]
            names.append(cstr(rva2off(nrva)))
        for n in sorted(names, key=lambda s: s.lower())[:200]:
            print("   ", n)
    else:
        print("\n[exports]    : none")

    # ── imports: descriptor = OFT(0) Time(4) Fwd(8) Name(12) FirstThunk(16) ──
    print("\n[imports]")
    imp_rva = dirs[1][0]
    if imp_rva:
        off = rva2off(imp_rva)
        i = 0
        while True:
            oft, _t, _f, name_rva, ft = struct.unpack_from("<IIIII", data, off + i * 20)
            if not (oft or name_rva or ft):
                break
            print("   ", cstr(rva2off(name_rva)))
            i += 1
            if i > 200:
                break
    else:
        print("    none")

    dly_rva = dirs[13][0]
    print("\n[delay imports]")
    if dly_rva:
        off = rva2off(dly_rva)
        i = 0
        while True:
            attrs, name_rva = struct.unpack_from("<II", data, off + i * 32)
            if not name_rva:
                break
            print("   ", cstr(rva2off(name_rva)))
            i += 1
            if i > 200:
                break
    else:
        print("    none")

    if not do_strings:
        return

    print("\n[key strings]")
    pat = re.compile(
        r"(paddle|inference|model|\.pdmodel|\.pdiparams|det_|rec_|cls_|json|ini|yaml|"
        r"dict|shape|gpu|cpu|thread|batch|license|struct|ocr|image|utf|ansi)",
        re.I)
    seen = set()
    for m in re.finditer(rb"[\x20-\x7e]{6,400}", data):
        s = m.group().decode("latin1")
        if pat.search(s) and s not in seen:
            seen.add(s)
            print("   ", s)
        if len(seen) > 260:
            print("    ... (truncated)")
            break


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "lib/paddle_ocr/PaddleOCR.dll",
         do_strings="--strings" in sys.argv)
