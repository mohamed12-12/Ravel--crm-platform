const state = {
  sessionId: null,
  session: null,
};

const els = {
  runtimeWorkbook: document.getElementById("runtime-workbook"),
  sourceWorkbook: document.getElementById("source-workbook"),
  travelerCount: document.getElementById("traveler-count"),
  interactionCount: document.getElementById("interaction-count"),
  tripStatusList: document.getElementById("trip-status-list"),
  leadCount: document.getElementById("lead-count"),
  qualificationRate: document.getElementById("qualification-rate"),
  urgentCount: document.getElementById("urgent-count"),
  dueTodayCount: document.getElementById("due-today-count"),
  leadStageList: document.getElementById("lead-stage-list"),
  recentLeads: document.getElementById("recent-leads"),
  bookingDraftCount: document.getElementById("booking-draft-count"),
  paymentPendingCount: document.getElementById("payment-pending-count"),
  bookingAlertCount: document.getElementById("booking-alert-count"),
  chatLog: document.getElementById("chat-log"),
  messageForm: document.getElementById("message-form"),
  messageInput: document.getElementById("message-input"),
  sendBtn: document.getElementById("send-btn"),
  newSessionBtn: document.getElementById("new-session-btn"),
  resetDemoBtn: document.getElementById("reset-demo-btn"),
  sessionChip: document.getElementById("session-chip"),
  crmSnapshot: document.getElementById("crm-snapshot"),
  tripResult: document.getElementById("trip-result"),
  writeResult: document.getElementById("write-result"),
  leadResult: document.getElementById("lead-result"),
  bookingFormPanel: document.getElementById("booking-form-panel"),
  bookingResult: document.getElementById("booking-result"),
  crmBrowser: document.getElementById("crm-browser"),
  leadActions: document.getElementById("lead-actions"),
  qualifyLeadBtn: document.getElementById("qualify-lead-btn"),
};

async function api(path, options = {}) {
  try {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    if (!response.ok) {
      const errData = await response.json().catch(() => ({}));
      throw new Error(errData.error || `Request failed: ${response.status}`);
    }
    return await response.json();
  } catch (err) {
    console.error(`API Error [${path}]:`, err);
    throw err;
  }
}

function escapeHtml(text) {
  return String(text || "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
}

function tagClassForResult(label) {
  const text = String(label || "").toLowerCase();
  if (text.includes("black") || text.includes("danger") || text.includes("handoff")) return "danger";
  if (text.includes("warn") || text.includes("review") || text.includes("tbd")) return "warn";
  return "ok";
}

function renderStats(stats) {
  if (!stats) return;
  if (els.travelerCount) els.travelerCount.textContent = stats.travelerCount ?? 0;
  if (els.interactionCount) els.interactionCount.textContent = stats.interactionCount ?? 0;
  if (els.leadCount) els.leadCount.textContent = stats.leadCount ?? 0;
  if (els.qualificationRate) els.qualificationRate.textContent = `${stats.pipeline?.qualificationRate ?? 0}%`;
  
  if (els.tripStatusList) {
    els.tripStatusList.innerHTML = "";
    Object.entries(stats.tripStatusCounts || {}).forEach(([s, c]) => {
      const div = document.createElement("div");
      div.className = "trip-status-item";
      div.innerHTML = `<span>${s}</span><strong>${c}</strong>`;
      els.tripStatusList.appendChild(div);
    });
  }
}

function renderMessages(messages) {
  if (!els.chatLog) return;
  els.chatLog.innerHTML = "";
  (messages || []).forEach((m) => {
    const div = document.createElement("div");
    div.className = `chat-bubble ${m.role}`;
    div.innerHTML = `<p>${escapeHtml(m.text)}</p>`;
    els.chatLog.appendChild(div);
  });
  els.chatLog.scrollTop = els.chatLog.scrollHeight;
}

function renderSession(session) {
  if (!session) return;
  state.session = session;
  state.sessionId = session.id;
  if (els.sessionChip) els.sessionChip.textContent = `Session ${session.id.slice(0, 8)} · ${session.stage}`;
  renderMessages(session.messages);
  renderStats(session.stats);
  
  const isComp = session.stage === "completed";
  const isIntake = session.stage === "awaiting_intake";

  if (els.messageInput) {
    els.messageInput.disabled = isComp || isIntake;
    els.messageInput.placeholder = isComp ? "Session finished." : (isIntake ? "Waiting for intake form..." : "Type reply...");
  }
  if (els.sendBtn) els.sendBtn.disabled = isComp || isIntake;

  const intakePanel = document.getElementById("intake-panel");
  if (intakePanel) {
    intakePanel.style.display = isIntake ? "block" : "none";
  }

  renderCrmSnapshot(session);
  renderWriteResult(session);
  renderLeadResult(session);
  renderTripResult(session);
  renderBookingForm(session);
  renderBookingResult(session);
}

function renderCrmSnapshot(session) {
  if (!els.crmSnapshot) return;
  const res = session.finalResult || session.preview;
  if (!res) {
    els.crmSnapshot.innerHTML = `<p class="muted">No traveler data in session.</p>`;
    return;
  }
  
  let matchBadge = "";
  if (res.match_status === "not_found") {
    matchBadge = '<span class="tag ok">✨ New Traveler Created</span>';
  } else if (res.match_status === "single_match") {
    matchBadge = '<span class="tag ok">👤 Existing Traveler Matched</span>';
  } else if (res.match_status === "multiple_matches") {
    matchBadge = '<span class="tag danger">⚠️ Duplicate Phone Conflict</span>';
  }

  let handoffBadge = "";
  if (res.handoff_required) {
    handoffBadge = `<span class="tag danger">🛑 Handoff Required: ${escapeHtml(res.handoff_reason || "Conflict")}</span>`;
  } else {
    handoffBadge = '<span class="tag ok">🤖 Automated Flow</span>';
  }

  const traveler = res.traveler;
  let travelerHtml = "";
  if (traveler) {
    const tList = Array.isArray(traveler) ? traveler : [traveler];
    travelerHtml = tList.map(t => `
      <div class="write-item">
        <span class="label">ID / Name</span>
        <span class="value">${escapeHtml(t.traveler_id || "TBD")} - ${escapeHtml(t.full_name || session.customerName)}</span>
      </div>
      <div class="write-item">
        <span class="label">Phone / Key</span>
        <span class="value">${escapeHtml(t.whatsapp || t.phone_lookup_key || session.rawPhone)}</span>
      </div>
      <div class="write-item">
        <span class="label">Status</span>
        <span class="value">${escapeHtml(t.status || "Active")}</span>
      </div>
    `).join("<hr style='border: 0; border-top: 1px solid var(--border-color); margin: 8px 0;'>");
  } else {
    travelerHtml = `
      <div class="write-item">
        <span class="label">Name</span>
        <span class="value">${escapeHtml(session.customerName)}</span>
      </div>
      <div class="write-item">
        <span class="label">Phone</span>
        <span class="value">+${escapeHtml(session.countryCode)}${escapeHtml(session.rawPhone)}</span>
      </div>
    `;
  }

  els.crmSnapshot.innerHTML = `
    <div class="detail-card">
      <div class="tag-row" style="margin-bottom: 8px; display: flex; gap: 4px; flex-wrap: wrap;">
        ${matchBadge}
        ${handoffBadge}
      </div>
      <div class="detail-stack" style="margin-top: 8px;">
        ${travelerHtml}
      </div>
    </div>
  `;
}

function renderTripResult(session) {
  if (!els.tripResult) return;
  const res = session.finalResult || session.preview;
  const tripRes = res?.trip_result;
  
  if (!tripRes) {
    els.tripResult.innerHTML = `<p class="muted">Trip suggestions will appear here after the customer shares trip type.</p>`;
    return;
  }
  
  const openTrips = tripRes.open_trips || [];
  const tbdTrips = tripRes.date_tbd_trips || [];
  
  let html = "";
  if (openTrips.length === 0 && tbdTrips.length === 0) {
    html = `<p class="muted">No upcoming trips available in this category.</p>`;
  } else {
    if (openTrips.length > 0) {
      html += `<h4 style="margin:8px 0 4px; font-size:0.8rem; text-transform:uppercase; color:var(--text-muted);">Upcoming Open Trips</h4>`;
      html += openTrips.map((t, idx) => `
        <div class="detail-card ok" style="margin-bottom: 8px;">
          <div style="display:flex; justify-content:space-between; align-items:center;">
            <strong>${idx+1}. ${escapeHtml(t.trip_name)}</strong>
            <span class="tag ok" style="font-size:0.75rem;">${t.remaining_places} Left</span>
          </div>
          <div style="font-size:0.8rem; color:var(--text-muted); margin-top:4px;">
            <span>ID: ${escapeHtml(t.trip_id)}</span> | 
            <span>Dates: ${escapeHtml(t.start_date)} to ${escapeHtml(t.end_date)}</span>
          </div>
        </div>
      `).join("");
    }
    if (tbdTrips.length > 0) {
      html += `<h4 style="margin:8px 0 4px; font-size:0.8rem; text-transform:uppercase; color:var(--text-muted);">Date TBD / Upcoming</h4>`;
      html += tbdTrips.map((t, idx) => `
        <div class="detail-card warn" style="margin-bottom: 8px;">
          <div style="display:flex; justify-content:space-between; align-items:center;">
            <strong>${idx+1}. ${escapeHtml(t.trip_name)}</strong>
            <span class="tag warn" style="font-size:0.75rem;">TBD</span>
          </div>
          <div style="font-size:0.8rem; color:var(--text-muted); margin-top:4px;">
            <span>ID: ${escapeHtml(t.trip_id)}</span> | 
            <span>Year: ${t.year}</span>
          </div>
        </div>
      `).join("");
    }
  }
  
  els.tripResult.innerHTML = html;
}

function renderBookingForm(session) {
  if (!els.bookingFormPanel) return;
  const res = session.finalResult;
  if (!res || !res.write_result?.lead_update) {
    els.bookingFormPanel.innerHTML = `<p class="muted">A lead must be created or resolved in this session first.</p>`;
    return;
  }
  
  if (session.bookingResult) {
    els.bookingFormPanel.innerHTML = `<p class="ok">Booking draft successfully created!</p>`;
    return;
  }

  const openTrips = res.trip_result?.open_trips || [];
  if (openTrips.length === 0) {
    els.bookingFormPanel.innerHTML = `<p class="muted">No confirmed open trips available for booking in this session.</p>`;
    return;
  }

  els.bookingFormPanel.innerHTML = `
    <form id="demo-booking-form" class="detail-card" style="padding: 12px; background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 6px;">
      <div class="form-group" style="margin-bottom: 8px;">
        <label style="font-size:0.75rem; font-weight:600; display:block; margin-bottom:4px;">Select Trip</label>
        <select id="book-trip-id" class="form-select" style="width:100%; font-size:0.8rem;" required>
          ${openTrips.map(t => `<option value="${t.trip_id}">${escapeHtml(t.trip_name)}</option>`).join("")}
        </select>
      </div>
      <div class="form-group" style="margin-bottom: 8px;">
        <label style="font-size:0.75rem; font-weight:600; display:block; margin-bottom:4px;">Room Type</label>
        <select id="book-room-type" class="form-select" style="width:100%; font-size:0.8rem;" required>
          <option value="Single">Single</option>
          <option value="Double">Double</option>
          <option value="Triple">Triple</option>
        </select>
      </div>
      <button type="submit" class="primary-btn" style="width:100%; padding:6px; font-size:0.8rem; margin-top:8px;">Submit Booking Draft</button>
    </form>
  `;

  document.getElementById("demo-booking-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const tripId = document.getElementById("book-trip-id").value;
    const roomType = document.getElementById("book-room-type").value;
    try {
      const data = await api(`/api/session/${session.id}/book`, {
        method: "POST",
        body: JSON.stringify({ tripId, roomType }),
      });
      renderSession(data.session);
    } catch (err) {
      alert(err.message);
    }
  });
}

function renderBookingResult(session) {
  if (!els.bookingResult) return;
  const b = session.bookingResult;
  if (!b) {
    els.bookingResult.innerHTML = `<p class="muted">Booking draft details and internal alert output will appear here after reservation.</p>`;
    return;
  }
  
  const bDraft = b.write_result?.booking_draft || b;
  els.bookingResult.innerHTML = `
    <div class="detail-card ok">
      <div class="label">Booking Created Successfully</div>
      <h3>${escapeHtml(bDraft.booking_id)}</h3>
      <div class="tag-row" style="margin-bottom: 8px;">
        <span class="tag ok">${escapeHtml(bDraft.booking_status || "Draft")}</span>
        <span class="tag ok">${escapeHtml(bDraft.room_type)} Room</span>
        <span class="tag warn">${escapeHtml(bDraft.payment_status || "Awaiting Deposit")}</span>
      </div>
      <div class="detail-stack" style="margin-top: 8px;">
        <div class="write-item">
          <span class="label">Trip Name</span>
          <span class="value">${escapeHtml(bDraft.trip_name || b.trip_name)}</span>
        </div>
        <div class="write-item">
          <span class="label">Traveler Name</span>
          <span class="value">${escapeHtml(bDraft.traveler_name || b.traveler_name)}</span>
        </div>
        <div class="write-item">
          <span class="label">Currency</span>
          <span class="value">${escapeHtml(bDraft.currency || b.currency || "USD")}</span>
        </div>
        ${bDraft.flight_option ? `
        <div class="write-item">
          <span class="label">Flight Option</span>
          <span class="value">${escapeHtml(bDraft.flight_option)}</span>
        </div>
        ` : ""}
      </div>
    </div>
  `;
}

function renderWriteResult(session) {
  if (!els.writeResult) return;
  const res = session.finalResult?.write_result;
  const prev = session.preview;

  if (!res) {
    if (prev && prev.handoff_required) {
      els.writeResult.innerHTML = `
        <div class="detail-card danger">
          <div class="tag danger">Handoff Required</div>
          <p style="margin: 8px 0 0; font-size: 0.9rem;">${escapeHtml(prev.handoff_reason)}</p>
        </div>
      `;
    } else {
      els.writeResult.innerHTML = `<p class="muted">No write activity logged yet.</p>`;
    }
    return;
  }

  const actions = session.finalResult?.actions || [];
  els.writeResult.innerHTML = `
    <div class="detail-stack">
      <div style="display:flex; gap:4px; flex-wrap:wrap; margin-bottom:8px;">
        ${actions.map(a => `<span class="tag ok">${escapeHtml(a)}</span>`).join("")}
      </div>
      ${res.created_traveler ? `
        <div class="write-item">
          <span class="label">New Traveler ID</span>
          <span class="value">${res.created_traveler.traveler_id}</span>
        </div>
      ` : ""}
      <div class="write-item">
        <span class="label">Interaction Logged</span>
        <span class="value">${res.interaction_log?.interaction_id || "Yes"}</span>
      </div>
      ${res.lead_update ? `
        <div class="write-item">
          <span class="label">Lead Update Row</span>
          <span class="value">${res.lead_update.lead_id || "Yes"}</span>
        </div>
      ` : ""}
    </div>
  `;
}

function renderLeadResult(session) {
  if (!els.leadResult) return;
  const res = session.finalResult || session.preview;
  const lead = res?.write_result?.lead_update || res?.lead_update;
  if (!lead) {
    els.leadResult.innerHTML = `<p class="muted">Lead data not yet saved.</p>`;
    if (els.leadActions) els.leadActions.style.display = "none";
    return;
  }
  if (els.leadActions) {
    els.leadActions.style.display = "flex";
    els.qualifyLeadBtn.onclick = () => handleQualifyLead(lead.lead_id);
  }
  
  let followUpHtml = "";
  if (lead.follow_up_due_date) {
    followUpHtml = `
      <div class="write-item">
        <span class="label">Follow Up Due Date</span>
        <span class="value">${escapeHtml(lead.follow_up_due_date)}</span>
      </div>
    `;
  }
  
  els.leadResult.innerHTML = `
    <div class="detail-card">
      <div class="label">Lead Snapshot</div>
      <h3>${escapeHtml(lead.lead_id)}</h3>
      <div class="tag-row" style="margin-bottom: 8px;">
        <span class="tag ok">${escapeHtml(lead.lead_stage)}</span>
        <span class="tag warn">${escapeHtml(lead.priority)} Priority</span>
      </div>
      <div class="detail-stack" style="margin-top: 8px;">
        <div class="write-item">
          <span class="label">Preferred Trip</span>
          <span class="value">${escapeHtml(lead.interested_trip_ids || lead.trip_id || "None selected")}</span>
        </div>
        <div class="write-item">
          <span class="label">Source / Channel</span>
          <span class="value">${escapeHtml(lead.lead_source || lead.channel || "Web Demo")}</span>
        </div>
        ${followUpHtml}
      </div>
    </div>
  `;
}

async function handleQualifyLead(id) {
  try {
    await api(`/api/lead/${id}/qualify`, { method: "POST" });
    alert("Lead Qualified!");
    const data = await api(`/api/session/${state.sessionId}`);
    renderSession(data.session);
  } catch (e) { alert(e.message); }
}

async function fetchCrmPreview() {
  if (!els.crmBrowser) return;
  try {
    const data = await api("/api/crm/preview");
    els.crmBrowser.innerHTML = "";
    (data.travelers || []).forEach(t => {
      const div = document.createElement("div");
      div.className = "meta-item";
      div.style.cursor = "pointer";
      div.innerHTML = `<div><strong>${escapeHtml(t.name)}</strong><br><small>${t.id}</small></div>`;
      div.onclick = () => {
        navigator.clipboard.writeText(t.name);
        alert("Copied name to clipboard!");
      };
      els.crmBrowser.appendChild(div);
    });
  } catch (e) { console.error("CRM Preview failed", e); }
}

async function bootstrap() {
  console.log("Bootstrapping app...");
  try {
    const data = await api("/api/bootstrap");
    if (els.runtimeWorkbook) els.runtimeWorkbook.textContent = data.runtimeWorkbook;
    if (els.sourceWorkbook) els.sourceWorkbook.textContent = data.sourceWorkbook;
    renderStats(data.stats);
    await fetchCrmPreview();
  } catch (e) {
    console.error("Bootstrap failed", e);
    alert("Server error. Please check if the Python app is running.");
  }
}

els.newSessionBtn?.addEventListener("click", async () => {
  try {
    const data = await api("/api/session", { method: "POST" });
    renderSession(data.session);
  } catch (e) { alert(e.message); }
});

els.resetDemoBtn?.addEventListener("click", async () => {
  if (!confirm("Reset all data?")) return;
  try {
    await api("/api/reset", { method: "POST" });
    location.reload();
  } catch (e) { alert(e.message); }
});

els.messageForm?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const txt = els.messageInput.value.trim();
  if (!txt || !state.sessionId) return;
  els.messageInput.value = "";
  try {
    const data = await api(`/api/session/${state.sessionId}/message`, { method: "POST", body: JSON.stringify({ text: txt }) });
    renderSession(data.session);
  } catch (err) { alert(err.message); }
});

document.getElementById("intake-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!state.sessionId) return;
  const formData = new FormData(e.target);
  const payload = Object.fromEntries(formData.entries());
  try {
    const data = await api(`/api/session/${state.sessionId}/intake`, { method: "POST", body: JSON.stringify(payload) });
    renderSession(data.session);
  } catch (err) { alert(err.message); }
});

bootstrap();
