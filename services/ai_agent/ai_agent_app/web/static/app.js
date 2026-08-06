const state = {
  sessionId: null,
  session: null,
  messagePending: false,
};

const PASSPORT_CHAT_STAGES = [
  "awaiting_passport_upload",
];

const els = {
  chatLog: document.getElementById("chat-log"),
  messageForm: document.getElementById("message-form"),
  messageInput: document.getElementById("message-input"),
  passportFileInput: document.getElementById("passport-file-input"),
  passportUploadBtn: document.getElementById("passport-upload-btn"),
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
};

async function api(path, options = {}) {
  const response = await fetch((window.API_PREFIX || "") + path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const errData = await response.json().catch(() => ({}));
    throw new Error(errData.message || errData.error || `Request failed: ${response.status}`);
  }
  return await response.json();
}

function escapeHtml(text) {
  return String(text || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function safeMediaUrl(url) {
  const value = String(url || "").trim();
  if (!value) return "";
  if (value.startsWith("/") && !value.startsWith("//")) return value;
  try {
    const parsed = new URL(value, window.location.origin);
    if (parsed.protocol === "http:" || parsed.protocol === "https:") return parsed.href;
  } catch (_err) {
    return "";
  }
  return "";
}

function detectTextDirection(text) {
  const value = String(text || "");
  const arabicCount = (value.match(/[؀-ۿ]/g) || []).length;
  const latinCount = (value.match(/[A-Za-z]/g) || []).length;
  if (arabicCount === 0 && latinCount === 0) return "auto";
  return arabicCount >= latinCount ? "rtl" : "ltr";
}

function inlineDirection(token, parentDir) {
  const value = String(token || "");
  if (/[؀-ۿ]/.test(value)) return "rtl";
  if (/[A-Za-z]/.test(value) || /\d/.test(value) || /^https?:\/\//i.test(value)) return "ltr";
  return parentDir || "auto";
}

function appendBidiText(parent, text, parentDir) {
  const value = String(text || "");
  const tokenPattern = /(https?:\/\/[^\s]+|[A-Z]{1,8}-[A-Z0-9-]{2,}[.!?]?|\+?\d[\d\s\-/:.]{2,}\d[.!?]?|[A-Za-z][A-Za-z0-9]*(?:[ '-][A-Za-z0-9]+)*[.!?]?)/g;
  let cursor = 0;
  for (const match of value.matchAll(tokenPattern)) {
    if (match.index > cursor) {
      parent.appendChild(document.createTextNode(value.slice(cursor, match.index)));
    }
    const token = match[0];
    const bdi = document.createElement("bdi");
    bdi.dir = inlineDirection(token, parentDir);
    bdi.textContent = token;
    parent.appendChild(bdi);
    cursor = match.index + token.length;
  }
  if (cursor < value.length) {
    parent.appendChild(document.createTextNode(value.slice(cursor)));
  }
}

function appendMessageLine(container, line, dir) {
  const p = document.createElement("p");
  p.dir = dir;
  appendBidiText(p, line, dir);
  container.appendChild(p);
}

function renderMessageText(container, text, dir) {
  const lines = String(text || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
  let currentList = null;
  lines.forEach((rawLine) => {
    const line = rawLine.trimEnd();
    if (!line.trim()) {
      currentList = null;
      return;
    }
    const match = line.match(/^\s*(\d+)[.)]\s+(.+)$/);
    if (match) {
      if (!currentList) {
        currentList = document.createElement("ol");
        currentList.dir = dir;
        currentList.className = "chat-list";
        container.appendChild(currentList);
      }
      const li = document.createElement("li");
      li.value = Number(match[1]);
      li.dir = dir;
      appendBidiText(li, match[2], dir);
      currentList.appendChild(li);
      return;
    }
    currentList = null;
    appendMessageLine(container, line, dir);
  });
}

function renderMessages(messages) {
  els.chatLog.innerHTML = "";
  (messages || []).forEach((message) => {
    if (message.role === "assistant" && message.state && message.state !== "completed") {
      return;
    }
    const div = document.createElement("div");
    const dir = detectTextDirection(message.text);
    div.className = `chat-bubble ${message.role} dir-${dir}`;
    div.dir = dir;
    renderMessageText(div, message.text, dir);
    const mediaItems = Array.isArray(message.media) ? message.media : [];
    const safeMedia = mediaItems
      .map((item) => ({
        url: safeMediaUrl(item.public_url || item.url),
        alt: String(item.alt_text || "Official trip image"),
        label: String(item.image_type || "image"),
      }))
      .filter((item) => item.url);
    if (safeMedia.length) {
      const grid = document.createElement("div");
      grid.className = "chat-media-grid";
      safeMedia.forEach((item) => {
        const frame = document.createElement("div");
        frame.className = "chat-media";
        const img = document.createElement("img");
        img.src = item.url;
        img.alt = item.alt;
        img.loading = "lazy";
        const caption = document.createElement("span");
        caption.textContent = item.label === "cover" ? "Official cover image" : "Official gallery image";
        frame.appendChild(img);
        frame.appendChild(caption);
        grid.appendChild(frame);
      });
      div.appendChild(grid);
    }
    els.chatLog.appendChild(div);
  });
  els.chatLog.scrollTop = els.chatLog.scrollHeight;
}

function renderSession(session) {
  if (!session) return;
  state.session = session;
  state.sessionId = session.id;
  const runtimeMode = session.runtimeMode || session.agentMode || "deterministic";
  const chatEnabled = Boolean(session.chatEnabled ?? session.chat_enabled ?? (runtimeMode !== "deterministic"));
  const requiresIntake = Boolean(session.requiresIntake ?? session.requires_intake ?? (runtimeMode === "deterministic" && session.stage === "awaiting_intake"));
  renderMessages(session.messages);

  const isGeminiMode = runtimeMode === "gemini";
  const isToolCallingMode = runtimeMode === "tool_calling";
  const canIntake = requiresIntake;
  const canChat = chatEnabled || [
    "awaiting_phone",
    "awaiting_country_code",
    "awaiting_trip_type",
    "awaiting_confirmation",
    "awaiting_group_size",
    "awaiting_room_type",
    "awaiting_flight",
    "awaiting_currency",
    "awaiting_clarification",
    ...PASSPORT_CHAT_STAGES,
  ].includes(session.stage);
  const hasActiveHandoff = ["handed_off", "handoff_created", "handoff_pending", "assigned"].includes(String(session.handoffState || "").toLowerCase());
  const isCompleted = ["completed", "cancelled"].includes(session.stage) || (session.stage === "handed_off" && !hasActiveHandoff);

  if (session.rawPhone && !els.phoneInput.value) {
    els.phoneInput.value = session.rawPhone;
  }
  const phoneNormalization = session.phoneNormalization || {};
  if (!els.phoneInput.value && phoneNormalization.normalized_e164) {
    els.phoneInput.value = phoneNormalization.normalized_e164;
  }
  els.intakeForm.hidden = isGeminiMode || isToolCallingMode || !canIntake;
  els.messageInput.disabled = state.messagePending || (!canChat && !hasActiveHandoff) || isCompleted;
  els.messageInput.placeholder = messagePlaceholder(session, runtimeMode);
  els.sendBtn.disabled = state.messagePending || (!canChat && !hasActiveHandoff) || isCompleted;
  renderQuickActions(session);
  renderPassportUpload(session);
}

function messagePlaceholder(session, runtimeMode = "deterministic") {
  const stage = session?.stage || "";
  const passportAttachmentRef = session?.passportAttachmentRef || "";
  const isGeminiMode = runtimeMode === "gemini";
  const isToolCallingMode = runtimeMode === "tool_calling";
  const hasActiveHandoff = ["handed_off", "handoff_created", "handoff_pending", "assigned"].includes(String(session?.handoffState || "").toLowerCase());
  if (stage === "handed_off" && hasActiveHandoff) return "Your request is under review. You can add a note...";
  if (["completed", "handed_off", "cancelled"].includes(stage)) return "Session finished.";
  if (isGeminiMode || isToolCallingMode) {
    if (stage === "awaiting_passport_upload") {
      return passportAttachmentRef
        ? "Passport attached. You can continue chatting..."
        : "Attach the passport if needed, or continue chatting...";
    }
    return "Ask naturally in Arabic or English...";
  }
  if (stage === "awaiting_phone") return "Enter WhatsApp number first...";
  if (stage === "awaiting_trip_type") return "Type local or international...";
  if (stage === "awaiting_confirmation") return "Type the trip number/name, or no...";
  if (stage === "awaiting_passport_upload") {
    return passportAttachmentRef
      ? "Passport uploaded. Type done to continue..."
      : "Upload passport, then type done...";
  }
  if (stage === "awaiting_country_code") return "Type the country code...";
  if (stage === "awaiting_group_size") return "Type the number of travelers...";
  if (stage === "awaiting_room_type") return "Type single room, double boys room, double girls room, or triple room...";
  if (stage === "awaiting_flight") return "Type with flights or no flights...";
  if (stage === "awaiting_currency") return "Type EGP or USD...";
  if (stage === "booking_created") return "Booking draft created.";
  if (stage === "awaiting_clarification") return "Please clarify your choice...";
  return "Waiting for intake form...";
}

function renderPassportUpload(session) {
  if (!els.passportUploadBtn || !els.passportFileInput) return;
  const isUploadStage = session.stage === "awaiting_passport_upload";
  const uploadEnabled = session.passportUploadEnabled !== false;
  const passportRequired = Boolean(session.passportRequired);
  const hasAttachment = Boolean(session.passportAttachmentRef);
  const canUpload = uploadEnabled && state.sessionId && (isUploadStage || (passportRequired && !hasAttachment));
  els.passportUploadBtn.hidden = !canUpload;
  els.passportUploadBtn.disabled = !canUpload;
  els.passportUploadBtn.textContent = hasAttachment ? "Replace Passport" : "Attach Passport";
  els.passportUploadBtn.title = hasAttachment
    ? `Current file: ${session.passportAttachmentRef}`
    : "Upload a passport image or PDF";
}

function renderQuickActions(session) {
  const stage = session.stage;
  const runtimeMode = session.runtimeMode || session.agentMode;
  if (runtimeMode === "gemini" || runtimeMode === "tool_calling") {
    els.quickActions.hidden = true;
    return;
  }
  const availableRoomReplies = getAvailableRoomReplies(session);
  const roomChoices = getAvailableRoomChoices(session);
  const visible = ["awaiting_trip_type", "awaiting_confirmation", "awaiting_room_type", "awaiting_flight", "awaiting_currency", "awaiting_clarification"].includes(stage);
  els.quickActions.hidden = !visible;
  const trip = getSelectedTrip(session);
  els.quickActions.querySelectorAll(".dynamic-room-choice").forEach((button) => button.remove());
  els.quickActions.querySelectorAll("button").forEach((button) => {
    const reply = button.dataset.reply;
    const tripTypeBtn = ["local", "international"].includes(reply);
    const confirmBtn = ["yes", "no"].includes(reply);
    const roomBtn = ["single", "double", "triple"].includes(reply);
    const flightBtn = ["with flights", "no flights"].includes(reply);
    const currencyBtn = ["egp", "usd"].includes(reply);
    const confirmBookingBtn = ["yes", "no"].includes(reply);

    if (stage === "awaiting_trip_type") button.hidden = !tripTypeBtn;
    else if (stage === "awaiting_confirmation") button.hidden = !confirmBtn;
    else if (stage === "awaiting_room_type") button.hidden = roomBtn;
    else if (stage === "awaiting_flight") button.hidden = !flightBtn;
    else if (stage === "awaiting_currency") button.hidden = !currencyBtn;
    else if (stage === "booking_created") button.hidden = !confirmBookingBtn;
    else if (stage === "awaiting_clarification") button.hidden = true;
    else button.hidden = true;

      if (roomBtn) {
      if (reply === "single") button.innerHTML = "Single room";
      if (reply === "double") {
        const boys = Number(trip?.boys_double || 0);
        const girls = Number(trip?.girls_double || 0);
        button.innerHTML = `Double room <span class="btn-sub">Boys room: ${boys} | Girls room: ${girls}</span>`;
      }
      if (reply === "triple") {
        const boys = Number(trip?.boys_triple || 0);
        const girls = Number(trip?.girls_triple || 0);
        button.innerHTML = `Triple room <span class="btn-sub">Boys room: ${boys} | Girls room: ${girls}</span>`;
      }
    }
  });

  if (stage === "awaiting_room_type") {
    roomChoices.forEach((choice) => {
      if (availableRoomReplies && !availableRoomReplies.includes(choice.reply)) {
        return;
      }
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn btn-quick dynamic-room-choice";
      button.dataset.reply = choice.reply;
      if (choice.roomGroup) {
        button.innerHTML = `${escapeHtml(choice.title)} <span class="btn-sub">${escapeHtml(choice.subtitle)}</span>`;
      } else {
        button.textContent = choice.title;
      }
      els.quickActions.appendChild(button);
    });
  }
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
  return getAvailableRoomChoices(session).map((choice) => choice.reply);
}

function getAvailableRoomChoices(session) {
  const trip = getSelectedTrip(session);
  if (!trip) return [];

  const choices = [];
  const single = Number(trip.available_single || 0);
  const double = Number(trip.available_double || 0);
  const triple = Number(trip.available_triple || 0);
  const doubleBoys = Number(trip.boys_double || 0);
  const doubleGirls = Number(trip.girls_double || 0);
  const tripleBoys = Number(trip.boys_triple || 0);
  const tripleGirls = Number(trip.girls_triple || 0);

  if (single > 0) {
    choices.push({
      reply: "single room",
      title: "Single room",
      subtitle: "Currently available",
      roomGroup: "",
    });
  }
  if (double > 0) {
    if (doubleBoys > 0) {
      choices.push({
        reply: "double boys room",
        title: "Double boys room",
        subtitle: "Currently available",
        roomGroup: "boys",
      });
    }
    if (doubleGirls > 0) {
      choices.push({
        reply: "double girls room",
        title: "Double girls room",
        subtitle: "Currently available",
        roomGroup: "girls",
      });
    }
    if (doubleBoys <= 0 && doubleGirls <= 0) {
      choices.push({
        reply: "double room",
        title: "Double room",
        subtitle: "Currently available",
        roomGroup: "",
      });
    }
  }
  if (triple > 0) {
    if (tripleBoys > 0) {
      choices.push({
        reply: "triple boys room",
        title: "Triple boys room",
        subtitle: "Currently available",
        roomGroup: "boys",
      });
    }
    if (tripleGirls > 0) {
      choices.push({
        reply: "triple girls room",
        title: "Triple girls room",
        subtitle: "Currently available",
        roomGroup: "girls",
      });
    }
    if (tripleBoys <= 0 && tripleGirls <= 0) {
      choices.push({
        reply: "triple room",
        title: "Triple room",
        subtitle: "Currently available",
        roomGroup: "",
      });
    }
  }
  return choices;
}

async function startSession() {
  try {
    const data = await api("/api/session", { method: "POST" });
    renderSession(data.session);
  } catch (err) {
    els.chatLog.innerHTML = `<div class="empty-state"><h3>Unable to connect</h3><p style="color:var(--text-dim);font-size:0.85rem">Please refresh the page to try again.</p></div>`;
  }
}

els.intakeForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.sessionId) {
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
  if (!text || !state.sessionId || state.messagePending) return;
  els.messageInput.value = "";
  state.messagePending = true;
  els.messageInput.disabled = true;
  els.sendBtn.disabled = true;
  els.sendBtn.textContent = "Sending...";
  try {
    const data = await api(`/api/session/${state.sessionId}/message`, {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    renderSession(data.session);
  } catch (err) {
    els.messageInput.value = text;
    alert(err.message);
  } finally {
    state.messagePending = false;
    els.sendBtn.textContent = "Send ↗";
    renderSession(state.session);
  }
});

els.passportUploadBtn?.addEventListener("click", () => {
  if (!state.sessionId || els.passportUploadBtn.disabled) return;
  els.passportFileInput?.click();
});

els.passportFileInput?.addEventListener("change", async (event) => {
  const file = event.target.files?.[0];
  if (!file || !state.sessionId) return;

  const formData = new FormData();
  formData.append("file", file);

  try {
    els.passportUploadBtn.disabled = true;
    els.passportUploadBtn.textContent = "Uploading...";
    const response = await fetch(`${window.API_PREFIX || ""}/api/session/${state.sessionId}/passport_attachment`, {
      method: "POST",
      body: formData,
    });
    if (!response.ok) {
      const errData = await response.json().catch(() => ({}));
      throw new Error(errData.error || `Upload failed: ${response.status}`);
    }
    const data = await response.json();
    renderSession(data.session);
    if (data.session?.stage === "awaiting_passport_upload") {
      els.messageInput.value = "done";
      els.messageInput.focus();
      els.messageInput.select();
    }
  } catch (err) {
    alert(err.message);
  } finally {
    if (els.passportFileInput) {
      els.passportFileInput.value = "";
    }
    if (state.session) {
      renderPassportUpload(state.session);
    }
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

startSession();
