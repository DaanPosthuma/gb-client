-- Basic opcode sanity checks. Program: LD SP,$FFFE; LD HL,$8000; XOR A; LDI (HL),A; INC B(x2 via DEC test)
SELECT 'boot-ish sequence' AS test,
    gb_sp(final) AS sp_expect_65534,
    gb_hl(final) AS hl_expect_32769,
    gb_a(final) AS a_expect_0,
    arrayElement(gb_mem(final), 0x8000 + 1) AS mem8000_expect_0,
    gb_pc(final) AS pc_expect_517
FROM (
    SELECT arrayFold((acc, x) -> gb_step(acc), range(4), gb_mk(
        arrayMap((byte, p) -> multiIf(
                p = 513, 0x31, p = 514, 0xFE, p = 515, 0xFF,   -- 0x200: LD SP,$FFFE
                p = 516, 0x21, p = 517, 0x00, p = 518, 0x80,   -- 0x203: LD HL,$8000
                p = 519, 0xAF,                                  -- 0x206: XOR A
                p = 520, 0x22,                                  -- 0x207: LDI (HL),A
                byte),
            arrayResize(CAST([] AS Array(UInt8)), 65536, 0), arrayEnumerate(arrayResize(CAST([] AS Array(UInt8)), 65536, 0))),
        arrayResize(CAST([] AS Array(UInt8)), 256, 0),
        CAST(0 AS UInt8),
        0, 0, 0, 0, 0, 0, 0, 0,
        CAST(0 AS UInt16), CAST(512 AS UInt16),
        CAST(0 AS UInt8), CAST(0 AS UInt32)
    )) AS final
);

-- CALL/RET roundtrip + PUSH/POP HL, starting PC=0x200: CALL $300; at $300: PUSH HL; POP HL; RET
SELECT 'call/ret/push/pop, pc expect 515' AS test, gb_pc(final) AS pc, gb_sp(final) AS sp_expect_65534
FROM (
    SELECT arrayFold((acc, x) -> gb_step(acc), range(4), gb_mk(
        arrayMap((byte, p) -> multiIf(
                p = 513, 0xCD, p = 514, 0x00, p = 515, 0x03,   -- 0x200: CALL $0300
                p = 0x300 + 1, 0xE5,                            -- 0x300: PUSH HL
                p = 0x301 + 1, 0xE1,                            -- 0x301: POP HL
                p = 0x302 + 1, 0xC9,                            -- 0x302: RET
                byte),
            arrayResize(CAST([] AS Array(UInt8)), 65536, 0), arrayEnumerate(arrayResize(CAST([] AS Array(UInt8)), 65536, 0))),
        arrayResize(CAST([] AS Array(UInt8)), 256, 0),
        CAST(0 AS UInt8),
        0, 0, 0, 0, 0, 0, 0, 0,
        CAST(0xFFFE AS UInt16), CAST(512 AS UInt16),
        CAST(0 AS UInt8), CAST(0 AS UInt32)
    )) AS final
);

-- JR backward-loop: DEC B; JR NZ,-2 - loop from B=3 down to 0, then fall through.
SELECT 'JR backward loop, b expect 0' AS test, gb_b(final) AS b, gb_pc(final) AS pc_expect_515
FROM (
    SELECT arrayFold((acc, x) -> gb_step(acc), range(7), gb_mk(
        arrayMap((byte, p) -> multiIf(
                p = 513, 0x05,                                  -- 0x200: DEC B
                p = 514, 0x20, p = 515, CAST(-4 AS UInt8),       -- 0x201: JR NZ,-4 (back to 0x200)
                byte),
            arrayResize(CAST([] AS Array(UInt8)), 65536, 0), arrayEnumerate(arrayResize(CAST([] AS Array(UInt8)), 65536, 0))),
        arrayResize(CAST([] AS Array(UInt8)), 256, 0),
        CAST(0 AS UInt8),
        0, 3, 0, 0, 0, 0, 0, 0,
        CAST(0xFFFE AS UInt16), CAST(512 AS UInt16),
        CAST(0 AS UInt8), CAST(0 AS UInt32)
    )) AS final
);

-- VBlank flag: run enough NOPs (cycles=4 each) to cross a full scanline*144 boundary.
SELECT 'vblank IF flag set after enough cycles' AS test,
    bitAnd(arrayElement(gb_mem(final), 0xFF0F + 1), 1) AS if_bit0_expect_1,
    gb_cycles(final) AS cycles
FROM (
    SELECT arrayFold((acc, x) -> gb_step(acc), range(intDiv(144 * 456, 4) + 2), gb_mk(
        arrayResize(CAST([] AS Array(UInt8)), 65536, 0),
        arrayResize(CAST([] AS Array(UInt8)), 256, 0),
        CAST(0 AS UInt8),
        0, 0, 0, 0, 0, 0, 0, 0,
        CAST(0xFFFE AS UInt16), CAST(512 AS UInt16),
        CAST(0 AS UInt8), CAST(0 AS UInt32)
    )) AS final
);
