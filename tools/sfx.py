#!/usr/bin/env python3
"""sfx.py - turn sound effects into samples the effects mixer can play.

  tools/sfx.py [--period 322] [--voices 4 | --hq] [--gain 1.0] <out-dir> <in.wav | in.raw>[@gain] ...

- WAV input (8 or 16 bit, mono or stereo, any rate) is resampled to the mixer's rate, 3546895 / period Hz on a
  PAL machine (period 322 = 11015 Hz). Raw input is taken as signed 8-bit at the mixer's rate already.
- Output: <out-dir>/<name>.raw, signed 8-bit, padded with silence to a multiple of 4 bytes.
- Standard mixer (MIXER_HQ_MODE 0): every sample is divided by --voices (mixer_sw_channels), so that the voices
  can be added without overflow. The effect keeps 8 - log2(voices) bits: 6 bits for 4 voices.
- High quality mixer (MIXER_HQ_MODE 1): pass --hq. Samples keep all 8 bits; the mixer clips the sum.
- A file name may end in @<gain>: that file's own loudness on top of --gain (e.g. the volume a game played it at,
  "fx.wav@0.3125" for volume 20 of 64).
Prints the peak and the bits kept for every file: a peak far under 127 (or 127/voices) wastes resolution, so
normalise the source or raise --gain.
"""
import argparse, math, os, struct, sys, wave

PAL_CLOCK = 3546895

def read_wav(path):
    with wave.open(path, "rb") as w:
        ch, width, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if width == 1:
        vals = [(b - 128) / 128.0 for b in raw]
    elif width == 2:
        vals = [v / 32768.0 for v in struct.unpack("<%dh" % (len(raw) // 2), raw)]
    else:
        sys.exit("%s: only 8 and 16 bit WAV files are read" % path)
    if ch > 1:
        vals = [sum(vals[i:i + ch]) / ch for i in range(0, len(vals) - ch + 1, ch)]
    return vals, rate

def resample(vals, src, dst):
    """Windowed-sinc resampling; the low-pass sits at the lower of the two Nyquist rates."""
    if abs(src - dst) <= dst / 1000 or not vals:     # equal but for the rounding of a WAV header's integer rate:
        return list(vals)                           # pass the samples through untouched
    ratio = dst / src
    cutoff = min(1.0, ratio)
    half = 16
    span = half / cutoff
    out = []
    for i in range(int(len(vals) * ratio)):
        centre = i / ratio
        lo, hi = int(math.floor(centre - span)) + 1, int(math.floor(centre + span))
        acc = 0.0
        for j in range(max(lo, 0), min(hi, len(vals) - 1) + 1):
            x = (j - centre) * cutoff
            if abs(x) < 1e-9:
                s = 1.0
            else:
                s = math.sin(math.pi * x) / (math.pi * x)
            acc += vals[j] * s * (0.5 + 0.5 * math.cos(math.pi * (j - centre) / span)) * cutoff
        out.append(acc)
    return out

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--period", type=int, default=322)
    ap.add_argument("--voices", type=int, default=4)
    ap.add_argument("--hq", action="store_true")
    ap.add_argument("--gain", type=float, default=1.0)
    ap.add_argument("outdir")
    ap.add_argument("files", nargs="+")
    a = ap.parse_args()
    rate = PAL_CLOCK / a.period
    limit = 127 if a.hq else 127 // a.voices
    scale = 128 if a.hq else limit                  # HQ: an 8-bit source keeps its exact values (only -128 clips)
    os.makedirs(a.outdir, exist_ok=True)
    for path in a.files:
        own = 1.0
        if "@" in os.path.basename(path):
            path, g = path.rsplit("@", 1)
            own = float(g)
        name = os.path.splitext(os.path.basename(path))[0]
        if path.lower().endswith(".wav"):
            vals, src = read_wav(path)
            vals = resample(vals, src, rate)
        else:
            vals = [(b - 256 if b > 127 else b) / 128.0 for b in open(path, "rb").read()]
        clipped = 0
        out = bytearray()
        for v in vals:
            q = int(round(v * a.gain * own * scale))
            if q > limit or q < -limit:
                clipped += q != -128 or not a.hq      # -128 -> -127 is no real clipping
                q = max(-limit, min(limit, q))
            out.append(q & 255)
        while len(out) % 4:
            out.append(0)
        peak = max((abs(b - 256 if b > 127 else b) for b in out), default=0)
        bits = math.log2(2 * peak + 1) if peak else 0
        open(os.path.join(a.outdir, name + ".raw"), "wb").write(out)
        print("%s.raw: %d bytes, %.2f s at %d Hz, peak %d of %d, %.1f bits used%s"
              % (name, len(out), len(out) / rate, round(rate), peak, limit, bits,
                 ", %d samples clipped: lower --gain" % clipped if clipped else ""))

if __name__ == "__main__":
    main()
