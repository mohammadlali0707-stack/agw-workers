/**
 * cluster_chat/web/app.js
 *
 * Client application for Cluster Chat Bridge.
 * Manages room navigation, live polling, clipboard screenshot paste,
 * drag-and-drop file upload, inline photo previews, and agent document cards.
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
    knownRooms: ["main", "dm-argon", "workers-room"],
    roomDescriptions: {
      "main": "General cluster broadcast channel",
      "dm-argon": "Direct agent chat with LeadDesk Argon",
      "workers-room": "LeadDesk agent worker mesh channel"
    },
    renderedMessageIds: new Set(),
    isAtBottom: true
  };

  // DOM Elements
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
    stagedTray: document.getElementById("staged-tray"),
    stagedCount: document.getElementById("staged-count"),
    stagedItems: document.getElementById("staged-items"),
    clearStagedBtn: document.getElementById("clear-staged-btn"),
    composerForm: document.getElementById("composer-form"),
    messageText: document.getElementById("message-text"),
    fileInput: document.getElementById("file-input"),
    attachBtn: document.getElementById("attach-btn"),
    sendBtn: document.getElementById("send-btn"),
    refreshBtn: document.getElementById("refresh-btn"),
    imageModal: document.getElementById("image-modal"),
    modalImg: document.getElementById("modal-img"),
    modalCaption: document.getElementById("modal-caption"),
    modalDownload: document.getElementById("modal-download"),
    modalClose: document.getElementById("modal-close")
  };

  // Format bytes into human-readable size
  function formatBytes(bytes) {
    if (bytes === 0 || !bytes) return "0 B";
    const k = 1024;
    const sizes = ["B", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
  }

  // Escape HTML strings for safety
  function escapeHtml(str) {
    if (!str) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  // Copy text to system clipboard with visual button feedback
  async function copyToClipboard(text, btnElement) {
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const textarea = document.createElement("textarea");
        textarea.value = text;
        textarea.style.position = "fixed";
        textarea.style.opacity = "0";
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand("copy");
        document.body.removeChild(textarea);
      }
      if (btnElement) {
        const originalText = btnElement.innerHTML;
        btnElement.classList.add("copied");
        btnElement.innerHTML = "✓ Copied!";
        setTimeout(() => {
          btnElement.classList.remove("copied");
          btnElement.innerHTML = originalText;
        }, 2000);
      }
    } catch (err) {
      console.error("Clipboard copy failed:", err);
      alert("Path: " + text);
    }
  }

  // Load active rooms from server API
  async function loadRooms() {
    try {
      const res = await fetch("/api/cluster-chat/rooms");
      if (res.ok) {
        const data = await res.json();
        if (data.ok && Array.isArray(data.rooms)) {
          state.knownRooms = Array.from(new Set([...state.knownRooms, ...data.rooms]));
          renderRoomButtons();
        }
      }
    } catch (e) {
      console.warn("Could not fetch rooms list:", e);
    }
  }

  // Render sidebar room buttons
  function renderRoomButtons() {
    elements.roomList.innerHTML = "";
    state.knownRooms.forEach(room => {
      const btn = document.createElement("button");
      btn.className = "room-btn" + (room === state.currentRoom ? " active" : "");
      btn.dataset.room = room;

      let icon = "💬";
      let badge = "room";
      if (room === "main") {
        icon = "🌐";
        badge = "general";
      } else if (room === "dm-argon") {
        icon = "🤖";
        badge = "lead";
      } else if (room === "workers-room") {
        icon = "⚙️";
        badge = "mesh";
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
    state.renderedMessageIds.clear();

    // Update UI headers
    elements.currentRoomTitle.textContent = room;
    elements.dropRoomTarget.textContent = room;
    elements.currentRoomDesc.textContent =
      state.roomDescriptions[room] || "Active cluster communication channel";

    // Update active class on buttons
    document.querySelectorAll(".room-btn").forEach(btn => {
      if (btn.dataset.room === room) {
        btn.classList.add("active");
      } else {
        btn.classList.remove("active");
      }
    });

    elements.messageList.innerHTML = '<div class="empty-state">Loading messages...</div>';
    fetchMessages(true);
  }

  // Fetch messages for current room
  async function fetchMessages(forceScroll = false) {
    try {
      const res = await fetch(`/api/cluster-chat/rooms/${encodeURIComponent(state.currentRoom)}/messages?limit=100`);
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      const data = await res.json();
      if (data.ok && Array.isArray(data.messages)) {
        renderMessages(data.messages, forceScroll);
      }
    } catch (err) {
      console.warn("Failed to fetch messages:", err);
    }
  }

  // Render messages in chat window
  function renderMessages(messages, forceScroll = false) {
    if (!messages || messages.length === 0) {
      elements.messageList.innerHTML = '<div class="empty-state">No messages in #' + escapeHtml(state.currentRoom) + ' yet. Start the conversation!</div>';
      return;
    }

    // Check if user is scrolled near bottom
    const isNearBottom =
      elements.chatContainer.scrollHeight - elements.chatContainer.scrollTop - elements.chatContainer.clientHeight < 120;

    elements.messageList.innerHTML = "";

    messages.forEach(msg => {
      const msgItem = document.createElement("div");
      msgItem.className = "message-item";
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
      if (msg.created_at) {
        try {
          const dt = new Date(msg.created_at);
          timeFormatted = dt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
        } catch (_) {
          timeFormatted = msg.created_at;
        }
      }

      let bodyHtml = "";
      if (msg.text) {
        bodyHtml = `<div class="message-body">${escapeHtml(msg.text)}</div>`;
      }

      // Render attachments
      let attachmentsHtml = "";
      if (Array.isArray(msg.attachments) && msg.attachments.length > 0) {
        attachmentsHtml = '<div class="attachments-grid">';
        msg.attachments.forEach(att => {
          const kind = att.kind || (att.mime_type && att.mime_type.startsWith("image/") ? "photo" : "document");
          const publicUrl = att.public_url || `/api/cluster-chat/files/${att.room}/${att.created_at ? att.created_at.slice(0, 10) : ""}/${att.filename}`;
          const downloadUrl = publicUrl + (publicUrl.includes("?") ? "&download=1" : "?download=1");
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
                  <a class="btn-link" href="${escapeHtml(downloadUrl)}" download="${escapeHtml(att.filename)}" title="Download photo">⬇️</a>
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
                    <button class="btn-badge" onclick="window.ClusterChatUI.copyPath('${localPathEscaped}', this)" title="Copy local path for local agent use">
                      📋 Path
                    </button>
                  ` : ""}
                  <a class="btn-badge" href="${escapeHtml(downloadUrl)}" download="${escapeHtml(att.filename)}" title="Download file">
                    ⬇️ Save
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
          <span class="message-time">${timeFormatted}</span>
        </div>
        ${bodyHtml}
        ${attachmentsHtml}
      `;

      elements.messageList.appendChild(msgItem);
    });

    if (forceScroll || isNearBottom) {
      elements.chatContainer.scrollTop = elements.chatContainer.scrollHeight;
    }
  }

  // Stage files for upload
  function stageFiles(fileList) {
    if (!fileList || fileList.length === 0) return;
    for (let i = 0; i < fileList.length; i++) {
      state.stagedFiles.push(fileList[i]);
    }
    renderStagedTray();
  }

  // Remove file from staging tray
  function unstageFile(index) {
    if (index >= 0 && index < state.stagedFiles.length) {
      state.stagedFiles.splice(index, 1);
      renderStagedTray();
    }
  }

  // Render staging tray preview
  function renderStagedTray() {
    if (state.stagedFiles.length === 0) {
      elements.stagedTray.classList.add("hidden");
      elements.stagedItems.innerHTML = "";
      return;
    }

    elements.stagedTray.classList.remove("hidden");
    elements.stagedCount.textContent = state.stagedFiles.length;
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
        <span title="${escapeHtml(file.name)}">${escapeHtml(file.name)} (${formatBytes(file.size)})</span>
        <button type="button" class="btn-remove" title="Remove attachment" onclick="window.ClusterChatUI.unstage(${idx})">×</button>
      `;
      elements.stagedItems.appendChild(item);
    });
  }

  // Send message with or without attachments
  async function sendMessage() {
    const text = elements.messageText.value.trim();
    const hasFiles = state.stagedFiles.length > 0;

    if (!text && !hasFiles) {
      return;
    }

    // Determine active sender
    let activeSender = elements.senderSelect.value;
    if (activeSender === "__custom__") {
      activeSender = elements.customSenderInput.value.trim();
      if (!activeSender) {
        alert("Please specify a custom sender name.");
        elements.customSenderInput.focus();
        return;
      }
    }

    const activeTarget = elements.targetAgentSelect.value || null;

    elements.sendBtn.disabled = true;
    elements.sendBtn.innerHTML = "<span>Sending...</span> ⏳";

    try {
      if (hasFiles) {
        // Multipart upload
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
        // Plain message JSON
        const payload = {
          sender: activeSender,
          text: text,
          target_agent: activeTarget,
          forward_telegram: true
        };

        const res = await fetch(`/api/cluster-chat/rooms/${encodeURIComponent(state.currentRoom)}/messages`, {
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
      elements.messageText.value = "";
      state.stagedFiles = [];
      renderStagedTray();
      await fetchMessages(true);
    } catch (err) {
      console.error("Failed to send message:", err);
      alert("Error sending message: " + err.message);
    } finally {
      elements.sendBtn.disabled = false;
      elements.sendBtn.innerHTML = "<span>Send</span> 🚀";
      elements.messageText.focus();
    }
  }

  // Open Lightbox Photo Modal
  function openPhoto(publicUrl, filename, downloadUrl) {
    elements.modalImg.src = publicUrl;
    elements.modalCaption.textContent = filename;
    elements.modalDownload.href = downloadUrl;
    elements.modalDownload.setAttribute("download", filename);
    elements.imageModal.classList.remove("hidden");
  }

  function closePhoto() {
    elements.imageModal.classList.add("hidden");
    elements.modalImg.src = "";
  }

  // Setup Event Listeners
  function setupEvents() {
    // Sender selector custom toggle
    elements.senderSelect.addEventListener("change", e => {
      if (e.target.value === "__custom__") {
        elements.customSenderInput.classList.remove("hidden");
        elements.customSenderInput.focus();
      } else {
        elements.customSenderInput.classList.add("hidden");
      }
    });

    // Refresh button
    elements.refreshBtn.addEventListener("click", () => fetchMessages(true));

    // Attachment button click
    elements.attachBtn.addEventListener("click", () => {
      elements.fileInput.click();
    });

    // File input change
    elements.fileInput.addEventListener("change", e => {
      stageFiles(e.target.files);
      elements.fileInput.value = "";
    });

    // Clear staged attachments
    elements.clearStagedBtn.addEventListener("click", () => {
      state.stagedFiles = [];
      renderStagedTray();
    });

    // Composer form submission
    elements.composerForm.addEventListener("submit", e => {
      e.preventDefault();
      sendMessage();
    });

    // Textarea enter key submit (Shift+Enter for newline)
    elements.messageText.addEventListener("keydown", e => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    });

    // Lightbox close
    elements.modalClose.addEventListener("click", closePhoto);
    elements.imageModal.querySelector(".modal-backdrop").addEventListener("click", closePhoto);
    document.addEventListener("keydown", e => {
      if (e.key === "Escape" && !elements.imageModal.classList.contains("hidden")) {
        closePhoto();
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
        if (item.kind === "file" || (item.type && item.type.startsWith("image/"))) {
          const file = item.getAsFile();
          if (file) {
            // Give generated name if clipboard image has generic name
            let filename = file.name;
            if (!filename || filename === "image.png") {
              const ext = file.type === "image/jpeg" ? ".jpg" : ".png";
              filename = `screenshot_${Date.now()}${ext}`;
            }
            const renamed = new File([file], filename, { type: file.type });
            pastedFiles.push(renamed);
          }
        }
      }

      if (pastedFiles.length > 0) {
        stageFiles(pastedFiles);
      }
    });

    // Drag and Drop Handling over chat container
    let dragCounter = 0;

    const dragEnterHandler = e => {
      e.preventDefault();
      e.stopPropagation();
      dragCounter++;
      elements.dropOverlay.classList.add("active");
    };

    const dragLeaveHandler = e => {
      e.preventDefault();
      e.stopPropagation();
      dragCounter--;
      if (dragCounter <= 0) {
        dragCounter = 0;
        elements.dropOverlay.classList.remove("active");
      }
    };

    const dragOverHandler = e => {
      e.preventDefault();
      e.stopPropagation();
    };

    const dropHandler = e => {
      e.preventDefault();
      e.stopPropagation();
      dragCounter = 0;
      elements.dropOverlay.classList.remove("active");

      if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
        stageFiles(e.dataTransfer.files);
      }
    };

    window.addEventListener("dragenter", dragEnterHandler);
    window.addEventListener("dragleave", dragLeaveHandler);
    window.addEventListener("dragover", dragOverHandler);
    window.addEventListener("drop", dropHandler);
  }

  // Expose global helpers for inline HTML event handlers
  window.ClusterChatUI = {
    unstage: unstageFile,
    openPhoto: openPhoto,
    copyPath: copyToClipboard
  };

  // Initialization
  async function init() {
    renderRoomButtons();
    setupEvents();
    await loadRooms();
    await fetchMessages(true);

    // Live polling every 3 seconds
    state.pollingTimer = setInterval(() => {
      fetchMessages(false);
    }, state.pollIntervalMs);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
