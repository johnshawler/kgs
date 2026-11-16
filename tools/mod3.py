#!/usr/bin/env python3
"""mod3.py - fold a 4-channel ProTracker module into channels 0-2, so that channel 3 is free for the effects mixer.

  tools/mod3.py [--fold auto|0|1|2|3] <in.mod> <out.mod>

The template gives the music three Paula channels and the effects mixer the fourth (kit/mfx.h). A game module
written for four channels therefore has to lose one, and this tool keeps as much of it as it can:

 1. It plays the song through once (speed/tempo, pattern breaks and jumps, sample lengths and loops, the
    volume commands C and A) and works out, for every pattern row and channel, how loud the channel is: the
    RMS of the stretch of sample played in that row times the volume, so that the quiet tail of a one-shot
    sample does not count as sounding. A pattern used at several song positions takes its loudest use.
 2. Every note of the folded channel, with the rows it sounds for, is moved to one of the other channels:
      - into a channel that is silent and empty for all those rows: nothing is lost;
      - otherwise into the channel where it costs least: the tail of a note that is still ringing there is cut,
        or the moved note is shortened by the next note of that channel. Cost = the loudness lost;
      - or it is dropped, when that loses less (a note that would cut a longer one).
    Effect commands that belong to a moved note go with it; commands of the notes it cuts are removed.
 3. Song commands (F speed/tempo, B jump, D break, E6 loop, EE delay) of the folded channel move to a free
    command slot in the same row; if a row has none, the command is lost and counted (auto avoids that).
 4. Notes that relied on the channel's previous sample (no sample number) get it written in, with a C command
    where the volume would otherwise change, because moving notes between channels changes "the previous
    sample" of a channel.
--fold picks the channel that is emptied; auto (the default) tries all four and keeps the one that loses least.
The channels that remain keep their order and end up as channels 0, 1, 2; channel 3 is left empty.
Prints what was lost, counted as the song plays (a pattern played twice counts twice), in rows at full
loudness. Only the Python standard library is needed.
"""
import argparse, copy, struct, sys

PAL_CLOCK = 3546895
GLOBAL = {0xB, 0xD, 0xF}                      # song commands; E6x and EEx are handled as well


class Mod:
    def __init__(self, data):
        if data[1080:1084] not in (b"M.K.", b"M!K!", b"FLT4", b"4CHN"):
            sys.exit("not a 4-channel ProTracker module")
        self.head = bytearray(data[:1084])
        self.songlen = data[950]
        self.order = list(data[952:952 + 128])
        self.npat = max(self.order) + 1
        self.samples = []
        for i in range(31):
            o = 20 + 30 * i
            ln, ft, vol, rs, rl = struct.unpack(">HBBHH", data[o + 22:o + 30])
            self.samples.append(dict(len=ln * 2, vol=min(vol, 64), rs=rs * 2, rl=rl * 2))
        self.pat = []
        for p in range(self.npat):
            rows = []
            for r in range(64):
                row = []
                for c in range(4):
                    x = data[1084 + p * 1024 + r * 16 + c * 4:][:4]
                    row.append([(x[0] & 0xf0) | (x[2] >> 4), ((x[0] & 15) << 8) | x[1], x[2] & 15, x[3]])
                rows.append(row)
            self.pat.append(rows)
        self.sampledata = data[1084 + self.npat * 1024:]

    def bytes(self):
        out = bytearray(self.head)
        for rows in self.pat:
            for row in rows:
                for s, per, e, prm in row:
                    out += bytes([(s & 0xf0) | (per >> 8), per & 255, ((s & 15) << 4) | e, prm])
        return bytes(out + self.sampledata)


def is_global(cell):
    e, prm = cell[2], cell[3]
    return e in GLOBAL or (e == 0xE and prm >> 4 in (6, 0xE))


def has_cmd(cell):
    return cell[2] != 0 or cell[3] != 0


def empty(cell):
    return cell[0] == 0 and cell[1] == 0 and not has_cmd(cell)


AUDIBLE = 0.02                                # a row is "sounding" above this loudness (about -34 dB)


def loudness_tables(mod):
    """Per sample: prefix sums of squared sample values, to get the RMS of any stretch quickly."""
    tabs, off = [], 0
    for sm in mod.samples:
        d = mod.sampledata[off:off + sm["len"]]
        off += sm["len"]
        acc, pre = 0, [0]
        for v in d:
            v = v - 256 if v > 127 else v
            acc += v * v; pre.append(acc)
        tabs.append(pre)
    return tabs


def segment_level(sm, pre, a, b):
    """RMS (0..1) of sample positions a..b (floats), following the loop or the end of a one-shot sample."""
    n = sm["len"]
    if n < 2:
        return 0.0
    looped = sm["rl"] > 2
    def norm(x):
        if x < n: return x
        if not looped: return None
        return sm["rs"] + (x - sm["rs"]) % sm["rl"]
    pts, x = [], a
    step = max(1.0, (b - a) / 8)
    while x < b:
        y = norm(int(x))
        if y is None: break
        pts.append(y); x += step
    if not pts:
        return 0.0
    lo, hi = min(pts), max(pts) + 1
    hi = min(hi, n)
    if hi <= lo:
        return 0.0
    return ((pre[hi] - pre[lo]) / (hi - lo)) ** 0.5 / 128.0


def play(mod):
    """Play the song once. Returns level[p][r][c]: how loud the channel is in that row (0..1: sample RMS times
    volume, the loudest of the pattern's uses), the list of (position, pattern) in the order played, and per
    (p, r, c) the volume a note without a sample number starts with (for step 4)."""
    tabs = loudness_tables(mod)
    level = [[[0.0] * 4 for _ in range(64)] for _ in range(mod.npat)]
    novol = {}
    speed, bpm, t = 6, 125, 0.0
    ch = [dict(smp=0, vol=0, per=0, t0=0.0, on=False) for _ in range(4)]
    seen, pos, row, played = set(), 0, 0, []
    while pos < mod.songlen:
        if (pos, row) in seen:
            break
        seen.add((pos, row))
        p = mod.order[pos]
        if not played or played[-1] != (pos, p):
            played.append((pos, p))
        cells = mod.pat[p][row]
        jump = None
        for c in range(4):
            e, prm = cells[c][2], cells[c][3]
            if e == 0xF and prm:
                if prm < 32: speed = prm
                else: bpm = prm
        rowlen = 2.5 / bpm * speed
        for c in range(4):
            s, per, e, prm = cells[c]
            st = ch[c]
            if per and not s:
                novol[(p, row, c)] = st["vol"]
            if s:
                st["smp"] = s
                st["vol"] = mod.samples[s - 1]["vol"]
            if per and st["smp"]:
                st["per"], st["t0"], st["on"] = per, t, True
            if e == 0xC:
                st["vol"] = min(prm, 64)
            vol0 = st["vol"]
            if e == 0xA:
                d = (prm >> 4) or -(prm & 15)
                st["vol"] = max(0, min(64, st["vol"] + d * (speed - 1)))
            if e == 0xE and prm >> 4 == 0xC and (prm & 15) < speed:
                st["vol"] = 0
            if st["on"] and st["per"]:
                rate = PAL_CLOCK / st["per"]
                sm = mod.samples[st["smp"] - 1]
                x0 = (t - st["t0"]) * rate
                lv = segment_level(sm, tabs[st["smp"] - 1], x0, x0 + rowlen * rate) * (vol0 + st["vol"]) / 128
                level[p][row][c] = max(level[p][row][c], lv)
        for c in range(4):
            e, prm = cells[c][2], cells[c][3]
            if e == 0xB: jump = (prm, 0)
            elif e == 0xD: jump = ((jump[0] if jump else pos + 1), (prm >> 4) * 10 + (prm & 15))
        t += rowlen
        if jump:
            pos, row = jump
            if row > 63: row = 0
        else:
            row += 1
            if row == 64: pos, row = pos + 1, 0
    return level, played, novol


def normalise(mod, novol):
    """Step 4: give every note a sample number. Returns the number of notes whose volume could not be kept."""
    lost = 0
    for p in range(mod.npat):
        for c in range(4):
            last = 0
            for r in range(64):
                cell = mod.pat[p][r][c]
                if cell[0]:
                    last = cell[0]
                elif cell[1]:
                    s = last or incoming_sample(mod, p, c)
                    if not s:
                        continue
                    cell[0] = s
                    v = novol.get((p, r, c))
                    if v is not None and v != mod.samples[s - 1]["vol"]:
                        if not has_cmd(cell): cell[2], cell[3] = 0xC, v
                        else: lost += 1
    return lost


def incoming_sample(mod, p, c):
    """The sample a channel holds when pattern p starts (its first use in the song)."""
    seq = [mod.order[i] for i in range(mod.songlen)]
    if p not in seq:
        return 0
    i = seq.index(p)
    for q in reversed(seq[:i]):
        for r in range(63, -1, -1):
            if mod.pat[q][r][c][0]:
                return mod.pat[q][r][c][0]
    return 0


def fold(mod, busy, k, uses):
    """Move channel k into the others (changes mod and busy). Returns (rows of sound lost, notes dropped,
    notes shortened or cutting another, song commands lost)."""
    others = [c for c in range(4) if c != k]
    side = {0: 0, 3: 0, 1: 1, 2: 1}     # Paula plays channels 0 and 3 on the left, 1 and 2 on the right
    lost_rows = dropped = touched = cmd_lost = 0
    for p in range(mod.npat):
        P, B, w = mod.pat[p], busy[p], uses[p]
        notes = [r for r in range(64) if P[r][k][1]]
        for i, a in enumerate(notes):
            nxt = notes[i + 1] if i + 1 < len(notes) else 64
            span = [r for r in range(a, nxt) if B[r][k] > AUDIBLE]
            b = span[-1] if span else a                 # the last row it sounds in (within this pattern)
            best = None
            for c in sorted(others, key=lambda c: (side[c] != side[k], c)):   # same side of the stereo first
                start = P[a][c]
                if start[0] or start[1] or (has_cmd(start) and (has_cmd(P[a][k]) or not is_global(start))):
                    continue                            # a note there, or its command slot is taken
                cnext = next((r for r in range(a + 1, 64) if P[r][c][1]), 64)
                keep_to = min(b, cnext - 1)              # the moved note is cut by c's next note
                cost = sum(B[r][k] for r in range(keep_to + 1, b + 1))
                cut = sum(B[r][c] for r in range(a, cnext))        # c's sound up to its next note is lost
                cost += cut
                for r in range(a + 1, keep_to + 1):
                    src, dst = P[r][k], P[r][c]
                    if has_cmd(src) and not is_global(src) and is_global(dst):
                        cost += 0.25                    # the moved note loses a command
                    elif has_cmd(dst) and not is_global(dst) and B[r][c] <= AUDIBLE:
                        cost += 0.25                    # a command of c that would act on the moved note
                if best is None or cost < best[0]:
                    best = (cost, c, keep_to, cnext, cut)
            mine = sum(B[r][k] for r in range(a, b + 1))
            if best is None or best[0] >= mine:
                lost_rows += mine * w; dropped += w
                continue
            cost, c, keep_to, cnext, cut = best
            lost_rows += cost * w
            touched += (cost > AUDIBLE) * w
            for r in range(a, keep_to + 1):
                src, dst = P[r][k], P[r][c]
                if r == a:
                    dst[0], dst[1] = src[0], src[1]
                if has_cmd(src) and not is_global(src):
                    if not is_global(dst): dst[2], dst[3] = src[2], src[3]
                elif has_cmd(dst) and not is_global(dst):
                    dst[2] = dst[3] = 0                 # a command of the sound that was cut, or of silence
                B[r][c] = B[r][k]
            for r in range(keep_to + 1, cnext):
                if cut > AUDIBLE and has_cmd(P[r][c]) and not is_global(P[r][c]):
                    P[r][c][2] = P[r][c][3] = 0         # commands of the note that was cut
                B[r][c] = 0.0
        for r in range(64):                             # song commands: to a free command slot in the same row
            src = P[r][k]
            if not is_global(src):
                continue
            slot = next((c for c in others if not has_cmd(P[r][c])), None)
            if slot is None: cmd_lost += w
            else: P[r][slot][2], P[r][slot][3] = src[2], src[3]
        for r in range(64):
            P[r][k] = [0, 0, 0, 0]
            B[r][k] = 0.0
            if k != 3:                                  # the remaining channels become 0, 1, 2
                P[r] = [P[r][c] for c in others] + [[0, 0, 0, 0]]
                B[r] = [B[r][c] for c in others] + [0.0]
    return lost_rows, dropped, touched, cmd_lost


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fold", default="auto")
    ap.add_argument("inp"); ap.add_argument("out")
    a = ap.parse_args()
    data = open(a.inp, "rb").read()
    mod = Mod(data)
    busy, played, novol = play(mod)
    total = [round(sum(busy[p][r][c] for _, p in played for r in range(64))) for c in range(4)]
    vol_lost = normalise(mod, novol)
    busy, played, novol = play(mod)
    cands = range(4) if a.fold == "auto" else [int(a.fold)]
    uses = [sum(1 for _, q in played if q == p) for p in range(mod.npat)]
    results = {}
    for k in cands:
        results[k] = fold(Mod(mod.bytes()), copy.deepcopy(busy), k, uses)
    k = min(results, key=lambda k: (results[k][3], results[k][0]))
    fold(mod, busy, k, uses)
    out = mod.bytes()
    open(a.out, "wb").write(out)
    print("%s: %d patterns, %d positions played; sound per channel %s (in rows at full loudness)"
          % (a.inp, mod.npat, len(played), total))
    for c in sorted(results):
        lr, dr, tc, cl = results[c]
        print("  fold channel %d: %.0f lost (%.1f%% of the sound), %d notes dropped, %d notes shortened or cutting another, "
              "%d song commands lost%s" % (c, lr, 100.0 * lr / max(1, sum(total)), dr, tc, cl, "   <- chosen" if c == k else ""))
    if vol_lost:
        print("  warning: %d notes without a sample number have a command already; their volume may change" % vol_lost)
    check = Mod(out)
    assert all(check.pat[p][r][3] == [0, 0, 0, 0] for p in range(check.npat) for r in range(64))
    print("  -> %s: channel 3 empty, %d bytes" % (a.out, len(out)))


if __name__ == "__main__":
    main()
