const form = document.querySelector("#chat-form");
const input = document.querySelector("#message");
const messages = document.querySelector("#messages");
const send = document.querySelector("#send");
const refreshStatus = document.querySelector("#refresh-status");
const calStatus = document.querySelector("#cal-status");
const targetStatus = document.querySelector("#target-status");
const llmStatus = document.querySelector("#llm-status");
const connectionPill = document.querySelector("#connection-pill");
const conversationId = crypto.randomUUID();

function classFor(value) {
  return value ? "ok" : "danger";
}

function setStatusText(node, text, className) {
  node.textContent = text;
  node.className = className;
}

async function loadStatus() {
  try {
    const response = await fetch("/health");
    const body = await response.json();
    setStatusText(calStatus, body.cal_configured ? "Connected" : "Mock mode", classFor(body.cal_configured));
    setStatusText(targetStatus, body.booking_target_configured ? "Configured" : "Missing", classFor(body.booking_target_configured));
    setStatusText(llmStatus, body.llm_provider === "openai" ? "OpenAI" : body.llm_provider, body.llm_provider === "openai" ? "ok" : "warn");
    connectionPill.textContent = body.llm_provider === "openai" ? "OpenAI active" : "Local mode";
  } catch (error) {
    setStatusText(calStatus, "Unavailable", "danger");
    setStatusText(targetStatus, "Unavailable", "danger");
    setStatusText(llmStatus, "Unavailable", "danger");
    connectionPill.textContent = "Offline";
  }
}

function addMessage({ text, role = "assistant", status = "ok", meta = [] }) {
  const wrapper = document.createElement("div");
  wrapper.className = `message ${role} ${status === "error" ? "error" : ""}`;

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  wrapper.appendChild(bubble);

  if (meta.length) {
    const metaRow = document.createElement("div");
    metaRow.className = "meta";
    meta.forEach((item) => {
      const tag = document.createElement("span");
      tag.className = `tag ${item === "openai" ? "openai" : ""}`;
      tag.textContent = item;
      metaRow.appendChild(tag);
    });
    wrapper.appendChild(metaRow);
  }

  messages.appendChild(wrapper);
  messages.scrollTop = messages.scrollHeight;
  return wrapper;
}

function summarizeResponse(body) {
  const parts = [body.reply || JSON.stringify(body)];
  if (body.booking?.uid) {
    parts.push(`\nBooking UID: ${body.booking.uid}`);
  }
  return parts.join("");
}

async function sendMessage(text) {
  addMessage({ text, role: "user" });
  send.disabled = true;
  input.disabled = true;
  const loading = addMessage({ text: "Working...", role: "assistant", meta: ["waiting"] });

  try {
    const response = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        conversation_id: conversationId,
        message: text,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      }),
    });
    const body = await response.json();
    loading.remove();
    const meta = [];
    if (body.action) meta.push(body.action);
    if (body.extractor) meta.push(body.extractor);
    if (body.status) meta.push(body.status);
    addMessage({
      text: summarizeResponse(body),
      role: "assistant",
      status: body.status,
      meta,
    });
  } catch (error) {
    loading.remove();
    addMessage({
      text: `Request failed: ${error.message}`,
      role: "assistant",
      status: "error",
      meta: ["network"],
    });
  } finally {
    send.disabled = false;
    input.disabled = false;
    input.focus();
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  await sendMessage(text);
});

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

refreshStatus.addEventListener("click", loadStatus);

document.querySelectorAll("[data-example]").forEach((button) => {
  button.addEventListener("click", () => {
    input.value = button.dataset.example;
    input.focus();
  });
});

loadStatus();
addMessage({
  text: "Hi. I can book, list, cancel, and reschedule Cal.com bookings. Use a real booking UID for cancel/reschedule.",
  role: "assistant",
  meta: ["ready"],
});
