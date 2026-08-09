-- CPU state is a positional Tuple, field order fixed by convention:
--   1 mem         (Array(UInt8) len 65536, flat address space)
--   2 boot_rom    (Array(UInt8) len 256)
--   3 boot_active (UInt8, 1 while the boot ROM overlays 0x0000-0x00FF)
--   4 a  5 b  6 c  7 d  8 e  9 h  10 l  11 f   (UInt8 registers)
--   12 sp  13 pc                              (UInt16)
--   14 ime       (UInt8, interrupt master enable)
--   15 cycles    (UInt32, running T-cycle counter - LY/VBlank are derived from this)
--   16 buttons   (UInt8, currently-held GB buttons, ACTIVE-HIGH: bit0=Right,
--                 1=Left, 2=Up, 3=Down, 4=A, 5=B, 6=Select, 7=Start - opposite
--                 polarity from the real P1 register on purpose, since "1 = held"
--                 reads naturally; gb_joypad_byte below converts to hardware's
--                 active-low format for the 0xFF00 read). Set externally by the
--                 driver via gb_set_buttons, never touched by CPU instructions
--                 themselves (every opcode passes it through unchanged).
-- No WITH/subqueries anywhere in this project either, for the same reason as CHIP-8:
-- they break when called from inside arrayFold's lambda.

CREATE OR REPLACE FUNCTION gb_set AS (arr, idx, val) ->
    arrayMap((x, i) -> if(i = idx, val, x), arr, arrayEnumerate(arr));
CREATE OR REPLACE FUNCTION gb_set2 AS (arr, idx1, val1, idx2, val2) ->
    arrayMap((x, i) -> multiIf(i = idx1, val1, i = idx2, val2, x), arr, arrayEnumerate(arr));

CREATE OR REPLACE FUNCTION gb_mk AS (mem, boot_rom, boot_active, a, b, c, d, e, h, l, f, sp, pc, ime, cycles, buttons) ->
    tuple(mem, boot_rom, boot_active, a, b, c, d, e, h, l, f, sp, pc, ime, cycles, buttons);

CREATE OR REPLACE FUNCTION gb_mem         AS (s) -> s.1;
CREATE OR REPLACE FUNCTION gb_boot_rom    AS (s) -> s.2;
CREATE OR REPLACE FUNCTION gb_boot_active AS (s) -> s.3;
CREATE OR REPLACE FUNCTION gb_a           AS (s) -> s.4;
CREATE OR REPLACE FUNCTION gb_b           AS (s) -> s.5;
CREATE OR REPLACE FUNCTION gb_c           AS (s) -> s.6;
CREATE OR REPLACE FUNCTION gb_d           AS (s) -> s.7;
CREATE OR REPLACE FUNCTION gb_e           AS (s) -> s.8;
CREATE OR REPLACE FUNCTION gb_h           AS (s) -> s.9;
CREATE OR REPLACE FUNCTION gb_l           AS (s) -> s.10;
CREATE OR REPLACE FUNCTION gb_f           AS (s) -> s.11;
CREATE OR REPLACE FUNCTION gb_sp          AS (s) -> s.12;
CREATE OR REPLACE FUNCTION gb_pc          AS (s) -> s.13;
CREATE OR REPLACE FUNCTION gb_ime         AS (s) -> s.14;
CREATE OR REPLACE FUNCTION gb_cycles      AS (s) -> s.15;
CREATE OR REPLACE FUNCTION gb_buttons     AS (s) -> s.16;

-- Rebuilds state with `buttons` changed AND mem[0xFF00] recomputed to reflect it
-- under whichever select bits are currently stored there (see gb_joypad_byte's
-- comment) - the driver's input-update path, called once per key event, never from
-- inside CPU-step's arrayFold, so no AST-size concern touching gb_joypad_byte here.
CREATE OR REPLACE FUNCTION gb_set_buttons AS (s, new_buttons) ->
    gb_mk(gb_set(gb_mem(s), 0xFF00 + 1, gb_joypad_byte(arrayElement(gb_mem(s), 0xFF00 + 1), new_buttons)),
          gb_boot_rom(s), gb_boot_active(s),
          gb_a(s), gb_b(s), gb_c(s), gb_d(s), gb_e(s), gb_h(s), gb_l(s), gb_f(s),
          gb_sp(s), gb_pc(s), gb_ime(s), gb_cycles(s), new_buttons);

-- 16-bit register pairs.
CREATE OR REPLACE FUNCTION gb_bc AS (s) -> CAST(gb_b(s) * 256 + gb_c(s) AS UInt16);
CREATE OR REPLACE FUNCTION gb_de AS (s) -> CAST(gb_d(s) * 256 + gb_e(s) AS UInt16);
CREATE OR REPLACE FUNCTION gb_hl AS (s) -> CAST(gb_h(s) * 256 + gb_l(s) AS UInt16);
CREATE OR REPLACE FUNCTION gb_af AS (s) -> CAST(gb_a(s) * 256 + bitAnd(gb_f(s), 0xF0) AS UInt16);

-- Flag bits within F: Z=0x80 N=0x40 H=0x20 C=0x10.
CREATE OR REPLACE FUNCTION gb_flag_z AS (f) -> CAST(bitAnd(bitShiftRight(f, 7), 1) AS UInt8);
CREATE OR REPLACE FUNCTION gb_flag_n AS (f) -> CAST(bitAnd(bitShiftRight(f, 6), 1) AS UInt8);
CREATE OR REPLACE FUNCTION gb_flag_h AS (f) -> CAST(bitAnd(bitShiftRight(f, 5), 1) AS UInt8);
CREATE OR REPLACE FUNCTION gb_flag_c AS (f) -> CAST(bitAnd(bitShiftRight(f, 4), 1) AS UInt8);
CREATE OR REPLACE FUNCTION gb_mkflags AS (z, n, h, c) ->
    CAST(bitShiftLeft(z, 7) + bitShiftLeft(n, 6) + bitShiftLeft(h, 5) + bitShiftLeft(c, 4) AS UInt8);

-- PPU timing (background-only, no STAT modes/sprites needed - the boot ROM never reads
-- them). One frame = 154 scanlines * 456 T-cycles; LY and the VBlank edge are both
-- pure functions of the running cycle counter, never stored as independent state.
CREATE OR REPLACE FUNCTION gb_ly AS (cycles) -> CAST(intDiv(cycles, 456) % 154 AS UInt8);
CREATE OR REPLACE FUNCTION gb_crossed_into_vblank AS (cycles_before, cycles_after) ->
    CAST(intDiv(cycles_before, 456) % 154 < 144 AND intDiv(cycles_after, 456) % 154 >= 144 AS UInt8);

-- DIV (0xFF04) increments every 256 T-cycles, purely derived like LY. Real hardware
-- resets it to 0 on any write; writes here are simply inert (gb_write8 still stores
-- into `mem`, but gb_read8 always overrides with the derived value) - a known,
-- documented approximation, same spirit as HALT-as-NOP.
CREATE OR REPLACE FUNCTION gb_div AS (cycles) -> CAST(intDiv(cycles, 256) % 256 AS UInt8);

-- Joypad (0xFF00 / P1): bits 7-6 always read 1 (unused); bits 5-4 are the
-- select lines; bits 3-0 are the button states for whichever group is selected
-- (bit=0 means SELECTED and bit=0 in the result means PRESSED - both active-low,
-- opposite of gb_buttons' active-high storage, hence the bitNot). If neither group is
-- selected, bits 3-0 read as all 1 (nothing pressed). Simplified vs. real hardware:
-- doesn't handle both groups selected simultaneously (rare, essentially no real game
-- does this) - falls through to the direction-only reading instead of the correct
-- AND-of-both-nibbles.
--
-- Unlike LY/DIV, this is NOT computed inside gb_read8: gb_read8 is called from nearly
-- every one of the 500 opcode handlers, so anything added to it gets multiplied by
-- every call site - even this fairly small function pushed the whole query tree just
-- over ClickHouse's 500000-node limit when tried there (reproduced, see git history).
-- Instead mem[0xFF00] is kept pre-computed: gb_write8 recomputes it whenever the game
-- changes the select bits, and gb_set_buttons recomputes it whenever input changes -
-- both far less frequently called than gb_read8, so a plain gb_read8 array read is
-- always correct without adding anything to that hot path.
CREATE OR REPLACE FUNCTION gb_joypad_byte AS (sel_byte, buttons) -> (
    CAST(bitOr(0xC0, bitOr(
        bitAnd(sel_byte, 0x30),
        multiIf(
            bitAnd(sel_byte, 0x20) = 0, bitAnd(bitNot(bitShiftRight(buttons, 4)), 0x0F),
            bitAnd(sel_byte, 0x10) = 0, bitAnd(bitNot(buttons), 0x0F),
            0x0F
        )
    )) AS UInt8)
);

-- Memory read: boot ROM overlays 0x0000-0x00FF while active; LY (0xFF44) and DIV
-- (0xFF04) are derived, never stored, so reads there are intercepted regardless of
-- what's in `mem`. 0xFF00 (joypad) is a plain array read - see gb_joypad_byte's
-- comment for why it's kept pre-computed instead of intercepted here.
CREATE OR REPLACE FUNCTION gb_read8 AS (s, addr) ->
    multiIf(
        addr = 0xFF44, gb_ly(gb_cycles(s)),
        addr = 0xFF04, gb_div(gb_cycles(s)),
        gb_boot_active(s) = 1 AND addr < 0x100, arrayElement(gb_boot_rom(s), CAST(addr AS UInt16) + 1),
        arrayElement(gb_mem(s), CAST(addr AS UInt32) + 1)
    );
CREATE OR REPLACE FUNCTION gb_read16 AS (s, addr) ->
    CAST(gb_read8(s, addr) + gb_read8(s, CAST(addr + 1 AS UInt16)) * 256 AS UInt16);
