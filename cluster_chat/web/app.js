/**
 * cluster_chat/web/app.js
 *
 * Client application for Cluster Chat Bridge.
 * Manages full-viewport chat (100dvh flex layout), compact room navigation,
 * default-hidden agent status modal, live polling, clipboard screenshot paste,
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
    knownRooms: ["main", "dm-argon", "workers"],
    roomDescriptions: {
      "main": "کانال عمومی پیام‌های کلاستر | General cluster broadcast channel",
      "dm-argon": "گفتگوی مستقیم با آرگون | Direct agent chat with LeadDesk Argon",
      "workers": "کانال ارتباطی ایجنت‌های ورکر | LeadDesk agent worker mesh channel",
      "workers-room": "کانال ارتباطی ایجنت‌های ورکر | LeadDesk agent worker mesh channel"
    },
    renderedMessageIds: new Set(),
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
        btnElement.innerHTML = "✓ کپی شد";
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

        card.innerHTML = `
          <div class="agent-avatar">${avatar}</div>
          <div class="agent-info">
            <div class="agent-name">${escapeHtml(agent.name)} <span class="agent-badge">${escapeHtml(agent.role || "")}</span></div>
            <div class="agent-state">
              <span class="dot ${isOnline ? "online" : "offline"}"></span>
              ${isOnline ? "آنلاین (آماده پردازش)" : "آفلاین"}
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

  // Load active rooms from server API
  async function loadRooms() {
    try {
      const res = await fetch("/api/cluster-chat/rooms");
      if (res.ok) {
        const data = await res.json();
        if (data.ok && Array.isArray(data.rooms)) {
          // Normalize room list: prefer "workers" over "workers-room" for UI tabs
          const mapped = data.rooms.map(r => (r === "workers-room" ? "workers" : r));
          state.knownRooms = Array.from(new Set([...state.knownRooms, ...mapped]));
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
    state.renderedMessageIds.clear();

    // Update UI headers safely
    if (elements.currentRoomTitle) {
      elements.currentRoomTitle.textContent = room;
    }
    if (elements.dropRoomTarget) {
      elements.dropRoomTarget.textContent = room;
    }
    if (elements.currentRoomDesc) {
      elements.currentRoomDesc.textContent =
        state.roomDescriptions[room] || "کانال ارتباطی فعال کلاستر";
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

  // Fetch messages for current room
  async function fetchMessages(forceScroll = false) {
    try {
      const roomParam = encodeURIComponent(state.currentRoom);
      const res = await fetch(`/api/cluster-chat/rooms/${roomParam}/messages?limit=100`);
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
    if (!elements.messageList || !elements.chatContainer) return;

    if (!messages || messages.length === 0) {
      elements.messageList.innerHTML =
        '<div class="empty-state">هیچ پیامی در اتاق #' +
        escapeHtml(state.currentRoom) +
        ' وجود ندارد. گفتگو را آغاز کنید!</div>';
      return;
    }

    const isNearBottom =
      elements.chatContainer.scrollHeight -
        elements.chatContainer.scrollTop -
        elements.chatContainer.clientHeight < 120;

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
                    <button type="button" class="btn-badge" onclick="window.ClusterChatUI.copyPath('${localPathEscaped}', this)" title="کپی مسیر محلی برای ایجنت">
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
          <span title="${escapeHtml(file.name)}">${escapeHtml(file.name)} (${formatBytes(file.size)})</span>
          <button type="button" class="btn-remove" title="حذف پیوست" onclick="window.ClusterChatUI.unstage(${idx})">×</button>
        `;
        elements.stagedItems.appendChild(item);
      });
    }
  }

  // Send message with or without attachments
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

    const activeTarget = (elements.targetAgentSelect && elements.targetAgentSelect.value) || null;

    if (elements.sendBtn) {
      elements.sendBtn.disabled = true;
      elements.sendBtn.innerHTML = "<span>در حال ارسال...</span> ⏳";
    }

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

  // Open Lightbox Photo Modal
  function openPhoto(publicUrl, filename, downloadUrl) {
    if (!elements.imageModal) return;
    if (elements.modalImg) elements.modalImg.src = publicUrl;
    if (elements.modalCaption) elements.modalCaption.textContent = filename;
    if (elements.modalDownload) {
      elements.modalDownload.href = downloadUrl;
      elements.modalDownload.setAttribute("download", filename);
    }
    elements.imageModal.classList.remove("hidden");
  }

  function closePhoto() {
    if (!elements.imageModal) return;
    elements.imageModal.classList.add("hidden");
    if (elements.modalImg) elements.modalImg.src = "";
  }

  // Setup Event Listeners
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
        }
      });
    }

    // Refresh button
    if (elements.refreshBtn) {
      elements.refreshBtn.addEventListener("click", () => fetchMessages(true));
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
        if (item.kind === "file" || (item.type && item.type.startsWith("image/"))) {
          const file = item.getAsFile();
          if (file) {
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
    if (elements.dropOverlay) {
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
  }

  // Expose global helpers for inline HTML event handlers & programmatic control
  window.ClusterChatUI = {
    unstage: unstageFile,
    openPhoto: openPhoto,
    copyPath: copyToClipboard,
    toggleAgentModal: toggleAgentStatusModal,
    updateAgentStatus: updateAgentStatus,
    switchRoom: switchRoom
  };

  // Initialization
  async function init() {
    renderRoomButtons();
    setupEvents();
    await loadRooms();
    await loadAgentStatus();
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
