/* Embeddable chat widget. The host page cannot style this tree, and this tree
   does not style the host page. Every visitor-visible string is assigned with
   textContent. */
(function () {
  "use strict";

  var script = document.currentScript;
  if (!script || window.__siteAiWidgetLoaded) {
    return;
  }
  window.__siteAiWidgetLoaded = true;

  var MAX_CHARS = 1000;
  var STRINGS = {
    pt: {
      title: "Assistente",
      greeting: "Olá. Em que posso ajudar?",
      placeholder: "Escreva a sua pergunta",
      send: "Enviar",
      close: "Fechar",
      open: "Abrir conversa",
      typing: "A escrever…",
      errorKey: "A chave do widget não é válida.",
      errorOrigin: "Este site não está autorizado a usar o chat.",
      errorRate: "Demasiados pedidos. Tente de novo em breve.",
      errorRetry: "Não foi possível enviar. Tente de novo.",
      tooLong: "A pergunta pode ter no máximo 1000 caracteres.",
      emptyAnswer: "Não consegui responder a isso.",
      rowsOne: "Resultado com 1 linha.",
      rowsMany: "Resultado com {n} linhas."
    },
    en: {
      title: "Assistant",
      greeting: "Hello. How can I help?",
      placeholder: "Type your question",
      send: "Send",
      close: "Close",
      open: "Open chat",
      typing: "Typing…",
      errorKey: "The widget key is not valid.",
      errorOrigin: "This site is not allowed to use the chat.",
      errorRate: "Too many requests, try again soon.",
      errorRetry: "Could not send. Try again.",
      tooLong: "A question can be at most 1000 characters.",
      emptyAnswer: "I could not answer that.",
      rowsOne: "Result with 1 row.",
      rowsMany: "Result with {n} rows."
    }
  };

  var key = (script.getAttribute("data-key") || "").trim();
  var lang = resolveLang(script.getAttribute("data-lang"));
  var copy = STRINGS[lang];
  var apiBase = apiBaseFrom(script.src || "");
  var storageKey = "site-ai-widget:" + key + ":session";

  function resolveLang(attr) {
    var given = String(attr || "").trim().toLowerCase();
    if (given === "pt" || given === "en") {
      return given;
    }
    var nav = "";
    try {
      nav = String(navigator.language || "").toLowerCase();
    } catch (err) {
      nav = "";
    }
    if (nav.indexOf("pt") === 0) {
      return "pt";
    }
    return "en";
  }

  function apiBaseFrom(src) {
    /* No src (an inline copy) means no API origin. Never fall back to a
       relative URL, which would post the visitor's text to the host page. */
    if (!src) {
      return "";
    }
    try {
      var url = new URL(src, window.location.href);
      var path = url.pathname || "";
      var suffix = "/embed.js";
      if (path.length >= suffix.length && path.slice(path.length - suffix.length) === suffix) {
        path = path.slice(0, path.length - suffix.length);
      }
      if (path.length > 1 && path.charAt(path.length - 1) === "/") {
        path = path.slice(0, -1);
      }
      return url.origin + path;
    } catch (err) {
      return "";
    }
  }

  function readToken() {
    try {
      return localStorage.getItem(storageKey) || "";
    } catch (err) {
      return "";
    }
  }

  function writeToken(token) {
    if (!token) {
      return;
    }
    try {
      localStorage.setItem(storageKey, token);
    } catch (err) {
      /* Storage can be blocked. The chat still works for this page view. */
    }
  }

  function cellText(value) {
    if (value === null || value === undefined) {
      return "";
    }
    return String(value);
  }

  function formatAnswer(data) {
    var lines = [];
    var mode = data && data.mode;
    if (mode === "sql") {
      var columns = data.columns || [];
      var rows = data.rows || [];
      if (columns.length) {
        lines.push(columns.map(cellText).join(" | "));
      }
      for (var i = 0; i < rows.length; i++) {
        var row = rows[i] || [];
        var cells = [];
        for (var j = 0; j < row.length; j++) {
          cells.push(cellText(row[j]));
        }
        lines.push(cells.join(" | "));
      }
    } else if (mode === "docs" && typeof data.answer === "string" && data.answer) {
      lines.push(data.answer);
    }
    /* mode "none": the server's fallback text is English only, so the caller
       shows the localized copy.emptyAnswer instead. */
    var sources = (data && data.sources) || [];
    for (var s = 0; s < sources.length; s++) {
      var src = sources[s] || {};
      var name = cellText(src.document_name);
      if (!name) {
        continue;
      }
      var page = cellText(src.page);
      lines.push(page ? name + " (p. " + page + ")" : name);
    }
    return lines.join("\n");
  }

  /* History stores no table rows, only how many there were. */
  function historyText(item) {
    if (item.role === "assistant" && item.mode === "none") {
      return copy.emptyAnswer;
    }
    if (item.role === "assistant" && item.mode === "sql") {
      var n = typeof item.row_count === "number" ? item.row_count : 0;
      return n === 1 ? copy.rowsOne : copy.rowsMany.replace("{n}", String(n));
    }
    return typeof item.text === "string" ? item.text : "";
  }

  function errorKind(status, data) {
    var code = "";
    if (data && data.detail && typeof data.detail.code === "string") {
      code = data.detail.code;
    }
    if (status === 401 || code === "widget_key_invalid") {
      return "key";
    }
    if (status === 403 || code === "origin_not_allowed") {
      return "origin";
    }
    if (status === 429 || code === "rate_limited") {
      return "rate";
    }
    if (status >= 200 && status < 300 && data) {
      return "ok";
    }
    return "retry";
  }

  function readJson(res) {
    return res.text().then(function (raw) {
      try {
        return JSON.parse(raw);
      } catch (err) {
        return null;
      }
    });
  }

  function postJson(path, payload) {
    return fetch(apiBase + path, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json"
      },
      credentials: "omit",
      body: JSON.stringify(payload)
    });
  }

  function widgetPath(action) {
    return "/v1/widget/" + encodeURIComponent(key) + "/" + action;
  }

  class SiteAiWidget extends HTMLElement {
    connectedCallback() {
      if (this._ready) {
        return;
      }
      this._ready = true;
      this._open = false;
      this._busy = false;
      this._blocked = false;
      this._historyStarted = false;
      this._build();
      this._onEscape = (event) => {
        if (event.key === "Escape" && this._open) {
          event.preventDefault();
          this.close();
        }
      };
      document.addEventListener("keydown", this._onEscape);
    }

    disconnectedCallback() {
      if (this._onEscape) {
        document.removeEventListener("keydown", this._onEscape);
      }
    }

    _build() {
      /* Closed: other scripts on the host page cannot reach this tree via
         element.shadowRoot. */
      var root = this.attachShadow({ mode: "closed" });
      var style = document.createElement("style");
      style.textContent = CSS;
      root.appendChild(style);

      var panel = document.createElement("section");
      panel.className = "panel";
      panel.setAttribute("role", "dialog");
      panel.setAttribute("aria-modal", "true");
      panel.setAttribute("aria-hidden", "true");

      var header = document.createElement("header");
      header.className = "header";
      var title = document.createElement("h2");
      title.textContent = copy.title;
      title.id = "title";
      panel.setAttribute("aria-labelledby", "title");
      var closeBtn = document.createElement("button");
      closeBtn.type = "button";
      closeBtn.className = "close";
      closeBtn.setAttribute("aria-label", copy.close);
      closeBtn.textContent = "\u00d7";
      closeBtn.addEventListener("click", () => this.close());
      header.appendChild(title);
      header.appendChild(closeBtn);

      var log = document.createElement("div");
      log.className = "log";
      log.setAttribute("role", "log");
      log.setAttribute("aria-live", "polite");

      var typing = document.createElement("p");
      typing.className = "typing";
      typing.textContent = copy.typing;
      typing.setAttribute("aria-hidden", "true");

      var composer = document.createElement("div");
      composer.className = "composer";
      var input = document.createElement("input");
      input.type = "text";
      input.maxLength = MAX_CHARS;
      input.placeholder = copy.placeholder;
      input.setAttribute("aria-label", copy.placeholder);
      input.autocomplete = "off";
      input.enterKeyHint = "send";
      var counter = document.createElement("span");
      counter.className = "counter";
      var send = document.createElement("button");
      send.type = "button";
      send.className = "send";
      send.textContent = copy.send;
      send.addEventListener("click", () => this._send());
      input.addEventListener("input", () => {
        this._renderCount();
        this._sync();
      });
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey) {
          event.preventDefault();
          this._send();
        }
      });
      composer.appendChild(input);
      composer.appendChild(counter);
      composer.appendChild(send);

      panel.appendChild(header);
      panel.appendChild(log);
      panel.appendChild(typing);
      panel.appendChild(composer);

      var bubble = document.createElement("button");
      bubble.type = "button";
      bubble.className = "bubble";
      bubble.setAttribute("aria-label", copy.open);
      bubble.setAttribute("aria-expanded", "false");
      bubble.setAttribute("aria-controls", "panel");
      panel.id = "panel";
      bubble.appendChild(icon());
      bubble.addEventListener("click", () => {
        if (this._open) {
          this.close();
        } else {
          this.open();
        }
      });

      root.appendChild(panel);
      root.appendChild(bubble);

      this._panel = panel;
      this._log = log;
      this._typing = typing;
      this._input = input;
      this._counter = counter;
      this._sendBtn = send;
      this._closeBtn = closeBtn;
      this._bubble = bubble;
      this._renderCount();
      this._sync();
    }

    open() {
      this._open = true;
      this.classList.add("is-open");
      this._panel.classList.add("is-open");
      this._panel.setAttribute("aria-hidden", "false");
      this._bubble.setAttribute("aria-expanded", "true");
      this._bubble.setAttribute("aria-label", copy.close);
      if (!this._historyStarted) {
        this._historyStarted = true;
        this._loadHistory();
      }
      this._focusComposer();
    }

    close() {
      if (!this._open) {
        return;
      }
      this._open = false;
      this.classList.remove("is-open");
      this._panel.classList.remove("is-open");
      this._panel.setAttribute("aria-hidden", "true");
      this._bubble.setAttribute("aria-expanded", "false");
      this._bubble.setAttribute("aria-label", copy.open);
      this._bubble.focus();
    }

    _focusComposer() {
      if (!this._open) {
        return;
      }
      if (this._blocked || this._input.disabled) {
        this._closeBtn.focus();
      } else {
        this._input.focus();
      }
    }

    _renderCount() {
      this._counter.textContent = String(this._input.value.length) + " / " + String(MAX_CHARS);
    }

    _sync() {
      var blocked = this._blocked || this._busy;
      this._input.disabled = blocked;
      this._sendBtn.disabled = blocked || this._input.value.trim().length === 0;
      this._typing.classList.toggle("is-on", this._busy);
      this._typing.setAttribute("aria-hidden", this._busy ? "false" : "true");
    }

    _scroll() {
      this._log.scrollTop = this._log.scrollHeight;
    }

    _add(role, text) {
      var item = document.createElement("div");
      item.className = role === "user" ? "msg user" : "msg assistant";
      var paragraph = document.createElement("p");
      paragraph.textContent = text;
      item.appendChild(paragraph);
      this._log.appendChild(item);
      this._scroll();
    }

    _fail(kind) {
      if (kind === "key" || kind === "origin") {
        this._blocked = true;
        this._add("assistant", kind === "key" ? copy.errorKey : copy.errorOrigin);
        return;
      }
      if (kind === "rate") {
        this._add("assistant", copy.errorRate);
        return;
      }
      this._add("assistant", copy.errorRetry);
    }

    _loadHistory() {
      if (!key || !apiBase) {
        this._fail("key");
        this._sync();
        this._focusComposer();
        return;
      }
      var token = readToken();
      if (!token) {
        this._add("assistant", copy.greeting);
        return;
      }
      this._busy = true;
      this._sync();
      var self = this;
      postJson(widgetPath("history"), { session_token: token })
        .then(function (res) {
          return readJson(res).then(function (data) {
            return { status: res.status, data: data };
          });
        })
        .then(function (result) {
          if (!self.isConnected) {
            return;
          }
          var kind = errorKind(result.status, result.data);
          if (kind !== "ok") {
            self._fail(kind);
            return;
          }
          var messages = result.data.messages || [];
          var shown = 0;
          for (var i = 0; i < messages.length; i++) {
            var item = messages[i];
            if (!item || (item.role !== "user" && item.role !== "assistant")) {
              continue;
            }
            var text = historyText(item);
            if (!text) {
              continue;
            }
            self._add(item.role, text);
            shown += 1;
          }
          if (!shown) {
            self._add("assistant", copy.greeting);
          }
        })
        .catch(function () {
          if (self.isConnected) {
            self._fail("retry");
          }
        })
        .then(function () {
          if (!self.isConnected) {
            return;
          }
          self._busy = false;
          self._sync();
          self._scroll();
          self._focusComposer();
        });
    }

    _send() {
      if (this._busy || this._blocked || !apiBase) {
        return;
      }
      var text = this._input.value.trim();
      if (!text) {
        return;
      }
      if (text.length > MAX_CHARS) {
        this._add("assistant", copy.tooLong);
        return;
      }
      this._busy = true;
      this._sync();
      this._add("user", text);
      this._input.value = "";
      this._renderCount();
      var self = this;
      var payload = {
        question: text,
        session_token: readToken() || null,
        lang: lang
      };
      postJson(widgetPath("ask"), payload)
        .then(function (res) {
          return readJson(res).then(function (data) {
            return { status: res.status, data: data };
          });
        })
        .then(function (result) {
          if (!self.isConnected) {
            return;
          }
          var kind = errorKind(result.status, result.data);
          if (kind !== "ok") {
            self._fail(kind);
            return;
          }
          var token = result.data.session_token;
          if (typeof token === "string" && token) {
            writeToken(token);
          }
          var answer = formatAnswer(result.data);
          self._add("assistant", answer || copy.emptyAnswer);
        })
        .catch(function () {
          if (self.isConnected) {
            self._fail("retry");
          }
        })
        .then(function () {
          if (!self.isConnected) {
            return;
          }
          self._busy = false;
          self._sync();
          self._scroll();
          self._focusComposer();
        });
    }
  }

  function icon() {
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("width", "28");
    svg.setAttribute("height", "28");
    svg.setAttribute("aria-hidden", "true");
    var path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("fill", "currentColor");
    path.setAttribute("d", "M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v8A2.5 2.5 0 0 1 17.5 16H9l-4.2 3.2c-.7.5-1.8 0-1.8-.9V5.5z");
    svg.appendChild(path);
    return svg;
  }

  var CSS = [
    ":host { all: initial; display: block; position: fixed; z-index: 2147483000; pointer-events: none; color-scheme: light; }",
    ".bubble, .panel, .bubble *, .panel * { box-sizing: border-box; font-family: \"Segoe UI\", system-ui, sans-serif; }",
    ".bubble { pointer-events: auto; position: fixed; right: 20px; bottom: 20px; width: 56px; height: 56px;",
    "  border: 0; border-radius: 50%; background: #0f766e; color: #fff; cursor: pointer;",
    "  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.28); display: flex; align-items: center; justify-content: center; }",
    ".bubble:focus-visible, .close:focus-visible, .send:focus-visible, .composer input:focus-visible {",
    "  outline: 3px solid #f59e0b; outline-offset: 2px; }",
    ".panel { pointer-events: auto; display: none; position: fixed; right: 20px; bottom: 88px; width: 360px;",
    "  max-width: calc(100vw - 32px); height: min(520px, calc(100vh - 120px)); background: #fff; color: #14221f;",
    "  border-radius: 16px; box-shadow: 0 16px 48px rgba(15, 23, 42, 0.28); flex-direction: column; overflow: hidden; }",
    ".panel.is-open { display: flex; }",
    ".header { display: flex; align-items: center; justify-content: space-between; gap: 12px;",
    "  padding: 12px 14px; background: #0f766e; color: #fff; }",
    ".header h2 { margin: 0; font-size: 16px; font-weight: 650; line-height: 1.3; }",
    ".close { border: 0; background: transparent; color: #fff; font-size: 24px; line-height: 1; width: 36px; height: 36px;",
    "  border-radius: 8px; cursor: pointer; }",
    ".log { flex: 1; overflow-y: auto; padding: 14px; display: flex; flex-direction: column; gap: 8px; background: #f8faf9; }",
    ".msg { max-width: 85%; padding: 8px 10px; border-radius: 12px; }",
    ".msg p { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 15px; line-height: 1.4; }",
    ".msg.user { align-self: flex-end; background: #ccfbf1; color: #134e4a; }",
    ".msg.assistant { align-self: flex-start; background: #fff; color: #14221f; border: 1px solid #d9e4e0; }",
    ".typing { display: none; margin: 0; padding: 0 14px 8px; color: #3f6212; font-size: 13px; background: #f8faf9; }",
    ".typing.is-on { display: block; }",
    ".composer { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; padding: 12px; border-top: 1px solid #e2e8e4; background: #fff; }",
    ".composer input { flex: 1 1 140px; min-width: 0; border: 1px solid #cbd5d1; border-radius: 10px; padding: 10px 12px;",
    "  font-size: 16px; color: #14221f; background: #fff; }",
    ".counter { flex: 0 0 auto; color: #64748b; font-size: 12px; }",
    ".send { flex: 0 0 auto; border: 0; border-radius: 10px; background: #0f766e; color: #fff; padding: 10px 14px;",
    "  font-size: 15px; cursor: pointer; }",
    ".send:disabled, .composer input:disabled { opacity: 0.55; cursor: not-allowed; }",
    "@media (max-width: 640px) {",
    "  :host(.is-open) .bubble { display: none; }",
    "  :host(.is-open) .panel { inset: 0; right: 0; bottom: 0; width: 100%; max-width: none; height: 100%; border-radius: 0; }",
    "}"
  ].join("\n");

  if (!window.customElements.get("site-ai-widget")) {
    window.customElements.define("site-ai-widget", SiteAiWidget);
  }

  function mount() {
    if (!document.body || document.querySelector("site-ai-widget")) {
      return;
    }
    document.body.appendChild(document.createElement("site-ai-widget"));
  }

  if (document.body) {
    mount();
  } else {
    document.addEventListener("DOMContentLoaded", mount, { once: true });
  }
})();
