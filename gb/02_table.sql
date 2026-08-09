CREATE DATABASE IF NOT EXISTS gb;

-- State stored as ONE Tuple column, not one column per field. With ~500 opcodes now
-- implemented, gb_run_batch's own expansion is large enough that an INSERT...SELECT
-- extracting each of the 15 fields via gb_X(ns) - referencing that one large result
-- 15 times - blows past ClickHouse's query-tree size limit the same way chaining
-- helpers inside a single opcode did. Storing/reading the tuple whole (referenced
-- exactly once per query) sidesteps it entirely. MATERIALIZED columns below are just
-- for convenient querying in the Explore tab / webapp, not part of the write path.
CREATE OR REPLACE TABLE gb.state
(
    run_id  UUID,
    step_id UInt64,
    -- Unnamed positional tuple, deliberately - gb_mk() builds plain tuple(...) values
    -- (unnamed), and ClickHouse treats named vs. unnamed tuples as different types,
    -- which arrayFold rejects when the accumulator and lambda-return types must match.
    -- Field order matches gb_mk's, see the comment atop 00_helpers.sql.
    state   Tuple(Array(UInt8), Array(UInt8), UInt8, UInt8, UInt8, UInt8, UInt8, UInt8, UInt8, UInt8, UInt8, UInt16, UInt16, UInt8, UInt32, UInt8),
    a UInt8 MATERIALIZED state.4,
    b UInt8 MATERIALIZED state.5,
    c UInt8 MATERIALIZED state.6,
    d UInt8 MATERIALIZED state.7,
    e UInt8 MATERIALIZED state.8,
    h UInt8 MATERIALIZED state.9,
    l UInt8 MATERIALIZED state.10,
    f UInt8 MATERIALIZED state.11,
    sp UInt16 MATERIALIZED state.12,
    pc UInt16 MATERIALIZED state.13,
    ime UInt8 MATERIALIZED state.14,
    cycles UInt32 MATERIALIZED state.15,
    boot_active UInt8 MATERIALIZED state.3,
    buttons UInt8 MATERIALIZED state.16,
    opcode UInt16 MATERIALIZED gb_read8(state, state.13)
)
ENGINE = MergeTree
ORDER BY (run_id, step_id);

-- Runs CPU steps until `n` more T-cycles have elapsed (not `n` instructions - a
-- fixed-size arrayFold needs an upper bound on iteration count, so this runs enough
-- steps to *guarantee* covering the target assuming the cheapest possible instruction
-- (4 cycles), turning every step past the target into a no-op). The VBlank IF flag is
-- set inline by gb_step itself now (see gb_maybe_set_vblank) - no post-batch wrapper.
CREATE OR REPLACE FUNCTION gb_run_batch AS (s, n) ->
    arrayFold(
        (acc, x) -> if(gb_cycles(acc) >= gb_cycles(s) + n, acc, gb_step(acc)),
        range(intDiv(n, 4) + 1),
        s
    );
