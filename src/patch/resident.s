; resident.s - The Killing Game Show, patched to play the in-game music and the sound effects together.
;
; Assembled to run at $80000 (vasm: -Fbin). tools/kgs_patch.py puts it in the game program's first hunk and
; src/patch/wrapper.s copies it here before the game starts, so it needs 1 MB of chip RAM or more (A1200,
; A500+, A600, A3000, A4000). The game itself was written for 512K and never touches memory above $80000.
;
; How the original works (addresses in the unpacked game, see README.md):
;   - MUSC and SFXS are both loaded to $2EA72 and $13FF2 says which one is there (1 music, 2 effects).
;   - The ProTracker replay at $14D18 drives all four Paula channels from the frame interrupt.
;   - The effect routine at $1A106 plays one of 25 effects on a channel chosen from its table at $12452 (only
;     in effects mode); the frame interrupt at $14ADA turns its DMA and audio interrupt on, and the audio
;     interrupt at $149D6 turns them off when the sample has played.
; What this patch changes:
;   - SFXS is loaded to $80400 as well, so both files are in memory; the game stays in music mode.
;   - The effect routine and the frame interrupt no longer ask which mode the game is in.
;   - Every effect plays on Paula channel 3 (its table's "force" bit is kept).
;   - While an effect holds channel 3, the replay's fourth channel writes to a dummy register block and keeps
;     its DMA bit at 0: the effect sounds whole, and the music's fourth voice comes back with its next note.
;     The other three music channels are never touched, and without effects the tune plays as the game has it.

SFX_LOAD        equ     $80400          ; where SFXS goes now (91,144 bytes, to $96588)
SFX_OLD         equ     $2EA72          ; where SFXS went before (shared with MUSC)
SFX_TABLE       equ     $12452          ; 25 effects, 10 bytes each: address.l, period.w, length.w, flags.w
CHAN4           equ     $153E0          ; the replay's state block of its fourth channel; +20 = its DMA bit
PENDING         equ     $1A1DA          ; effects started this frame: DMA bits the frame interrupt turns on
MODE            equ     $13FF2          ; 0 nothing loaded, 1 music, 2 effects
GAME_ENTRY      equ     $1254C          ; what the jmp at $3300 goes to

        org     $80000

; ----------------------------------------------------------------------------------------------- start
; The game's start-up stub (at $2CB00) pokes "jmp $80000" over $3300 from the EUROSOFT list that the wrapper
; left at $C0, then jumps to $3300. We arrive in supervisor mode with interrupts off.
init:
        bra.w   start
        dc.w    patches-init                    ; for tools/kgs_patch.py: it checks every patched site
start:
        move.w  #$4EF9,$3300                    ; put back "jmp GAME_ENTRY"
        move.l  #GAME_ENTRY,$3302
        lea     patches(pc),a0
.next:  move.l  (a0)+,d0                        ; address.l, byte count.w (even), bytes
        beq.s   .table
        move.l  d0,a1
        move.w  (a0)+,d1
        lsr.w   #1,d1
        subq.w  #1,d1
.copy:  move.w  (a0)+,(a1)+
        dbf     d1,.copy
        bra.s   .next
.table: lea     SFX_TABLE,a0                    ; every effect: the new address, Paula channel 3 only
        moveq   #24,d0
.fx:    move.l  (a0),d1
        cmp.l   #SFX_LOAD,d1
        bhs.s   .done                           ; already moved (the game started again)
        add.l   #SFX_LOAD-SFX_OLD,d1
        move.l  d1,(a0)
        move.w  8(a0),d1
        and.w   #$0010,d1                       ; keep "force"
        or.w    #$0008,d1                       ; channel 3
        move.w  d1,8(a0)
.done:  lea     10(a0),a0
        dbf     d0,.fx
        jmp     GAME_ENTRY

; -------------------------------------------------------------------------------- is an effect playing?
; Z clear when channel 3 belongs to an effect: started this frame (PENDING), or its audio interrupt is on
; and its DMA is running. Changes d0 only.
fx_busy:
        move.w  PENDING,d0
        and.w   #$0008,d0
        bne.s   .yes
        move.w  $DFF01C,d0                      ; INTENAR: AUD3
        and.w   #$0400,d0
        beq.s   .no
        move.w  $DFF002,d0                      ; DMACONR: AUD3 (a stopped effect leaves its interrupt on)
        and.w   #$0008,d0
.no:
.yes:   rts

; ------------------------------------------------- the replay's fourth channel: real or dummy registers
; Replaces "lea $dff0d0,a5" before both calls for the fourth channel ($14D96 effects, $14E5C new notes).
hook_chan4:
        move.l  d0,-(sp)
        bsr.s   fx_busy
        bne.s   .duck
        lea     $DFF0D0,a5
        move.w  #$0008,CHAN4+20
        move.l  (sp)+,d0
        rts
.duck:  lea     dummy(pc),a5
        clr.w   CHAN4+20                        ; its DMA on/off writes become no-ops
        move.l  (sp)+,d0
        rts

; Replaces the fourth channel's repeat pointer and length writes at $14F88 (a5 = $DFF000, a6 = CHAN4).
hook_repeat4:
        move.l  d0,-(sp)
        bsr.s   fx_busy
        bne.s   .skip
        move.l  10(a6),$D0(a5)
        move.w  14(a6),$D4(a5)
.skip:  move.l  (sp)+,d0
        rts

; ----------------------------------------------------------------------------------- load both files
; Replaces the start of $1C8CA, which loaded SFXS or MUSC (to the same buffer) by the option chosen.
; Both are loaded once; MODE stays 1 (music) and the game clears it when something else used the buffer.
load_both:
        tst.w   MODE
        bne.s   .have
        jsr     $1C80A                          ; SFXS -> SFX_LOAD (patched below), MODE = 2
        jsr     $1C7F2                          ; MUSC -> $2EA72, MODE = 1
.have:  rts

dummy:  ds.w    8                               ; where the ducked fourth channel writes

; ------------------------------------------------------------------------------------------- patches
; address.l, byte count.w, bytes (even count). The tool checks each site's original bytes before patching.
patches:
        dc.l    $1A116                          ; effect routine: "bne return if not effects mode"
        dc.w    4
        dc.w    $4E71,$4E71
        dc.l    $14AEC                          ; frame interrupt: after the music ...
        dc.w    2
        dc.w    $4E71                           ; ... do not skip the effects part
        dc.l    $14AF2
        dc.w    2
        dc.w    $4E71                           ; ... whatever the mode
        dc.l    $14D96                          ; replay, effects of channel 4: lea $dff0d0,a5
        dc.w    6
        dc.w    $4EB9
        dc.l    hook_chan4
        dc.l    $14E5C                          ; replay, new note on channel 4: lea $dff0d0,a5
        dc.w    6
        dc.w    $4EB9
        dc.l    hook_chan4
        dc.l    $14F88                          ; replay, channel 4 repeat: two moves
        dc.w    12
        dc.w    $4EB9
        dc.l    hook_repeat4
        dc.w    $4E71,$4E71,$4E71
        dc.l    $1C8CA                          ; loader: load both, then on to the level part at $1C8E6
        dc.w    10
        dc.w    $4EB9
        dc.l    load_both
        dc.w    $6000,$1C8E6-($1C8CA+8)
        dc.l    $1C814                          ; SFXS destination
        dc.w    4
        dc.l    SFX_LOAD
        dc.l    0
patches_end:
