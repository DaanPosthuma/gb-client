-- TIMA (0xFF05) / TMA (0xFF06) / TAC (0xFF07) are plain memory bytes - only TIMA
-- needs active ticking. TAC bit2 = enable, bits1-0 = clock select -> period in
-- T-cycles per TIMA increment. Array lookup instead of multiIf so `tac` is
-- referenced once, not three times (see the size note below - every reference here
-- gets multiplied across all 500 opcode handlers, so keeping each helper's own
-- parameter-reference count minimal matters a lot more than it looks).
CREATE OR REPLACE FUNCTION gb_timer_period AS (tac) ->
    arrayElement([1024, 16, 64, 256], CAST(bitAnd(tac, 3) AS UInt8) + 1);

-- How many TIMA increments occurred between old_cycles and new_cycles (usually 0 or 1;
-- can occasionally be 2 for the fastest period (16 cycles) combined with a long
-- instruction). References `tac` 3 times (enable check + 2x period lookup) - flat,
-- does not call any other multi-reference helper, so this does not compound further.
CREATE OR REPLACE FUNCTION gb_timer_ticks AS (tac, old_cycles, new_cycles) ->
    if(bitAnd(tac, 4) = 0, 0,
       CAST(intDiv(new_cycles, gb_timer_period(tac)) - intDiv(old_cycles, gb_timer_period(tac)) AS Int64));

-- Combined VBlank-IF-bit + TIMA-tick handler, applied to every opcode's mem field
-- (see gen_step.py's op()) and to interrupt entry (gb_service_interrupt). Earlier
-- attempts (see git history) branched on overflow to reload TIMA from TMA and set
-- the timer-interrupt IF bit - correct, but referencing mem_expr ~13 times (even
-- after minimizing every helper to touch its own parameters as few times as
-- possible) still blew the query tree past 500000 nodes once multiplied across 500
-- opcodes (ClickHouse UDF calls macro-expand by substitution: a parameter
-- referenced N times duplicates its whole argument expression N times, compounding
-- across nested call levels - there's no let-binding to get one evaluation reused).
-- Scoped down to a branch-free version: TIMA still increments correctly via modulo
-- arithmetic (readable by any code that polls it), but on overflow it wraps via
-- %256 instead of reloading from TMA, and it does NOT request the timer interrupt.
-- That's a real, documented gap versus real hardware (games relying on TMA reload
-- or the timer interrupt will misbehave) but was the tradeoff that fit; revisit if a
-- test ROM actually needs it.
CREATE OR REPLACE FUNCTION gb_maybe_tick AS (mem_expr, old_cycles, new_cycles) ->
    gb_set2(mem_expr,
        0xFF05 + 1, CAST((arrayElement(mem_expr, 0xFF05 + 1) + gb_timer_ticks(arrayElement(mem_expr, 0xFF07 + 1), old_cycles, new_cycles)) % 256 AS UInt8),
        0xFF0F + 1, CAST(bitOr(arrayElement(mem_expr, 0xFF0F + 1), gb_crossed_into_vblank(old_cycles, new_cycles)) AS UInt8)
    );
