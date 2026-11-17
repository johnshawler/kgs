#!/usr/bin/env python3
"""kgs_patch.py - patch The Killing Game Show so that the in-game music and the sound effects play together.

  tools/kgs_patch.py <game disk.adf> <patched.adf>

The game disk is the AGA-fixed one, "Killing Game Show, The (1990)(Psygnosis)(Disk 2 of 2)[f AGA Fix Ltd]"
(volume "aRAYA-fIX Limited"): the tool adds the patch to that fix's first hunk. Only the file "kgs" changes; the
other files, the boot block and the rest of the disk stay as they are. The patched game needs 1 MB of chip
RAM or more (A1200, A500+, A600, A3000, A4000); see src/patch/resident.s for what the patch does.

Needs the template's cross tools for the 68000 assembler (tools/setup.sh: Docker image amigadev/crosstools).
Every patched address is checked against the bytes the patch expects to replace, and the game hunk must
unpack to the known image, so another version of the game is refused rather than broken.
"""
import hashlib, os, struct, subprocess, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kgs_extract import adf_files, unpack_program, GAME_BASE  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGE_MD5 = "782f364e"                       # the unpacked game (all five TOSEC game-disk dumps)
HUNK0_LEN = 528                              # the AGA fix's first hunk
CALL_GAME = 0x14C                            # in it: "jsr <game hunk>" (relocated at 0x14E)
RESIDENT = 0x80000
# What each patched site holds in the original (checked before patching).
EXPECT = {0x1A116: "660000bc", 0x14AEC: "603e", 0x14AF2: "6638", 0x14D96: "4bf900dff0d0",
          0x14E5C: "4bf900dff0d0", 0x14F88: "2b6e000a00d03b6e000e00d4", 0x1C8CA: "083900000001c8286704",
          0x1C814: "0002ea72", 0x3300: "4ef90001254c"}
BS, DATA = 512, 488


# ------------------------------------------------------------------------------------------- assembling
def assemble(workdir):
    rel = os.path.relpath(workdir, ROOT)
    cmd = ("set -e; cd /work/src/patch; "
           "vasmm68k_mot -quiet -Fbin -m68000 -o /work/{w}/resident.bin resident.s; "
           
           "vasmm68k_mot -quiet -Fbin -m68000 -I/work/{w} -o /work/{w}/wrapper.bin wrapper.s").format(w=rel)
    subprocess.run(["docker", "run", "--rm", "-v", ROOT + ":/work", "amigadev/crosstools:m68k-amigaos",
                    "bash", "-c", cmd], check=True)
    return open(os.path.join(workdir, "resident.bin"), "rb").read(), open(os.path.join(workdir, "wrapper.bin"), "rb").read()


def patch_list(res):
    """The (address, bytes) list at the end of resident.bin (its offset is stored at +4)."""
    off = struct.unpack(">H", res[4:6])[0]
    out = []
    while True:
        a = struct.unpack(">I", res[off:off + 4])[0]
        if not a:
            return out
        n = struct.unpack(">H", res[off + 4:off + 6])[0]
        out.append((a, res[off + 6:off + 6 + n]))
        off += 6 + n


# ------------------------------------------------------------------------------------------ the program
def read_hunks(exe):
    L = lambda p: struct.unpack(">I", exe[p:p + 4])[0]
    assert L(0) == 0x3f3 and L(4) == 0, "not a plain hunk executable"
    count, first, last = L(8), L(12), L(16)
    sizes = [L(20 + 4 * i) for i in range(last - first + 1)]
    p, hunks, cur = 20 + 4 * len(sizes), [], None
    while p < len(exe):
        t = L(p); p += 4
        if t & 0x3fffffff in (0x3e9, 0x3ea):
            n = L(p) * 4; cur = dict(type=t, data=bytearray(exe[p + 4:p + 4 + n]), relocs={}); hunks.append(cur); p += 4 + n
        elif t == 0x3ec:
            while True:
                n = L(p); p += 4
                if not n: break
                h = L(p); p += 4
                cur["relocs"].setdefault(h, []).extend(L(p + 4 * i) for i in range(n)); p += 4 * n
        elif t == 0x3f2:
            pass
        else:
            raise ValueError("hunk type %x not handled" % t)
    return sizes, hunks


def write_hunks(sizes, hunks):
    out = bytearray(struct.pack(">IIIII", 0x3f3, 0, len(hunks), 0, len(hunks) - 1))
    for s, h in zip(sizes, hunks):
        out += struct.pack(">I", (s & 0xc0000000) | (len(h["data"]) // 4))
    for h in hunks:
        out += struct.pack(">II", h["type"], len(h["data"]) // 4) + h["data"]
        if h["relocs"]:
            out += struct.pack(">I", 0x3ec)
            for target, offs in sorted(h["relocs"].items()):
                out += struct.pack(">II", len(offs), target) + b"".join(struct.pack(">I", o) for o in sorted(offs))
            out += struct.pack(">I", 0)
        out += struct.pack(">I", 0x3f2)
    return bytes(out)


def patch_program(exe, workdir):
    sizes, hunks = read_hunks(exe)
    if len(hunks) != 2 or len(hunks[0]["data"]) != HUNK0_LEN or bytes(hunks[0]["data"][CALL_GAME:CALL_GAME + 2]) != b"\x4e\xb9" \
            or hunks[0]["relocs"] != {1: [CALL_GAME + 2]}:
        sys.exit("kgs is not the AGA-fixed game program this patch is made for (or it is patched already)")
    image, _ = unpack_program(exe)
    if hashlib.md5(image).hexdigest()[:8] != IMAGE_MD5:
        sys.exit("the game in kgs is not the version this patch is made for")
    res, wrap = assemble(workdir)
    if len(res) > 0x400:
        sys.exit("resident.s is %d bytes: more than the $400 below the effects bank" % len(res))
    for a, b in patch_list(res):
        want = EXPECT.get(a)
        have = image[a - GAME_BASE:a - GAME_BASE + len(b)].hex()
        if want is None or not have.startswith(want) or len(want) // 2 != len(b):
            sys.exit("patch at $%X: the game holds %s there, the patch expects %s" % (a, have, want))
    assert image[0:6].hex() == EXPECT[0x3300]
    h0 = hunks[0]
    off = len(h0["data"])
    h0["data"] += wrap + b"\0" * (-len(wrap) % 4)
    disp = off - (CALL_GAME + 2)
    h0["data"][CALL_GAME:CALL_GAME + 6] = struct.pack(">HhH", 0x4eba, disp, 0x4e71)   # jsr wrapper(pc); nop
    h0["relocs"] = {1: [off + 4]}                                                   # wrapper's "game" long
    return write_hunks(sizes, hunks), len(res), len(wrap)


# ------------------------------------------------------------------------------ rewriting one OFS file
class OFS:
    def __init__(self, img):
        self.img = bytearray(img)
        assert self.img[:3] == b"DOS" and not self.img[3] & 1, "not an OFS disk"
        root = self.blk(880)
        self.bmblock = self.L(root, 0x13c)

    def blk(self, n): return self.img[n * BS:(n + 1) * BS]
    def L(self, b, o): return struct.unpack(">I", b[o:o + 4])[0]

    def put(self, n, b):
        b = bytearray(b)
        struct.pack_into(">I", b, 20, 0)
        s = sum(struct.unpack(">128I", bytes(b))) & 0xffffffff
        struct.pack_into(">I", b, 20, (-s) & 0xffffffff)
        self.img[n * BS:(n + 1) * BS] = b

    def find(self, name):
        root = self.blk(880)
        h = len(name)
        for c in name.upper(): h = (h * 13 + ord(c)) & 0x7ff
        key = self.L(root, 24 + 4 * (h % 72))
        while key:
            b = self.blk(key)
            if b[0x1b1:0x1b1 + b[0x1b0]].decode("latin1").lower() == name.lower():
                return key
            key = self.L(b, 0x1f0)
        raise KeyError(name)

    def free_map(self):
        bm = self.blk(self.bmblock)
        return {n for n in range(2, 1760) if (self.L(bm, 4 + 4 * ((n - 2) // 32)) >> ((n - 2) % 32)) & 1}

    def write_map(self, free):
        bm = bytearray(BS)
        for n in free:
            i = n - 2
            v = self.L(bm, 4 + 4 * (i // 32)) | (1 << (i % 32))
            struct.pack_into(">I", bm, 4 + 4 * (i // 32), v)
        s = sum(struct.unpack(">127I", bytes(bm[4:]))) & 0xffffffff
        struct.pack_into(">I", bm, 0, (-s) & 0xffffffff)
        self.img[self.bmblock * BS:(self.bmblock + 1) * BS] = bm

    def replace(self, name, data):
        hdr = self.find(name)
        h = bytearray(self.blk(hdr))
        old, ext, b = [], [], h
        while True:
            old += [self.L(b, 0x134 - 4 * i) for i in range(self.L(b, 8))]
            nxt = self.L(b, 0x1f8)
            if not nxt: break
            ext.append(nxt); b = self.blk(nxt)
        free = self.free_map() | set(old) | set(ext)
        chunks = [data[i:i + DATA] for i in range(0, len(data), DATA)]
        next_ = max(0, (len(chunks) - 1) // 72)
        pool = old + ext + sorted(free - set(old) - set(ext))
        if len(chunks) + next_ > len(pool):
            sys.exit("the disk has no room for the patched %s (%d more bytes than it can hold)"
                     % (name, (len(chunks) + next_ - len(pool)) * DATA))
        keys, exts = pool[:len(chunks)], pool[len(chunks):len(chunks) + next_]
        for i, (k, c) in enumerate(zip(keys, chunks)):
            b = bytearray(BS)
            struct.pack_into(">IIIII", b, 0, 8, hdr, i + 1, len(c), keys[i + 1] if i + 1 < len(keys) else 0)
            b[24:24 + len(c)] = c
            self.put(k, b)
        holders = [hdr] + exts
        blocks = {hdr: h}
        for e in exts:
            b = bytearray(BS)
            struct.pack_into(">II", b, 0, 16, e)
            struct.pack_into(">I", b, 0x1f4, hdr)
            struct.pack_into(">i", b, 0x1fc, -3)
            blocks[e] = b
        for j, owner in enumerate(holders):
            b = blocks[owner]
            part = keys[72 * j:72 * (j + 1)]
            b[24:0x138] = bytes(0x138 - 24)
            struct.pack_into(">I", b, 8, len(part))
            for i, k in enumerate(part):
                struct.pack_into(">I", b, 0x134 - 4 * i, k)
            struct.pack_into(">I", b, 0x1f8, holders[j + 1] if j + 1 < len(holders) else 0)
        struct.pack_into(">I", h, 16, keys[0] if keys else 0)
        struct.pack_into(">I", h, 0x144, len(data))
        for k, b in blocks.items():
            self.put(k, b)
        self.write_map(free - set(keys) - set(exts))
        return len(free - set(keys) - set(exts))


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    src, out = sys.argv[1], sys.argv[2]
    img = open(src, "rb").read()
    files = adf_files(src)
    if "kgs" not in files or "sfxs" not in files:
        sys.exit("%s is not the game disk (disk 2)" % src)
    with tempfile.TemporaryDirectory(dir=os.path.join(ROOT, "dist")) as work:
        exe, nres, nwrap = patch_program(files["kgs"], work)
    disk = OFS(img)
    free = disk.replace("kgs", exe)
    open(out, "wb").write(disk.img)
    check = adf_files(out)
    assert check["kgs"] == exe and all(check[f] == files[f] for f in files if f != "kgs"), "the written disk reads back wrong"
    print("%s: kgs %d -> %d bytes (wrapper %d, resident patch %d), %d blocks left free"
          % (out, len(files["kgs"]), len(exe), nwrap - nres, nres, free))


if __name__ == "__main__":
    main()
