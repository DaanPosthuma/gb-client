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

-- Applied to every opcode handler's mem field (see gen_step.py's op()) so the VBlank
-- IF flag gets set as part of the instruction's own single flat gb_mk call, rather
-- than as a wrapper around gb_step's result afterward. `mem_after_op` is always a
-- small, local-to-one-instruction expression (gb_mem(s) or one gb_set(...) on it) -
-- referencing it twice here is cheap, unlike referencing gb_step's full ~500-opcode
-- dispatch result multiple times, which is what broke previously.
CREATE OR REPLACE FUNCTION gb_maybe_set_vblank AS (mem_after_op, old_cycles, new_cycles) ->
    if(gb_crossed_into_vblank(old_cycles, new_cycles) = 1,
       gb_set(mem_after_op, 0xFF0F + 1, CAST(bitOr(arrayElement(mem_after_op, 0xFF0F + 1), 1) AS UInt8)),
       mem_after_op);

-- Rebuilds a state tuple with every register/pc/sp/ime/cycles field given explicitly,
-- keeping mem/boot_rom/boot_active from `s` (call gb_write8 first if memory changed too).
CREATE OR REPLACE FUNCTION gb_with_regs AS (s, a, b, c, d, e, h, l, f, sp, pc, ime, cycles) ->
    gb_mk(gb_mem(s), gb_boot_rom(s), gb_boot_active(s), a, b, c, d, e, h, l, f, sp, pc, ime, cycles);

-- pc/cycles advance with no register change (NOP-shaped instructions).
CREATE OR REPLACE FUNCTION gb_advance AS (s, len, t) ->
    gb_with_regs(s, gb_a(s), gb_b(s), gb_c(s), gb_d(s), gb_e(s), gb_h(s), gb_l(s), gb_f(s),
                 gb_sp(s), CAST(gb_pc(s) + len AS UInt16), gb_ime(s), CAST(gb_cycles(s) + t AS UInt32));
