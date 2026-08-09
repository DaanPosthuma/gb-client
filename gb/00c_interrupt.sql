-- Interrupt servicing. IE (0xFFFF) and IF (0xFF0F) are plain memory bytes -
-- no special read/write interception needed (gb_maybe_set_vblank already ORs
-- the VBlank bit into IF inline, per-opcode). This file only adds the
-- dispatch-to-ISR step, checked once per gb_step before the normal opcode
-- dispatch. HALT is still treated as a NOP (see gen_step.py) - not tracking a
-- true halted CPU state - so this is a busy-wait approximation of HALT, not
-- cycle-exact, but functionally correct for interrupt-driven control flow.
-- EI is also immediate rather than delayed-by-one-instruction like real
-- hardware; both are known, documented simplifications.
CREATE OR REPLACE FUNCTION gb_ie AS (s) -> arrayElement(gb_mem(s), 0xFFFF + 1);
CREATE OR REPLACE FUNCTION gb_iflag AS (s) -> arrayElement(gb_mem(s), 0xFF0F + 1);
CREATE OR REPLACE FUNCTION gb_pending_interrupts AS (s) -> bitAnd(bitAnd(gb_ie(s), gb_iflag(s)), 0x1F);

-- Priority: VBlank(0) > LCD STAT(1) > Timer(2) > Serial(3) > Joypad(4) - lowest set bit wins.
CREATE OR REPLACE FUNCTION gb_interrupt_bit AS (pending) ->
    multiIf(bitAnd(pending, 1) != 0, 0, bitAnd(pending, 2) != 0, 1, bitAnd(pending, 4) != 0, 2, bitAnd(pending, 8) != 0, 3, 4);
CREATE OR REPLACE FUNCTION gb_interrupt_vector AS (bit) ->
    CAST(multiIf(bit = 0, 0x40, bit = 1, 0x48, bit = 2, 0x50, bit = 3, 0x58, 0x60) AS UInt16);

-- Assumes the caller (gb_step) has already checked IME=1 and a pending
-- interrupt exists. Pushes PC, jumps to the vector, clears IME and the
-- serviced IF bit; costs 20 T-cycles (5 M-cycles), same as real hardware.
CREATE OR REPLACE FUNCTION gb_service_interrupt AS (s) ->
    gb_mk(
        gb_maybe_tick(
            gb_set(
                gb_set2(gb_mem(s),
                    CAST(CAST(gb_sp(s) - 2 AS UInt16) AS UInt32) + 1, CAST(gb_pc(s) % 256 AS UInt8),
                    CAST(CAST(gb_sp(s) - 2 AS UInt16) AS UInt32) + 2, CAST(intDiv(gb_pc(s), 256) AS UInt8)
                ),
                0xFF0F + 1,
                CAST(bitAnd(gb_iflag(s), bitXor(0xFF, bitShiftLeft(1, gb_interrupt_bit(gb_pending_interrupts(s))))) AS UInt8)
            ),
            gb_cycles(s), CAST(gb_cycles(s) + 20 AS UInt32)
        ),
        gb_boot_rom(s), gb_boot_active(s),
        gb_a(s), gb_b(s), gb_c(s), gb_d(s), gb_e(s), gb_h(s), gb_l(s), gb_f(s),
        CAST(gb_sp(s) - 2 AS UInt16),
        gb_interrupt_vector(gb_interrupt_bit(gb_pending_interrupts(s))),
        CAST(0 AS UInt8),
        CAST(gb_cycles(s) + 20 AS UInt32),
        gb_buttons(s)
    );
