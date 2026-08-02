const form = document.querySelector("#chat-form");
const input = document.querySelector("#message");
const messages = document.querySelector("#messages");
const status = document.querySelector("#status");
const button = document.querySelector("#send");
const sessionId = crypto.randomUUID ? crypto.randomUUID() : String(Date.now());

appendMessage("agent", "Hi, I am Nova from Nanovate. How can I help today?");

function appendMessage(role, content, options = {}) {
  const item = document.createElement("article");
  item.className = "nv-niva-message-item";
  if (role === "user") item.classList.add("nv-niva-message-item--user");
  if (options.error) item.classList.add("nv-niva-message-item--error");

  const avatar = document.createElement("span");
  avatar.className = "nv-niva-message-item__avatar";
  if (role === "user") {
    avatar.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><circle cx="12" cy="7" r="4" stroke="currentColor" stroke-width="2"/></svg>';
  } else {
    const image = document.createElement("img");
    image.src = "/assets/agent_icon.svg";
    image.alt = "Nova";
    avatar.appendChild(image);
  }

  const contentWrap = document.createElement("div");
  contentWrap.className = "nv-niva-message-item__content";

  const meta = document.createElement("div");
  meta.className = "nv-niva-message-item__meta";

  const name = document.createElement("span");
  name.className = "nv-niva-message-item__name";
  name.textContent = role === "user" ? "You" : "Nova";

  const time = document.createElement("span");
  time.className = "nv-niva-message-item__time";
  time.textContent = new Intl.DateTimeFormat([], {
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date());

  if (role === "user") {
    meta.append(time, name);
  } else {
    meta.append(name, time);
  }

  const bubble = document.createElement("div");
  bubble.className = "nv-niva-message-item__bubble";
  bubble.textContent = content;

  contentWrap.append(meta, bubble);
  if (role === "user") {
    item.append(contentWrap, avatar);
  } else {
    item.append(avatar, contentWrap);
  }

  messages.appendChild(item);
  messages.scrollTop = messages.scrollHeight;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text) return;

  appendMessage("user", text);
  input.value = "";
  status.textContent = "Thinking";
  button.disabled = true;

  try {
    const response = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, session_id: sessionId }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Request failed");
    appendMessage("agent", payload.response);
    status.textContent = "Ready";
  } catch (error) {
    appendMessage("agent", `Error: ${error.message}`, { error: true });
    status.textContent = "Error";
  } finally {
    button.disabled = false;
    input.focus();
  }
});
