# The Killing Game Show: music and sound effects together

In *The Killing Game Show* (Psygnosis 1990; music and effects by Ray Norrish and Tim Wright) the in-game tune
and the sound effects share one memory buffer and one switch: you hear **either** the music **or** the effects.
This project plays both at once on a stock Amiga 500. It is built on the
[musicfx](https://github.com/johnshawler/musicfx) template:

- the in-game tune plays on Paula channels 0–2 (ptplayer), folded down from four channels;
- the credits and intro tunes are left exactly as the game has them: the game plays no effects over them, so
  they keep all four channels (the credits tune plays that way here, without the mixer);
- all 25 of the game's effects are mixed into channel 3 by the Audio Mixer, up to four at once;
- so the music never loses a voice to an effect and the effects never cut each other until five sound at once.

| Referee (`tools/check.sh`)                    | audio CPU, every voice busy | result |
|-----------------------------------------------|-----------------------------|--------|
| Amiga 500, 68000 7 MHz, AROS ROM (the gate)   | 10.6 % (limit 15 %)         | PASS   |
| Amiga 1200, 68020 14 MHz, Kickstart 3.1 40.68 | 5.6 %                       | PASS   |

## Quick start

```bash
tools/setup.sh
```

```bash
tools/kgs_assets.sh "<game disk, disk 2>.adf" "<intro disk, disk 1>.adf"
```

```bash
tools/check.sh
```

```bash
tools/record.sh recordings/kgs_a500.wav script
```

The game's audio is **not** in this repository; it is public, and the music and samples are Psygnosis'.
`tools/kgs_assets.sh` makes `original/` and `assets/` from your own disk images (any of the TOSEC game-disk
dumps gives byte-identical data; the intro disk is only needed for the intro tune). Only Python 3 is needed
for that step. `tools/setup.sh` installs the emulator and the cross compiler, as in the template.

On an A1200 with a real Kickstart (never committed):

```bash
UAE_MODEL=A1200 UAE_KICKSTART=/path/to/kick31.rom tools/check.sh
```

## The program: `dist/kgs_mfx`

| Command          | What it does |
|------------------|--------------|
| `kgs_mfx`        | Play. Keys **1–0**, **Q–P**, **A–G** fire effects 0–24 over the in-game tune. **Space** switches to the credits tune (the game's own, four channels, no effects) and back, **M** turns the music on or off, **Esc** or the left mouse button quits. The border goes greener with each sounding voice and flashes red when an effect is refused (always during the credits tune). |
| `kgs_mfx script` | The fixed 64 s sequence for recordings: 10 s of the tune alone, every effect alone, then 30 s of the tune with effects fired as in play. |
| `kgs_mfx ab`     | What the fold costs: 30 s of the game's own four-channel tune, then the same 30 s folded into three (no mixer). |
| `kgs_mfx bench`  | Measures for `tools/check.sh` (writes `bench.txt`). |

`tools/record.sh` records the emulator's output (48 kHz stereo). `recordings/` is where the WAV/FLAC files go;
they are not committed either.

## What was found on the disks

Everything here was found by disassembling the game; `tools/kgs_extract.py` documents it again in code.

| Disk file             | What it is |
|-----------------------|------------|
| `kgs` (game disk)     | The game program. Its second hunk unpacks itself: a backward bit-stream packer of the ByteKiller family, then a run-length pass (escape byte `$A5`), to address `$3300`; it starts at `$2CB00`. The 170 KB image holds the credits tune and the effect table. |
| `musc` (game disk)    | "t.k.g ingame4", the in-game tune: a 4-channel ProTracker module, 36 patterns, 91,024 bytes. |
| `sfxs` (game disk)    | The 25 effects: 91,144 bytes of headerless signed 8-bit samples. |
| `intr` (intro disk)   | The intro, with "t.k.g loader final", a 4-channel module of 248,994 bytes. |
| `lvl1`–`lvl8`, `endm` | Levels and the end sequence (not used here). |

- `musc`, `sfxs`, `intr`, `prt1`, `prt2` and `endm` start with `CRND`. They are unpacked by the game's routine at
  `$1DBDC`: another backward bit-stream packer, read LSB first, with an XOR checksum. A 16-byte trailer holds
  the unpacked length, the checksum and the stream's first 8 bytes (the header overwrites them).
- The game loads `MUSC` **and** `SFXS` to the same address, `$2EA72`. A word at `$13FF2` says which one is there
  (1 = music, 2 = effects), so the original can never play both at once.
- The credits tune, "t.k.g credits" (27 patterns, 37,740 bytes), is inside the game program at `$21852`.
- The effect table is at `$12452`, 10 bytes per effect: sample address (long), Paula period, length in words,
  and a channel mask whose bit 4 means "take a channel even if another effect holds it". The routine at
  `$1A106` plays an effect with the volume in `d1`: 64 for every effect except number 21, which gets 20.
  `original/sfx.txt` lists all 25. Twenty play at period 428 (8,287 Hz); the others at 1016, 720, 604 and 856.

## How the music and effects were prepared

**Effects.** `tools/kgs_extract.py` writes each effect as an 8-bit WAV at its own rate. The mixer runs at
period 428 (8,287 Hz, `PERIOD=428` in `musicfx.conf`), the rate of 20 of the 25 effects, so those reach the
mixer **bit for bit** (only −128 becomes −127). The other five are resampled by `tools/sfx.py`. HQ mode keeps
all 8 bits. Effect 21 gets the game's volume of 20/64 through a per-file gain (`21.wav@0.3125`). Priorities
follow the game's force bit: forced effects get priority 2, the rest 1.

**Music: four channels into three, for the in-game tune only.** The template gives the music three channels
while the effects play, and the in-game tune uses four throughout. The credits and intro tunes are not
touched: the game plays no effects over them. `tools/mod3.py` folds a module:

1. It plays the song once, following speed, tempo, pattern breaks and jumps, and the volume commands. For
   every row and channel it works out how loud the channel is: the RMS of the stretch of sample played,
   times the volume. That way a decaying one-shot tail does not count as "sounding".
2. Each note of the emptied channel moves to another channel where it costs least. Ideally that channel is
   silent and empty, and nothing is lost. Otherwise the move cuts a tail, the moved note is shortened, or the
   note is dropped if that loses less. Commands that belong to a moved note go with it.
3. Speed, tempo, break and jump commands move to a free command slot in the same row. None is lost in the
   folds chosen here.
4. Notes that relied on "the channel's previous sample" get the sample written in, with a C command where
   the volume would otherwise change.

It tries emptying each of the four channels and keeps the cheapest (`assets/music/fold.txt`). For the
in-game tune that is channel 3: 11.9 % of the sound (loudness-weighted, as played) is lost and 475 notes are
dropped. Emptying channel 2 would drop fewer notes (346) but lose more sound (13.0 %).

The in-game tune is genuinely four-voice almost everywhere. Its fourth channel is the quietest (an
accompaniment line), so most of its notes cannot find room and are dropped. The A/B recording measures the
loss on the emulated A500: aligned second by second, 15.9 % of the original's energy is missing or different
in the 3-channel version (about 3 % of that is alignment noise). The opening patterns lean on channel 0 in
the original too, which is why the first seconds sound left-heavy.

## Lessons (worth carrying back to the template)

- **ptplayer starts with every channel muted.** Its per-channel enable flags are only set by
  `mt_channelmask()`. `kit/mfx.c` always calls it; any other caller of `mt_init()` must too, or it gets silence.
- **`mt_Enable` must be `volatile`.** With `mt_Enable = 1; wait(); mt_Enable = 0;` GCC -O2 drops the first
  store, because nothing in C reads it; only the music interrupt does. The music never starts. Fixed in
  `kit/mfx.c` as well, where it had only worked because the store ended a function.
- **`tools/sfx.py` changed sound it should have passed through.** A WAV stores its rate as a whole number
  (8287), the mixer runs at 8287.13 Hz, and the 1.000016 resampling blurred transients by up to 15 steps.
  Rates within 0.1 % now pass through. HQ mode scaled by 127/128, so loud samples lost a step; it now scales
  by 128.
- The A500 with FS-UAE's AROS ROM takes about 15–20 s to boot before the program starts.

## Files

| Path                              | |
|-----------------------------------|-|
| `src/kgs_mfx.c`                   | the program |
| `tools/kgs_extract.py`            | disk images → `original/`: OFS reader and both unpackers, the modules, the effects, `sfx.txt` |
| `tools/mod3.py`                   | folds a 4-channel module into channels 0–2 and reports what it lost |
| `tools/kgs_assets.sh`             | extract, then fold the in-game tune into `assets/music/ingame3.mod` |
| `original/sfx.txt`                | the game's effect table |
| `assets/music/fold.txt`           | what the fold lost |
| `kit/`, `tools/` (others), `examples/` | the musicfx template. `examples/` keeps `mixer_config.i`. The template's `demo_mfx.c` needs its own `assets/fx`, which this project's effects replace. |
