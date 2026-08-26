const state = {
  filter: "",
  incidents: [],
  selectedId: null,
};

const listEl = document.getElementById("incident-list");
const emptyEl = document.getElementById("empty");
const detailEl = document.getElementById("detail");
const toastEl = document.getElementById("toast");

function toast(msg) {
  toastEl.textContent = msg;
  toastEl.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => toastEl.classList.remove("show"), 2800);
}

function statusLabel(s) {
  return { open: "Offen", theft: "Diebstahl", false_alarm: "Fehlalarm" }[s] || s;
}

function fmtTime(iso) {
  try {
    return new Date(iso).toLocaleString("de-DE", {
      day: "2-digit", month: "2-digit", year: "numeric",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    });
  } catch {
    return iso;
  }
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

async function loadStats() {
  const s = await api("/api/incidents/stats");
  document.querySelectorAll(".stat").forEach((el) => {
    const key = el.dataset.key;
    el.querySelector(".stat-value").textContent = s[key] ?? "–";
  });
}

async function loadIncidents() {
  const q = state.filter ? `?status=${encodeURIComponent(state.filter)}` : "";
  state.incidents = await api(`/api/incidents${q}`);
  renderList();
  if (state.selectedId) {
    const still = state.incidents.find((i) => i.id === state.selectedId);
    if (still) renderDetail(still);
    else {
      state.selectedId = null;
      detailEl.innerHTML = `<div class="detail-placeholder"><p>Vorfall wählen, um Videoausschnitt und Bon-Abgleich zu sehen.</p></div>`;
    }
  }
}

function renderList() {
  listEl.innerHTML = "";
  emptyEl.classList.toggle("hidden", state.incidents.length > 0);
  state.incidents.forEach((inc) => {
    const btn = document.createElement("button");
    btn.className = `incident${inc.id === state.selectedId ? " active" : ""}`;
    btn.type = "button";
    const diffClass = inc.difference > 0 ? "pos" : "neg";
    const diffText =
      inc.difference > 0
        ? `+${inc.difference} mehr im Video`
        : `${inc.difference} weniger im Video`;
    btn.innerHTML = `
      <div class="incident-top">
        <span class="bon-id">Bon ${inc.external_id}</span>
        <span class="badge ${inc.status}">${statusLabel(inc.status)}</span>
      </div>
      <div class="meta">${fmtTime(inc.receipt_time)} · Bon ${inc.receipt_articles} · KI ${inc.ai_articles}</div>
      <div class="diff ${diffClass}">${diffText}</div>`;
    btn.addEventListener("click", () => {
      state.selectedId = inc.id;
      renderList();
      renderDetail(inc);
    });
    listEl.appendChild(btn);
  });
}

function renderDetail(inc) {
  detailEl.innerHTML = `
    <div class="detail-body">
      <div class="video-wrap">
        ${inc.clip_url ? `<video src="${inc.clip_url}" controls autoplay muted playsinline></video>` : `<div class="detail-placeholder"><p>Kein Videoausschnitt</p></div>`}
      </div>
      <div class="detail-info">
        <h2>Bon ${inc.external_id}</h2>
        <p class="meta">${fmtTime(inc.receipt_time)} · Backend: ${inc.ai_backend || "–"}</p>
        <div class="compare">
          <div><span>Bon-Artikel</span><strong>${inc.receipt_articles}</strong></div>
          <div><span>KI-Zählung</span><strong>${inc.ai_articles}</strong></div>
          <div><span>Differenz</span><strong style="color:${inc.difference ? "var(--danger)" : "var(--ok)"}">${inc.difference > 0 ? "+" : ""}${inc.difference}</strong></div>
        </div>
        <textarea class="notes" id="notes" placeholder="Notiz (optional)…">${inc.notes || ""}</textarea>
        <div class="actions">
          <button class="btn ok" type="button" data-action="false_alarm">Als Fehlalarm</button>
          <button class="btn danger" type="button" data-action="theft">Als Diebstahl</button>
          <button class="btn ghost" type="button" data-action="open">Wieder öffnen</button>
        </div>
      </div>
    </div>`;
  detailEl.querySelectorAll("[data-action]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const status = btn.dataset.action;
      const notes = detailEl.querySelector("#notes").value;
      await api(`/api/incidents/${inc.id}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status, notes }),
      });
      toast(status === "theft" ? "Als Diebstahl markiert" : status === "false_alarm" ? "Als Fehlalarm geschlossen" : "Wieder geöffnet");
      await Promise.all([loadStats(), loadIncidents()]);
    });
  });
}

document.querySelectorAll(".filter").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".filter").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    state.filter = btn.dataset.status;
    loadIncidents();
  });
});

document.getElementById("btn-scan").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  btn.disabled = true;
  try {
    const r = await api("/api/scan", { method: "POST" });
    toast(`Abgleich: ${r.ingested} neu, ${r.incidents_created} Abweichungen`);
    await Promise.all([loadStats(), loadIncidents()]);
  } catch (err) {
    toast("Abgleich fehlgeschlagen");
    console.error(err);
  } finally {
    btn.disabled = false;
  }
});

document.getElementById("pos-upload").addEventListener("change", async (e) => {
  const file = e.target.files?.[0];
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  try {
    const res = await fetch("/api/upload-pos", { method: "POST", body: fd });
    const data = await res.json();
    toast(`Datei geladen: ${data.ingested} Transaktionen, ${data.incidents_created} Abweichungen`);
    await Promise.all([loadStats(), loadIncidents()]);
  } catch (err) {
    toast("Upload fehlgeschlagen");
    console.error(err);
  } finally {
    e.target.value = "";
  }
});

Promise.all([loadStats(), loadIncidents()]).catch(console.error);
setInterval(() => {
  loadStats();
  loadIncidents();
}, 15000);
