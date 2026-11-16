/* kgs_mfx.c - The Killing Game Show's music and sound effects playing together on a stock Amiga 500.
 *
 * In the original game the in-game tune and the effects share one buffer and one switch: you hear either the
 * music or the effects, never both. Here the tune plays on Paula channels 0-2 (folded from four channels by
 * tools/mod3.py) and all 25 of the game's effects are mixed into channel 3 by the Audio Mixer, up to four at
 * once, so neither cuts into the other (kit/mfx.h).
 *
 *   kgs_mfx              play: keys 1-0, Q-P and A-G fire effects 0-24, Space switches between the in-game
 *                        and the credits tune, M music on/off, Esc or the left mouse button quits.
 *                        The border flashes red when an effect is refused, and is greener the more voices sound.
 *   kgs_mfx bench [N]    measure and quit: writes bench.txt (N frames per phase, default 150) for tools/check.sh
 *   kgs_mfx script       the fixed sequence used for recordings (tools/record.sh), about 65 seconds:
 *                        the tune alone, every effect alone, then the tune with effects fired as in play
 *   kgs_mfx ab           what the fold costs: the first 30 s of the original four-channel in-game tune, then
 *                        the same 30 s of the three-channel one (both without the mixer), for tools/record.sh
 *
 * Build: tools/check.sh (see musicfx.conf). The assets come from tools/kgs_assets.sh.
 */
#include <proto/dos.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sys.h"
#include "mfx.h"
#include "incbin.h"

/* Modules in chip RAM (Paula plays them from there); effects anywhere (the mixer copies them as it mixes). */
CHIP_INCBIN(mod_ingame,  "assets/music/ingame3.mod");     /* "t.k.g ingame4", channel 3 empty */
CHIP_INCBIN(mod_credits, "assets/music/credits3.mod");    /* "t.k.g credits", channel 3 empty */
CHIP_INCBIN(mod_ingame4, "original/ingame.mod");          /* the game's own four-channel tune, for "ab" */

/* ptplayer, for "ab" only: the four-channel tune plays without the mixer (kit/mfx.c drives it otherwise). */
void mt_install(void *custom_base __asm("a6"), void *vbr __asm("a0"), UBYTE pal __asm("d0"));
void mt_remove(void *custom_base __asm("a6"));
void mt_init(void *custom_base __asm("a6"), void *module __asm("a0"), void *samples __asm("a1"), UBYTE pos __asm("d0"));
void mt_end(void *custom_base __asm("a6"));
void mt_mastervol(void *custom_base __asm("a6"), UWORD volume __asm("d0"));
void mt_channelmask(void *custom_base __asm("a6"), UBYTE mask __asm("d0"));
extern volatile UBYTE mt_Enable;      /* volatile: the music interrupt reads it; without it GCC may drop a store */

#define FX(n) DATA_INCBIN(fx##n, "assets/fx/" #n ".raw")
FX(00); FX(01); FX(02); FX(03); FX(04); FX(05); FX(06); FX(07); FX(08); FX(09);
FX(10); FX(11); FX(12); FX(13); FX(14); FX(15); FX(16); FX(17); FX(18); FX(19);
FX(20); FX(21); FX(22); FX(23); FX(24);
#define NFX 25
static UBYTE *const fx_start[NFX] = { fx00, fx01, fx02, fx03, fx04, fx05, fx06, fx07, fx08, fx09, fx10, fx11, fx12,
                                      fx13, fx14, fx15, fx16, fx17, fx18, fx19, fx20, fx21, fx22, fx23, fx24 };
static UBYTE *const fx_end[NFX] = { fx00_end, fx01_end, fx02_end, fx03_end, fx04_end, fx05_end, fx06_end, fx07_end,
                                    fx08_end, fx09_end, fx10_end, fx11_end, fx12_end, fx13_end, fx14_end, fx15_end,
                                    fx16_end, fx17_end, fx18_end, fx19_end, fx20_end, fx21_end, fx22_end, fx23_end,
                                    fx24_end };
/* The game's "force" flag (bit 4 of the channel mask in its table, original/sfx.txt): such an effect takes its
   channel even when another effect holds it; the others wait for a free one. Here: priority 2 against 1. */
static const UBYTE fx_force[NFX] = { 1,1,1,1,0, 0,0,1,1,1, 1,1,0,0,0, 0,0,1,1,1, 1,0,0,0,1 };
static MfxSound fx[NFX];

/* Raw key codes of the keys that fire effects 0-24: 1..0, Q..P, A..G */
static const UBYTE fx_key[NFX] = { 0x01,0x02,0x03,0x04,0x05,0x06,0x07,0x08,0x09,0x0a,
                                   0x10,0x11,0x12,0x13,0x14,0x15,0x16,0x17,0x18,0x19,
                                   0x20,0x21,0x22,0x23,0x24 };
#define KEY_M 0x37

/* Phases of the bench: what happens once per frame. */
static void one_effect(void) { if (mfx_voices_busy() == 0) mfx_play(&fx[0]); }
static void all_voices(void) { int n = mfx_cfg_voices - mfx_voices_busy(); while (n-- > 0) mfx_play(&fx[24]); }

static ULONG run(ULONG frames, void (*each)(void))
{
    ULONG n = 0, f, end = sys_frame + frames;
    while ((f = sys_frame) < end) {
        if (each) each();
        while (sys_frame == f) n++;
    }
    return n;
}

#define CUSTOM_BASE ((void *)0xdff000)
static void play_alone(void *module, ULONG frames)
{
    mt_install(CUSTOM_BASE, sys_vbr, sys_is_pal);
    mt_init(CUSTOM_BASE, module, 0, 0);
    mt_channelmask(CUSTOM_BASE, 15);    /* needed: ptplayer starts with every channel muted until this is called */
    mt_mastervol(CUSTOM_BASE, 64);
    mt_Enable = 1;
    run(frames, 0);
    mt_Enable = 0;
    mt_end(CUSTOM_BASE);
    mt_remove(CUSTOM_BASE);
}

static ULONG pct10(ULONG base, ULONG n) { return n >= base ? 0 : (base - n) * 1000 / base; }
static ULONG frames_of(int i) { return fx[i].length / ((sys_is_pal ? 3546895UL : 3579545UL) / mfx_cfg_period / (sys_is_pal ? 50 : 60)) + 1; }

/* "Game play" for the script: a fixed pseudo-random stream of effects, now and then several at once. */
static ULONG seed = 12345;
static ULONG rnd(void) { seed = seed * 1103515245UL + 12345; return (seed >> 16) & 0x7fff; }
static void game_play(ULONG frames)
{
    ULONG end = sys_frame + frames, next = sys_frame;
    while (sys_frame < end) {
        if (sys_frame >= next) {
            int burst = (rnd() % 5 == 0) ? 3 : 1;
            while (burst--) mfx_play(&fx[rnd() % NFX]);
            next = sys_frame + 8 + rnd() % 30;
        }
        sys_wait_frame();
    }
}

int main(int argc, char **argv)
{
    int bench = argc > 1 && !strcmp(argv[1], "bench");
    int script = argc > 1 && !strcmp(argv[1], "script");
    int ab = argc > 1 && !strcmp(argv[1], "ab");
    ULONG frames = argc > 2 ? strtoul(argv[2], 0, 10) : 150;
    ULONG idle = 0, music = 0, one = 0, all = 0, one_n = 0, all_n = 0, over_played = 0, over_refused = 0;
    ULONG phase_refused = 0;
    char buf[400];
    int i, started = 0;

    for (i = 0; i < NFX; i++) {
        fx[i].data = fx_start[i];
        fx[i].length = fx_end[i] - fx_start[i];
        fx[i].priority = fx_force[i] ? 2 : 1;
    }
    if (!mfx_open() || !sys_take()) { sys_free_all(); return 20; }
    sys_vbl_hook = sys_key_poll;

    if (bench) {
        idle = run(frames, 0);                              /* nothing sounds */
        started = mfx_start(mod_ingame);
        music = run(frames, 0);                             /* the in-game tune on three channels */
        one = run(frames, one_effect);                      /* + one effect at a time */
        one_n = mfx_played;
        all = run(frames, all_voices);                      /* + every voice always busy: the worst case */
        all_n = mfx_played - one_n; phase_refused = mfx_refused;
        run(120, 0);                                        /* let them end */
        mfx_requested = mfx_played = mfx_refused = 0;
        for (i = 0; i < mfx_cfg_voices; i++) {              /* every voice important ... */
            MfxSound s = fx[24]; s.priority = 5; mfx_play(&s);
        }
        { MfxSound s = fx[0]; s.priority = 1; mfx_play(&s); }  /* ... and one more, unimportant: must be refused */
        over_played = mfx_played; over_refused = mfx_refused;
        run(120, 0);
    } else if (ab) {
        play_alone(mod_ingame4, 1500);                      /* 0-30 s: the original, four channels */
        run(50, 0);
        play_alone(mod_ingame, 1500);                       /* 31-61 s: the same music folded into three */
        run(25, 0);
        sys_give();
        sys_free_all();
        return 0;
    } else if (script) {
        started = mfx_start(mod_ingame);
        run(500, 0);                                        /* 0-10 s: the in-game tune alone */
        mfx_music(0);
        run(25, 0);
        for (i = 0; i < NFX; i++) { mfx_play(&fx[i]); run(frames_of(i) + 15, 0); }  /* every effect alone */
        mfx_music(1);
        game_play(1500);                                    /* 30 s of the tune with effects as in a game */
        run(100, 0);
    } else {
        UBYTE was[128];
        int music_on = 1, tune = 0;
        started = mfx_start(mod_ingame);
        memset(was, 0, sizeof was);
        while (!sys_keys[KEY_ESC] && !sys_mouse_button()) {
            UWORD colour = 0;
            for (i = 0; i < NFX; i++)
                if (sys_keys[fx_key[i]] && !was[fx_key[i]] && !mfx_play(&fx[i])) colour = 0xf00;
            if (sys_keys[KEY_M] && !was[KEY_M]) mfx_music(music_on ^= 1);
            if (sys_keys[KEY_SPACE] && !was[KEY_SPACE]) {
                mfx_stop();
                tune ^= 1;
                started = mfx_start(tune ? mod_credits : mod_ingame);
                music_on = 1;
            }
            for (i = 0; i < 128; i++) was[i] = sys_keys[i];
            custom->color[0] = colour ? colour : (UWORD)(mfx_voices_busy() * 0x020);
            sys_wait_frame();
        }
        custom->color[0] = 0;
    }

    mfx_stop();
    sys_give();
    sys_free_all();

    if (bench) {
        BPTR fh;
        ULONG m = pct10(idle, music), o = pct10(idle, one), f = pct10(idle, all);
        sprintf(buf, "frames=%lu idle=%lu music_pct=%lu.%lu one_fx_pct=%lu.%lu all_fx_pct=%lu.%lu one_fx_played=%lu all_fx_played=%lu refused=%lu "
                     "overload_played=%lu overload_refused=%lu voices=%u mixer_hz=%lu hq=%u pal=%u music_in_chip=%d\n",
                frames, idle, m / 10, m % 10, o / 10, o % 10, f / 10, f % 10, one_n, all_n, phase_refused, over_played, over_refused,
                mfx_cfg_voices, (sys_is_pal ? 3546895UL : 3579545UL) / mfx_cfg_period, mfx_cfg_hq, sys_is_pal, started);
        fh = Open((STRPTR)"bench.txt", MODE_NEWFILE);
        if (fh) { Write(fh, buf, strlen(buf)); Close(fh); }
    }
    return 0;
}
