"""Game Boy emulator whose CPU/PPU logic lives entirely in ClickHouse SQL, same
architecture as the CHIP-8 project. Scoped for now to exactly what SameBoy's
open-source DMG boot ROM needs (~35 opcodes, background-only PPU) - see 01_step.sql
and 03_ppu.sql for the "why this subset" notes.
"""

import os
import uuid
from pathlib import Path

import clickhouse_connect


def get_client(**overrides):
    params = {"host": os.environ.get("CH_HOST", "localhost"), "port": int(os.environ.get("CH_HTTP_PORT", "8123"))}
    params.update(overrides)
    return clickhouse_connect.get_client(**params)


SQL_DIR = Path(__file__).parent
SQL_FILES = ["00_helpers.sql", "00b_write.sql", "01_step.sql", "02_table.sql", "03_ppu.sql"]

MEM_SIZE = 65536
SCREEN_W, SCREEN_H = 160, 144
CYCLES_PER_FRAME = 70224  # 154 scanlines * 456 T-cycles

STATE_COLUMNS = ["run_id", "step_id", "state"]

# 2-bit shade index (0=lightest..3=darkest) -> 8-bit grayscale for display.
SHADE_TO_GRAY = [255, 170, 85, 0]


def load_sql(client) -> None:
    # Naive ';'-splitting breaks as soon as a comment contains a semicolon (it did:
    # see 00_helpers.sql's PPU-timing comment) - shell out to the real client instead
    # of reimplementing a SQL tokenizer.
    import subprocess

    for filename in SQL_FILES:
        subprocess.run(
            ["/workspace/ClickHouse/build-relwithdebinfo/programs/clickhouse", "client",
             "--host", "127.0.0.1", "--multiquery"],
            stdin=(SQL_DIR / filename).open("rb"),
            check=True, capture_output=True,
        )


def new_run(client, boot_rom: bytes, cart_rom: bytes, rom_name: str = "") -> uuid.UUID:
    if len(boot_rom) != 256:
        raise ValueError(f"boot ROM must be exactly 256 bytes, got {len(boot_rom)}")
    if len(cart_rom) > MEM_SIZE:
        raise ValueError(f"cartridge too large: {len(cart_rom)} bytes")

    mem = bytearray(MEM_SIZE)
    mem[: len(cart_rom)] = cart_rom

    run_id = uuid.uuid4()
    state = (list(mem), list(boot_rom), 1, 0, 0, 0, 0, 0, 0, 0, 0, 0xFFFE, 0x0000, 0, 0)
    client.insert("gb.state", [[run_id, 0, state]], column_names=STATE_COLUMNS)
    return run_id


def run_batch(client, run_id: uuid.UUID, cycles: int) -> None:
    # `state` is a single Tuple column specifically so this reads/writes it exactly
    # once each - with ~500 opcodes implemented, gb_run_batch's own expansion is large
    # enough that extracting 15 fields the old way (referencing its result 15 times)
    # blew past ClickHouse's query-tree size limit. See 02_table.sql's comment.
    query = """
        INSERT INTO gb.state (run_id, step_id, state)
        SELECT run_id, step_id + 1, gb_run_batch(state, {cycles:UInt32})
        FROM gb.state
        WHERE run_id = {run_id:UUID}
        ORDER BY step_id DESC
        LIMIT 1
    """
    client.command(query, parameters={"run_id": str(run_id), "cycles": cycles})


def get_state_row(client, run_id: uuid.UUID):
    # Selects the MATERIALIZED convenience columns (cheap: each is one tuple-element
    # access), not the raw `state` tuple - avoids pulling the 64KB mem array back to
    # Python just to check registers.
    cols = "step_id, a, b, c, d, e, h, l, f, sp, pc, ime, cycles, boot_active, opcode"
    result = client.query(
        f"SELECT {cols} FROM gb.state WHERE run_id = {{run_id:UUID}} ORDER BY step_id DESC LIMIT 1",
        parameters={"run_id": str(run_id)},
    )
    return dict(zip(result.column_names, result.result_rows[0]))


def render_frame_shades(client, run_id: uuid.UUID) -> list[int]:
    result = client.query(
        "SELECT gb_render_frame(state) AS frame FROM gb.state WHERE run_id = {run_id:UUID} ORDER BY step_id DESC LIMIT 1",
        parameters={"run_id": str(run_id)},
    )
    return result.result_rows[0][0]


def render_ascii(shades: list[int]) -> str:
    chars = " .:#"
    lines = []
    for row in range(SCREEN_H):
        lines.append("".join(chars[shades[row * SCREEN_W + col]] for col in range(SCREEN_W)))
    return "\n".join(lines)


def render_png(shades: list[int], path: Path, scale: int = 4) -> None:
    from PIL import Image

    img = Image.new("L", (SCREEN_W, SCREEN_H))
    img.putdata([SHADE_TO_GRAY[s] for s in shades])
    img = img.resize((SCREEN_W * scale, SCREEN_H * scale), Image.NEAREST)
    img.save(path)


def run_rom(boot_rom_path: Path, cart_path: Path, frames: int, png_path: Path | None = None) -> str:
    client = get_client()
    load_sql(client)
    run_id = new_run(client, boot_rom_path.read_bytes(), cart_path.read_bytes(), rom_name=cart_path.name)

    last_render = ""
    last_shades = None
    for _ in range(frames):
        run_batch(client, run_id, CYCLES_PER_FRAME)
        last_shades = render_frame_shades(client, run_id)
        last_render = render_ascii(last_shades)
    if png_path is not None and last_shades is not None:
        render_png(last_shades, png_path)
    return last_render


if __name__ == "__main__":
    import sys

    boot_path = SQL_DIR.parent / "roms" / "dmg_boot.bin"
    cart_path = Path(sys.argv[1]) if len(sys.argv) > 1 else SQL_DIR.parent / "roms" / "minimal_cart.gb"
    frames = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    png_path = Path(sys.argv[3]) if len(sys.argv) > 3 else None
    print(run_rom(boot_path, cart_path, frames=frames, png_path=png_path))
