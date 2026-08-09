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

document.getElementById("new-run").onclick = newRun;
document.getElementById("step-frame").onclick = stepFrame;
document.getElementById("play-pause").onclick = () => {
    playing = !playing;
    document.getElementById("play-pause").textContent = playing ? "⏸ Pause" : "▶ Play";
    if (playing) playLoop();
};

loadRoms();
drawFrame(new Array(SCREEN_W * SCREEN_H).fill(0));
