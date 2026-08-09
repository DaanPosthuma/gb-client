-- CPU state is a positional Tuple, field order fixed by convention:
--   1 mem         (Array(UInt8) len 65536, flat address space)
--   2 boot_rom    (Array(UInt8) len 256)
--   3 boot_active (UInt8, 1 while the boot ROM overlays 0x0000-0x00FF)
--   4 a  5 b  6 c  7 d  8 e  9 h  10 l  11 f   (UInt8 registers)
--   12 sp  13 pc                              (UInt16)
--   14 ime       (UInt8, interrupt master enable)
--   15 cycles    (UInt32, running T-cycle counter - LY/VBlank are derived from this)
-- No WITH/subqueries anywhere in this project either, for the same reason as CHIP-8:
-- they break when called from inside arrayFold's lambda.

CREATE OR REPLACE FUNCTION gb_set AS (arr, idx, val) ->
    arrayMap((x, i) -> if(i = idx, val, x), arr, arrayEnumerate(arr));
CREATE OR REPLACE FUNCTION gb_set2 AS (arr, idx1, val1, idx2, val2) ->
    arrayMap((x, i) -> multiIf(i = idx1, val1, i = idx2, val2, x), arr, arrayEnumerate(arr));

CREATE OR REPLACE FUNCTION gb_mk AS (mem, boot_rom, boot_active, a, b, c, d, e, h, l, f, sp, pc, ime, cycles) ->
    tuple(mem, boot_rom, boot_active, a, b, c, d, e, h, l, f, sp, pc, ime, cycles);

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

-- Memory read: boot ROM overlays 0x0000-0x00FF while active; LY (0xFF44) is derived,
-- never stored, so a read there is intercepted regardless of what's in `mem`.
CREATE OR REPLACE FUNCTION gb_read8 AS (s, addr) ->
    multiIf(
        addr = 0xFF44, gb_ly(gb_cycles(s)),
        gb_boot_active(s) = 1 AND addr < 0x100, arrayElement(gb_boot_rom(s), CAST(addr AS UInt16) + 1),
        arrayElement(gb_mem(s), CAST(addr AS UInt32) + 1)
    );
CREATE OR REPLACE FUNCTION gb_read16 AS (s, addr) ->
    CAST(gb_read8(s, addr) + gb_read8(s, CAST(addr + 1 AS UInt16)) * 256 AS UInt16);
