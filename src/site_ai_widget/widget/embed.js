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
      expand: "Ecrã inteiro",
      collapse: "Voltar ao painel",
      typing: "A escrever…",
      suggestions: ["O que vendem?", "Qual o horário?", "Como posso contactar-vos?"],
      sourceDoc: "Documento",
      sourcePage: "p.",
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
      expand: "Full screen",
      collapse: "Back to the panel",
      typing: "Typing…",
      suggestions: ["What do you sell?", "What are your hours?", "How can I contact you?"],
      sourceDoc: "Document",
      sourcePage: "p.",
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

  function stripCitations(text) {
    /* Replace markers with a space so "Amendoeiras [1]48" stays "Amendoeiras 48". */
    return String(text || "")
      .replace(/[ \t]*(?:【\d+】|\[\d+\])[ \t]*/g, " ")
      .replace(/[ \t]+([.,;:!?])/g, "$1")
      .replace(/[ \t]{2,}/g, " ")
      .replace(/[ \t]+\n/g, "\n")
      .trim();
  }

  function appendInline(parent, text) {
    var pattern = /\*\*(.+?)\*\*|__(.+?)__/g;
    var last = 0;
    var match;
    while ((match = pattern.exec(text)) !== null) {
      if (match.index > last) {
        parent.appendChild(document.createTextNode(text.slice(last, match.index)));
      }
      var strong = document.createElement("strong");
      strong.textContent = match[1] || match[2] || "";
      parent.appendChild(strong);
      last = match.index + match[0].length;
    }
    if (last < text.length) {
      parent.appendChild(document.createTextNode(text.slice(last)));
    }
  }

  /* Bold, lists and line breaks via DOM nodes only. Never assign HTML from replies. */
  function renderSafeMarkdown(text) {
    var root = document.createElement("div");
    root.className = "md";
    var lines = stripCitations(text).split("\n");
    var list = null;
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      var bullet = line.match(/^\s*[-*]\s+(.+)$/);
      if (bullet) {
        if (!list) {
          list = document.createElement("ul");
          root.appendChild(list);
        }
        var li = document.createElement("li");
        appendInline(li, bullet[1]);
        list.appendChild(li);
        continue;
      }
      list = null;
      if (!line.trim()) {
        root.appendChild(document.createElement("br"));
        continue;
      }
      var p = document.createElement("p");
      appendInline(p, line);
      root.appendChild(p);
    }
    return root;
  }

  function formatAnswer(data) {
    var lines = [];
    var mode = data && data.mode;
    /* Prefer the server-built natural answer (SQL and docs). Never invent values. */
    if (typeof data.answer === "string" && data.answer) {
      lines.push(data.answer);
    } else if (mode === "sql") {
      /* Legacy fallback: values only, never bare column names. */
      var rows = data.rows || [];
      for (var i = 0; i < rows.length; i++) {
        var row = rows[i] || [];
        var cells = [];
        for (var j = 0; j < row.length; j++) {
          var cell = cellText(row[j]);
          if (cell) {
            cells.push(cell);
          }
        }
        if (cells.length) {
          lines.push(cells.join(": "));
        }
      }
    }
    /* mode "none": the server's fallback text is English only, so the caller
       shows the localized copy.emptyAnswer instead. */
    return lines.join("\n");
  }

  /* Chips under the bubble: document sources only (table names are schema
     text and never shown to visitors). Greetings get none. textContent only. */
  function sourceChips(data) {
    var chips = [];
    if (!data || data.mode === "chat") {
      return chips;
    }
    var sources = data.sources || [];
    for (var s = 0; s < sources.length; s++) {
      var src = sources[s] || {};
      var name = cellText(src.document_name);
      if (!name) {
        continue;
      }
      var page = cellText(src.page);
      var label = copy.sourceDoc + ": " + name;
      chips.push(page ? label + " " + copy.sourcePage + " " + page : label);
    }
    return chips;
  }

  /* Prefer the stored assistant text (same string the visitor saw). */
  function historyText(item) {
    /* "none" first: its stored text is the server's English fallback, and the
       live answer showed the localized copy.emptyAnswer instead. */
    if (item.role === "assistant" && item.mode === "none") {
      return copy.emptyAnswer;
    }
    if (typeof item.text === "string" && item.text) {
      return item.text;
    }
    if (item.role === "assistant" && item.mode === "sql") {
      var n = typeof item.row_count === "number" ? item.row_count : 0;
      return n === 1 ? copy.rowsOne : copy.rowsMany.replace("{n}", String(n));
    }
    return "";
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
      this._full = false;
      this._busy = false;
      this._blocked = false;
      this._historyStarted = false;
      this._build();
      this._onEscape = (event) => {
        if (event.key === "Escape" && this._open) {
          event.preventDefault();
          if (this._full) {
            this._setFull(false);
          } else {
            this.close();
          }
          return;
        }
        if (event.key === "Tab" && this._full && this._open) {
          this._trapFocus(event);
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
      applyStyles(root);

      var panel = document.createElement("section");
      panel.className = "panel";
      panel.setAttribute("role", "dialog");
      panel.setAttribute("aria-modal", "true");
      panel.setAttribute("aria-hidden", "true");

      var header = document.createElement("header");
      header.className = "header";
      var brand = document.createElement("div");
      brand.className = "brand";
      brand.appendChild(icon());
      var title = document.createElement("h2");
      title.textContent = copy.title;
      title.id = "title";
      panel.setAttribute("aria-labelledby", "title");
      brand.appendChild(title);
      var actions = document.createElement("div");
      actions.className = "actions";
      var expandBtn = document.createElement("button");
      expandBtn.type = "button";
      expandBtn.className = "expand";
      expandBtn.setAttribute("aria-label", copy.expand);
      expandBtn.setAttribute("aria-pressed", "false");
      expandBtn.textContent = copy.expand;
      expandBtn.addEventListener("click", () => this._setFull(!this._full));
      var closeBtn = document.createElement("button");
      closeBtn.type = "button";
      closeBtn.className = "close";
      closeBtn.setAttribute("aria-label", copy.close);
      closeBtn.textContent = "\u00d7";
      closeBtn.addEventListener("click", () => this.close());
      actions.appendChild(expandBtn);
      actions.appendChild(closeBtn);
      header.appendChild(brand);
      header.appendChild(actions);

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
      root.addEventListener("focusin", (event) => {
        this._focusAt = event.target;
      });
      this._root = root;

      this._panel = panel;
      this._log = log;
      this._typing = typing;
      this._input = input;
      this._counter = counter;
      this._sendBtn = send;
      this._closeBtn = closeBtn;
      this._expandBtn = expandBtn;
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

    _setFull(on) {
      this._full = on;
      this._panel.classList.toggle("is-full", on);
      this.classList.toggle("is-full", on);
      this._expandBtn.setAttribute("aria-pressed", on ? "true" : "false");
      this._expandBtn.setAttribute("aria-label", on ? copy.collapse : copy.expand);
      this._expandBtn.textContent = on ? copy.collapse : copy.expand;
      this._focusComposer();
    }

    _trapFocus(event) {
      if (!this._panel) {
        return;
      }
      var nodes = this._panel.querySelectorAll("button, input, textarea");
      var items = [];
      for (var i = 0; i < nodes.length; i++) {
        if (!nodes[i].disabled) {
          items.push(nodes[i]);
        }
      }
      if (!items.length) {
        return;
      }
      var first = items[0];
      var last = items[items.length - 1];
      if (event.shiftKey && this._focusAt === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && this._focusAt === last) {
        event.preventDefault();
        first.focus();
      }
    }

    close() {
      if (!this._open) {
        return;
      }
      this._open = false;
      this._setFull(false);
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
        this._focusAt = this._closeBtn;
      } else {
        this._input.focus();
        this._focusAt = this._input;
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

    _add(role, text, chips) {
      var item = document.createElement("div");
      item.className = role === "user" ? "msg user" : "msg assistant";
      if (role === "assistant") {
        item.appendChild(renderSafeMarkdown(text));
      } else {
        var paragraph = document.createElement("p");
        paragraph.textContent = text;
        item.appendChild(paragraph);
      }
      if (chips && chips.length) {
        var row = document.createElement("div");
        row.className = "chips";
        for (var i = 0; i < chips.length; i++) {
          var chip = document.createElement("span");
          chip.className = "chip";
          chip.textContent = chips[i];
          row.appendChild(chip);
        }
        item.appendChild(row);
      }
      this._log.appendChild(item);
      this._scroll();
    }

    _showGreeting() {
      this._add("assistant", copy.greeting);
      var row = document.createElement("div");
      row.className = "chips";
      var suggestions = copy.suggestions || [];
      for (var i = 0; i < suggestions.length && i < 3; i++) {
        var button = document.createElement("button");
        button.type = "button";
        button.className = "chip";
        button.textContent = suggestions[i];
        button.addEventListener("click", ((question) => {
          return () => {
            if (this._busy || this._blocked) {
              return;
            }
            this._input.value = question;
            this._send();
          };
        })(suggestions[i]));
        row.appendChild(button);
      }
      this._log.appendChild(row);
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
        this._showGreeting();
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
            self._showGreeting();
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
          self._add("assistant", answer || copy.emptyAnswer, sourceChips(result.data));
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

  /* Constructable stylesheet first: unlike a <style> element it is allowed by a
     strict Content-Security-Policy (style-src without 'unsafe-inline'). */
  function applyStyles(root) {
    try {
      if (typeof CSSStyleSheet === "function" && "adoptedStyleSheets" in root) {
        var sheet = new CSSStyleSheet();
        sheet.replaceSync(CSS);
        root.adoptedStyleSheets = [sheet];
        return;
      }
    } catch (err) {
      /* Older browsers: fall through to a <style> element. */
    }
    var style = document.createElement("style");
    style.textContent = CSS;
    root.appendChild(style);
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
    ".bubble:focus-visible, .close:focus-visible, .expand:focus-visible, .chip:focus-visible, .send:focus-visible, .composer input:focus-visible {",
    "  outline: 3px solid #f59e0b; outline-offset: 2px; }",
    ".panel { pointer-events: auto; display: none; position: fixed; right: 20px; bottom: 88px; width: 360px;",
    "  max-width: calc(100vw - 32px); height: min(520px, calc(100vh - 120px)); background: #fff; color: #14221f;",
    "  border-radius: 16px; box-shadow: 0 16px 48px rgba(15, 23, 42, 0.28); flex-direction: column; overflow: hidden; }",
    ".panel.is-open { display: flex; }",
    ".panel.is-full { inset: 0; right: 0; bottom: 0; width: 100%; max-width: none; height: 100%; border-radius: 0; z-index: 2147483001; }",
    ":host(.is-full) .bubble { display: none; }",
    ".header { display: flex; align-items: center; justify-content: space-between; gap: 12px;",
    "  padding: 12px 14px; background: #0f766e; color: #fff; }",
    ".brand { display: flex; align-items: center; gap: 8px; min-width: 0; }",
    ".brand svg { width: 28px; height: 28px; flex: 0 0 auto; }",
    ".header h2 { margin: 0; font-size: 16px; font-weight: 650; line-height: 1.3; }",
    ".actions { display: flex; align-items: center; gap: 4px; }",
    ".expand { border: 0; background: transparent; color: #fff; font-size: 13px; border-radius: 8px;",
    "  padding: 6px 8px; cursor: pointer; }",
    ".close { border: 0; background: transparent; color: #fff; font-size: 24px; line-height: 1; width: 36px; height: 36px;",
    "  border-radius: 8px; cursor: pointer; }",
    ".log { flex: 1; overflow-y: auto; padding: 14px; display: flex; flex-direction: column; gap: 8px; background: #f8faf9; }",
    ".msg { max-width: 85%; padding: 8px 10px; border-radius: 12px; }",
    ".msg p, .msg .md { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 15px; line-height: 1.4; }",
    ".msg .md p { margin: 0 0 4px; }",
    ".msg .md ul { margin: 0 0 4px; padding-left: 1.2em; }",
    ".msg .md li { margin: 0; }",
    ".msg.user { align-self: flex-end; background: #ccfbf1; color: #134e4a; }",
    ".msg.assistant { align-self: flex-start; background: #fff; color: #14221f; border: 1px solid #d9e4e0; }",
    ".chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }",
    ".chip { border: 1px solid #99f6e4; background: #f0fdfa; color: #115e59; border-radius: 999px;",
    "  padding: 4px 8px; font-size: 12px; line-height: 1.3; cursor: pointer; }",
    "span.chip { cursor: default; }",
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
