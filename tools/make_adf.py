#!/usr/bin/env python3
"""make_adf.py - write a bootable AmigaDOS floppy image (OFS, 880 KB) that runs a program at boot.

  tools/make_adf.py <out.adf> <volume name> <startup-sequence text> <file> [<file> ...]

Every <file> goes into the disk's root under its own name; the startup-sequence goes into S/. The boot block
is the standard one (it starts dos.library), so the disk boots on Kickstart 1.2 and later and on FS-UAE's AROS
ROM. No C: commands are on the disk: the startup-sequence can only run programs that are on it (and the
Shell's built-in commands on Kickstart 2.0 and later). Only the Python standard library is needed.
"""
import os, struct, sys

BLOCKS, BS, ROOT = 1760, 512, 880
T_HEADER, T_DATA, T_LIST = 2, 8, 16
ST_ROOT, ST_DIR, ST_FILE = 1, 2, -3
DAYS, MINS = 4706, 720                       # 1990-11-19, 12:00: the date every block carries

# moveq/lea/jsr FindResident("dos.library"); return its rt_Init in a0 (the boot block of AmigaDOS "install")
BOOT_CODE = bytes.fromhex("43fa00184eaeffa04a80670a2040206800167000" "4e7570ff4e75") + b"dos.library\0"


def put(b, off, fmt, *v):
    struct.pack_into(">" + fmt, b, off, *v)


def checksum(b, off=20):
    put(b, off, "I", 0)
    s = sum(struct.unpack(">128I", bytes(b))) & 0xffffffff
    put(b, off, "I", (-s) & 0xffffffff)


def name_hash(name):
    h = len(name)
    for c in name.upper():
        h = (h * 13 + ord(c)) & 0x7ff
    return h % 72


class Disk:
    def __init__(self, label):
        self.img = bytearray(BLOCKS * BS)
        self.used = {0, 1, ROOT, ROOT + 1}
        self.next = ROOT + 2
        self.blocks = {ROOT: self.header(ROOT, label, 0, ST_ROOT)}

    def alloc(self):
        while self.next in self.used:
            self.next += 1
            if self.next >= BLOCKS:
                self.next = 2
            if len(self.used) >= BLOCKS:
                sys.exit("the disk is full")
        self.used.add(self.next)
        return self.next

    def header(self, key, name, parent, sec):
        b = bytearray(BS)
        put(b, 0, "I", T_HEADER)
        put(b, 4, "I", 0 if sec == ST_ROOT else key)
        if sec == ST_ROOT:
            put(b, 12, "I", 72)
            put(b, 0x138, "i", -1)               # bitmap valid
            put(b, 0x13c, "I", ROOT + 1)         # the one bitmap block
            put(b, 0x1d8, "III", DAYS, MINS, 0)  # disk altered
            put(b, 0x1e4, "III", DAYS, MINS, 0)  # disk created
        put(b, 0x1a4, "III", DAYS, MINS, 0)
        n = name.encode("latin1")[:30]
        b[0x1b0] = len(n); b[0x1b1:0x1b1 + len(n)] = n
        put(b, 0x1f4, "I", parent)
        put(b, 0x1fc, "i", sec)
        return b

    def link(self, dirkey, key, name):
        """Put block key into the hash table of directory dirkey (chaining on collisions)."""
        d = self.blocks[dirkey]
        slot = 24 + 4 * name_hash(name)
        cur = struct.unpack(">I", d[slot:slot + 4])[0]
        if not cur:
            put(d, slot, "I", key); return
        while True:
            e = self.blocks[cur]
            nxt = struct.unpack(">I", e[0x1f0:0x1f4])[0]
            if not nxt:
                put(e, 0x1f0, "I", key); return
            cur = nxt

    def mkdir(self, parent, name):
        key = self.alloc()
        self.blocks[key] = self.header(key, name, parent, ST_DIR)
        self.link(parent, key, name)
        return key

    def add(self, parent, name, data):
        hdr = self.alloc()
        h = self.header(hdr, name, parent, ST_FILE)
        put(h, 0x144, "I", len(data))
        self.blocks[hdr] = h
        self.link(parent, hdr, name)
        chunks = [data[i:i + BS - 24] for i in range(0, len(data), BS - 24)]
        keys = [self.alloc() for _ in chunks]
        for i, (k, c) in enumerate(zip(keys, chunks)):           # OFS data blocks
            b = bytearray(BS)
            put(b, 0, "IIIII", T_DATA, hdr, i + 1, len(c), keys[i + 1] if i + 1 < len(keys) else 0)
            b[24:24 + len(c)] = c
            self.blocks[k] = b
        tab, holder = keys, h                                    # 72 pointers per header / extension block
        put(h, 16, "I", keys[0] if keys else 0)
        while True:
            part, tab = tab[:72], tab[72:]
            put(holder, 8, "I", len(part))
            for i, k in enumerate(part):
                put(holder, 0x134 - 4 * i, "I", k)
            if not tab:
                break
            ext = self.alloc()
            e = bytearray(BS)
            put(e, 0, "II", T_LIST, ext)
            put(e, 0x1f4, "I", hdr)
            put(e, 0x1fc, "i", ST_FILE)
            put(holder, 0x1f8, "I", ext)
            self.blocks[ext] = e
            holder = e

    def write(self, path):
        boot = bytearray(1024)
        boot[0:4] = b"DOS\0"
        put(boot, 8, "I", ROOT)
        boot[12:12 + len(BOOT_CODE)] = BOOT_CODE
        s = 0
        for (v,) in struct.iter_unpack(">I", bytes(boot)):
            s += v
            if s > 0xffffffff: s = (s & 0xffffffff) + 1
        put(boot, 4, "I", (~s) & 0xffffffff)
        self.img[0:1024] = boot
        bm = bytearray(BS)                                       # bit set = block free, blocks 2..1759
        for blk in range(2, BLOCKS):
            if blk not in self.used:
                i = blk - 2
                v = struct.unpack(">I", bm[4 + 4 * (i // 32):8 + 4 * (i // 32)])[0] | (1 << (i % 32))
                put(bm, 4 + 4 * (i // 32), "I", v)
        checksum(bm, 0)
        self.img[(ROOT + 1) * BS:(ROOT + 2) * BS] = bm
        for k, b in self.blocks.items():
            checksum(b)
            self.img[k * BS:(k + 1) * BS] = b
        open(path, "wb").write(self.img)
        return len(self.used)


def main():
    if len(sys.argv) < 5:
        sys.exit(__doc__)
    out, label, startup, files = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4:]
    d = Disk(label)
    s = d.mkdir(ROOT, "s")
    d.add(s, "startup-sequence", (startup.replace("\\n", "\n").rstrip("\n") + "\n").encode("latin1"))
    for f in files:
        d.add(ROOT, os.path.basename(f), open(f, "rb").read())
    used = d.write(out)
    print("%s: \"%s\", %d of %d blocks used (%d KB free)" % (out, label, used, BLOCKS, (BLOCKS - used) * 488 // 1024))


if __name__ == "__main__":
    main()
