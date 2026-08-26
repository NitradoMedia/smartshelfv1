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
  toast._t = setTimeout(() => toastEl.classList.remove("show"), 3200);
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
  if (!res.ok) {
    let msg = await res.text();
    try {
      const j = JSON.parse(msg);
      msg = j.detail || msg;
    } catch { /* keep text */ }
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return res.json();
}

function openModal(id) {
  document.getElementById(id).classList.remove("hidden");
}
function closeModal(id) {
  document.getElementById(id).classList.add("hidden");
}

document.querySelectorAll("[data-close]").forEach((btn) => {
  btn.addEventListener("click", () => closeModal(btn.dataset.close));
});
document.querySelectorAll(".modal").forEach((modal) => {
  modal.addEventListener("click", (e) => {
    if (e.target === modal) modal.classList.add("hidden");
  });
});

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
    toast(err.message || "Abgleich fehlgeschlagen");
  } finally {
    btn.disabled = false;
  }
});

/* ---- Manual reconcile ---- */
document.getElementById("btn-open-manual").addEventListener("click", () => openModal("modal-manual"));

document.getElementById("form-manual").addEventListener("submit", async (e) => {
  e.preventDefault();
  const pos = document.getElementById("manual-pos").files?.[0];
  const vids = document.getElementById("manual-videos").files;
  if (!pos) {
    toast("Bitte Excel/CSV wählen");
    return;
  }
  const fd = new FormData();
  fd.append("pos_file", pos);
  if (vids) {
    for (const v of vids) fd.append("videos", v);
  }
  const btn = document.getElementById("btn-manual-submit");
  btn.disabled = true;
  try {
    const res = await fetch("/api/manual-reconcile", { method: "POST", body: fd });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Upload fehlgeschlagen");
    toast(
      `Manuell: ${data.transactions_in_file} Bons, ${data.videos_staged?.length || 0} Videos → ${data.incidents_created} Abweichungen`
    );
    closeModal("modal-manual");
    e.target.reset();
    await Promise.all([loadStats(), loadIncidents()]);
  } catch (err) {
    toast(err.message || "Manueller Abgleich fehlgeschlagen");
  } finally {
    btn.disabled = false;
  }
});

/* ---- FTP settings ---- */
async function loadFtpForm() {
  const s = await api("/api/settings");
  const f = s.ftp || {};
  document.getElementById("ftp-enabled").checked = !!f.enabled;
  document.getElementById("ftp-host").value = f.host || "";
  document.getElementById("ftp-port").value = f.port || 21;
  document.getElementById("ftp-user").value = f.user || "";
  document.getElementById("ftp-password").value = "";
  document.getElementById("ftp-password").placeholder = f.password_set
    ? "gesetzt – leer lassen zum Behalten"
    : "Passwort";
  document.getElementById("ftp-remote-dir").value = f.remote_dir || "/";
  document.getElementById("ftp-window").value = f.match_window_seconds || 180;
  document.getElementById("ftp-passive").checked = f.passive !== false;
  document.getElementById("video-source").value = s.video_source || "auto";
}

document.getElementById("btn-open-ftp").addEventListener("click", async () => {
  openModal("modal-ftp");
  try {
    await loadFtpForm();
  } catch (err) {
    toast(err.message || "Einstellungen laden fehlgeschlagen");
  }
});

async function saveFtpFromForm() {
  const body = {
    video_source: document.getElementById("video-source").value,
    ftp: {
      enabled: document.getElementById("ftp-enabled").checked,
      host: document.getElementById("ftp-host").value.trim(),
      port: Number(document.getElementById("ftp-port").value) || 21,
      user: document.getElementById("ftp-user").value.trim(),
      password: document.getElementById("ftp-password").value,
      remote_dir: document.getElementById("ftp-remote-dir").value.trim() || "/",
      passive: document.getElementById("ftp-passive").checked,
      match_window_seconds: Number(document.getElementById("ftp-window").value) || 180,
    },
  };
  if (!body.ftp.password) delete body.ftp.password;
  await api("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

document.getElementById("form-ftp").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await saveFtpFromForm();
    toast("FTP / Videoquelle gespeichert");
    await loadFtpForm();
  } catch (err) {
    toast(err.message || "Speichern fehlgeschlagen");
  }
});

document.getElementById("btn-ftp-test").addEventListener("click", async () => {
  const status = document.getElementById("ftp-status");
  try {
    await saveFtpFromForm();
    const r = await api("/api/ftp/test", { method: "POST" });
    status.classList.remove("hidden");
    status.textContent = JSON.stringify(r, null, 2);
    toast("FTP-Verbindung OK");
  } catch (err) {
    status.classList.remove("hidden");
    status.textContent = String(err.message || err);
    toast("FTP-Test fehlgeschlagen");
  }
});

document.getElementById("btn-ftp-pull").addEventListener("click", async () => {
  const btn = document.getElementById("btn-ftp-pull");
  btn.disabled = true;
  try {
    await saveFtpFromForm();
    const r = await api("/api/ftp/pull-and-scan", { method: "POST" });
    toast(`FTP-Abgleich: ${r.ingested} neu, ${r.incidents_created} Abweichungen`);
    closeModal("modal-ftp");
    await Promise.all([loadStats(), loadIncidents()]);
  } catch (err) {
    toast(err.message || "FTP-Abgleich fehlgeschlagen");
  } finally {
    btn.disabled = false;
  }
});

Promise.all([loadStats(), loadIncidents()]).catch(console.error);
setInterval(() => {
  loadStats();
  loadIncidents();
}, 15000);
