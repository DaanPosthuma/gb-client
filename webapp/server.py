"""Web app for the Game Boy-in-SQL project: a story page, an interactive boot-sequence
runner, and a SQL/database explorer. Mirrors ch-client's webapp structure. Run with:
    uv run uvicorn webapp.server:app --host 0.0.0.0 --port 8001
"""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import gb
from gb import get_client

APP_DIR = Path(__file__).parent
PROJECT_DIR = APP_DIR.parent
ROMS_DIR = PROJECT_DIR / "roms"

app = FastAPI(title="Game Boy in SQL")
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")

_client = None


def client():
    global _client
    if _client is None:
        _client = get_client()
        gb.load_sql(_client)
    return _client


def _page(name: str) -> HTMLResponse:
    return HTMLResponse((APP_DIR / "static" / name).read_text())


@app.get("/", response_class=HTMLResponse)
def home():
    return _page("story.html")


@app.get("/run", response_class=HTMLResponse)
def run_page():
    return _page("run.html")


@app.get("/explore", response_class=HTMLResponse)
def explore_page():
    return _page("explore.html")


@app.get("/favicon.ico")
def favicon():
    raise HTTPException(404)


@app.get("/api/roms")
def list_roms():
    return sorted(p.name for p in ROMS_DIR.glob("*.gb"))


class NewRunRequest(BaseModel):
    rom: str


@app.post("/api/runs")
def create_run(req: NewRunRequest):
    cart_path = ROMS_DIR / req.rom
    if not cart_path.is_file() or cart_path.suffix != ".gb":
        raise HTTPException(404, f"No such cartridge: {req.rom}")
    boot_path = ROMS_DIR / "dmg_boot.bin"
    run_id = gb.new_run(client(), boot_path.read_bytes(), cart_path.read_bytes(), rom_name=req.rom)
    return {"run_id": str(run_id)}


@app.get("/api/runs")
def list_runs(limit: int = 20):
    result = client().query(
        """
        SELECT run_id, min(step_id) AS created_step, max(step_id) AS steps
        FROM gb.state
        GROUP BY run_id
        ORDER BY created_step DESC
        LIMIT {limit:UInt32}
        """,
        parameters={"limit": limit},
    )
    return [dict(zip(result.column_names, row)) for row in result.result_rows]


def _state_json(row: dict, shades: list[int]) -> dict:
    return {
        "step_id": row["step_id"],
        "pc": row["pc"], "sp": row["sp"],
        "a": row["a"], "b": row["b"], "c": row["c"], "d": row["d"],
        "e": row["e"], "h": row["h"], "l": row["l"], "f": row["f"],
        "ime": row["ime"], "cycles": row["cycles"], "boot_active": row["boot_active"],
        "frame": shades,
    }


class StepRequest(BaseModel):
    frames: int = 1


@app.post("/api/runs/{run_id}/step")
def step_run(run_id: str, req: StepRequest):
    for _ in range(max(1, req.frames)):
        gb.run_batch(client(), run_id, gb.CYCLES_PER_FRAME)
    row = gb.get_state_row(client(), run_id)
    shades = gb.render_frame_shades(client(), run_id)
    return _state_json(row, shades)


@app.get("/api/runs/{run_id}/state")
def get_run_state(run_id: str):
    row = gb.get_state_row(client(), run_id)
    shades = gb.render_frame_shades(client(), run_id)
    return _state_json(row, shades)


READONLY_PREFIXES = ("SELECT", "SHOW", "DESCRIBE", "DESC", "EXPLAIN", "WITH")


class SqlRequest(BaseModel):
    query: str


@app.post("/api/sql")
def run_sql(req: SqlRequest):
    stripped = req.query.strip().rstrip(";").strip()
    if not stripped.upper().startswith(READONLY_PREFIXES):
        raise HTTPException(400, "Only read-only queries are allowed here (SELECT/SHOW/DESCRIBE/EXPLAIN/WITH).")
    try:
        result = client().query(stripped)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc))
    return {"columns": result.column_names, "rows": [list(row) for row in result.result_rows]}


@app.get("/api/schema/tables")
def schema_tables():
    result = client().query(
        """
        SELECT database, name, engine, total_rows, formatReadableSize(total_bytes) AS size
        FROM system.tables WHERE database = 'gb' ORDER BY name
        """
    )
    return [dict(zip(result.column_names, row)) for row in result.result_rows]


@app.get("/api/schema/functions")
def schema_functions():
    result = client().query(
        "SELECT name, create_query FROM system.functions WHERE name LIKE 'gb_%' ORDER BY name"
    )
    return [dict(zip(result.column_names, row)) for row in result.result_rows]


SOURCE_FILES = {
    "00_helpers.sql": gb.SQL_DIR / "00_helpers.sql",
    "00b_write.sql": gb.SQL_DIR / "00b_write.sql",
    "01_step.sql": gb.SQL_DIR / "01_step.sql",
    "02_table.sql": gb.SQL_DIR / "02_table.sql",
    "03_ppu.sql": gb.SQL_DIR / "03_ppu.sql",
    "gen_step.py": gb.SQL_DIR / "gen_step.py",
    "driver.py": gb.SQL_DIR / "__init__.py",
}


@app.get("/api/source/{name}")
def get_source(name: str):
    path = SOURCE_FILES.get(name)
    if path is None:
        raise HTTPException(404, "Unknown source file")
    return {"name": name, "text": path.read_text()}
