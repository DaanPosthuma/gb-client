-- 8-bit memory write. LY (0xFF44) is read-only/derived (ignored here); a nonzero
-- write to rBANK (0xFF50) unmaps the boot ROM, matching real hardware.
CREATE OR REPLACE FUNCTION gb_write8 AS (s, addr, val) ->
    gb_mk(
        if(addr = 0xFF44, gb_mem(s), gb_set(gb_mem(s), CAST(addr AS UInt32) + 1, val)),
        gb_boot_rom(s),
        if(addr = 0xFF50 AND val != 0, CAST(0 AS UInt8), gb_boot_active(s)),
        gb_a(s), gb_b(s), gb_c(s), gb_d(s), gb_e(s), gb_h(s), gb_l(s), gb_f(s),
        gb_sp(s), gb_pc(s), gb_ime(s), gb_cycles(s)
    );

-- VBlank IF-flag setting now lives in gb_maybe_tick (00d_timer.sql), merged together
-- with TIMA ticking into one combined function - see that file's comment for why they
-- can't be two separately-nested wrapper functions.

-- Rebuilds a state tuple with every register/pc/sp/ime/cycles field given explicitly,
-- keeping mem/boot_rom/boot_active from `s` (call gb_write8 first if memory changed too).
CREATE OR REPLACE FUNCTION gb_with_regs AS (s, a, b, c, d, e, h, l, f, sp, pc, ime, cycles) ->
    gb_mk(gb_mem(s), gb_boot_rom(s), gb_boot_active(s), a, b, c, d, e, h, l, f, sp, pc, ime, cycles);

-- pc/cycles advance with no register change (NOP-shaped instructions).
CREATE OR REPLACE FUNCTION gb_advance AS (s, len, t) ->
    gb_with_regs(s, gb_a(s), gb_b(s), gb_c(s), gb_d(s), gb_e(s), gb_h(s), gb_l(s), gb_f(s),
                 gb_sp(s), CAST(gb_pc(s) + len AS UInt16), gb_ime(s), CAST(gb_cycles(s) + t AS UInt32));
