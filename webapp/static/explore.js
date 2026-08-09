function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
}

function fmtCell(v) {
    if (Array.isArray(v)) {
        if (v.length > 12) return `[${v.slice(0, 12).join(", ")}, &hellip; (${v.length})]`;
        return `[${v.join(", ")}]`;
    }
    return escapeHtml(v);
}

async function runSql(query) {
    const errBox = document.getElementById("sql-error");
    errBox.style.display = "none";
    const res = await fetch("/api/sql", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({query}),
    });
    const body = await res.json();
    if (!res.ok) {
        errBox.textContent = body.detail || "Query failed";
        errBox.style.display = "block";
        document.querySelector("#sql-results thead").innerHTML = "";
        document.querySelector("#sql-results tbody").innerHTML = "";
        return;
    }
    document.querySelector("#sql-results thead").innerHTML =
        "<tr>" + body.columns.map(c => `<th>${escapeHtml(c)}</th>`).join("") + "</tr>";
    document.querySelector("#sql-results tbody").innerHTML =
        body.rows.map(row => "<tr>" + row.map(v => `<td>${fmtCell(v)}</td>`).join("") + "</tr>").join("")
        || `<tr><td colspan="${body.columns.length || 1}" style="color:var(--text-dim)">no rows</td></tr>`;
}

document.getElementById("run-sql").onclick = () => runSql(document.getElementById("sql-input").value);
document.querySelectorAll(".canned").forEach(btn => {
    btn.onclick = () => {
        document.getElementById("sql-input").value = btn.dataset.q;
        runSql(btn.dataset.q);
    };
});

// --- tabs ---
document.querySelectorAll(".tab-btn").forEach(btn => {
    btn.onclick = () => {
        document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        ["sql", "schema", "source"].forEach(t => {
            document.getElementById("tab-" + t).style.display = t === btn.dataset.tab ? "block" : "none";
        });
        if (btn.dataset.tab === "schema") loadSchema();
    };
});

async function loadSchema() {
    const tables = await fetch("/api/schema/tables").then(r => r.json());
    document.querySelector("#tables-table tbody").innerHTML = tables.map(t =>
        `<tr><td>${t.name}</td><td>${t.engine}</td><td>${t.total_rows}</td><td>${t.size}</td></tr>`
    ).join("");

    const fns = await fetch("/api/schema/functions").then(r => r.json());
    document.getElementById("fn-count").textContent = fns.length;
    document.getElementById("functions-list").innerHTML = fns.map(f => `
        <details style="margin-bottom:6px">
            <summary style="cursor:pointer;font-family:var(--mono);font-size:13px">${f.name}</summary>
            <pre style="margin-top:6px"><code>${escapeHtml(f.create_query)}</code></pre>
        </details>
    `).join("");
}

// --- source viewer ---
document.querySelectorAll(".src-btn").forEach(btn => {
    btn.onclick = async () => {
        document.querySelectorAll(".src-btn").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        const data = await fetch(`/api/source/${btn.dataset.f}`).then(r => r.json());
        document.getElementById("source-code").textContent = data.text;
    };
});
document.querySelector(".src-btn").click();
