const state = {
  sessionId: null,
  session: null,
};

const els = {
  sheetBackend: document.getElementById("sheet-backend"),
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
  intakeForm: document.getElementById("intake-form"),
  fullNameInput: document.getElementById("full-name-input"),
  birthdayInput: document.getElementById("birthday-input"),
  genderInput: document.getElementById("gender-input"),
  nationalityInput: document.getElementById("nationality-input"),
  countryCodeInput: document.getElementById("country-code-input"),
  phoneInput: document.getElementById("phone-input"),
  submitIntakeBtn: document.getElementById("submit-intake-btn"),
  quickActions: document.getElementById("quick-actions"),
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
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const errData = await response.json().catch(() => ({}));
    throw new Error(errData.error || `Request failed: ${response.status}`);
  }
  return await response.json();
}

function escapeHtml(text) {
  return String(text || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function renderStats(stats) {
  if (!stats) return;
  els.travelerCount.textContent = stats.travelerCount ?? 0;
  els.interactionCount.textContent = stats.interactionCount ?? 0;
  els.leadCount.textContent = stats.leadCount ?? 0;
  els.qualificationRate.textContent = `${stats.pipeline?.qualificationRate ?? 0}%`;
  els.urgentCount.textContent = stats.followUpSummary?.urgent ?? 0;
  els.dueTodayCount.textContent = stats.followUpSummary?.dueToday ?? 0;
  els.bookingDraftCount.textContent = stats.bookingDraftCount ?? 0;
  els.paymentPendingCount.textContent = stats.paymentPendingCount ?? 0;
  els.bookingAlertCount.textContent = stats.bookingAlertCount ?? 0;

  els.tripStatusList.innerHTML = "";
  Object.entries(stats.tripStatusCounts || {}).forEach(([status, count]) => {
    const div = document.createElement("div");
    div.className = "trip-status-item";
    div.innerHTML = `<span>${escapeHtml(status)}</span><strong>${count}</strong>`;
    els.tripStatusList.appendChild(div);
  });

  els.leadStageList.innerHTML = "";
  Object.entries(stats.leadStageCounts || {}).forEach(([status, count]) => {
    const div = document.createElement("div");
    div.className = "trip-status-item";
    div.innerHTML = `<span>${escapeHtml(status || "Blank")}</span><strong>${count}</strong>`;
    els.leadStageList.appendChild(div);
  });

  els.recentLeads.innerHTML = "";
  (stats.recentLeads || []).forEach((lead) => {
    const div = document.createElement("div");
    div.className = "detail-card";
    div.innerHTML = `
      <h3>${escapeHtml(lead.leadId)}</h3>
      <p class="muted">${escapeHtml(lead.customerName)} - ${escapeHtml(lead.leadStage)}</p>
    `;
    els.recentLeads.appendChild(div);
  });
}

function renderMessages(messages) {
  els.chatLog.innerHTML = "";
  (messages || []).forEach((message) => {
    const div = document.createElement("div");
    div.className = `chat-bubble ${message.role}`;
    div.innerHTML = `<p>${escapeHtml(message.text)}</p>`;
    els.chatLog.appendChild(div);
  });
  els.chatLog.scrollTop = els.chatLog.scrollHeight;
}

function renderSession(session) {
  if (!session) return;
  state.session = session;
  state.sessionId = session.id;
  els.sessionChip.textContent = `Session ${session.id.slice(0, 8)} - ${session.stage}`;
  renderMessages(session.messages);
  renderStats(session.stats);

  const canIntake = session.stage === "awaiting_intake";
  const canChat = ["awaiting_phone", "awaiting_trip_type", "awaiting_confirmation", "awaiting_room_type", "awaiting_flight", "awaiting_currency"].includes(session.stage);
  const isCompleted = session.stage === "completed";

  if (session.rawPhone && !els.phoneInput.value) {
    els.phoneInput.value = session.rawPhone;
  }
  els.intakeForm.hidden = !canIntake;
  els.messageInput.disabled = !canChat || isCompleted;
  els.messageInput.placeholder = messagePlaceholder(session.stage);
  els.sendBtn.disabled = !canChat || isCompleted;
  renderQuickActions(session);

  renderCrmSnapshot(session);
  renderTripResult(session);
  renderWriteResult(session);
  renderLeadResult(session);
  renderBookingPanel(session);
  renderBookingResult(session);
}

function messagePlaceholder(stage) {
  if (stage === "completed") return "Session finished.";
  if (stage === "awaiting_phone") return "Enter WhatsApp number first...";
  if (stage === "awaiting_trip_type") return "Type local or international...";
  if (stage === "awaiting_confirmation") return "Type the trip number/name, or no...";
  if (stage === "awaiting_room_type") return "Type single, double, or triple...";
  if (stage === "awaiting_flight") return "Type with flights or no flights...";
  if (stage === "awaiting_currency") return "Type EGP or USD...";
  return "Waiting for intake form...";
}

function renderQuickActions(session) {
  const stage = session.stage;
  const availableRooms = getAvailableRoomReplies(session);
  const visible = ["awaiting_trip_type", "awaiting_confirmation", "awaiting_room_type", "awaiting_flight", "awaiting_currency"].includes(stage);
  els.quickActions.hidden = !visible;
  els.quickActions.querySelectorAll("button").forEach((button) => {
    const reply = button.dataset.reply;
    const tripTypeBtn = ["local", "international"].includes(reply);
    const confirmBtn = ["yes", "no"].includes(reply);
    const roomBtn = ["single", "double", "triple"].includes(reply);
    const flightBtn = ["with flights", "no flights"].includes(reply);
    const currencyBtn = ["egp", "usd"].includes(reply);

    if (stage === "awaiting_trip_type") button.hidden = !tripTypeBtn;
    else if (stage === "awaiting_confirmation") button.hidden = !confirmBtn;
    else if (stage === "awaiting_room_type") button.hidden = !roomBtn || (availableRooms && !availableRooms.includes(reply));
    else if (stage === "awaiting_flight") button.hidden = !flightBtn;
    else if (stage === "awaiting_currency") button.hidden = !currencyBtn;
    else button.hidden = true;
  });
}

function getSelectedTrip(session) {
  const tripResult = session.finalResult?.trip_result || session.preview?.trip_result;
  const trips = [
    ...(tripResult?.open_trips || []),
    ...(tripResult?.date_tbd_trips || []),
  ];
  return trips.find((trip) => trip.trip_id === session.selectedTripId) || null;
}

function getAvailableRoomReplies(session) {
  const trip = getSelectedTrip(session);
  if (!trip) return null;
  const roomFields = {
    single: "available_single",
    double: "available_double",
    triple: "available_triple",
  };
  return Object.entries(roomFields)
    .filter(([, field]) => Number(trip[field] || 0) > 0)
    .map(([reply]) => reply);
}

function renderCrmSnapshot(session) {
  const traveler = session.finalResult?.traveler || session.preview?.traveler;
  if (!traveler) {
    els.crmSnapshot.innerHTML = `<p class="muted">No session data yet.</p>`;
    return;
  }
  const t = Array.isArray(traveler) ? traveler[0] : traveler;
  els.crmSnapshot.innerHTML = `
    <div class="detail-card">
      <h3>${escapeHtml(t.full_name)}</h3>
      <div class="meta-item"><span>Status</span><strong>${escapeHtml(t.status || "Active")}</strong></div>
      <div class="meta-item"><span>ID</span><strong>${escapeHtml(t.traveler_id)}</strong></div>
    </div>
  `;
}

function renderTripResult(session) {
  const result = session.finalResult || session.preview;
  const tripResult = result?.trip_result;
  if (!tripResult) {
    els.tripResult.innerHTML = `<p class="muted">Trip suggestions will appear here after the customer shares trip type.</p>`;
    return;
  }

  const rows = [];
  (tripResult.open_trips || []).forEach((trip) => {
    const remaining = trip.remaining_places ?? "Unknown";
    rows.push(`
      <div class="trip-pill">
        <div>
          <strong>${escapeHtml(trip.trip_name)}</strong><br>
          <small>${escapeHtml(trip.start_date)} to ${escapeHtml(trip.end_date)}</small>
        </div>
        <span class="tag ok">${escapeHtml(remaining)} places</span>
      </div>
    `);
  });
  (tripResult.date_tbd_trips || []).forEach((trip) => {
    rows.push(`
      <div class="trip-pill">
        <div><strong>${escapeHtml(trip.trip_name)}</strong><br><small>Date TBD</small></div>
        <span class="tag warn">Follow up</span>
      </div>
    `);
  });
  els.tripResult.innerHTML = rows.join("") || `<p class="muted">No confirmed upcoming trips match this request.</p>`;
}

function renderWriteResult(session) {
  const write = session.finalResult?.write_result;
  if (!write) {
    els.writeResult.innerHTML = `<p class="muted">No sheet write yet. Lead is written only after confirmation.</p>`;
    return;
  }
  if (session.finalResult?.handoff_required) {
    els.writeResult.innerHTML = `
      <div class="detail-card">
        <h3>Review Required</h3>
        <div class="meta-item"><span>Reason</span><strong>${escapeHtml(session.finalResult?.handoff_reason || "manual_review")}</strong></div>
        <p class="muted">No traveler profile was overwritten during this handoff.</p>
      </div>
    `;
    return;
  }
  const created = write.created_traveler;
  const lead = write.lead_update;
  els.writeResult.innerHTML = `
    <div class="detail-card">
      <h3>${created ? "Created Traveler" : "Updated Existing Traveler"}</h3>
      <div class="meta-item"><span>Traveler</span><strong>${escapeHtml(created?.traveler_id || session.finalResult?.traveler?.traveler_id || "Resolved")}</strong></div>
      <div class="meta-item"><span>Lead</span><strong>${escapeHtml(lead?.lead_id || "N/A")}</strong></div>
    </div>
  `;
}

function renderLeadResult(session) {
  const lead = session.finalResult?.write_result?.lead_update;
  if (!lead) {
    els.leadResult.innerHTML = `<p class="muted">Waiting for confirmed lead...</p>`;
    els.leadActions.style.display = "none";
    return;
  }
  els.leadActions.style.display = "flex";
  els.qualifyLeadBtn.onclick = () => handleQualifyLead(lead.lead_id);
  els.leadResult.innerHTML = `
    <div class="detail-card">
      <h3>${escapeHtml(lead.lead_id)}</h3>
      <div class="tag-row"><span class="tag ok">${escapeHtml(lead.lead_stage)}</span></div>
    </div>
  `;
}

function renderBookingPanel(session) {
  const openTrips = session.finalResult?.trip_result?.open_trips || [];
  const travelerId = session.finalResult?.write_result?.created_traveler?.traveler_id || session.finalResult?.traveler?.traveler_id;
  if (!openTrips.length || !travelerId) {
    els.bookingFormPanel.innerHTML = `<p class="muted">Confirm a lead with open trips before creating a booking draft.</p>`;
    return;
  }
  els.bookingFormPanel.innerHTML = `
    <select id="booking-trip-select" class="booking-select">
      ${openTrips.map((trip) => `<option value="${escapeHtml(trip.trip_id)}">${escapeHtml(trip.trip_name)}</option>`).join("")}
    </select>
    <select id="booking-room-select" class="booking-select">
      <option>Double</option>
      <option>Single</option>
      <option>Triple</option>
    </select>
    <button id="create-booking-btn" class="primary-btn" type="button">Create Booking Draft</button>
  `;
  document.getElementById("create-booking-btn")?.addEventListener("click", handleCreateBooking);
}

function renderBookingResult(session) {
  const booking = session.bookingResult;
  if (!booking) {
    els.bookingResult.innerHTML = `<p class="muted">Booking draft details and internal alert output will appear here after reservation.</p>`;
    return;
  }
  els.bookingResult.innerHTML = `
    <div class="detail-card">
      <h3>${escapeHtml(booking.booking_id)}</h3>
      <div class="meta-item"><span>Trip</span><strong>${escapeHtml(booking.trip_name)}</strong></div>
      <div class="meta-item"><span>Room</span><strong>${escapeHtml(booking.room_type)}</strong></div>
      <div class="meta-item"><span>After Draft</span><strong>${escapeHtml(booking.available_after_draft)} left</strong></div>
    </div>
  `;
}

async function handleQualifyLead(id) {
  try {
    await api(`/api/lead/${id}/qualify`, { method: "POST" });
    const data = await api(`/api/session/${state.sessionId}`);
    renderSession(data.session);
  } catch (err) {
    alert(err.message);
  }
}

async function handleCreateBooking() {
  const tripId = document.getElementById("booking-trip-select")?.value;
  const roomType = document.getElementById("booking-room-select")?.value;
  if (!tripId || !roomType || !state.sessionId) return;
  try {
    const data = await api(`/api/session/${state.sessionId}/book`, {
      method: "POST",
      body: JSON.stringify({ tripId, roomType }),
    });
    renderSession(data.session);
  } catch (err) {
    alert(err.message);
  }
}

async function fetchCrmPreview() {
  try {
    const data = await api("/api/crm/preview");
    els.crmBrowser.innerHTML = "";
    (data.travelers || []).forEach((traveler) => {
      const div = document.createElement("div");
      div.className = "meta-item";
      div.innerHTML = `<div><strong>${escapeHtml(traveler.name)}</strong><br><small>${escapeHtml(traveler.id)}</small></div>`;
      els.crmBrowser.appendChild(div);
    });
  } catch (err) {
    els.crmBrowser.innerHTML = `<p class="muted">${escapeHtml(err.message)}</p>`;
  }
}

async function bootstrap() {
  try {
    const data = await api("/api/bootstrap");
    els.sheetBackend.textContent = data.sheetBackend === 'google' ? 'Google Sheets' : 'Local XLSX';
    els.runtimeWorkbook.textContent = data.runtimeWorkbook;
    els.sourceWorkbook.textContent = data.sourceWorkbook;
    renderStats(data.stats);
    await fetchCrmPreview();
  } catch (err) {
    alert("Server error. Please check if the Python app is running.");
  }
}

els.newSessionBtn?.addEventListener("click", async () => {
  try {
    const data = await api("/api/session", { method: "POST" });
    renderSession(data.session);
  } catch (err) {
    alert(err.message);
  }
});

els.resetDemoBtn?.addEventListener("click", async () => {
  if (!confirm("Reset all demo data from the source workbook?")) return;
  try {
    await api("/api/reset", { method: "POST" });
    location.reload();
  } catch (err) {
    alert(err.message);
  }
});

els.intakeForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.sessionId) {
    alert("Start a session first.");
    return;
  }
  const payload = {
    fullName: els.fullNameInput.value.trim(),
    birthday: els.birthdayInput.value,
    gender: els.genderInput.value,
    nationality: els.nationalityInput.value.trim(),
    countryCode: els.countryCodeInput.value.trim(),
    rawPhone: els.phoneInput.value.trim() || state.session?.rawPhone || "",
    language: "en",
  };
  try {
    els.submitIntakeBtn.disabled = true;
    const data = await api(`/api/session/${state.sessionId}/intake`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    renderSession(data.session);
  } catch (err) {
    alert(err.message);
  } finally {
    els.submitIntakeBtn.disabled = false;
  }
});

els.messageForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = els.messageInput.value.trim();
  if (!text || !state.sessionId) return;
  els.messageInput.value = "";
  try {
    const data = await api(`/api/session/${state.sessionId}/message`, {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    renderSession(data.session);
  } catch (err) {
    alert(err.message);
  }
});

els.quickActions?.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-reply]");
  if (!button || !state.sessionId) return;
  try {
    const data = await api(`/api/session/${state.sessionId}/message`, {
      method: "POST",
      body: JSON.stringify({ text: button.dataset.reply }),
    });
    renderSession(data.session);
  } catch (err) {
    alert(err.message);
  }
});

bootstrap();
