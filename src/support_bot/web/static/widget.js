/*
 * BoxHub support chat widget.
 * Usage: <script src="https://your-host/widget.js" defer></script>
 * Optional attributes: data-title, data-color, data-api (API origin, defaults to the script's origin).
 * Opening from code: window.BoxHubChat.open("Optional question").
 */
(() => {
  "use strict";

  if (window.BoxHubChat) return;

  const script = document.currentScript;
  const config = {
    api: (script && script.dataset.api) || (script ? new URL(script.src).origin : location.origin),
    title: (script && script.dataset.title) || "BoxHub Support",
    color: (script && script.dataset.color) || "#1f6f5c",
  };

  const TEXTS = {
    subtitle: "Answers from our knowledge base",
    greeting: "Hi! I can answer questions about units, prices, access and payments. What would you like to know?",
    placeholder: "Type your question…",
    send: "Send",
    open: "Open support chat",
    close: "Close chat",
    reset: "Start a new conversation",
    sources: "Sources",
    handedOff: "Passed to an operator",
    error: "Sorry, something went wrong. Please try again in a moment.",
    rateLimited: "You are sending messages too quickly. Please wait a minute and try again.",
    tooLong: "Please keep your question under 1000 characters.",
  };
  const SUGGESTIONS = [
    "How much is a medium unit?",
    "Can I store a motorcycle?",
    "What happens if I pay late?",
  ];
  const MAX_QUESTION = 1000;
  const STORAGE_KEY = "boxhub-chat";

  // ---------- state ----------

  const storage = {
    load() {
      try {
        return JSON.parse(sessionStorage.getItem(STORAGE_KEY) || "null");
      } catch {
        return null;
      }
    },
    save(value) {
      try {
        sessionStorage.setItem(STORAGE_KEY, JSON.stringify(value));
      } catch {
        // private mode or storage disabled: the chat still works, it just won't survive a reload
      }
    },
  };

  function newSessionId() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return `s-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
  }

  const saved = storage.load();
  const state = {
    sessionId: (saved && saved.sessionId) || newSessionId(),
    messages: (saved && Array.isArray(saved.messages) ? saved.messages : []).slice(-50),
    busy: false,
  };

  function persist() {
    const finished = state.messages.filter((message) => !message.pending);
    storage.save({ sessionId: state.sessionId, messages: finished.slice(-50) });
  }

  // ---------- DOM ----------

  const host = document.createElement("div");
  host.id = "boxhub-chat";
  const root = host.attachShadow({ mode: "open" });
  root.innerHTML = `
    <style>${styles(config.color)}</style>
    <button class="launcher" type="button" aria-label="${TEXTS.open}" aria-expanded="false">
      ${icon("chat")}
    </button>
    <section class="panel" role="dialog" aria-label="${escapeAttr(config.title)}" hidden>
      <header>
        <div class="avatar">${icon("box")}</div>
        <div class="heading">
          <div class="title"></div>
          <div class="subtitle">${TEXTS.subtitle}</div>
        </div>
        <button class="icon-button reset" type="button" aria-label="${TEXTS.reset}" title="${TEXTS.reset}">${icon("refresh")}</button>
        <button class="icon-button close" type="button" aria-label="${TEXTS.close}" title="${TEXTS.close}">${icon("close")}</button>
      </header>
      <div class="messages" aria-live="polite"></div>
      <div class="suggestions"></div>
      <form class="composer">
        <textarea rows="1" maxlength="${MAX_QUESTION}" placeholder="${TEXTS.placeholder}" aria-label="${TEXTS.placeholder}"></textarea>
        <button class="send" type="submit" aria-label="${TEXTS.send}">${icon("send")}</button>
      </form>
    </section>
  `;

  const $ = (selector) => root.querySelector(selector);
  const launcher = $(".launcher");
  const panel = $(".panel");
  const list = $(".messages");
  const suggestions = $(".suggestions");
  const form = $(".composer");
  const input = $("textarea");
  const sendButton = $(".send");
  $(".title").textContent = config.title;

  for (const question of SUGGESTIONS) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    chip.textContent = question;
    chip.addEventListener("click", () => ask(question));
    suggestions.append(chip);
  }

  // ---------- rendering ----------

  function render() {
    list.replaceChildren(renderBubble({ role: "bot", text: TEXTS.greeting }));
    for (const message of state.messages) list.append(renderBubble(message));
    suggestions.hidden = state.messages.length > 0;
    scrollToBottom(true);
  }

  function renderBubble(message) {
    const item = document.createElement("div");
    item.className = `message ${message.role}`;
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    item.append(bubble);
    updateBubble(item, message);
    return item;
  }

  function updateBubble(item, message) {
    const bubble = item.querySelector(".bubble");
    if (message.pending && !message.text) {
      bubble.innerHTML = '<span class="typing" aria-label="Typing"><i></i><i></i><i></i></span>';
    } else {
      // textContent, not innerHTML: the answer is model output and must never become markup
      bubble.textContent = message.text;
    }

    item.querySelector(".meta")?.remove();
    if (message.pending) return;
    if (message.handedOff) {
      const note = document.createElement("div");
      note.className = "meta handoff";
      note.textContent = TEXTS.handedOff;
      item.append(note);
    } else if (message.sources && message.sources.length) {
      item.append(renderSources(message.sources));
    }
  }

  function renderSources(sources) {
    const meta = document.createElement("div");
    meta.className = "meta sources";
    const label = document.createElement("span");
    label.textContent = `${TEXTS.sources}:`;
    meta.append(label);
    for (const source of sources) {
      const isLink = typeof source.url === "string" && /^https?:\/\//.test(source.url);
      const chip = document.createElement(isLink ? "a" : "span");
      chip.className = "source";
      chip.textContent = source.title;
      if (isLink) {
        chip.href = source.url;
        chip.target = "_blank";
        chip.rel = "noopener noreferrer";
      }
      meta.append(chip);
    }
    return meta;
  }

  function scrollToBottom(force) {
    const nearBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 80;
    if (force || nearBottom) list.scrollTop = list.scrollHeight;
  }

  // ---------- chat ----------

  async function ask(rawQuestion) {
    const question = rawQuestion.trim();
    if (!question || state.busy) return;
    if (question.length > MAX_QUESTION) {
      appendMessage({ role: "bot", text: TEXTS.tooLong, handedOff: false });
      return;
    }

    open();
    setBusy(true);
    appendMessage({ role: "user", text: question });
    const answer = { role: "bot", text: "", pending: true };
    const item = appendMessage(answer);
    const update = () => {
      updateBubble(item, answer);
      scrollToBottom(false);
    };

    try {
      const response = await fetch(`${config.api}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, session_id: state.sessionId }),
      });
      if (response.status === 429) throw new ChatError(TEXTS.rateLimited);
      if (!response.ok || !response.body) throw new ChatError(TEXTS.error);

      let finished = false;
      await readEvents(response.body, (event, data) => {
        if (event === "delta") {
          answer.text += data.text;
        } else if (event === "done") {
          // the final text wins: it replaces a partial answer if the model was cut off
          answer.text = data.answer;
          answer.sources = data.sources;
          answer.handedOff = !data.answered;
          finished = true;
        } else if (event === "error") {
          answer.text = data.message;
          answer.handedOff = true;
          finished = true;
        }
        update();
      });
      if (!finished) throw new ChatError(TEXTS.error);
    } catch (error) {
      answer.text = error instanceof ChatError ? error.message : TEXTS.error;
      answer.sources = [];
      answer.handedOff = false;
    } finally {
      answer.pending = false;
      update();
      persist();
      setBusy(false);
    }
  }

  class ChatError extends Error {}

  async function readEvents(body, onEvent) {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      const blocks = buffer.split(/\r?\n\r?\n/);
      buffer = done ? "" : blocks.pop();
      for (const block of blocks) dispatch(block, onEvent);
      if (done) return;
    }
  }

  function dispatch(block, onEvent) {
    let event = "message";
    const data = [];
    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
    }
    if (!data.length) return;
    try {
      onEvent(event, JSON.parse(data.join("\n")));
    } catch {
      // ignore a malformed event instead of breaking the whole answer
    }
  }

  function appendMessage(message) {
    state.messages.push(message);
    suggestions.hidden = true;
    const item = renderBubble(message);
    list.append(item);
    scrollToBottom(true);
    if (!message.pending) persist();
    return item;
  }

  function setBusy(busy) {
    state.busy = busy;
    sendButton.disabled = busy;
    form.classList.toggle("busy", busy);
  }

  function reset() {
    if (state.busy) return;
    state.sessionId = newSessionId();
    state.messages = [];
    persist();
    render();
    input.focus();
  }

  // ---------- open / close ----------

  function open(question) {
    if (panel.hidden) {
      panel.hidden = false;
      launcher.setAttribute("aria-expanded", "true");
      host.classList.add("open");
      scrollToBottom(true);
      // на телефоне фокус сразу открывает клавиатуру и закрывает половину чата
      if (!matchMedia("(pointer: coarse)").matches) setTimeout(() => input.focus(), 50);
    }
    if (typeof question === "string" && question.trim()) ask(question);
  }

  function close() {
    panel.hidden = true;
    launcher.setAttribute("aria-expanded", "false");
    host.classList.remove("open");
    launcher.focus();
  }

  launcher.addEventListener("click", () => (panel.hidden ? open() : close()));
  $(".close").addEventListener("click", close);
  $(".reset").addEventListener("click", reset);
  panel.addEventListener("keydown", (event) => {
    if (event.key === "Escape") close();
  });

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const question = input.value;
    if (!question.trim() || state.busy) return;
    input.value = "";
    autosize();
    ask(question);
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  input.addEventListener("input", autosize);

  function autosize() {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 120)}px`;
  }

  // ---------- helpers ----------

  function escapeAttr(value) {
    return String(value).replace(/[&"<>]/g, (char) => `&#${char.charCodeAt(0)};`);
  }

  function icon(name) {
    const paths = {
      chat: '<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v8a2.5 2.5 0 0 1-2.5 2.5H10l-4.2 3.6A.5.5 0 0 1 5 19.2V16h-.5A2.5 2.5 0 0 1 2 13.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="M8 8.5h8M8 11.5h5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>',
      box: '<path d="M12 3 4 7v10l8 4 8-4V7l-8-4Z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="m4 7 8 4 8-4M12 11v10" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>',
      close: '<path d="m6 6 12 12M18 6 6 18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
      refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
      send: '<path d="M4 12 20 4l-6 16-3-7-7-1Z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>',
    };
    return `<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">${paths[name]}</svg>`;
  }

  function styles(accent) {
    return `
      :host { all: initial; }
      * { box-sizing: border-box; }
      :host, button, textarea {
        font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
      }
      .launcher, .panel { --accent: ${accent}; }
      .launcher {
        position: fixed; right: 24px; bottom: 24px; z-index: 2147483000;
        width: 60px; height: 60px; border: 0; border-radius: 50%;
        background: var(--accent); color: #fff; cursor: pointer;
        display: grid; place-items: center;
        box-shadow: 0 8px 24px rgba(0, 0, 0, .22);
        transition: transform .15s ease;
      }
      .launcher:hover { transform: scale(1.06); }
      .launcher svg { width: 28px; height: 28px; }
      .panel {
        position: fixed; right: 24px; bottom: 96px; z-index: 2147483000;
        width: 380px; height: min(600px, calc(100vh - 120px));
        display: flex; flex-direction: column;
        background: #fff; color: #1d2433; border-radius: 18px; overflow: hidden;
        box-shadow: 0 16px 48px rgba(15, 23, 42, .22);
        font-size: 15px; line-height: 1.45;
        animation: rise .18s ease-out;
      }
      .panel[hidden] { display: none; }
      header {
        display: flex; align-items: center; gap: 10px;
        padding: 14px 12px 14px 16px; background: var(--accent); color: #fff;
      }
      .avatar {
        width: 38px; height: 38px; border-radius: 50%; flex: none;
        display: grid; place-items: center; background: rgba(255, 255, 255, .18);
      }
      .heading { flex: 1; min-width: 0; }
      .title { font-weight: 650; font-size: 16px; }
      .subtitle { font-size: 12.5px; opacity: .85; }
      .icon-button {
        width: 34px; height: 34px; border: 0; border-radius: 10px; flex: none;
        background: transparent; color: inherit; cursor: pointer;
        display: grid; place-items: center;
      }
      .icon-button:hover { background: rgba(255, 255, 255, .16); }
      .icon-button svg { width: 18px; height: 18px; }
      .messages {
        flex: 1; overflow-y: auto; padding: 16px;
        display: flex; flex-direction: column; gap: 12px; background: #f6f7f9;
      }
      .message { display: flex; flex-direction: column; max-width: 86%; }
      .message.user { align-self: flex-end; align-items: flex-end; }
      .message.bot { align-self: flex-start; }
      .bubble {
        padding: 10px 14px; border-radius: 16px; white-space: pre-wrap; overflow-wrap: anywhere;
      }
      .user .bubble { background: var(--accent); color: #fff; border-bottom-right-radius: 4px; }
      .bot .bubble {
        background: #fff; border: 1px solid #e3e6eb; border-bottom-left-radius: 4px;
      }
      .meta {
        margin-top: 6px; font-size: 12.5px; color: #5b6475;
        display: flex; flex-wrap: wrap; align-items: center; gap: 6px;
      }
      .source {
        padding: 2px 8px; border-radius: 999px; background: #e9eef3; color: #334155;
        text-decoration: none;
      }
      a.source:hover { text-decoration: underline; }
      .handoff::before { content: "↗"; }
      .suggestions {
        display: flex; flex-wrap: wrap; gap: 8px; padding: 0 16px 12px; background: #f6f7f9;
      }
      .suggestions[hidden] { display: none; }
      .chip {
        border: 1px solid var(--accent); color: var(--accent); background: #fff;
        border-radius: 999px; padding: 6px 12px; font-size: 13.5px; cursor: pointer;
      }
      .chip:hover { background: var(--accent); color: #fff; }
      .composer {
        display: flex; align-items: flex-end; gap: 8px; padding: 12px;
        border-top: 1px solid #e3e6eb; background: #fff;
      }
      textarea {
        flex: 1; resize: none; border: 1px solid #d5dae1; border-radius: 12px;
        padding: 10px 12px; font-size: 15px; line-height: 1.4; max-height: 120px;
        color: inherit; background: #fff; outline: none;
      }
      textarea:focus { border-color: var(--accent); box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 18%, transparent); }
      .send {
        width: 42px; height: 42px; border: 0; border-radius: 12px; flex: none;
        background: var(--accent); color: #fff; cursor: pointer;
        display: grid; place-items: center;
      }
      .send:disabled { opacity: .5; cursor: default; }
      .send svg { width: 20px; height: 20px; }
      .typing { display: inline-flex; gap: 4px; padding: 4px 0; }
      .typing i {
        width: 7px; height: 7px; border-radius: 50%; background: #9aa3b2;
        animation: blink 1.2s infinite ease-in-out;
      }
      .typing i:nth-child(2) { animation-delay: .15s; }
      .typing i:nth-child(3) { animation-delay: .3s; }
      textarea:focus-visible { outline: none; }
      button:focus-visible, a:focus-visible {
        outline: 2px solid var(--accent); outline-offset: 2px;
      }
      @keyframes blink { 0%, 80%, 100% { opacity: .3; } 40% { opacity: 1; } }
      @keyframes rise { from { opacity: 0; transform: translateY(8px); } }
      @media (max-width: 480px) {
        .panel {
          right: 0; bottom: 0; width: 100vw; height: 100dvh; border-radius: 0;
        }
        :host(.open) .launcher { display: none; }
        .launcher { right: 16px; bottom: 16px; }
      }
      @media (prefers-reduced-motion: reduce) {
        .panel, .typing i, .launcher { animation: none; transition: none; }
      }
    `;
  }

  // ---------- start ----------

  render();
  const mount = () => document.body.append(host);
  if (document.body) mount();
  else document.addEventListener("DOMContentLoaded", mount);

  window.BoxHubChat = { open, close, reset };
})();
