#!/usr/bin/env python3
"""Generates 01_step.sql: every opcode handler as ONE flat gb_mk(...) call.

Chaining state-returning helpers (gb_write8(gb_set_hl(...))) multiplies AST size at
each level, since each helper re-references its input parameter ~13 times to carry
untouched fields forward - 2-3 levels of chaining blows past ClickHouse's 500,000-node
query-tree limit even for a single instruction. Flat generation sidesteps this: every
field of every gb_mk call is computed directly from gb_X(s) accessors on the ONE
original state parameter, never from another function's state-tuple output.

Now generates the (near-)full LR35902 instruction set via the standard systematic
opcode encoding (register index 0-7 = B,C,D,E,H,L,(HL),A) rather than hand-listing
every opcode, since most of the ~500-opcode space is regular.
"""

FIELDS = ["a", "b", "c", "d", "e", "h", "l", "f", "sp", "pc", "ime", "cycles"]
PC = "gb_pc(s)"
CYC = "gb_cycles(s)"
R8 = ["b", "c", "d", "e", "h", "l", None, "a"]  # index 6 = (HL), handled specially


def mem_default():
    return "gb_mem(s)"


def op(name, mem=None, boot_active=None, pc=None, cycles=None, **regs):
    vals = {f: f"gb_{f}(s)" for f in FIELDS}
    vals.update(regs)
    if pc is not None:
        vals["pc"] = pc
    if cycles is not None:
        vals["cycles"] = cycles
    mem_expr = mem if mem is not None else mem_default()
    # VBlank IF-flag setting rides along on every instruction's own mem field (see
    # gb_maybe_set_vblank's comment) instead of a separate post-batch wrapper.
    wrapped_mem = f"gb_maybe_set_vblank({mem_expr}, {CYC}, {vals['cycles']})"
    boot_active_expr = boot_active if boot_active is not None else "gb_boot_active(s)"
    args = [wrapped_mem, "gb_boot_rom(s)", boot_active_expr] + [vals[f] for f in FIELDS]
    return name, f"gb_mk({', '.join(args)})"


def pc_adv(n):
    return f"CAST({PC} + {n} AS UInt16)"


def cyc_adv(n):
    return f"CAST({CYC} + {n} AS UInt32)"


N8 = f"gb_read8(s, {pc_adv(1)})"
N16 = f"gb_read16(s, {pc_adv(1)})"
CB_OP = f"gb_read8(s, {pc_adv(1)})"

handlers = []
OP_TABLE = {}
CB_TABLE = {}


def r8_get(idx):
    return "gb_read8(s, gb_hl(s))" if idx == 6 else f"gb_{R8[idx]}(s)"


def add_handler(opcode, name, **kw):
    handlers.append(op(name, **kw))
    OP_TABLE[opcode] = name


def add_cb_handler(opcode, name, **kw):
    handlers.append(op(name, **kw))
    CB_TABLE[opcode] = name


# ============================================================ LD r,r' (0x40-0x7F) ==
for dst in range(8):
    for src in range(8):
        opc = 0x40 + 8 * dst + src
        if opc == 0x76:
            continue  # HALT, handled separately
        if dst == 6:  # LD (HL),r
            add_handler(opc, f"ld_hl_{R8[src]}",
                        mem=f"gb_set(gb_mem(s), CAST(gb_hl(s) AS UInt32) + 1, {r8_get(src)})",
                        pc=pc_adv(1), cycles=cyc_adv(8))
        elif src == 6:  # LD r,(HL)
            add_handler(opc, f"ld_{R8[dst]}_hl", pc=pc_adv(1), cycles=cyc_adv(8), **{R8[dst]: r8_get(6)})
        else:  # LD r,r'
            add_handler(opc, f"ld_{R8[dst]}_{R8[src]}", pc=pc_adv(1), cycles=cyc_adv(4), **{R8[dst]: r8_get(src)})

# ============================================================ LD r,d8 ==============
LD_D8_OPS = {0x06: "b", 0x0E: "c", 0x16: "d", 0x1E: "e", 0x26: "h", 0x2E: "l", 0x3E: "a"}
for opc, r in LD_D8_OPS.items():
    add_handler(opc, f"ld_{r}_d8", pc=pc_adv(2), cycles=cyc_adv(8), **{r: N8})
add_handler(0x36, "ld_hl_d8", mem=f"gb_set(gb_mem(s), CAST(gb_hl(s) AS UInt32) + 1, {N8})", pc=pc_adv(2), cycles=cyc_adv(12))

# ============================================================ INC r / DEC r ========
INC_DEC_OPS = {0x04: "b", 0x0C: "c", 0x14: "d", 0x1C: "e", 0x24: "h", 0x2C: "l", 0x3C: "a"}
for opc, r in INC_DEC_OPS.items():
    new_v = f"CAST((gb_{r}(s) + 1) % 256 AS UInt8)"
    add_handler(opc, f"inc_{r}", pc=pc_adv(1), cycles=cyc_adv(4), **{r: new_v,
                "f": f"gb_mkflags(CAST({new_v} = 0 AS UInt8), 0, CAST((gb_{r}(s) % 16) = 15 AS UInt8), gb_flag_c(gb_f(s)))"})
DEC_OPS = {0x05: "b", 0x0D: "c", 0x15: "d", 0x1D: "e", 0x25: "h", 0x2D: "l", 0x3D: "a"}
for opc, r in DEC_OPS.items():
    new_v = f"CAST((gb_{r}(s) + 255) % 256 AS UInt8)"
    add_handler(opc, f"dec_{r}", pc=pc_adv(1), cycles=cyc_adv(4), **{r: new_v,
                "f": f"gb_mkflags(CAST({new_v} = 0 AS UInt8), 1, CAST((gb_{r}(s) % 16) = 0 AS UInt8), gb_flag_c(gb_f(s)))"})
INC_HL_MEM = f"CAST((gb_read8(s, gb_hl(s)) + 1) % 256 AS UInt8)"
add_handler(0x34, "inc_hl_mem", mem=f"gb_set(gb_mem(s), CAST(gb_hl(s) AS UInt32) + 1, {INC_HL_MEM})", pc=pc_adv(1), cycles=cyc_adv(12),
            f=f"gb_mkflags(CAST({INC_HL_MEM} = 0 AS UInt8), 0, CAST((gb_read8(s, gb_hl(s)) % 16) = 15 AS UInt8), gb_flag_c(gb_f(s)))")
DEC_HL_MEM = f"CAST((gb_read8(s, gb_hl(s)) + 255) % 256 AS UInt8)"
add_handler(0x35, "dec_hl_mem", mem=f"gb_set(gb_mem(s), CAST(gb_hl(s) AS UInt32) + 1, {DEC_HL_MEM})", pc=pc_adv(1), cycles=cyc_adv(12),
            f=f"gb_mkflags(CAST({DEC_HL_MEM} = 0 AS UInt8), 1, CAST((gb_read8(s, gb_hl(s)) % 16) = 0 AS UInt8), gb_flag_c(gb_f(s)))")

# ============================================================ 8-bit ALU (r8 + d8) ==
def alu_add(x):
    s_ = f"(gb_a(s) + {x})"
    return f"CAST({s_} % 256 AS UInt8)", f"gb_mkflags(CAST({s_} % 256 = 0 AS UInt8), 0, CAST((gb_a(s) % 16) + ({x} % 16) > 15 AS UInt8), CAST({s_} > 255 AS UInt8))"

def alu_adc(x):
    cy = "gb_flag_c(gb_f(s))"
    s_ = f"(gb_a(s) + {x} + {cy})"
    return f"CAST({s_} % 256 AS UInt8)", f"gb_mkflags(CAST({s_} % 256 = 0 AS UInt8), 0, CAST((gb_a(s) % 16) + ({x} % 16) + {cy} > 15 AS UInt8), CAST({s_} > 255 AS UInt8))"

def alu_sub(x):
    d_ = f"(gb_a(s) - {x} + 256) % 256"
    return f"CAST({d_} AS UInt8)", f"gb_mkflags(CAST({d_} = 0 AS UInt8), 1, CAST((gb_a(s) % 16) < ({x} % 16) AS UInt8), CAST(gb_a(s) < {x} AS UInt8))"

def alu_sbc(x):
    cy = "gb_flag_c(gb_f(s))"
    amt = f"({x} + {cy})"
    d_ = f"(gb_a(s) - {amt} + 512) % 256"
    return f"CAST({d_} AS UInt8)", f"gb_mkflags(CAST({d_} = 0 AS UInt8), 1, CAST((gb_a(s) % 16) < (({x} % 16) + {cy}) AS UInt8), CAST(gb_a(s) < {amt} AS UInt8))"

def alu_and(x):
    r_ = f"CAST(bitAnd(gb_a(s), {x}) AS UInt8)"
    return r_, f"gb_mkflags(CAST({r_} = 0 AS UInt8), 0, 1, 0)"

def alu_xor(x):
    r_ = f"CAST(bitXor(gb_a(s), {x}) AS UInt8)"
    return r_, f"gb_mkflags(CAST({r_} = 0 AS UInt8), 0, 0, 0)"

def alu_or(x):
    r_ = f"CAST(bitOr(gb_a(s), {x}) AS UInt8)"
    return r_, f"gb_mkflags(CAST({r_} = 0 AS UInt8), 0, 0, 0)"

def alu_cp(x):
    d_ = f"(gb_a(s) - {x} + 256) % 256"
    return None, f"gb_mkflags(CAST({d_} = 0 AS UInt8), 1, CAST((gb_a(s) % 16) < ({x} % 16) AS UInt8), CAST(gb_a(s) < {x} AS UInt8))"

ALU_FNS = [alu_add, alu_adc, alu_sub, alu_sbc, alu_and, alu_xor, alu_or, alu_cp]
ALU_NAMES = ["add", "adc", "sub", "sbc", "and", "xor", "or", "cp"]

for op_idx in range(8):
    for src in range(8):
        opc = 0x80 + 8 * op_idx + src
        result, flags = ALU_FNS[op_idx](r8_get(src))
        kw = {"f": flags}
        if result is not None:
            kw["a"] = result
        add_handler(opc, f"{ALU_NAMES[op_idx]}_a_{R8[src]}", pc=pc_adv(1), cycles=cyc_adv(8 if src == 6 else 4), **kw)

ALU_D8_OPS = {0xC6: 0, 0xCE: 1, 0xD6: 2, 0xDE: 3, 0xE6: 4, 0xEE: 5, 0xF6: 6, 0xFE: 7}
for opc, op_idx in ALU_D8_OPS.items():
    result, flags = ALU_FNS[op_idx](N8)
    kw = {"f": flags}
    if result is not None:
        kw["a"] = result
    add_handler(opc, f"{ALU_NAMES[op_idx]}_a_d8", pc=pc_adv(2), cycles=cyc_adv(8), **kw)

# ============================================================ 16-bit loads/ALU =====
R16 = {0: ("b", "c"), 1: ("d", "e"), 2: ("h", "l")}
for idx, (hi, lo) in R16.items():
    opc = 0x01 + 0x10 * idx
    add_handler(opc, f"ld_{hi}{lo}_d16", pc=pc_adv(3), cycles=cyc_adv(12),
                **{hi: f"CAST(intDiv({N16}, 256) AS UInt8)", lo: f"CAST({N16} % 256 AS UInt8)"})
add_handler(0x31, "ld_sp_d16", pc=pc_adv(3), cycles=cyc_adv(12), sp=N16)

for idx, (hi, lo) in R16.items():
    pair = f"CAST(gb_{hi}(s) * 256 + gb_{lo}(s) AS UInt32)"
    inc_opc, dec_opc = 0x03 + 0x10 * idx, 0x0B + 0x10 * idx
    add_handler(inc_opc, f"inc_{hi}{lo}", pc=pc_adv(1), cycles=cyc_adv(8),
                **{hi: f"CAST(intDiv(({pair} + 1) % 65536, 256) AS UInt8)", lo: f"CAST(({pair} + 1) % 65536 % 256 AS UInt8)"})
    add_handler(dec_opc, f"dec_{hi}{lo}", pc=pc_adv(1), cycles=cyc_adv(8),
                **{hi: f"CAST(intDiv(({pair} + 65535) % 65536, 256) AS UInt8)", lo: f"CAST(({pair} + 65535) % 65536 % 256 AS UInt8)"})
add_handler(0x33, "inc_sp", pc=pc_adv(1), cycles=cyc_adv(8), sp="CAST((gb_sp(s) + 1) % 65536 AS UInt16)")
add_handler(0x3B, "dec_sp", pc=pc_adv(1), cycles=cyc_adv(8), sp="CAST((gb_sp(s) + 65535) % 65536 AS UInt16)")

HL = "gb_hl(s)"
for idx, (hi, lo) in {**R16}.items():
    src_pair = f"CAST(gb_{hi}(s) * 256 + gb_{lo}(s) AS UInt32)" if idx != 2 else f"CAST({HL} AS UInt32)"
    opc = 0x09 + 0x10 * idx
    sum_ = f"({HL} + {src_pair})"
    add_handler(opc, f"add_hl_{hi}{lo}", pc=pc_adv(1), cycles=cyc_adv(8),
                h=f"CAST(intDiv({sum_} % 65536, 256) AS UInt8)", l=f"CAST({sum_} % 65536 % 256 AS UInt8)",
                f=f"gb_mkflags(gb_flag_z(gb_f(s)), 0, CAST(({HL} % 4096) + ({src_pair} % 4096) > 4095 AS UInt8), CAST({sum_} > 65535 AS UInt8))")
add_handler(0x39, "add_hl_sp", pc=pc_adv(1), cycles=cyc_adv(8),
            h=f"CAST(intDiv(({HL} + gb_sp(s)) % 65536, 256) AS UInt8)", l=f"CAST(({HL} + gb_sp(s)) % 65536 % 256 AS UInt8)",
            f=f"gb_mkflags(gb_flag_z(gb_f(s)), 0, CAST(({HL} % 4096) + (gb_sp(s) % 4096) > 4095 AS UInt8), CAST({HL} + gb_sp(s) > 65535 AS UInt8))")

# LD (BC),A / LD (DE),A / LD A,(BC) / LD A,(DE)
add_handler(0x02, "ld_bc_mem_a", mem="gb_set(gb_mem(s), CAST(gb_bc(s) AS UInt32) + 1, gb_a(s))", pc=pc_adv(1), cycles=cyc_adv(8))
add_handler(0x12, "ld_de_mem_a", mem="gb_set(gb_mem(s), CAST(gb_de(s) AS UInt32) + 1, gb_a(s))", pc=pc_adv(1), cycles=cyc_adv(8))
add_handler(0x0A, "ld_a_bc", pc=pc_adv(1), cycles=cyc_adv(8), a="gb_read8(s, gb_bc(s))")
add_handler(0x1A, "ld_a_de", pc=pc_adv(1), cycles=cyc_adv(8), a="gb_read8(s, gb_de(s))")

# PUSH/POP for all 4 pairs (BC,DE,HL,AF)
SP_M2 = "CAST(gb_sp(s) - 2 AS UInt16)"
SP_P2 = "CAST(gb_sp(s) + 2 AS UInt16)"
for name, hi_expr, lo_expr in [("bc", "gb_b(s)", "gb_c(s)"), ("de", "gb_d(s)", "gb_e(s)"), ("hl", "gb_h(s)", "gb_l(s)")]:
    add_handler({"bc": 0xC5, "de": 0xD5, "hl": 0xE5}[name], f"push_{name}",
                mem=f"gb_set2(gb_mem(s), CAST({SP_M2} AS UInt32) + 1, {lo_expr}, CAST({SP_M2} AS UInt32) + 2, {hi_expr})",
                pc=pc_adv(1), cycles=cyc_adv(16), sp=SP_M2)
add_handler(0xF5, "push_af", mem=f"gb_set2(gb_mem(s), CAST({SP_M2} AS UInt32) + 1, gb_f(s), CAST({SP_M2} AS UInt32) + 2, gb_a(s))",
            pc=pc_adv(1), cycles=cyc_adv(16), sp=SP_M2)
POP_LO, POP_HI = "gb_read8(s, gb_sp(s))", "gb_read8(s, CAST(gb_sp(s) + 1 AS UInt16))"
add_handler(0xC1, "pop_bc", pc=pc_adv(1), cycles=cyc_adv(12), sp=SP_P2, c=POP_LO, b=POP_HI)
add_handler(0xD1, "pop_de", pc=pc_adv(1), cycles=cyc_adv(12), sp=SP_P2, e=POP_LO, d=POP_HI)
add_handler(0xE1, "pop_hl", pc=pc_adv(1), cycles=cyc_adv(12), sp=SP_P2, l=POP_LO, h=POP_HI)
add_handler(0xF1, "pop_af", pc=pc_adv(1), cycles=cyc_adv(12), sp=SP_P2, f=f"CAST(bitAnd({POP_LO}, 0xF0) AS UInt8)", a=POP_HI)

# LD (a16),SP / LD SP,HL / LD HL,SP+r8 / ADD SP,r8
add_handler(0x08, "ld_a16_sp",
            mem=f"gb_set2(gb_mem(s), CAST({N16} AS UInt32) + 1, CAST(gb_sp(s) % 256 AS UInt8), CAST({N16} AS UInt32) + 2, CAST(intDiv(gb_sp(s), 256) AS UInt8))",
            pc=pc_adv(3), cycles=cyc_adv(20))
add_handler(0xF9, "ld_sp_hl", pc=pc_adv(1), cycles=cyc_adv(8), sp=HL)
R8_OFF = f"CAST({N8} AS Int8)"
HLSP_SUM = f"CAST((CAST(gb_sp(s) AS Int32) + {R8_OFF}) % 65536 AS UInt32)"
add_handler(0xF8, "ld_hl_sp_r8", pc=pc_adv(2), cycles=cyc_adv(12),
            h=f"CAST(intDiv({HLSP_SUM}, 256) AS UInt8)", l=f"CAST({HLSP_SUM} % 256 AS UInt8)",
            f=f"gb_mkflags(0, 0, CAST((gb_sp(s) % 16) + ({R8_OFF} % 16 + 16) % 16 > 15 AS UInt8), CAST((gb_sp(s) % 256) + ({R8_OFF} % 256 + 256) % 256 > 255 AS UInt8))")
SP_R8_SUM = f"CAST((CAST(gb_sp(s) AS Int32) + {R8_OFF}) % 65536 AS UInt16)"
add_handler(0xE8, "add_sp_r8", pc=pc_adv(2), cycles=cyc_adv(16), sp=SP_R8_SUM,
            f=f"gb_mkflags(0, 0, CAST((gb_sp(s) % 16) + ({R8_OFF} % 16 + 16) % 16 > 15 AS UInt8), CAST((gb_sp(s) % 256) + ({R8_OFF} % 256 + 256) % 256 > 255 AS UInt8))")

# LDH (C),A / LD A,(C)
add_handler(0xE2, "ldh_c_a", mem="gb_set(gb_mem(s), CAST(0xFF00 + gb_c(s) AS UInt32) + 1, gb_a(s))", pc=pc_adv(1), cycles=cyc_adv(8))
add_handler(0xF2, "ldh_a_c", pc=pc_adv(1), cycles=cyc_adv(8), a="gb_read8(s, CAST(0xFF00 + gb_c(s) AS UInt16))")

# ============================================================ rotates/misc on A ====
RLCA_C = "CAST(bitShiftRight(gb_a(s), 7) AS UInt8)"
add_handler(0x07, "rlca", pc=pc_adv(1), cycles=cyc_adv(4), a=f"CAST(bitAnd(bitOr(bitShiftLeft(gb_a(s), 1), {RLCA_C}), 0xFF) AS UInt8)",
            f=f"gb_mkflags(0, 0, 0, {RLCA_C})")
RRCA_C = "CAST(bitAnd(gb_a(s), 1) AS UInt8)"
add_handler(0x0F, "rrca", pc=pc_adv(1), cycles=cyc_adv(4), a=f"CAST(bitOr(bitShiftRight(gb_a(s), 1), bitShiftLeft({RRCA_C}, 7)) AS UInt8)",
            f=f"gb_mkflags(0, 0, 0, {RRCA_C})")
RLA_C = "CAST(bitShiftRight(gb_a(s), 7) AS UInt8)"
add_handler(0x17, "rla", pc=pc_adv(1), cycles=cyc_adv(4), a=f"CAST(bitAnd(bitOr(bitShiftLeft(gb_a(s), 1), gb_flag_c(gb_f(s))), 0xFF) AS UInt8)",
            f=f"gb_mkflags(0, 0, 0, {RLA_C})")
RRA_C = "CAST(bitAnd(gb_a(s), 1) AS UInt8)"
add_handler(0x1F, "rra", pc=pc_adv(1), cycles=cyc_adv(4), a=f"CAST(bitOr(bitShiftRight(gb_a(s), 1), bitShiftLeft(gb_flag_c(gb_f(s)), 7)) AS UInt8)",
            f=f"gb_mkflags(0, 0, 0, {RRA_C})")
add_handler(0x2F, "cpl", pc=pc_adv(1), cycles=cyc_adv(4), a="CAST(bitXor(gb_a(s), 0xFF) AS UInt8)",
            f="gb_mkflags(gb_flag_z(gb_f(s)), 1, 1, gb_flag_c(gb_f(s)))")
add_handler(0x37, "scf", pc=pc_adv(1), cycles=cyc_adv(4), f="gb_mkflags(gb_flag_z(gb_f(s)), 0, 0, 1)")
add_handler(0x3F, "ccf", pc=pc_adv(1), cycles=cyc_adv(4), f="gb_mkflags(gb_flag_z(gb_f(s)), 0, 0, CAST(1 - gb_flag_c(gb_f(s)) AS UInt8))")

# DAA: BCD-adjust A after an add/sub, per the standard algorithm.
DAA_ADJ = (
    "multiIf(gb_flag_n(gb_f(s)) = 0, "
    "CAST((if(gb_flag_c(gb_f(s)) = 1 OR gb_a(s) > 0x99, 0x60, 0) + if(gb_flag_h(gb_f(s)) = 1 OR (gb_a(s) % 16) > 9, 0x06, 0)) AS UInt16), "
    "CAST((if(gb_flag_c(gb_f(s)) = 1, 0x60, 0) + if(gb_flag_h(gb_f(s)) = 1, 0x06, 0)) AS UInt16))"
)
DAA_RESULT = f"CAST(if(gb_flag_n(gb_f(s)) = 0, gb_a(s) + {DAA_ADJ}, gb_a(s) - {DAA_ADJ} + 256) % 256 AS UInt8)"
DAA_CARRY = f"CAST(gb_flag_c(gb_f(s)) = 1 OR (gb_flag_n(gb_f(s)) = 0 AND gb_a(s) > 0x99) AS UInt8)"
add_handler(0x27, "daa", pc=pc_adv(1), cycles=cyc_adv(4), a=DAA_RESULT,
            f=f"gb_mkflags(CAST({DAA_RESULT} = 0 AS UInt8), gb_flag_n(gb_f(s)), 0, {DAA_CARRY})")

# ============================================================ control flow =========
handlers.append(op("nop", pc=pc_adv(1), cycles=cyc_adv(4)))
OP_TABLE[0x00] = "nop"
handlers.append(op("halt", pc=pc_adv(1), cycles=cyc_adv(4)))  # approximated as NOP - no interrupt dispatch modeled yet
OP_TABLE[0x76] = "halt"
handlers.append(op("stop", pc=pc_adv(2), cycles=cyc_adv(4)))
OP_TABLE[0x10] = "stop"
handlers.append(op("di", pc=pc_adv(1), cycles=cyc_adv(4), ime="CAST(0 AS UInt8)"))
OP_TABLE[0xF3] = "di"
handlers.append(op("ei", pc=pc_adv(1), cycles=cyc_adv(4), ime="CAST(1 AS UInt8)"))
OP_TABLE[0xFB] = "ei"

handlers.append(op("jp_a16", pc=f"CAST({N16} AS UInt16)", cycles=cyc_adv(16)))
OP_TABLE[0xC3] = "jp_a16"
handlers.append(op("jp_hl", pc=HL, cycles=cyc_adv(4)))
OP_TABLE[0xE9] = "jp_hl"

FLAG_COND = {"nz": "gb_flag_z(gb_f(s)) = 0", "z": "gb_flag_z(gb_f(s)) = 1", "nc": "gb_flag_c(gb_f(s)) = 0", "c": "gb_flag_c(gb_f(s)) = 1"}
JP_CC_OPS = {0xC2: "nz", 0xCA: "z", 0xD2: "nc", 0xDA: "c"}
for opc, cc in JP_CC_OPS.items():
    cond = FLAG_COND[cc]
    add_handler(opc, f"jp_{cc}_a16", pc=f"CAST(if({cond}, {N16}, {PC} + 3) AS UInt16)", cycles=f"CAST({CYC} + if({cond}, 16, 12) AS UInt32)")

JR_COND_OPS = {0x20: "nz", 0x28: "z", 0x30: "nc", 0x38: "c"}
OFFSET = f"CAST({N8} AS Int8)"
for opc, cc in JR_COND_OPS.items():
    cond = FLAG_COND[cc]
    add_handler(opc, f"jr_{cc}", pc=f"CAST(if({cond}, {PC} + 2 + {OFFSET}, {PC} + 2) AS UInt16)", cycles=f"CAST({CYC} + if({cond}, 12, 8) AS UInt32)")
add_handler(0x18, "jr_d8", pc=f"CAST({PC} + 2 + {OFFSET} AS UInt16)", cycles=cyc_adv(12))

PC_P3_LO = f"CAST(({PC} + 3) % 256 AS UInt8)"
PC_P3_HI = f"CAST(intDiv({PC} + 3, 256) AS UInt8)"
CALL_PUSH_MEM = f"gb_set2(gb_mem(s), CAST({SP_M2} AS UInt32) + 1, {PC_P3_LO}, CAST({SP_M2} AS UInt32) + 2, {PC_P3_HI})"
add_handler(0xCD, "call_a16", mem=CALL_PUSH_MEM, sp=SP_M2, pc=f"CAST({N16} AS UInt16)", cycles=cyc_adv(24))
CALL_CC_OPS = {0xC4: "nz", 0xCC: "z", 0xD4: "nc", 0xDC: "c"}
for opc, cc in CALL_CC_OPS.items():
    cond = FLAG_COND[cc]
    add_handler(opc, f"call_{cc}_a16",
                mem=f"if({cond}, {CALL_PUSH_MEM}, gb_mem(s))", sp=f"CAST(if({cond}, {SP_M2}, gb_sp(s)) AS UInt16)",
                pc=f"CAST(if({cond}, {N16}, {PC} + 3) AS UInt16)", cycles=f"CAST({CYC} + if({cond}, 24, 12) AS UInt32)")

RET_LO, RET_HI = "gb_read8(s, gb_sp(s))", "gb_read8(s, CAST(gb_sp(s) + 1 AS UInt16))"
add_handler(0xC9, "ret", sp=SP_P2, pc=f"CAST({RET_LO} + {RET_HI} * 256 AS UInt16)", cycles=cyc_adv(16))
add_handler(0xD9, "reti", sp=SP_P2, pc=f"CAST({RET_LO} + {RET_HI} * 256 AS UInt16)", cycles=cyc_adv(16), ime="CAST(1 AS UInt8)")
RET_CC_OPS = {0xC0: "nz", 0xC8: "z", 0xD0: "nc", 0xD8: "c"}
for opc, cc in RET_CC_OPS.items():
    cond = FLAG_COND[cc]
    add_handler(opc, f"ret_{cc}", sp=f"CAST(if({cond}, {SP_P2}, gb_sp(s)) AS UInt16)",
                pc=f"CAST(if({cond}, {RET_LO} + {RET_HI} * 256, {PC} + 1) AS UInt16)", cycles=f"CAST({CYC} + if({cond}, 20, 8) AS UInt32)")

RST_OPS = {0xC7: 0x00, 0xCF: 0x08, 0xD7: 0x10, 0xDF: 0x18, 0xE7: 0x20, 0xEF: 0x28, 0xF7: 0x30, 0xFF: 0x38}
for opc, vec in RST_OPS.items():
    add_handler(opc, f"rst_{vec:02x}", mem=CALL_PUSH_MEM, sp=SP_M2, pc=f"CAST({vec} AS UInt16)", cycles=cyc_adv(16))

add_handler(0x2A, "ldi_a_hl", pc=pc_adv(1), cycles=cyc_adv(8), a="gb_read8(s, gb_hl(s))",
            h="CAST(intDiv((gb_hl(s) + 1) % 65536, 256) AS UInt8)", l="CAST((gb_hl(s) + 1) % 65536 % 256 AS UInt8)")
add_handler(0x3A, "ldd_a_hl", pc=pc_adv(1), cycles=cyc_adv(8), a="gb_read8(s, gb_hl(s))",
            h="CAST(intDiv((gb_hl(s) + 65535) % 65536, 256) AS UInt8)", l="CAST((gb_hl(s) + 65535) % 65536 % 256 AS UInt8)")
add_handler(0x22, "ldi_hl_a", mem="gb_set(gb_mem(s), CAST(gb_hl(s) AS UInt32) + 1, gb_a(s))", pc=pc_adv(1), cycles=cyc_adv(8),
            h="CAST(intDiv((gb_hl(s) + 1) % 65536, 256) AS UInt8)", l="CAST((gb_hl(s) + 1) % 65536 % 256 AS UInt8)")
add_handler(0x32, "ldd_hl_a", mem="gb_set(gb_mem(s), CAST(gb_hl(s) AS UInt32) + 1, gb_a(s))", pc=pc_adv(1), cycles=cyc_adv(8),
            h="CAST(intDiv((gb_hl(s) + 65535) % 65536, 256) AS UInt8)", l="CAST((gb_hl(s) + 65535) % 65536 % 256 AS UInt8)")

LDH_ADDR = f"CAST(0xFF00 + {N8} AS UInt16)"
add_handler(0xE0, "ldh_a8_a", mem=f"if({LDH_ADDR} = 0xFF44, gb_mem(s), gb_set(gb_mem(s), CAST({LDH_ADDR} AS UInt32) + 1, gb_a(s)))",
            boot_active=f"if({LDH_ADDR} = 0xFF50 AND gb_a(s) != 0, CAST(0 AS UInt8), gb_boot_active(s))",
            pc=pc_adv(2), cycles=cyc_adv(12))
add_handler(0xF0, "ldh_a_a8", pc=pc_adv(2), cycles=cyc_adv(12), a=f"gb_read8(s, {LDH_ADDR})")
LD16_ADDR = N16
add_handler(0xEA, "ld_a16_a", mem=f"if({LD16_ADDR} = 0xFF44, gb_mem(s), gb_set(gb_mem(s), CAST({LD16_ADDR} AS UInt32) + 1, gb_a(s)))",
            pc=pc_adv(3), cycles=cyc_adv(16))
add_handler(0xFA, "ld_a_a16", pc=pc_adv(3), cycles=cyc_adv(16), a=f"gb_read8(s, {N16})")

# ============================================================ CB-prefixed ==========
SHIFT_NAMES = ["rlc", "rrc", "rl", "rr", "sla", "sra", "swap", "srl"]

def shift_expr(kind, val):
    c_old = "gb_flag_c(gb_f(s))"
    if kind == "rlc":
        c_out = f"CAST(bitShiftRight({val}, 7) AS UInt8)"
        return f"CAST(bitAnd(bitOr(bitShiftLeft({val}, 1), {c_out}), 0xFF) AS UInt8)", c_out
    if kind == "rrc":
        c_out = f"CAST(bitAnd({val}, 1) AS UInt8)"
        return f"CAST(bitOr(bitShiftRight({val}, 1), bitShiftLeft({c_out}, 7)) AS UInt8)", c_out
    if kind == "rl":
        c_out = f"CAST(bitShiftRight({val}, 7) AS UInt8)"
        return f"CAST(bitAnd(bitOr(bitShiftLeft({val}, 1), {c_old}), 0xFF) AS UInt8)", c_out
    if kind == "rr":
        c_out = f"CAST(bitAnd({val}, 1) AS UInt8)"
        return f"CAST(bitOr(bitShiftRight({val}, 1), bitShiftLeft({c_old}, 7)) AS UInt8)", c_out
    if kind == "sla":
        c_out = f"CAST(bitShiftRight({val}, 7) AS UInt8)"
        return f"CAST(bitAnd(bitShiftLeft({val}, 1), 0xFF) AS UInt8)", c_out
    if kind == "sra":
        c_out = f"CAST(bitAnd({val}, 1) AS UInt8)"
        return f"CAST(bitOr(bitShiftRight({val}, 1), bitAnd({val}, 0x80)) AS UInt8)", c_out
    if kind == "swap":
        return f"CAST(bitOr(bitShiftLeft(bitAnd({val}, 0xF), 4), bitShiftRight({val}, 4)) AS UInt8)", "CAST(0 AS UInt8)"
    if kind == "srl":
        c_out = f"CAST(bitAnd({val}, 1) AS UInt8)"
        return f"CAST(bitShiftRight({val}, 1) AS UInt8)", c_out

for op_idx, kind in enumerate(SHIFT_NAMES):
    for reg in range(8):
        opc = op_idx * 8 + reg
        val = r8_get(reg)
        result, carry = shift_expr(kind, val)
        if reg == 6:
            add_cb_handler(opc, f"cb_{kind}_hl", mem=f"gb_set(gb_mem(s), CAST(gb_hl(s) AS UInt32) + 1, {result})",
                           pc=pc_adv(2), cycles=cyc_adv(16), f=f"gb_mkflags(CAST({result} = 0 AS UInt8), 0, 0, {carry})")
        else:
            add_cb_handler(opc, f"cb_{kind}_{R8[reg]}", pc=pc_adv(2), cycles=cyc_adv(8),
                           **{R8[reg]: result, "f": f"gb_mkflags(CAST({result} = 0 AS UInt8), 0, 0, {carry})"})

for b in range(8):
    for reg in range(8):
        opc = 0x40 + 8 * b + reg
        val = r8_get(reg)
        z = f"CAST(bitAnd(bitShiftRight({val}, {b}), 1) = 0 AS UInt8)"
        add_cb_handler(opc, f"cb_bit{b}_{R8[reg] if reg != 6 else 'hl'}", pc=pc_adv(2), cycles=cyc_adv(12 if reg == 6 else 8),
                       f=f"gb_mkflags({z}, 0, 1, gb_flag_c(gb_f(s)))")

for b in range(8):
    for reg in range(8):
        opc = 0x80 + 8 * b + reg
        val = r8_get(reg)
        result = f"CAST(bitAnd({val}, {(~(1 << b)) & 0xFF}) AS UInt8)"
        if reg == 6:
            add_cb_handler(opc, f"cb_res{b}_hl", mem=f"gb_set(gb_mem(s), CAST(gb_hl(s) AS UInt32) + 1, {result})", pc=pc_adv(2), cycles=cyc_adv(16))
        else:
            add_cb_handler(opc, f"cb_res{b}_{R8[reg]}", pc=pc_adv(2), cycles=cyc_adv(8), **{R8[reg]: result})

for b in range(8):
    for reg in range(8):
        opc = 0xC0 + 8 * b + reg
        val = r8_get(reg)
        result = f"CAST(bitOr({val}, {1 << b}) AS UInt8)"
        if reg == 6:
            add_cb_handler(opc, f"cb_set{b}_hl", mem=f"gb_set(gb_mem(s), CAST(gb_hl(s) AS UInt32) + 1, {result})", pc=pc_adv(2), cycles=cyc_adv(16))
        else:
            add_cb_handler(opc, f"cb_set{b}_{R8[reg]}", pc=pc_adv(2), cycles=cyc_adv(8), **{R8[reg]: result})

# ============================================================ emit ================
fallback_name, fallback_body = op("unknown", pc=pc_adv(1), cycles=cyc_adv(4))


# Tried a balanced binary-search if()-tree here instead of a flat multiIf,
# on the theory that short-circuiting would cut dispatch cost. A synthetic
# microbenchmark (trivial scalar branch bodies) showed ~1.85x speedup, but
# measured against the real dispatch table it was a ~2.3x REGRESSION
# (4.2s vs ~1.85s per 1000 cycles) - each branch body here builds/copies a
# 65536-element memory array, and short-circuit filtering apparently carries
# real per-level overhead against array-valued branches that swamps any
# savings from skipping unmatched leaves. Reverted to flat multiIf.

lines = ["-- AUTOGENERATED by gen_step.py - do not hand-edit, edit the generator instead.", ""]
for name, body in handlers:
    lines.append(f"CREATE OR REPLACE FUNCTION gb_op_{name} AS (s) -> {body};")
lines.append("")

lines.append("CREATE OR REPLACE FUNCTION gb_dispatch_cb AS (s, cb_op) ->")
lines.append("    multiIf(")
for byte, name in sorted(CB_TABLE.items()):
    lines.append(f"        cb_op = {hex(byte)}, gb_op_{name}(s),")
lines.append(f"        {fallback_body}")
lines.append("    );")
lines.append("")

lines.append("CREATE OR REPLACE FUNCTION gb_dispatch AS (s, op) ->")
lines.append("    multiIf(")
lines.append(f"        op = 0xCB, gb_dispatch_cb(s, {CB_OP}),")
for byte, name in sorted(OP_TABLE.items()):
    lines.append(f"        op = {hex(byte)}, gb_op_{name}(s),")
lines.append(f"        {fallback_body}")
lines.append("    );")
lines.append("")
lines.append("CREATE OR REPLACE FUNCTION gb_step AS (s) -> gb_dispatch(s, gb_read8(s, gb_pc(s)));")

print("\n".join(lines))
print(f"-- {len(OP_TABLE)} unprefixed + {len(CB_TABLE)} CB-prefixed opcodes implemented", flush=True)
