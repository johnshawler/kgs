#!/usr/bin/env python3
"""kgs_extract.py - pull the music and sound effects out of The Killing Game Show (Psygnosis, 1990) floppy images.

  tools/kgs_extract.py <game disk (disk 2).adf> [<intro disk (disk 1).adf>] [out-dir, default original]

Writes into out-dir:
  ingame.mod    "t.k.g ingame4"       the in-game tune (file MUSC on the game disk), 4 channels
  credits.mod   "t.k.g credits"       the title/credits tune, built into the game program (file kgs)
  intro.mod     "t.k.g loader final"  the intro tune (file intr on the intro disk), only with the intro disk
  sfx/NN.wav    the 25 sound effects of the file SFXS, at the rate the game plays each one (8 bit mono)
  sfx.txt       the game's effect table: offset, length, Paula period, channel mask, volume used by the game

How the data is stored (found by disassembling the game; see README.md):
  - Both disks are AmigaDOS OFS floppies. The program "kgs" is a hunk executable whose second hunk unpacks
    itself: a backward bit-stream packer (ByteKiller family) and then a run-length pass (escape byte $A5),
    to address $3300; it starts at $2CB00.
  - The data files start with "CRND" and are unpacked by the routine at $1DBDC of the game: another backward
    bit-stream packer with a 16-byte trailer (unpacked length, checksum, the stream's first 8 bytes).
  - MUSC and SFXS are both loaded to $2EA72: the original game plays either the music or the effects (a
    variable at $13FF2 says which), never both at once.
  - The effect table is at $12452: 10 bytes per effect (sample address.l, period.w, length in words.w,
    channel mask.w), read by the routine at $1A106 (volume in d1).
Only the Python standard library is needed.
"""
import os, struct, sys, wave

PAL_CLOCK = 3546895
GAME_BASE = 0x3300          # where the unpacked game program lives
SFX_LOAD = 0x2EA72          # where MUSC / SFXS are loaded
SFX_TABLE = 0x12452
SFX_COUNT = 25
CREDITS_MOD = 0x21852
# Volume (d1) the game passes to the effect routine, from its callers; every other effect is played at 64.
SFX_VOLUME = {21: 20}


# ---------------------------------------------------------------- AmigaDOS OFS/FFS floppy
def adf_files(path):
    d = open(path, "rb").read()
    ffs = d[3] & 1
    blk = lambda n: d[n * 512:(n + 1) * 512]
    L = lambda b, i: struct.unpack(">I", b[i:i + 4])[0]
    files = {}

    def read(hdr):
        b, size, data = blk(hdr), L(blk(hdr), 0x144), bytearray()
        while True:
            for i in range(L(b, 8)):
                db = blk(L(b, 0x134 - 4 * i))
                data += db if ffs else db[24:24 + L(db, 12)]
            if not L(b, 0x1f8):
                return bytes(data[:size])
            b = blk(L(b, 0x1f8))

    def walk(n, path):
        b = blk(n)
        for i in range(72):
            h = L(b, 24 + 4 * i)
            while h:
                e = blk(h)
                name = e[0x1b1:0x1b1 + e[0x1b0]].decode("latin1")
                if struct.unpack(">i", e[0x1fc:0x200])[0] == 2:
                    walk(h, path + name + "/")
                else:
                    files[(path + name).lower()] = read(h)
                h = L(e, 0x1f0)
    walk(880, "")
    return files


# ---------------------------------------------------------------- the self-unpacking game program
def unpack_program(exe):
    """The hunk 'kgs': hunk 1 begins with the unpacker; its parameters follow the code."""
    p, hunks = 0, []
    L = lambda: struct.unpack(">I", exe[p:p + 4])[0]
    assert L() == 0x3f3
    p = 8
    first, last = struct.unpack(">II", exe[p + 4:p + 12]); p += 12 + 4 * (last - first + 1)
    while p < len(exe):
        t = L() & 0x3fffffff; p += 4
        if t in (0x3e9, 0x3ea):
            n = L() * 4; hunks.append(exe[p + 4:p + 4 + n]); p += 4 + n
        elif t == 0x3ec:
            while True:
                n = L(); p += 4
                if not n: break
                p += 4 + 4 * n
        elif t == 0x3f2:
            pass
        else:
            raise ValueError("unknown hunk type %x" % t)
    h = hunks[-1]
    # code at 0..$1f1, then a0 (end of output), a1 (start of output), a2 (stream length), 5 longs of settings
    a0, a1, a2 = struct.unpack(">III", h[0x1f2:0x1fe])
    out = bytestream_back(h, 0x1fe + a2, a0 - a1)
    esc, dest, entry = struct.unpack(">HII", h[0x1fe:0x208])
    assert dest == GAME_BASE, hex(dest)
    return unrle(out, esc & 0xff), entry


def bytestream_back(src, end, outlen):
    """Port of the unpacker at the start of the program (bits MSB first, read backwards)."""
    out, p, a0 = bytearray(outlen), end, outlen
    M = 0xffffffff
    def rd():
        nonlocal p
        p -= 4
        return struct.unpack(">I", src[p:p + 4])[0]
    d3 = rd()
    def bit():
        nonlocal d3
        c = d3 >> 31; d3 = (d3 << 1) & M
        if d3 == 0:
            d3 = rd(); c = d3 >> 31; d3 = ((d3 << 1) | 1) & M
        return c
    def bits(n):
        v = 0
        for _ in range(n): v = (v << 1) | bit()
        return v
    def lit(n):
        nonlocal a0
        for _ in range(n):
            a0 -= 1; out[a0] = bits(8)
    while True:
        d1, match = bits(3), True
        if d1 < 6: lit(d1 + 1)
        elif d1 == 7:
            if not bit(): lit(bits(4) + 7)
            else:
                d1 = bits(10)
                if d1: lit(d1)
                else: lit(bits(16) + 1); match = False
        if a0 <= 0: break
        if not match: continue
        d1, d0, n = bits(2), 7, 2
        if d1 == 1:
            n = 3
            if bit(): d0 = 13
        elif d1 >= 2:
            if d1 == 3:
                d1 = bits(2)
                n = (d1 + 4 if d1 < 2 else bits(8) + 10 if d1 == 3 else bits(2) + 6) + 1
            else:
                n = 4
            d0 = 15
            if not bit():
                d0 = 11
                if not bit(): d0 = 7
        off = bits(d0 + 1) + 1
        for _ in range(n):
            a0 -= 1; out[a0] = out[a0 + off]
        if a0 <= 0: break
    return bytes(out)


def unrle(data, esc):
    o, i = bytearray(), 0
    while True:
        b = data[i]; i += 1; o.append(b)
        if b != esc: continue
        n = data[i]; i += 1
        if n == 0: continue          # an escaped escape byte
        o.pop()
        if n == 1: return bytes(o)   # end marker
        o += bytes([data[i]]) * (n + 1); i += 1


# ---------------------------------------------------------------- CRND data files
def uncrnd(f):
    """Port of the game's routine at $1DBDC (bits LSB first, read backwards, checksum by XOR)."""
    assert f[:4] == b"CRND", "not a CRND file"
    t = 4 + struct.unpack(">I", f[4:8])[0]
    outlen, d5 = struct.unpack(">II", f[t:t + 8])
    f = f[t + 8:t + 16] + f[8:]      # the header overwrote the stream's first 8 bytes: the trailer keeps them
    out, p, a2 = bytearray(outlen), t, outlen
    def rd():
        nonlocal p, d5
        p -= 4; v = struct.unpack(">I", f[p:p + 4])[0]; d5 ^= v
        return v
    d0 = rd()
    def bit():
        nonlocal d0
        c = d0 & 1; d0 >>= 1
        if d0 == 0:
            d0 = rd(); c = d0 & 1; d0 = (d0 >> 1) | 0x80000000
        return c
    def bits(n):
        v = 0
        for _ in range(n): v = (v << 1) | bit()
        return v
    while a2 > 0:
        if bit():
            d2 = bits(2)
            if d2 < 2: n, ob = d2 + 3, 9 + d2
            elif d2 == 2: n, ob = bits(8) + 1, 12
            else: n, ob = bits(8) + 9, 0
        elif bit(): n, ob = 2, 8
        else: n, ob = bits(3) + 1, 0
        if not ob:
            for _ in range(n): a2 -= 1; out[a2] = bits(8)
        else:
            off = bits(ob)
            for _ in range(n): a2 -= 1; out[a2] = out[a2 + off]
    if d5 != 0:
        raise ValueError("CRND checksum mismatch")
    return bytes(out)


# ---------------------------------------------------------------- ProTracker modules
def mod_size(d, base=0):
    assert d[base + 1080:base + 1084] == b"M.K.", "no M.K. module at %x" % base
    npat = max(d[base + 952:base + 1080]) + 1
    smp = sum(struct.unpack(">H", d[base + 42 + 30 * i:base + 44 + 30 * i])[0] * 2 for i in range(31))
    return 1084 + npat * 1024 + smp


def cut_mod(d, base):
    return d[base:base + mod_size(d, base)]


def write_wav(path, data, rate):
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(1); w.setframerate(rate)
        w.writeframes(bytes((b + 128) & 255 for b in data))      # WAV 8 bit is unsigned


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    out = args[-1] if len(args) > 1 and not args[-1].lower().endswith(".adf") else "original"
    disks = [a for a in args if a.lower().endswith(".adf")]
    os.makedirs(os.path.join(out, "sfx"), exist_ok=True)
    files = {}
    for d in disks:
        files.update(adf_files(d))
    if "kgs" not in files or "musc" not in files or "sfxs" not in files:
        sys.exit("the first disk must be the game disk (disk 2: files kgs, musc, sfxs)")

    game, entry = unpack_program(files["kgs"])
    print("kgs: unpacked %d bytes to $%x, entry $%x" % (len(game), GAME_BASE, entry))
    mods = {"ingame": uncrnd(files["musc"]), "credits": cut_mod(game, CREDITS_MOD - GAME_BASE)}
    if "intr" in files:
        intr = uncrnd(files["intr"])
        mods["intro"] = cut_mod(intr, intr.find(b"t.k.g loader final"))
    for name, m in mods.items():
        m = cut_mod(m, 0)
        open(os.path.join(out, name + ".mod"), "wb").write(m)
        print("%s.mod: \"%s\", %d bytes" % (name, m[:20].rstrip(b"\0").decode("latin1"), len(m)))

    bank = uncrnd(files["sfxs"])
    lines = ["# The Killing Game Show sound effects: the game's table at $%X (bank SFXS loaded to $%X)." % (SFX_TABLE, SFX_LOAD),
             "# channels: the Paula channels the game may use for it (0123); force: it takes a channel another effect holds",
             "# num  offset  bytes  period  rate_hz  channels  force  volume"]
    for i in range(SFX_COUNT):
        a = SFX_TABLE - GAME_BASE + 10 * i
        ptr, period, words, mask = struct.unpack(">IHHH", game[a:a + 10])
        off, n = ptr - SFX_LOAD, words * 2
        assert 0 <= off and off + n <= len(bank), "effect %d is outside the bank" % i
        rate = round(PAL_CLOCK / period)
        write_wav(os.path.join(out, "sfx", "%02d.wav" % i), bank[off:off + n], rate)
        lines.append("%4d  %6d  %5d  %6d  %7d      %s  %5s  %6d" % (i, off, n, period, rate, format(mask & 15, "04b")[::-1],
                                                                  "yes" if mask & 16 else "no", SFX_VOLUME.get(i, 64)))
    open(os.path.join(out, "sfx.txt"), "w").write("\n".join(lines) + "\n")
    print("sfx: %d effects, %d bytes of samples -> %s/sfx/*.wav, %s/sfx.txt" % (SFX_COUNT, len(bank), out, out))


if __name__ == "__main__":
    main()
