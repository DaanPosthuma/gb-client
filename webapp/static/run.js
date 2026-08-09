const SCREEN_W = 160, SCREEN_H = 144;
const canvas = document.getElementById("display-canvas");
const ctx = canvas.getContext("2d");
const scale = canvas.width / SCREEN_W;
const SHADE_TO_GRAY = [255, 170, 85, 0];

let runId = null;
let playing = false;

function drawFrame(shades) {
    const img = ctx.createImageData(SCREEN_W, SCREEN_H);
    for (let i = 0; i < shades.length; i++) {
        const g = SHADE_TO_GRAY[shades[i]];
        img.data[i * 4] = g; img.data[i * 4 + 1] = g; img.data[i * 4 + 2] = g; img.data[i * 4 + 3] = 255;
    }
    // Draw at native res then let CSS/canvas scaling upscale via a temp canvas.
    const tmp = document.createElement("canvas");
    tmp.width = SCREEN_W; tmp.height = SCREEN_H;
    tmp.getContext("2d").putImageData(img, 0, 0);
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(tmp, 0, 0, SCREEN_W, SCREEN_H, 0, 0, canvas.width, canvas.height);
}

function hex(v, digits) { return "0x" + v.toString(16).toUpperCase().padStart(digits, "0"); }

function renderState(s) {
    drawFrame(s.frame);
    document.getElementById("s-step").textContent = s.step_id;
    document.getElementById("s-pc").textContent = hex(s.pc, 4);
    document.getElementById("s-sp").textContent = hex(s.sp, 4);
    document.getElementById("s-cycles").textContent = s.cycles;
    document.getElementById("s-ime").textContent = s.ime;
    document.getElementById("s-boot").textContent = s.boot_active ? "active" : "unmapped";

    const regs = [["A", s.a], ["B", s.b], ["C", s.c], ["D", s.d], ["E", s.e], ["H", s.h], ["L", s.l], ["F", s.f]];
    document.getElementById("reg-grid").innerHTML = regs.map(([name, val]) =>
        `<div><span>${name}</span> = ${hex(val, 2)}</div>`
    ).join("");
}

async function loadRoms() {
    const roms = await fetch("/api/roms").then(r => r.json());
    document.getElementById("rom-select").innerHTML = roms.map(r => `<option value="${r}">${r}</option>`).join("");
}

async function newRun() {
    playing = false;
    document.getElementById("play-pause").textContent = "▶ Play";
    const rom = document.getElementById("rom-select").value;
    const res = await fetch("/api/runs", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({rom}),
    }).then(r => r.json());
    runId = res.run_id;
    document.getElementById("run-badge").textContent = `run ${runId.slice(0, 8)} (${rom})`;
    document.getElementById("run-badge").classList.add("ok");
    document.getElementById("step-frame").disabled = false;
    document.getElementById("play-pause").disabled = false;
    const state = await fetch(`/api/runs/${runId}/state`).then(r => r.json());
    renderState(state);
}

async function stepFrame() {
    if (!runId) return;
    document.getElementById("status-badge").textContent = "running frame…";
    const t0 = performance.now();
    const res = await fetch(`/api/runs/${runId}/step`, {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({frames: 1}),
    });
    const state = await res.json();
    renderState(state);
    document.getElementById("status-badge").textContent = `frame took ${((performance.now() - t0) / 1000).toFixed(1)}s`;
}

async function playLoop() {
    while (playing) {
        await stepFrame();
    }
}

// Keyboard -> joypad. Held state is tracked client-side and pushed to
// /api/runs/{id}/buttons on every change (gb_set_buttons is a cheap array rebuild,
// not a CPU step, so this is fine to call on every keydown/keyup).
const KEY_MAP = {
    ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right",
    KeyZ: "a", KeyX: "b", Enter: "start", ShiftRight: "select", ShiftLeft: "select",
};
const held = new Set();
let buttonsInFlight = false;
let buttonsDirty = false;

function renderHeldBadges() {
    const el = document.getElementById("held-buttons");
    if (!el) return;
    el.textContent = held.size ? [...held].join(" + ") : "(none)";
}

async function syncButtons() {
    if (!runId) return;
    if (buttonsInFlight) { buttonsDirty = true; return; }
    buttonsInFlight = true;
    buttonsDirty = false;
    const snapshot = [...held];
    await fetch(`/api/runs/${runId}/buttons`, {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({held: snapshot}),
    });
    buttonsInFlight = false;
    if (buttonsDirty) syncButtons();
}

window.addEventListener("keydown", (e) => {
    const btn = KEY_MAP[e.code];
    if (!btn) return;
    e.preventDefault();
    if (held.has(btn)) return;
    held.add(btn);
    renderHeldBadges();
    syncButtons();
});
window.addEventListener("keyup", (e) => {
    const btn = KEY_MAP[e.code];
    if (!btn) return;
    e.preventDefault();
    if (!held.has(btn)) return;
    held.delete(btn);
    renderHeldBadges();
    syncButtons();
});
// Releasing focus (e.g. alt-tab) mid-press would otherwise leave a button stuck held.
window.addEventListener("blur", () => {
    if (!held.size) return;
    held.clear();
    renderHeldBadges();
    syncButtons();
});

document.getElementById("new-run").onclick = newRun;
document.getElementById("step-frame").onclick = stepFrame;
document.getElementById("play-pause").onclick = () => {
    playing = !playing;
    document.getElementById("play-pause").textContent = playing ? "⏸ Pause" : "▶ Play";
    if (playing) playLoop();
};

loadRoms();
drawFrame(new Array(SCREEN_W * SCREEN_H).fill(0));
renderHeldBadges();
