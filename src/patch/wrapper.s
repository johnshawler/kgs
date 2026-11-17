; wrapper.s - added to the end of the first hunk of the game program "kgs" (the AGA fix by Fix Limited).
;
; That hunk sets the machine up for the game under AmigaOS (display, caches, vector base) and then calls the
; game, the second hunk, which takes the machine over and unpacks itself. tools/kgs_patch.py makes its call
; to the game come here first. Here, still under AmigaOS:
;   1. claim $80000-$965FF from exec (the resident patch and the effects bank live there; the game was written
;      for 512K and never touches it). If that memory is not free, say so and start the unpatched game.
;   2. copy the resident patch (resident.s) to $80000;
;   3. leave a "EUROSOFT" poke list at $C0: the game's own start-up stub ($2CB00) applies it once the game is
;      unpacked, which turns its first instruction into "jmp $80000";
;   4. go on to the game, as the AGA fix did.
; Position independent; the long at offset 4 is relocated to the game hunk by the tool.

REGION          equ     $80000
REGION_SIZE     equ     $16600
_LVOAllocAbs    equ     -204
_LVOCacheClearU equ     -636
_LVOOldOpenLibrary equ  -408
_LVOCloseLibrary equ    -414
_LVOOutput      equ     -60
_LVOWrite       equ     -48

wrapper:
        bra.w   start
game:   dc.l    0                               ; the game hunk (relocated)
start:
        movem.l d0-d7/a0-a6,-(sp)
        move.l  4.w,a6
        move.l  #REGION_SIZE,d0
        lea     REGION,a1
        jsr     _LVOAllocAbs(a6)
        tst.l   d0
        beq.s   no_room
        lea     resident(pc),a0
        lea     REGION,a1
        move.w  #(resident_end-resident)/2-1,d0
.copy:  move.w  (a0)+,(a1)+
        dbf     d0,.copy
        lea     $C0.w,a1                        ; the poke list: "EUROSOFT", (address.l, word)..., -1
        move.l  #"EURO",(a1)+
        move.l  #"SOFT",(a1)+
        move.l  #$3300,(a1)+
        move.w  #$4EF9,(a1)+
        move.l  #$3302,(a1)+
        move.w  #REGION>>16,(a1)+
        move.l  #$3304,(a1)+
        move.w  #REGION&$FFFF,(a1)+
        moveq   #-1,d0
        move.l  d0,(a1)
        cmp.w   #37,20(a6)                      ; CacheClearU needs Kickstart 2.0
        blo.s   go
        jsr     _LVOCacheClearU(a6)
go:     movem.l (sp)+,d0-d7/a0-a6
        move.l  game(pc),-(sp)
        rts

no_room: lea    dosname(pc),a1
        jsr     _LVOOldOpenLibrary(a6)
        tst.l   d0
        beq.s   go
        move.l  d0,a6
        jsr     _LVOOutput(a6)
        move.l  d0,d1
        beq.s   .close
        lea     message(pc),a0
        move.l  a0,d2
        moveq   #message_end-message,d3
        jsr     _LVOWrite(a6)
.close: move.l  a6,a1
        move.l  4.w,a6
        jsr     _LVOCloseLibrary(a6)
        bra.s   go

dosname:        dc.b    "dos.library",0
message:        dc.b    "Music+effects patch: memory at $80000 is in use, needs 1 MB chip RAM",10
message_end:
        even
resident:
        incbin  "resident.bin"
resident_end:
        even
