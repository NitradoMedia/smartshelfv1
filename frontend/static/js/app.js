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
        ${inc.clip_url ? `<video controls autoplay muted playsinline preload="metadata">
          <source src="${inc.clip_url}" type="video/mp4" />
          Video nicht abspielbar.
        </video>` : `<div class="detail-placeholder"><p>Kein Videoausschnitt</p></div>`}
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
async function loadManualLibrary() {
  const box = document.getElementById("manual-library");
  try {
    const data = await api("/api/videos");
    const vids = data.videos || [];
    if (!vids.length) {
      box.innerHTML = `<p class="meta">Noch keine gespeicherten Aufnahmen. Zuerst unter „Aufnahme“ aufzeichnen oder Videos hochladen.</p>`;
      return;
    }
    box.innerHTML = vids.map((v) => `
      <label class="pick">
        <input type="checkbox" name="selected_videos" value="${v.id}" />
        <span><strong>${v.name}</strong> <span class="meta">(${v.source}, ${(v.size/1024/1024).toFixed(2)} MB)</span></span>
      </label>`).join("");
  } catch (err) {
    box.innerHTML = `<p class="err-text">${err.message || "Bibliothek laden fehlgeschlagen"}</p>`;
  }
}

document.getElementById("btn-open-manual").addEventListener("click", async () => {
  openModal("modal-manual");
  await loadManualLibrary();
});

document.getElementById("form-manual").addEventListener("submit", async (e) => {
  e.preventDefault();
  const pos = document.getElementById("manual-pos").files?.[0];
  const vids = document.getElementById("manual-videos").files;
  const selected = [...document.querySelectorAll('#manual-library input[name="selected_videos"]:checked')].map((el) => el.value);
  if (!pos) {
    toast("Bitte Excel/CSV wählen");
    return;
  }
  if ((!vids || !vids.length) && !selected.length) {
    toast("Bitte Video hochladen oder Aufnahme auswählen");
    return;
  }
  const fd = new FormData();
  fd.append("pos_file", pos);
  if (vids) {
    for (const v of vids) fd.append("videos", v);
  }
  for (const ref of selected) fd.append("selected_videos", ref);
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

/* ---- RTSP recording ---- */
let recPoll = null;
let previewPoll = null;
let previewSessionId = null;
let recPreviewSessionId = null;
const rtspSources = { list: [] };

function setPreviewImage(imgId, hintId, url) {
  const img = document.getElementById(imgId);
  const hint = document.getElementById(hintId);
  if (!url) {
    img.classList.add("hidden");
    img.removeAttribute("src");
    hint.classList.remove("hidden");
    return;
  }
  hint.classList.add("hidden");
  img.classList.remove("hidden");
  img.src = `${url}?t=${Date.now()}`;
}

async function stopRtspPreview() {
  if (previewPoll) {
    clearInterval(previewPoll);
    previewPoll = null;
  }
  if (previewSessionId) {
    try {
      await api(`/api/preview/${previewSessionId}/stop`, { method: "POST" });
    } catch { /* ignore */ }
    previewSessionId = null;
  }
  setPreviewImage("preview-rtsp-img", "preview-rtsp-hint", null);
  document.getElementById("preview-rtsp-hint").textContent = "URL eintragen und „Vorschau“ klicken.";
}

document.getElementById("btn-preview-start").addEventListener("click", async () => {
  const url = document.getElementById("rec-url").value.trim();
  if (!url) {
    toast("Bitte RTSP-URL eintragen");
    return;
  }
  refreshNetDiag().catch(() => {});
  const btn = document.getElementById("btn-preview-start");
  btn.disabled = true;
  document.getElementById("preview-rtsp-hint").textContent = "Verbinde…";
  try {
    await stopRtspPreview();
    const sess = await api("/api/preview/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, label: document.getElementById("rec-name").value.trim() || "Vorschau" }),
    });
    previewSessionId = sess.id;
    setPreviewImage("preview-rtsp-img", "preview-rtsp-hint", sess.frame_url);
    previewPoll = setInterval(() => {
      if (previewSessionId) {
        setPreviewImage("preview-rtsp-img", "preview-rtsp-hint", `/api/preview/${previewSessionId}/frame.jpg`);
      }
    }, 500);
    toast("Vorschau aktiv");
  } catch (err) {
    document.getElementById("preview-rtsp-hint").textContent = err.message || "Vorschau fehlgeschlagen";
    toast(err.message || "Vorschau fehlgeschlagen");
  } finally {
    btn.disabled = false;
  }
});

document.getElementById("btn-preview-stop").addEventListener("click", async () => {
  await stopRtspPreview();
  toast("Vorschau gestoppt");
});

async function refreshRecordPanel() {
  const data = await api("/api/recordings");
  const src = await api("/api/rtsp/sources");
  rtspSources.list = src.sources || [];

  const sel = document.getElementById("rec-source");
  const cur = sel.value;
  sel.innerHTML = `<option value="">— neue URL —</option>` +
    rtspSources.list.map((s) => `<option value="${s.name}">${s.name}</option>`).join("");
  if ([...sel.options].some((o) => o.value === cur)) sel.value = cur;

  const activeEl = document.getElementById("rec-active");
  const active = (data.active || []).filter((j) =>
    j.status === "recording" || j.status === "stopping"
  );
  const failed = (data.active || []).filter((j) => j.status === "failed").slice(0, 3);

  // recording preview
  const liveRec = active.find((j) => j.preview_frame_url) || active[0];
  if (liveRec && liveRec.preview_frame_url) {
    recPreviewSessionId = liveRec.preview_id || recPreviewSessionId;
    setPreviewImage("preview-rec-img", "preview-rec-hint", liveRec.preview_frame_url);
  } else if (liveRec && liveRec.id) {
    // try attach preview if missing
    try {
      const sess = await api(`/api/preview/recording/${liveRec.id}/start`, { method: "POST" });
      recPreviewSessionId = sess.id;
      setPreviewImage("preview-rec-img", "preview-rec-hint", sess.frame_url);
    } catch {
      document.getElementById("preview-rec-hint").textContent = "Warte auf erste Frames…";
    }
  } else if (!active.length) {
    setPreviewImage("preview-rec-img", "preview-rec-hint", null);
    document.getElementById("preview-rec-hint").textContent = "Startet automatisch mit der Aufnahme.";
  }

  if (!active.length) {
    activeEl.innerHTML = `<p class="meta">Keine laufende Aufnahme.</p>` +
      (failed.length ? failed.map((j) => `
        <div class="rec-item">
          <div>
            <strong>${j.name}</strong> <span class="badge open">fehlgeschlagen</span>
            <div class="err-text">${j.error || "unbekannt"}</div>
          </div>
        </div>`).join("") : "");
  } else {
    activeEl.innerHTML = active.map((j) => `
      <div class="rec-item">
        <div>
          <span class="rec-dot"></span><strong>${j.name}</strong>
          <div class="meta">${j.elapsed_seconds ?? 0}s${j.max_seconds ? ` / max ${j.max_seconds}s` : ""} · ${(j.bytes_written/1024).toFixed(0)} KB · ${j.rtsp_url}</div>
          ${j.error ? `<div class="err-text">${j.error}</div>` : ""}
        </div>
        <div class="rec-actions">
          <button type="button" class="btn danger" data-stop="${j.id}">Stoppen</button>
        </div>
      </div>`).join("");
    activeEl.querySelectorAll("[data-stop]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        try {
          await api(`/api/recordings/${btn.dataset.stop}/stop`, { method: "POST" });
          toast("Aufnahme gestoppt");
          await refreshRecordPanel();
        } catch (err) {
          toast(err.message || "Stop fehlgeschlagen");
        }
      });
    });
  }

  const filesEl = document.getElementById("rec-files");
  const files = data.files || [];
  if (!files.length) {
    filesEl.innerHTML = `<p class="meta">Noch keine Dateien.</p>`;
  } else {
    filesEl.innerHTML = files.slice(0, 20).map((f) => `
      <div class="rec-item">
        <div>
          <span class="rec-dot off"></span><strong>${f.name}</strong>
          <div class="meta">${(f.size / 1024 / 1024).toFixed(2)} MB</div>
        </div>
        <div class="rec-actions">
          <a class="btn ghost" href="${f.url}" target="_blank" rel="noopener">Öffnen</a>
          <button type="button" class="btn primary" data-use="${f.source}:${f.name}">Für Analyse</button>
          <button type="button" class="btn ghost" data-del="${f.name}">Löschen</button>
        </div>
      </div>`).join("");
    filesEl.querySelectorAll("[data-use]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        closeModal("modal-record");
        openModal("modal-manual");
        await loadManualLibrary();
        const ref = btn.dataset.use;
        const box = document.querySelector(`#manual-library input[value="${ref.replace(/"/g, '\\"')}"]`);
        if (box) box.checked = true;
        toast("Aufnahme ausgewählt – Excel wählen und abgleichen");
      });
    });
    filesEl.querySelectorAll("[data-del]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (!confirm(`Datei ${btn.dataset.del} löschen?`)) return;
        try {
          await api(`/api/recordings/file/${encodeURIComponent(btn.dataset.del)}`, { method: "DELETE" });
          toast("Datei gelöscht");
          await refreshRecordPanel();
        } catch (err) {
          toast(err.message || "Löschen fehlgeschlagen");
        }
      });
    });
  }
}

document.getElementById("btn-open-record").addEventListener("click", async () => {
  openModal("modal-record");
  try {
    await refreshRecordPanel();
    await refreshNetDiag();
  } catch (err) {
    toast(err.message || "Aufnahmen laden fehlgeschlagen");
  }
  clearInterval(recPoll);
  recPoll = setInterval(() => {
    if (!document.getElementById("modal-record").classList.contains("hidden")) {
      refreshRecordPanel().catch(() => {});
    }
  }, 1500);
});

async function refreshNetDiag() {
  const box = document.getElementById("net-diag");
  if (!box) return;
  const url = document.getElementById("rec-url").value.trim() || "rtsp://admin:admin@192.168.1.32:554/11";
  try {
    const d = await api("/api/network/diagnose", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const tcp = d.tcp ? (d.tcp.ok ? "OK" : `FEHLER – ${d.tcp.detail}`) : "–";
    box.className = "net-diag" + (d.tcp && d.tcp.ok ? " ok" : "");
    box.textContent =
      `Server: ${(d.local_ips || []).join(", ") || d.hostname}` +
      `${d.looks_like_cloud ? " (Cloud – kein Heimnetz)" : " (lokales Netz)"}\n` +
      `Kamera ${d.rtsp_host || "?"}:${d.rtsp_port || 554} → ${tcp}\n\n` +
      (d.recommendation || "");
  } catch (err) {
    box.className = "net-diag";
    box.textContent = err.message || "Netz-Diagnose fehlgeschlagen";
  }
}

document.getElementById("rec-url").addEventListener("change", () => refreshNetDiag());

document.getElementById("rec-source").addEventListener("change", () => {
  const name = document.getElementById("rec-source").value;
  const found = rtspSources.list.find((s) => s.name === name);
  if (found) {
    document.getElementById("rec-name").value = found.name;
    document.getElementById("rec-url").value = found.url;
  }
});

document.getElementById("form-record").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = document.getElementById("btn-rec-start");
  const maxVal = document.getElementById("rec-max").value;
  const body = {
    name: document.getElementById("rec-name").value.trim() || "kamera",
    url: document.getElementById("rec-url").value.trim(),
    max_seconds: maxVal ? Number(maxVal) : null,
    copy_to_videos: document.getElementById("rec-copy-videos").checked,
    save_source: true,
  };
  if (!body.url) {
    toast("Bitte RTSP-URL eintragen");
    return;
  }
  btn.disabled = true;
  try {
    const job = await api("/api/recordings/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    toast(`Aufnahme gestartet (${job.id})`);
    await refreshRecordPanel();
  } catch (err) {
    toast(err.message || "Start fehlgeschlagen");
  } finally {
    btn.disabled = false;
  }
});

Promise.all([loadStats(), loadIncidents()]).catch(console.error);
setInterval(() => {
  loadStats();
  loadIncidents();
}, 15000);
