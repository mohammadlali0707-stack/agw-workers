/**
 * cluster_chat/web/app.js
 *
 * Client application for Cluster Chat Bridge.
 * Supports:
 * - Full-viewport chat (100dvh flex layout) & compact room navigation
 * - Default-hidden agent status modal, live polling, clipboard screenshot paste
 * - Host-agnostic root-relative URLs (zero airboxvip.top dependencies)
 * - Dynamic WebSocket / SSE origin resolution from window.location
 * - Resilient Connection Manager with automatic 4-second timeout & HTTP polling fallback (?since=)
 * - Message deduplication via client-side Set of renderedMessageIds
 * - Plain HTTP direct-IP browser compatibility (document.execCommand clipboard fallback)
 * - Room navigation, staging uploads, inline photo previews & document cards
 */

(function () {
  "use strict";

  // Application state
  const state = {
    currentRoom: "main",
    sender: "mohammad",
    targetAgent: "",
    stagedFiles: [],
    pollingTimer: null,
    pollIntervalMs: 3000,
    knownRooms: ["main", "dm-argon", "workers", "workers-room"],
    roomDescriptions: {
      "main": "کانال عمومی پیام‌های کلاستر | General cluster broadcast channel",
      "dm-argon": "گفتگوی مستقیم با آرگون | Direct agent chat with LeadDesk Argon",
      "workers": "کانال ارتباطی ایجنت‌های ورکر | LeadDesk agent worker mesh channel",
      "workers-room": "کانال ارتباطی ایجنت‌های ورکر | LeadDesk agent worker mesh channel"
    },
    renderedMessageIds: new Set(),
    lastMessageId: null,
    lastMessageTimestamp: null,
    isAtBottom: true
  };

  // DOM Elements with safe lookups
  const elements = {
    roomList: document.getElementById("room-list"),
    currentRoomTitle: document.getElementById("current-room-title"),
    currentRoomDesc: document.getElementById("current-room-desc"),
    dropRoomTarget: document.getElementById("drop-room-target"),
    senderSelect: document.getElementById("sender-select"),
    customSenderInput: document.getElementById("custom-sender-input"),
    targetAgentSelect: document.getElementById("target-agent-select"),
    chatContainer: document.getElementById("chat-container"),
    dropOverlay: document.getElementById("drop-overlay"),
    messageList: document.getElementById("message-list"),
    stagedTray: document.getElementById("staged-tray") || document.getElementById("attachment-preview"),
    stagedCount: document.getElementById("staged-count"),
    stagedItems: document.getElementById("staged-items"),
    clearStagedBtn: document.getElementById("clear-staged-btn"),
    composerForm: document.getElementById("composer-form"),
    messageText: document.getElementById("message-text"),
    fileInput: document.getElementById("file-input"),
    attachBtn: document.getElementById("attach-btn"),
    sendBtn: document.getElementById("send-btn"),
    refreshBtn: document.getElementById("refresh-btn"),
    agentStatusToggle: document.getElementById("agent-status-toggle"),
    agentStatusModal: document.getElementById("agent-status-modal"),
    agentModalClose: document.getElementById("agent-modal-close"),
    agentModalBackdrop: document.getElementById("agent-modal-backdrop"),
    agentCardsContainer: document.getElementById("agent-cards-container"),
    statusBadgeText: document.getElementById("status-badge-text"),
    connectionStatus: document.getElementById("connection-status"),
    imageModal: document.getElementById("image-modal"),
    modalImg: document.getElementById("modal-img"),
    modalCaption: document.getElementById("modal-caption"),
    modalDownload: document.getElementById("modal-download"),
    modalClose: document.getElementById("modal-close"),
    statusIndicator: document.getElementById("status-indicator"),
    pollInterval: document.getElementById("poll-interval"),
    headerStatusBadge: document.getElementById("header-status-badge")
  };

  // Format bytes into human-readable size
  function formatBytes(bytes) {
    if (bytes === 0 || !bytes) return "0 B";
    const k = 1024;
    const sizes = ["B", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
  }

  // Escape HTML helper
  function escapeHtml(str) {
    if (!str && str !== 0) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  /**
   * Asia/Tehran timezone formatting helpers (P9)
   */
  function parseUtcDate(ts) {
    if (ts === null || ts === undefined || typeof ts === "boolean") return null;
    if (ts instanceof Date) return isNaN(ts.getTime()) ? null : ts;
    if (typeof ts === "number") {
      if (!Number.isFinite(ts)) return null;
      return new Date(ts > 1e11 ? ts : ts * 1000);
    }

    if (typeof ts !== "string") return null;
    let s = ts.trim();
    if (!s) return null;

    // Disallow non-finite strings
    const lower = s.toLowerCase();
    if (lower === "nan" || lower === "infinity" || lower === "-infinity" || lower === "inf" || lower === "-inf") {
      return null;
    }

    // Prioritize standard ISO date strings (starts with YYYY-MM-DD)
    if (/^\d{4}-\d{2}-\d{2}/.test(s)) {
      if (/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(s) && !/(Z|[+-]\d{2}:?\d{2})$/i.test(s)) {
        s = s.replace(" ", "T") + "Z";
      }
      const d = new Date(s);
      return isNaN(d.getTime()) ? null : d;
    }

    // Numeric epoch timestamp string (10 digits seconds or 13 digits ms)
    if (/^\d{10}(\.\d+)?$/.test(s) || /^\d{13}$/.test(s)) {
      const num = parseFloat(s);
      if (!Number.isFinite(num)) return null;
      return new Date(num > 1e11 ? num : num * 1000);
    }

    return null;
  }

  function formatTehranTime(ts) {
    if (ts === null || ts === undefined || ts === "") return "";
    const d = parseUtcDate(ts);
    if (!d) return typeof ts === "string" ? ts : "";
    try {
      return new Intl.DateTimeFormat("en-GB", {
        timeZone: "Asia/Tehran",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false
      }).format(d);
    } catch (_) {
      return typeof ts === "string" ? ts : "";
    }
  }

  function formatTehranDateTime(ts) {
    if (ts === null || ts === undefined || ts === "") return "";
    const d = parseUtcDate(ts);
    if (!d) return typeof ts === "string" ? ts : "";
    try {
      return new Intl.DateTimeFormat("en-CA", {
        timeZone: "Asia/Tehran",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false
      }).format(d).replace(", ", " ");
    } catch (_) {
      return typeof ts === "string" ? ts : "";
    }
  }

  /**
   * Normalize any legacy absolute airboxvip.top or host-bound URL to a root-relative path.
   * Ensures zero hardcoded airboxvip.top links exist in DOM elements or network calls.
   */
  function normalizeAttachmentUrl(url) {
    if (!url || typeof url !== "string") return "";
    let clean = url.trim();

    // Strip legacy airboxvip.top domains (http, https, wss, ws, scheme-relative)
    clean = clean.replace(/^(?:https?:)?\/\/(?:[a-zA-Z0-9-]+\.)*airboxvip\.top(?::\d+)?/i, "");

    // If it's an absolute URL pointing to any host, convert cluster-chat endpoints to root-relative
    if (/^https?:\/\//i.test(clean)) {
      try {
        const parsed = new URL(clean);
        if (
          parsed.host === window.location.host ||
          parsed.pathname.startsWith("/api/") ||
          parsed.pathname.startsWith("/uploads/") ||
          parsed.pathname.startsWith("/files/")
        ) {
          clean = parsed.pathname + parsed.search + parsed.hash;
        }
      } catch (_) {
        clean = clean.replace(/^https?:\/\/[^\/]+/i, "");
      }
    }

    // Ensure leading slash if not a data URI or blob URI
    if (!clean.startsWith("/") && !clean.startsWith("blob:") && !clean.startsWith("data:")) {
      clean = "/" + clean;
    }
    return clean;
  }

  /**
   * Normalize message body text to eliminate any airboxvip.top absolute references
   */
  function normalizeMessageText(text) {
    if (!text || typeof text !== "string") return "";
    return text.replace(/https?:\/\/(?:[a-zA-Z0-9-]+\.)*airboxvip\.top(?::\d+)?(\/[^\s<>"']*)/gi, "$1");
  }

  /**
   * Plain HTTP Direct-IP Browser Compatibility:
   * Uses navigator.clipboard.writeText when supported in secure context, with an automatic
   * fallback to a temporary <textarea> and document.execCommand('copy') for plain http://<IP>:<PORT>.
   */
  async function copyToClipboard(text, btnElement) {
    if (!text) return false;
    let success = false;

    // 1. Try modern Clipboard API if available and inside a secure context
    if (window.isSecureContext && navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      try {
        await navigator.clipboard.writeText(text);
        success = true;
      } catch (_) {
        success = false;
      }
    }

    // 2. Fallback for plain HTTP direct IP (insecure context) or when Clipboard API fails
    if (!success) {
      try {
        const textarea = document.createElement("textarea");
        textarea.value = text;
        textarea.setAttribute("readonly", "");
        textarea.style.position = "fixed";
        textarea.style.top = "-9999px";
        textarea.style.left = "-9999px";
        textarea.style.opacity = "0";
        document.body.appendChild(textarea);
        textarea.focus();
        textarea.select();
        success = document.execCommand("copy");
        document.body.removeChild(textarea);
      } catch (err) {
        console.warn("Fallback execCommand copy failed:", err);
        success = false;
      }
    }

    // 3. Visual user feedback
    if (success && btnElement) {
      const originalText = btnElement.innerHTML;
      btnElement.classList.add("copied");
      btnElement.innerHTML = "✓ کپی شد | Copied!";
      setTimeout(() => {
        btnElement.classList.remove("copied");
        btnElement.innerHTML = originalText;
      }, 2000);
    } else if (!success && typeof prompt === "function") {
      prompt("Copy to clipboard (Ctrl+C, Enter):", text);
    }
    return success;
  }

  /**
   * Resilient Connection Manager:
   * - Attempts realtime transport (WebSocket or SSE) first.
   * - Constructs WebSocket URL dynamically from window.location (ws: / wss:).
   * - If realtime connection fails to open within 4s, errors out, or closes,
   *   automatically activates an HTTP polling loop (?since=<lastMessageId>) every 2.5s.
   * - Applies adaptive backoff up to 10s on network errors.
   * - Periodically attempts to re-establish the realtime connection in the background.
   * - Displays non-intrusive connection status badge:
   *   'Live (Direct)', 'Polling Fallback (Resilient)', 'Offline / Retrying'.
   */
  class ResilientConnectionManager {
    constructor(options) {
      this.options = options || {};
      this.mode = "CONNECTING"; // "CONNECTING", "LIVE", "POLLING", "OFFLINE"
      this.basePollIntervalMs = 2500; // 2.5s
      this.maxPollIntervalMs = 10000; // 10s
      this.currentPollInterval = 2500;
      this.pollTimer = null;
      this.realtimeTimeoutTimer = null;
      this.backgroundProbeTimer = null;
      this.socket = null;
      this.eventSource = null;
      this.isPollingActive = false;
      this.probeIntervalMs = 30000; // 30s background probe
    }

    start() {
      this.attemptRealtimeTransport();
    }

    attemptRealtimeTransport() {
      if (this.realtimeTimeoutTimer) {
        clearTimeout(this.realtimeTimeoutTimer);
        this.realtimeTimeoutTimer = null;
      }
      this.cleanupRealtime();

      this.updateStatus("connecting", "Connecting...", "realtime probe");

      // 4-second timeout to fall back to HTTP polling
      this.realtimeTimeoutTimer = setTimeout(() => {
        console.warn("Realtime connection timed out (4s); activating resilient HTTP polling fallback.");
        this.activatePollingFallback("timeout_4s");
      }, 4000);

      // 1. Try WebSocket with dynamic origin
      if (typeof WebSocket !== "undefined") {
        try {
          const wsProto = window.location.protocol === "https:" ? "wss:" : "ws:";
          const wsHost = window.location.host;
          const wsUrl = `${wsProto}//${wsHost}/ws`;

          this.socket = new WebSocket(wsUrl);

          this.socket.onopen = () => {
            if (this.realtimeTimeoutTimer) {
              clearTimeout(this.realtimeTimeoutTimer);
              this.realtimeTimeoutTimer = null;
            }
            this.handleRealtimeSuccess("WebSocket");
          };

          this.socket.onmessage = (event) => {
            this.handleIncomingData(event.data);
          };

          this.socket.onerror = (err) => {
            console.warn("WebSocket transport error:", err);
            this.activatePollingFallback("ws_error");
          };

          this.socket.onclose = () => {
            console.warn("WebSocket closed unexpectedly.");
            this.activatePollingFallback("ws_closed");
          };
          return;
        } catch (err) {
          console.warn("WebSocket initialization exception:", err);
        }
      }

      // 2. Try EventSource (SSE) with relative URL
      if (typeof EventSource !== "undefined") {
        try {
          const sseUrl = `/api/sse?room=${encodeURIComponent(state.currentRoom)}`;
          this.eventSource = new EventSource(sseUrl);

          this.eventSource.onopen = () => {
            if (this.realtimeTimeoutTimer) {
              clearTimeout(this.realtimeTimeoutTimer);
              this.realtimeTimeoutTimer = null;
            }
            this.handleRealtimeSuccess("SSE");
          };

          this.eventSource.onmessage = (event) => {
            this.handleIncomingData(event.data);
          };

          this.eventSource.onerror = (err) => {
            console.warn("SSE transport error:", err);
            this.activatePollingFallback("sse_error");
          };
          return;
        } catch (err) {
          console.warn("SSE initialization exception:", err);
        }
      }

      // Neither realtime supported; fallback immediately
      this.activatePollingFallback("realtime_unsupported");
    }

    handleRealtimeSuccess(transportType) {
      this.mode = "LIVE";
      this.stopPollingLoop();
      this.updateStatus("live", `Live (${transportType})`, "Connected");
    }

    handleIncomingData(raw) {
      try {
        const parsed = typeof raw === "string" ? JSON.parse(raw) : raw;
        if (parsed && parsed.type === "message" && parsed.message) {
          appendNewMessages([parsed.message]);
        } else if (parsed && parsed.messages && Array.isArray(parsed.messages)) {
          appendNewMessages(parsed.messages);
        }
      } catch (e) {
        console.warn("Could not parse incoming realtime event:", e);
      }
    }

    activatePollingFallback(reason) {
      if (this.realtimeTimeoutTimer) {
        clearTimeout(this.realtimeTimeoutTimer);
        this.realtimeTimeoutTimer = null;
      }
      this.cleanupRealtime();

      if (this.mode !== "OFFLINE") {
        this.mode = "POLLING";
        this.updateStatus("polling", "Polling Fallback (Resilient)", `${(this.currentPollInterval / 1000).toFixed(1)}s poll`);
      }

      this.startPollingLoop();
      this.startBackgroundProbe();
    }

    startPollingLoop() {
      if (this.isPollingActive) return;
      this.isPollingActive = true;
      this.scheduleNextPoll(100); // Trigger first poll quickly
    }

    stopPollingLoop() {
      this.isPollingActive = false;
      if (this.pollTimer) {
        clearTimeout(this.pollTimer);
        this.pollTimer = null;
      }
    }

    scheduleNextPoll(delayMs) {
      if (!this.isPollingActive) return;
      if (this.pollTimer) {
        clearTimeout(this.pollTimer);
      }
      const delay = typeof delayMs === "number" ? delayMs : this.currentPollInterval;
      this.pollTimer = setTimeout(async () => {
        await this.executePoll();
      }, delay);
    }

    async executePoll() {
      if (!this.isPollingActive) return;
      try {
        await pollMessages();
        // Reset adaptive backoff on successful HTTP poll
        this.currentPollInterval = this.basePollIntervalMs;
        if (this.mode !== "LIVE") {
          this.mode = "POLLING";
          this.updateStatus("polling", "Polling Fallback (Resilient)", `${(this.currentPollInterval / 1000).toFixed(1)}s poll`);
        }
      } catch (err) {
        console.warn("HTTP polling error:", err);
        // Adaptive backoff on network error (2.5s -> 3.75s -> ... up to 10s)
        this.currentPollInterval = Math.min(this.maxPollIntervalMs, Math.round(this.currentPollInterval * 1.5));
        this.mode = "OFFLINE";
        this.updateStatus("offline", "Offline / Retrying", `retrying in ${(this.currentPollInterval / 1000).toFixed(1)}s`);
      } finally {
        if (this.isPollingActive) {
          this.scheduleNextPoll(this.currentPollInterval);
        }
      }
    }

    startBackgroundProbe() {
      if (this.backgroundProbeTimer) return;
      this.backgroundProbeTimer = setInterval(() => {
        if (this.mode === "POLLING" || this.mode === "OFFLINE") {
          console.log("Background probing realtime transport...");
          this.attemptRealtimeTransport();
        }
      }, this.probeIntervalMs);
    }

    cleanupRealtime() {
      if (this.socket) {
        try {
          this.socket.onopen = null;
          this.socket.onmessage = null;
          this.socket.onerror = null;
          this.socket.onclose = null;
          this.socket.close();
        } catch (_) {}
        this.socket = null;
      }
      if (this.eventSource) {
        try {
          this.eventSource.onopen = null;
          this.eventSource.onmessage = null;
          this.eventSource.onerror = null;
          this.eventSource.close();
        } catch (_) {}
        this.eventSource = null;
      }
    }

    updateStatus(statusKey, labelText, detailText) {
      if (elements.statusIndicator) {
        elements.statusIndicator.className = `status-indicator ${statusKey}`;
      }
      if (elements.connectionStatus) {
        elements.connectionStatus.textContent = labelText;
      }
      if (elements.headerStatusBadge) {
        elements.headerStatusBadge.className = `connection-badge ${statusKey}`;
        elements.headerStatusBadge.textContent = labelText;
      }
      if (elements.pollInterval) {
        elements.pollInterval.textContent = detailText || "";
      }
    }
  }

  // Create global connection manager instance
  const connectionManager = new ResilientConnectionManager();

  // Toggle Agent Status & Identity Modal / Drawer
  function toggleAgentStatusModal(show) {
    if (!elements.agentStatusModal) return;
    const isHidden = elements.agentStatusModal.classList.contains("hidden");
    const willShow = typeof show === "boolean" ? show : isHidden;

    if (willShow) {
      elements.agentStatusModal.classList.remove("hidden");
      if (elements.agentStatusToggle) {
        elements.agentStatusToggle.setAttribute("aria-expanded", "true");
      }
    } else {
      elements.agentStatusModal.classList.add("hidden");
      if (elements.agentStatusToggle) {
        elements.agentStatusToggle.setAttribute("aria-expanded", "false");
      }
    }
  }

  // Safely update Agent Status grid and compact header badge
  function updateAgentStatus(agents) {
    if (!Array.isArray(agents)) return;
    const onlineAgents = agents.filter(a => (a.status || "").toLowerCase() === "online");
    const count = onlineAgents.length;

    if (elements.statusBadgeText) {
      elements.statusBadgeText.textContent = `ایجنت‌ها (${count} آنلاین)`;
    }

    if (elements.agentCardsContainer) {
      elements.agentCardsContainer.innerHTML = "";
      agents.forEach(agent => {
        const card = document.createElement("div");
        const isOnline = (agent.status || "").toLowerCase() === "online";
        card.className = "agent-card " + (isOnline ? "online" : "offline");
        card.dataset.agent = agent.name;

        let avatar = agent.avatar;
        if (!avatar) {
          if (agent.name === "argon") avatar = "🤖";
          else if (agent.name === "mohammad") avatar = "👑";
          else if (agent.name === "cookie") avatar = "🍪";
          else if (agent.name === "coca") avatar = "🥤";
          else avatar = "⚡";
        }

        const lastSeenText = agent.last_seen ? ` (${formatTehranTime(agent.last_seen)})` : "";
        card.innerHTML = `
          <div class="agent-avatar">${avatar}</div>
          <div class="agent-info">
            <div class="agent-name">${escapeHtml(agent.name)} <span class="agent-badge">${escapeHtml(agent.role || "")}</span></div>
            <div class="agent-state">
              <span class="dot ${isOnline ? "online" : "offline"}"></span>
              ${isOnline ? "آنلاین (آماده پردازش)" : "آفلاین"}${lastSeenText}
            </div>
          </div>
        `;
        elements.agentCardsContainer.appendChild(card);
      });
    }
  }

  // Fetch agent status from server endpoint (safely updates UI)
  async function loadAgentStatus() {
    try {
      const res = await fetch("/api/cluster-chat/agents");
      if (res.ok) {
        const data = await res.json();
        if (data.ok && Array.isArray(data.agents)) {
          updateAgentStatus(data.agents);
        }
      }
    } catch (e) {
      // Safe fallback - default cards remain visible inside modal
      console.warn("Could not fetch agents status:", e);
    }
  }

  // Load active rooms from server API using relative path
  async function loadRooms() {
    try {
      const res = await fetch("/api/cluster-chat/rooms");
      if (res.ok) {
        const data = await res.json();
        if (data.ok && Array.isArray(data.rooms)) {
          const mapped = data.rooms.map(r => (r === "workers-room" ? "workers" : r));
          state.knownRooms = Array.from(new Set([...state.knownRooms, ...data.rooms, ...mapped]));
          renderRoomButtons();
        }
      }
    } catch (e) {
      console.warn("Could not fetch rooms list:", e);
    }
  }

  // Render header room navigation pills
  function renderRoomButtons() {
    if (!elements.roomList) return;
    elements.roomList.innerHTML = "";

    // Guarantee main, dm-argon, workers are present
    const standardDisplayRooms = ["main", "dm-argon", "workers"];
    const allRooms = Array.from(new Set([...standardDisplayRooms, ...state.knownRooms]));

    allRooms.forEach(room => {
      // Skip redundant workers-room button if workers is displayed
      if (room === "workers-room" && allRooms.includes("workers")) {
        return;
      }

      const btn = document.createElement("button");
      btn.type = "button";
      const isActive = room === state.currentRoom || (room === "workers" && state.currentRoom === "workers-room");
      btn.className = "room-btn" + (isActive ? " active" : "");
      btn.dataset.room = room;

      let icon = "💬";
      let badge = "اتاق";
      if (room === "main") {
        icon = "🌐";
        badge = "عمومی";
      } else if (room === "dm-argon") {
        icon = "🤖";
        badge = "آرگون";
      } else if (room === "workers" || room === "workers-room") {
        icon = "⚙️";
        badge = "ورکرها";
      }

      btn.innerHTML = `
        <span class="room-icon">${icon}</span>
        <span class="room-name">${escapeHtml(room)}</span>
        <span class="room-badge">${badge}</span>
      `;

      btn.addEventListener("click", () => switchRoom(room));
      elements.roomList.appendChild(btn);
    });
  }

  // Switch active room
  function switchRoom(room) {
    if (state.currentRoom === room) return;
    state.currentRoom = room;

    // Reset cursor and message deduplication set
    state.renderedMessageIds.clear();
    state.lastMessageId = null;
    state.lastMessageTimestamp = null;

    // Update UI headers safely
    if (elements.currentRoomTitle) {
      elements.currentRoomTitle.textContent = room;
    }
    if (elements.dropRoomTarget) {
      elements.dropRoomTarget.textContent = room;
    }
    if (elements.currentRoomDesc) {
      elements.currentRoomDesc.textContent =
        state.roomDescriptions[room] || "کانال ارتباطی فعال کلاستر | Active cluster communication channel";
    }

    // Update active class on buttons
    document.querySelectorAll(".room-btn").forEach(btn => {
      const btnRoom = btn.dataset.room;
      if (btnRoom === room || (btnRoom === "workers" && room === "workers-room")) {
        btn.classList.add("active");
      } else {
        btn.classList.remove("active");
      }
    });

    if (elements.messageList) {
      elements.messageList.innerHTML = '<div class="empty-state">در حال بارگذاری پیام‌ها...</div>';
    }
    fetchMessages(true);
  }

  /**
   * Load initial message history for a room
   */
  async function loadInitialRoomMessages(room, forceScroll = false) {
    try {
      const roomParam = encodeURIComponent(room || state.currentRoom);
      const res = await fetch(`/api/cluster-chat/rooms/${roomParam}/messages?limit=100`, {
        cache: "no-store",
        headers: { "Cache-Control": "no-cache", "Pragma": "no-cache" }
      });
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      const data = await res.json();
      if (data.ok && Array.isArray(data.messages)) {
        renderInitialMessages(data.messages, forceScroll);
      }
    } catch (err) {
      console.warn("Failed to load initial room messages:", err);
      if (elements.messageList && state.renderedMessageIds.size === 0) {
        elements.messageList.innerHTML = `<div class="empty-state error">خطا در بارگذاری پیام‌ها (${escapeHtml(err.message)})</div>`;
      }
    }
  }

  // Fetch messages for current room (compatibility helper)
  async function fetchMessages(forceScroll = false) {
    await loadInitialRoomMessages(state.currentRoom, forceScroll);
  }

  /**
   * Incremental HTTP polling using root-relative endpoint GET /api/messages?since=...
   */
  async function pollMessages() {
    if (!state.currentRoom) return;

    const params = new URLSearchParams();
    params.set("room", state.currentRoom);
    if (state.lastMessageId) {
      params.set("since", state.lastMessageId);
    } else if (state.lastMessageTimestamp) {
      params.set("since", state.lastMessageTimestamp);
    } else {
      params.set("limit", "100");
    }

    const res = await fetch(`/api/messages?${params.toString()}`, {
      cache: "no-store",
      headers: {
        "Cache-Control": "no-cache",
        "Pragma": "no-cache"
      }
    });

    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }

    const data = await res.json();
    if (data.ok && Array.isArray(data.messages)) {
      appendNewMessages(data.messages);
    }
  }

  /**
   * Append newly received messages to the chat view with deduplication
   * Guarantees no message is rendered twice across polling and realtime reconnects.
   */
  function appendNewMessages(messages) {
    if (!elements.messageList || !Array.isArray(messages) || messages.length === 0) return;

    // Filter out messages already rendered in DOM
    const toRender = messages.filter(m => m && m.message_id && !state.renderedMessageIds.has(m.message_id));
    if (toRender.length === 0) return;

    // Remove empty-state if present
    const emptyState = elements.messageList.querySelector(".empty-state");
    if (emptyState) {
      emptyState.remove();
    }

    toRender.forEach(msg => {
      state.renderedMessageIds.add(msg.message_id);
      state.lastMessageId = msg.message_id;
      state.lastMessageTimestamp = msg.created_at;

      const msgElem = createMessageElement(msg);
      elements.messageList.appendChild(msgElem);
    });

    if (state.isAtBottom) {
      scrollToBottom();
    }
  }

  /**
   * Render initial full message list for room
   */
  function renderInitialMessages(messages, forceScroll = false) {
    if (!elements.messageList) return;
    elements.messageList.innerHTML = "";
    state.renderedMessageIds.clear();
    state.lastMessageId = null;
    state.lastMessageTimestamp = null;

    if (!messages || messages.length === 0) {
      elements.messageList.innerHTML =
        '<div class="empty-state">هیچ پیامی در اتاق #' +
        escapeHtml(state.currentRoom) +
        ' وجود ندارد. گفتگو را آغاز کنید!</div>';
      return;
    }

    messages.forEach(msg => {
      if (!msg || !msg.message_id) return;
      if (state.renderedMessageIds.has(msg.message_id)) return;

      state.renderedMessageIds.add(msg.message_id);
      state.lastMessageId = msg.message_id;
      state.lastMessageTimestamp = msg.created_at;

      const msgElem = createMessageElement(msg);
      elements.messageList.appendChild(msgElem);
    });

    if (forceScroll || state.isAtBottom) {
      scrollToBottom();
    }
  }

  function renderMessages(messages, forceScroll = false) {
    renderInitialMessages(messages, forceScroll);
  }

  /**
   * Build DOM element for a single message with zero airboxvip links and full relative URLs
   */
  function createMessageElement(msg) {
    const msgItem = document.createElement("div");
    msgItem.className = "message-item";
    if (msg.message_id) {
      msgItem.dataset.messageId = msg.message_id;
    }

    if (msg.target_agent) {
      msgItem.classList.add("highlight-target");
    }

    const senderClean = (msg.sender || "unknown").toLowerCase();
    let senderClass = "sender-badge";
    if (senderClean === "mohammad") senderClass += " mohammad";
    else if (senderClean === "argon") senderClass += " argon";

    let targetHtml = "";
    if (msg.target_agent) {
      targetHtml = `<span class="target-badge">➔ @${escapeHtml(msg.target_agent)}</span>`;
    }

    let timeFormatted = "";
    let timeFullTitle = "";
    if (msg.created_at) {
      timeFormatted = formatTehranTime(msg.created_at);
      timeFullTitle = formatTehranDateTime(msg.created_at);
    }

    let bodyHtml = "";
    const cleanText = normalizeMessageText(msg.text || "");
    if (msg.text) {
      // Normalize any legacy airboxvip.top text links in the body to root-relative paths
      bodyHtml = `<div class="message-body">${escapeHtml(cleanText)}</div>`;
    }

    // Render attachments with root-relative paths
    let attachmentsHtml = "";
    if (Array.isArray(msg.attachments) && msg.attachments.length > 0) {
      attachmentsHtml = '<div class="attachments-grid">';
      msg.attachments.forEach(att => {
        const kind = att.kind || (att.mime_type && att.mime_type.startsWith("image/") ? "photo" : "document");
        const rawUrl = att.public_url || att.url || `/api/cluster-chat/files/${att.room || state.currentRoom}/${att.created_at ? att.created_at.slice(0, 10) : ""}/${att.filename}`;
        
        // Normalize any legacy airboxvip.top URL to relative path before DOM rendering
        const publicUrl = normalizeAttachmentUrl(rawUrl);
        const downloadUrl = normalizeAttachmentUrl(publicUrl + (publicUrl.includes("?") ? "&download=1" : "?download=1"));
        const sizeStr = formatBytes(att.size_bytes);

        if (kind === "photo") {
          attachmentsHtml += `
            <div class="photo-card">
              <div class="photo-thumb-container" onclick="window.ClusterChatUI.openPhoto('${escapeHtml(publicUrl)}', '${escapeHtml(att.filename || "Photo")}', '${escapeHtml(downloadUrl)}')">
                <img class="photo-thumb" src="${escapeHtml(publicUrl)}" alt="${escapeHtml(att.filename || "Photo")}" loading="lazy" />
              </div>
              <div class="photo-meta">
                <span class="photo-name" title="${escapeHtml(att.filename)}">${escapeHtml(att.filename)}</span>
                <span class="photo-size">${sizeStr}</span>
                <a class="btn-link" href="${escapeHtml(downloadUrl)}" download="${escapeHtml(att.filename)}" title="دانلود عکس">⬇️</a>
              </div>
            </div>
          `;
        } else {
          // Document card
          let docIcon = "📄";
          const ext = (att.filename || "").toLowerCase();
          if (ext.endsWith(".zip") || ext.endsWith(".tar") || ext.endsWith(".gz")) docIcon = "📦";
          else if (ext.endsWith(".patch") || ext.endsWith(".diff")) docIcon = "🩹";
          else if (ext.endsWith(".json") || ext.endsWith(".yaml") || ext.endsWith(".yml")) docIcon = "📋";
          else if (ext.endsWith(".py") || ext.endsWith(".js") || ext.endsWith(".ts")) docIcon = "💻";
          else if (ext.endsWith(".pdf")) docIcon = "📑";

          const localPathEscaped = escapeHtml(att.local_path || "");

          attachmentsHtml += `
            <div class="document-card">
              <span class="doc-icon">${docIcon}</span>
              <div class="doc-details">
                <span class="doc-filename" title="${escapeHtml(att.filename)}">${escapeHtml(att.filename)}</span>
                <div class="doc-subinfo">
                  <span>${sizeStr}</span>
                  <span>•</span>
                  <span>${escapeHtml(att.mime_type || "document")}</span>
                </div>
              </div>
              <div class="doc-actions">
                ${att.local_path ? `
                  <button type="button" class="btn-badge btn-badge-sm" onclick="window.ClusterChatUI.copyPath('${localPathEscaped}', this)" title="کپی مسیر محلی برای ایجنت">
                    📋 مسیر
                  </button>
                ` : ""}
                <a class="btn-badge" href="${escapeHtml(downloadUrl)}" download="${escapeHtml(att.filename)}" title="ذخیره فایل">
                  ⬇️ دریافت
                </a>
              </div>
            </div>
          `;
        }
      });
      attachmentsHtml += "</div>";
    }

    msgItem.innerHTML = `
      <div class="message-header">
        <span class="${senderClass}">${escapeHtml(msg.sender)}</span>
        ${targetHtml}
        <span class="message-time"${timeFullTitle ? ` title="${escapeHtml(timeFullTitle)}"` : ""}>${timeFormatted}</span>
      </div>
      ${bodyHtml}
      ${attachmentsHtml}
    `;

    return msgItem;
  }

  // Scroll message container to bottom
  function scrollToBottom() {
    if (!elements.chatContainer) return;
    elements.chatContainer.scrollTop = elements.chatContainer.scrollHeight;
  }

  // Stage files for attachment upload
  function stageFiles(files) {
    if (!files || files.length === 0) return;
    Array.from(files).forEach(f => {
      state.stagedFiles.push(f);
    });
    renderStagedTray();
  }

  // Unstage attachment by index
  function unstageFile(index) {
    if (index >= 0 && index < state.stagedFiles.length) {
      state.stagedFiles.splice(index, 1);
      renderStagedTray();
    }
  }

  // Render staging tray preview
  function renderStagedTray() {
    if (!elements.stagedTray) return;

    if (state.stagedFiles.length === 0) {
      elements.stagedTray.classList.add("hidden");
      if (elements.stagedItems) elements.stagedItems.innerHTML = "";
      return;
    }

    elements.stagedTray.classList.remove("hidden");
    if (elements.stagedCount) elements.stagedCount.textContent = state.stagedFiles.length;
    if (elements.stagedItems) {
      elements.stagedItems.innerHTML = "";

      state.stagedFiles.forEach((file, idx) => {
        const item = document.createElement("div");
        item.className = "staged-item";

        const isImage = file.type && file.type.startsWith("image/");
        let thumbHtml = "📄";
        if (isImage) {
          const objUrl = URL.createObjectURL(file);
          thumbHtml = `<img class="staged-thumb" src="${objUrl}" alt="thumb">`;
        }

        item.innerHTML = `
          ${thumbHtml}
          <span class="staged-name" title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</span>
          <span class="staged-size">${formatBytes(file.size)}</span>
          <button type="button" class="btn-remove-staged" onclick="window.ClusterChatUI.unstage(${idx})" title="حذف پیوست">×</button>
        `;

        elements.stagedItems.appendChild(item);
      });
    }
  }

  // Send message or upload attachments
  async function sendMessage() {
    if (!elements.messageText) return;
    const text = elements.messageText.value.trim();
    const hasFiles = state.stagedFiles.length > 0;

    if (!text && !hasFiles) {
      return;
    }

    // Determine active sender safely
    let activeSender = (elements.senderSelect && elements.senderSelect.value) || "mohammad";
    if (activeSender === "__custom__") {
      activeSender = elements.customSenderInput ? elements.customSenderInput.value.trim() : "";
      if (!activeSender) {
        alert("لطفاً نام فرستنده دلخواه را وارد کنید.");
        if (elements.customSenderInput) elements.customSenderInput.focus();
        return;
      }
    }
    state.sender = activeSender;

    const activeTarget = (elements.targetAgentSelect && elements.targetAgentSelect.value) || null;

    if (elements.sendBtn) {
      elements.sendBtn.disabled = true;
      elements.sendBtn.innerHTML = "<span>در حال ارسال...</span> ⏳";
    }

    try {
      if (hasFiles) {
        // Multipart upload via relative URL
        const formData = new FormData();
        formData.append("room", state.currentRoom);
        formData.append("sender", activeSender);
        if (text) {
          formData.append("text", text);
        }
        if (activeTarget) {
          formData.append("target_agent", activeTarget);
        }
        formData.append("forward_telegram", "true");

        state.stagedFiles.forEach(file => {
          formData.append("file", file, file.name);
        });

        const res = await fetch("/api/cluster-chat/upload", {
          method: "POST",
          body: formData
        });

        if (!res.ok) {
          const errData = await res.json().catch(() => ({}));
          throw new Error(errData.error || `Upload failed with HTTP ${res.status}`);
        }
      } else {
        // Plain message JSON via relative URL
        const payload = {
          sender: activeSender,
          text: text,
          target_agent: activeTarget,
          forward_telegram: true
        };

        const roomParam = encodeURIComponent(state.currentRoom);
        const res = await fetch(`/api/cluster-chat/rooms/${roomParam}/messages`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });

        if (!res.ok) {
          const errData = await res.json().catch(() => ({}));
          throw new Error(errData.error || `Post failed with HTTP ${res.status}`);
        }
      }

      // Reset composer
      if (elements.messageText) {
        elements.messageText.value = "";
        elements.messageText.style.height = "";
      }
      state.stagedFiles = [];
      renderStagedTray();

      // Trigger immediate poll to fetch the new message
      await connectionManager.executePoll();
      await fetchMessages(true);
    } catch (err) {
      console.error("Failed to send message:", err);
      alert("خطا در ارسال پیام: " + err.message);
    } finally {
      if (elements.sendBtn) {
        elements.sendBtn.disabled = false;
        elements.sendBtn.innerHTML = "<span>ارسال</span> 🚀";
      }
      if (elements.messageText) {
        elements.messageText.focus();
      }
    }
  }

  // Open Lightbox Photo Modal with normalized root-relative URLs
  function openPhoto(publicUrl, filename, downloadUrl) {
    if (!elements.imageModal) return;
    const cleanPublicUrl = normalizeAttachmentUrl(publicUrl);
    const cleanDownloadUrl = normalizeAttachmentUrl(downloadUrl);

    if (elements.modalImg) elements.modalImg.src = cleanPublicUrl;
    if (elements.modalCaption) elements.modalCaption.textContent = filename;
    if (elements.modalDownload) {
      elements.modalDownload.href = cleanDownloadUrl;
      elements.modalDownload.setAttribute("download", filename);
    }
    elements.imageModal.classList.remove("hidden");
  }

  function closePhoto() {
    if (!elements.imageModal) return;
    elements.imageModal.classList.add("hidden");
  }

  // Setup DOM event handlers
  function setupEvents() {
    // Sender selector custom toggle
    if (elements.senderSelect) {
      elements.senderSelect.addEventListener("change", e => {
        if (e.target.value === "__custom__") {
          if (elements.customSenderInput) {
            elements.customSenderInput.classList.remove("hidden");
            elements.customSenderInput.focus();
          }
        } else {
          if (elements.customSenderInput) {
            elements.customSenderInput.classList.add("hidden");
          }
          state.sender = e.target.value;
        }
      });
    }

    // Refresh button
    if (elements.refreshBtn) {
      elements.refreshBtn.addEventListener("click", () => {
        loadInitialRoomMessages(state.currentRoom, true);
        loadAgentStatus();
      });
    }

    // Attachment button click
    if (elements.attachBtn && elements.fileInput) {
      elements.attachBtn.addEventListener("click", () => {
        elements.fileInput.click();
      });
    }

    // File input change
    if (elements.fileInput) {
      elements.fileInput.addEventListener("change", e => {
        stageFiles(e.target.files);
        elements.fileInput.value = "";
      });
    }

    // Clear staged attachments
    if (elements.clearStagedBtn) {
      elements.clearStagedBtn.addEventListener("click", () => {
        state.stagedFiles = [];
        renderStagedTray();
      });
    }

    // Agent status modal toggle
    if (elements.agentStatusToggle) {
      elements.agentStatusToggle.addEventListener("click", () => {
        toggleAgentStatusModal();
      });
    }

    // Agent status modal close
    if (elements.agentModalClose) {
      elements.agentModalClose.addEventListener("click", () => {
        toggleAgentStatusModal(false);
      });
    }

    if (elements.agentModalBackdrop) {
      elements.agentModalBackdrop.addEventListener("click", () => {
        toggleAgentStatusModal(false);
      });
    }

    // Composer form submission
    if (elements.composerForm) {
      elements.composerForm.addEventListener("submit", e => {
        e.preventDefault();
        sendMessage();
      });
    }

    // Textarea enter key submit (Shift+Enter for newline) & auto-resizing
    if (elements.messageText) {
      elements.messageText.addEventListener("input", () => {
        elements.messageText.style.height = "auto";
        elements.messageText.style.height = Math.min(elements.messageText.scrollHeight, 120) + "px";
      });

      elements.messageText.addEventListener("keydown", e => {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          sendMessage();
        }
      });
    }

    // Lightbox close
    if (elements.modalClose) {
      elements.modalClose.addEventListener("click", closePhoto);
    }
    if (elements.imageModal) {
      const backdrop = elements.imageModal.querySelector(".modal-backdrop");
      if (backdrop) backdrop.addEventListener("click", closePhoto);
    }

    // Global keyboard listener (Escape dismisses modals)
    document.addEventListener("keydown", e => {
      if (e.key === "Escape") {
        if (elements.imageModal && !elements.imageModal.classList.contains("hidden")) {
          closePhoto();
        }
        if (elements.agentStatusModal && !elements.agentStatusModal.classList.contains("hidden")) {
          toggleAgentStatusModal(false);
        }
      }
    });

    // Clipboard Paste Listener (Supports screenshots and files pasted directly)
    window.addEventListener("paste", e => {
      const clipboardData = e.clipboardData || window.clipboardData;
      if (!clipboardData || !clipboardData.items) return;

      const items = clipboardData.items;
      const pastedFiles = [];

      for (let i = 0; i < items.length; i++) {
        const item = items[i];
        if (item.kind === "file") {
          const file = item.getAsFile();
          if (file) {
            let renamedFile = file;
            if (file.name === "image.png" || !file.name) {
              const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
              renamedFile = new File([file], `screenshot_${timestamp}.png`, { type: file.type });
            }
            pastedFiles.push(renamedFile);
          }
        }
      }

      if (pastedFiles.length > 0) {
        stageFiles(pastedFiles);
      }
    });

    // Drag & Drop File Upload over Chat Container
    if (elements.chatContainer && elements.dropOverlay) {
      let dragCounter = 0;

      ["dragenter", "dragover", "dragleave", "drop"].forEach(eventName => {
        elements.chatContainer.addEventListener(eventName, e => {
          e.preventDefault();
          e.stopPropagation();
        });
      });

      elements.chatContainer.addEventListener("dragenter", e => {
        dragCounter++;
        elements.dropOverlay.classList.add("active");
      });

      elements.chatContainer.addEventListener("dragleave", e => {
        dragCounter--;
        if (dragCounter <= 0) {
          dragCounter = 0;
          elements.dropOverlay.classList.remove("active");
        }
      });

      elements.chatContainer.addEventListener("drop", e => {
        dragCounter = 0;
        elements.dropOverlay.classList.remove("active");
        const dt = e.dataTransfer;
        if (dt && dt.files && dt.files.length > 0) {
          stageFiles(dt.files);
        }
      });
    }

    // Scroll listener for sticky scroll-to-bottom behavior
    if (elements.chatContainer) {
      elements.chatContainer.addEventListener("scroll", () => {
        const threshold = 80;
        const distFromBottom =
          elements.chatContainer.scrollHeight -
          elements.chatContainer.scrollTop -
          elements.chatContainer.clientHeight;
        state.isAtBottom = distFromBottom <= threshold;
      });
    }
  }

  // Expose global helpers for HTML event handlers, programmatic control, and test harnesses
  window.ClusterChatUI = {
    parseUtcDate: parseUtcDate,
    formatTehranTime: formatTehranTime,
    formatTehranDateTime: formatTehranDateTime,
    unstage: unstageFile,
    openPhoto: openPhoto,
    closePhoto: closePhoto,
    copyPath: copyToClipboard,
    copyToClipboard: copyToClipboard,
    normalizeAttachmentUrl: normalizeAttachmentUrl,
    normalizeMessageText: normalizeMessageText,
    getConnectionManager: () => connectionManager,
    getState: () => state,
    appendNewMessages: appendNewMessages,
    renderInitialMessages: renderInitialMessages,
    createMessageElement: createMessageElement,
    toggleAgentModal: toggleAgentStatusModal,
    updateAgentStatus: updateAgentStatus,
    switchRoom: switchRoom
  };

  if (typeof window !== "undefined") {
    window.parseUtcDate = parseUtcDate;
    window.formatTehranTime = formatTehranTime;
    window.formatTehranDateTime = formatTehranDateTime;
  }

  // Initialization
  async function init() {
    renderRoomButtons();
    setupEvents();
    await loadRooms();
    await loadAgentStatus();
    await loadInitialRoomMessages(state.currentRoom, true);

    // Start resilient connection manager (attempts realtime with 4s fallback to 2.5s polling)
    connectionManager.start();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
