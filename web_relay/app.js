/**
 * web_relay/app.js
 *
 * Standalone GitHub Pages / Static Web UI Fallback Client for Cluster-Chat.
 * Interacts directly with GitHub REST API (Contents, Actions Dispatches)
 * with ETag conditional polling, local deduplication, client-side WebCrypto
 * AES-256-GCM encryption, attachment handling, and Web Bridge execution.
 */

(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory(require('./crypto.js'));
  } else {
    root.GitHubRelayClient = factory(root.RelayCrypto);
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function (RelayCrypto) {
  'use strict';

  if (!RelayCrypto) {
    console.error('RelayCrypto library not found! Please include crypto.js before app.js.');
  }

  const VALID_ROOMS = ['main', 'dm-argon', 'workers'];
  const STORAGE_KEY_SEEN = 'cluster_chat_relay_seen_ids_v1';
  const STORAGE_KEY_CONFIG = 'cluster_chat_relay_config_v1';

  /**
   * Escape HTML entities to prevent XSS
   */
  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  /**
   * Format message body with safe link and code formatting
   */
  function formatMessageBody(rawText) {
    if (!rawText) return '';
    const text = String(rawText);

    // Code blocks ```code```
    const codeBlocks = [];
    let processed = text.replace(/```([a-zA-Z0-9_\-]*)\n?([\s\S]*?)```/g, (match, lang, code) => {
      const idx = codeBlocks.length;
      codeBlocks.push({ lang, code: escapeHtml(code.trim()) });
      return `@@CODE_BLOCK_${idx}@@`;
    });

    // Inline code `code`
    const inlineCodes = [];
    processed = processed.replace(/`([^`\n]+)`/g, (match, code) => {
      const idx = inlineCodes.length;
      inlineCodes.push(escapeHtml(code));
      return `@@INLINE_CODE_${idx}@@`;
    });

    // Markdown links [title](url)
    const links = [];
    processed = processed.replace(/\[([^\]]+)\]\((https?:\/\/[^\s\)]+)\)/g, (match, title, url) => {
      const idx = links.length;
      links.push({ title: escapeHtml(title), url: escapeHtml(url) });
      return `@@LINK_${idx}@@`;
    });

    // Bare URLs
    processed = processed.replace(/\b(https?:\/\/[^\s<>'"]+)/g, (match, url) => {
      const cleanUrl = url.replace(/[.,;:)]+$/, '');
      const trailing = url.slice(cleanUrl.length);
      const idx = links.length;
      links.push({ title: escapeHtml(cleanUrl), url: escapeHtml(cleanUrl) });
      return `@@LINK_${idx}@@` + trailing;
    });

    // Escape remaining HTML
    processed = escapeHtml(processed);

    // Restore Links
    processed = processed.replace(/@@LINK_(\d+)@@/g, (match, idx) => {
      const link = links[parseInt(idx, 10)];
      if (!link) return '';
      return `<a href="${link.url}" target="_blank" rel="noopener noreferrer">${link.title}</a>`;
    });

    // Restore Inline Code
    processed = processed.replace(/@@INLINE_CODE_(\d+)@@/g, (match, idx) => {
      const code = inlineCodes[parseInt(idx, 10)];
      if (!code) return '';
      return `<code>${code}</code>`;
    });

    // Restore Code Blocks
    processed = processed.replace(/@@CODE_BLOCK_(\d+)@@/g, (match, idx) => {
      const b = codeBlocks[parseInt(idx, 10)];
      if (!b) return '';
      return `<pre><code>${b.code}</code></pre>`;
    });

    return processed;
  }

  /**
   * Format ISO date string into human friendly time
   */
  function formatTimestamp(isoStr) {
    if (!isoStr) return '';
    try {
      const date = new Date(isoStr);
      return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
    } catch (e) {
      return isoStr;
    }
  }

  /**
   * Format file size
   */
  function formatBytes(bytes) {
    if (!bytes || bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
  }

  // ==============================================================================
  // GitHubRelayClient Class
  // ==============================================================================

  class GitHubRelayClient {
    constructor(config = {}) {
      this.owner = config.owner || 'mohammadlali0707-stack';
      this.repo = config.repo || 'agw-workers';
      this.branch = config.branch || 'main';
      this.authPat = config.pat || config.token || '';
      this.passphrase = config.passphrase || '';
      this.pollInterval = config.pollInterval || 10000;
      this.apiBaseUrl = 'https://api.github.com';
      this.rateLimitedUntil = 0;

      this.activeRoom = 'main';
      this.messages = {
        main: [],
        'dm-argon': [],
        workers: [],
      };

      this.etags = new Map();
      this.seenMsgIds = new Set();
      this.attachmentCache = new Map();
      this.rateLimit = {
        limit: 5000,
        remaining: 5000,
        reset: 0,
        used: 0,
      };

      this.pollTimer = null;
      this.isPolling = false;
      this.onMessagesUpdated = null;
      this.onRateLimitUpdated = null;
      this.onStatusChanged = null;

      this.loadSeenIds();
    }

    get token() {
      return this.authPat;
    }

    set token(val) {
      this.authPat = val || '';
    }

    /**
     * Load deduplication seen IDs from storage
     */
    loadSeenIds() {
      if (typeof localStorage === 'undefined') return;
      try {
        const stored = localStorage.getItem(`${STORAGE_KEY_SEEN}_${this.owner}_${this.repo}`);
        if (stored) {
          const arr = JSON.parse(stored);
          if (Array.isArray(arr)) {
            this.seenMsgIds = new Set(arr);
          }
        }
      } catch (err) {
        console.warn('Failed to load seenMsgIds from localStorage:', err);
      }
    }

    /**
     * Save deduplication seen IDs to storage (capped at 2000 items)
     */
    saveSeenIds() {
      if (typeof localStorage === 'undefined') return;
      try {
        const arr = Array.from(this.seenMsgIds);
        const capped = arr.slice(-2000);
        localStorage.setItem(`${STORAGE_KEY_SEEN}_${this.owner}_${this.repo}`, JSON.stringify(capped));
      } catch (err) {
        console.warn('Failed to save seenMsgIds to localStorage:', err);
      }
    }

    /**
     * Centralized GitHub API HTTP request handler with ETag support
     */
    async apiRequest(endpoint, options = {}) {
      const url = endpoint.startsWith('http') ? endpoint : `${this.apiBaseUrl}${endpoint}`;
      const method = options.method || 'GET';
      const headers = {
        Accept: 'application/vnd.github.v3+json',
        'X-GitHub-Api-Version': '2022-11-28',
      };

      if (this.token) {
        headers['Authorization'] = `Bearer ${this.token.trim()}`;
      }

      if (options.etag) {
        headers['If-None-Match'] = options.etag;
      }

      if (options.body && typeof options.body === 'object' && !(options.body instanceof FormData)) {
        headers['Content-Type'] = 'application/json';
        options.body = JSON.stringify(options.body);
      }

      try {
        const response = await fetch(url, {
          method,
          headers,
          body: options.body,
        });

        // Track rate limits from GitHub response headers
        const rem = response.headers.get('x-ratelimit-remaining');
        const lim = response.headers.get('x-ratelimit-limit');
        const res = response.headers.get('x-ratelimit-reset');
        const used = response.headers.get('x-ratelimit-used');

        if (rem !== null && lim !== null) {
          this.rateLimit = {
            remaining: parseInt(rem, 10),
            limit: parseInt(lim, 10),
            reset: parseInt(res || '0', 10),
            used: parseInt(used || '0', 10),
          };
          if (typeof this.onRateLimitUpdated === 'function') {
            this.onRateLimitUpdated(this.rateLimit);
          }
        }

        // Automatic Rate-Limit Cooldown Backoff
        if (response.status === 403 || response.status === 429) {
          if (this.rateLimit.remaining === 0 || response.status === 429) {
            const resetEpoch = this.rateLimit.reset ? this.rateLimit.reset * 1000 : Date.now() + 60000;
            const waitMs = Math.max(resetEpoch - Date.now(), 60000);
            this.rateLimitedUntil = Date.now() + waitMs;
            const waitSec = Math.ceil(waitMs / 1000);
            if (typeof this.onStatusChanged === 'function') {
              this.onStatusChanged('offline', `سقف مجاز سهمیه گیت‌هاب (403/429) تکمیل شد. توقف استعلام تا ${waitSec} ثانیه دیگر.`);
            }
          }
        }

        const etag = response.headers.get('etag');

        if (response.status === 304) {
          return { status: 304, notModified: true, etag };
        }

        let data = null;
        const contentType = response.headers.get('content-type') || '';
        if (contentType.includes('application/json')) {
          data = await response.json();
        } else {
          data = await response.text();
        }

        return {
          status: response.status,
          ok: response.ok,
          notModified: false,
          etag,
          data,
          headers: response.headers,
        };
      } catch (err) {
        return {
          status: 0,
          ok: false,
          notModified: false,
          error: err.message,
        };
      }
    }

    /**
     * Start automatic polling loop
     */
    startPolling() {
      if (this.pollTimer) {
        clearInterval(this.pollTimer);
      }
      this.pollActiveRoom();
      this.pollTimer = setInterval(() => {
        this.pollActiveRoom();
      }, this.pollInterval);
    }

    /**
     * Stop automatic polling loop
     */
    stopPolling() {
      if (this.pollTimer) {
        clearInterval(this.pollTimer);
        this.pollTimer = null;
      }
    }

    /**
     * Poll outbox for current active room and outbox files
     */
    async pollActiveRoom() {
      if (!this.token) {
        if (typeof this.onStatusChanged === 'function') {
          this.onStatusChanged('offline', 'توکن گیت‌هاب تنظیم نشده');
        }
        return;
      }

      // Check Rate Limit cooldown
      if (this.rateLimitedUntil && Date.now() < this.rateLimitedUntil) {
        const remSec = Math.ceil((this.rateLimitedUntil - Date.now()) / 1000);
        if (typeof this.onStatusChanged === 'function') {
          this.onStatusChanged('offline', `در انتظار بازنشانی سهمیه گیت‌هاب (${remSec} ثانیه)...`);
        }
        return;
      }

      this.isPolling = true;
      if (typeof this.onStatusChanged === 'function') {
        this.onStatusChanged('polling', 'در حال استعلام پیام‌ها...');
      }

      try {
        let hasNew = false;
        // 1. Poll active room snapshot
        const resRoom = await this.pollRoom(this.activeRoom);
        if (resRoom && resRoom.newCount > 0) {
          hasNew = true;
        }

        // 2. Poll individual outbox messages
        const resOutbox = await this.pollOutboxDirectory();
        if (resOutbox && resOutbox.newCount > 0) {
          hasNew = true;
        }

        if (typeof this.onStatusChanged === 'function') {
          this.onStatusChanged('online', 'متصل (آماده)');
        }

        if (hasNew && typeof this.onMessagesUpdated === 'function') {
          this.onMessagesUpdated(this.activeRoom);
        }
      } catch (err) {
        console.error('Error during polling:', RelayCrypto.scrubSecrets(err.message));
        if (typeof this.onStatusChanged === 'function') {
          this.onStatusChanged('offline', 'خطا در ارتباط: ' + RelayCrypto.scrubSecrets(err.message));
        }
      } finally {
        this.isPolling = false;
      }
    }

    /**
     * Poll rolling room snapshot: relay_queue/outbox/snapshot_<room>.json
     */
    async pollRoom(room) {
      if (!VALID_ROOMS.includes(room)) return { newCount: 0 };

      const path = `relay_queue/outbox/snapshot_${room}.json`;
      const cachedEtag = this.etags.get(path);

      const endpoint = `/repos/${this.owner}/${this.repo}/contents/${path}?ref=${encodeURIComponent(this.branch)}`;
      const res = await this.apiRequest(endpoint, { etag: cachedEtag });

      if (res.status === 304 || res.notModified) {
        // Content not modified, 0 rate limit cost!
        return { newCount: 0 };
      }

      if (!res.ok) {
        if (res.status === 404) {
          // Snapshot not created yet by VPS daemon
          return { newCount: 0 };
        }
        throw new Error(`GitHub API error (${res.status}): ${res.data && res.data.message ? res.data.message : 'Unknown'}`);
      }

      if (res.etag) {
        this.etags.set(path, res.etag);
      }

      // Parse JSON from base64 content
      let snapshotData = null;
      try {
        const cleanContent = (res.data.content || '').replace(/\s+/g, '');
        const rawBytes = RelayCrypto.base64ToBytes(cleanContent);
        const dec = new TextDecoder();
        snapshotData = JSON.parse(dec.decode(rawBytes));
      } catch (err) {
        console.warn('Failed to parse snapshot JSON for room ' + room, err);
        return { newCount: 0 };
      }

      let envelopes = [];
      if (Array.isArray(snapshotData)) {
        envelopes = snapshotData;
      } else if (snapshotData && Array.isArray(snapshotData.messages)) {
        envelopes = snapshotData.messages;
      }

      let newCount = 0;
      for (const env of envelopes) {
        if (!env || !env.msg_id) continue;
        if (this.seenMsgIds.has(env.msg_id)) continue;

        await this.processIncomingEnvelope(env, room);
        newCount++;
      }

      if (newCount > 0) {
        this.saveSeenIds();
      }

      return { newCount };
    }

    /**
     * Check individual files in relay_queue/outbox/
     */
    async pollOutboxDirectory() {
      const path = 'relay_queue/outbox';
      const cachedEtag = this.etags.get(path);
      const endpoint = `/repos/${this.owner}/${this.repo}/contents/${path}?ref=${encodeURIComponent(this.branch)}`;

      const res = await this.apiRequest(endpoint, { etag: cachedEtag });
      if (res.status === 304 || res.notModified || !res.ok || !Array.isArray(res.data)) {
        return { newCount: 0 };
      }

      if (res.etag) {
        this.etags.set(path, res.etag);
      }

      let newCount = 0;
      for (const item of res.data) {
        if (item.type !== 'file' || !item.name.endsWith('.json')) continue;
        if (item.name.startsWith('snapshot_') || item.name.startsWith('.')) continue;

        // Message file: msg_*.json or web_*.json
        const fileRes = await this.apiRequest(item.url);
        if (!fileRes.ok || !fileRes.data || !fileRes.data.content) continue;

        try {
          const cleanContent = (fileRes.data.content || '').replace(/\s+/g, '');
          const rawBytes = RelayCrypto.base64ToBytes(cleanContent);
          const dec = new TextDecoder();
          const env = JSON.parse(dec.decode(rawBytes));
          if (env && env.msg_id && !this.seenMsgIds.has(env.msg_id)) {
            const targetRoom = env.room || this.activeRoom;
            await this.processIncomingEnvelope(env, targetRoom);
            newCount++;
          }
        } catch (e) {
          console.warn('Failed to parse envelope from ' + item.name, e);
        }
      }

      if (newCount > 0) {
        this.saveSeenIds();
      }

      return { newCount };
    }

    /**
     * Process, decrypt, and store incoming message envelope
     */
    async processIncomingEnvelope(env, fallbackRoom) {
      if (!env || !env.msg_id) return;
      if (this.seenMsgIds.has(env.msg_id)) return;
      const room = VALID_ROOMS.includes(env.room) ? env.room : fallbackRoom;
      this.seenMsgIds.add(env.msg_id);

      let decryptedBody = env.body || '';
      let isDecrypted = false;
      let decryptError = null;

      if (env.encrypted) {
        if (this.passphrase) {
          try {
            decryptedBody = await RelayCrypto.decryptText(env, this.passphrase);
            isDecrypted = true;
          } catch (err) {
            decryptError = 'خطا در رمزگشایی: ' + err.message;
            decryptedBody = env.body;
          }
        } else {
          decryptError = 'پیام رمزگذاری شده است اما کلید محرمانه تنظیم نشده است.';
          decryptedBody = env.body;
        }
      }

      const processedMsg = {
        version: env.version || 1,
        msg_id: env.msg_id,
        room: room,
        sender: env.sender || 'unknown',
        created_at: env.created_at || new Date().toISOString(),
        encrypted: !!env.encrypted,
        cipher: env.cipher || 'none',
        iv: env.iv || null,
        raw_body: env.body,
        body: decryptedBody,
        is_decrypted: isDecrypted,
        decrypt_error: decryptError,
        attachments: Array.isArray(env.attachments) ? env.attachments : [],
        meta: env.meta || {},
      };

      if (!this.messages[room]) {
        this.messages[room] = [];
      }
      this.messages[room].push(processedMsg);
      // Sort chronologically
      this.messages[room].sort((a, b) => new Date(a.created_at) - new Date(b.created_at));
    }

    /**
     * Upload an attachment file to relay_queue/attachments/
     */
    async uploadAttachment(file) {
      if (file.size > 4 * 1024 * 1024) {
        throw new Error(`حجم فایل ${file.name} بیشتر از سقف مجاز ۴ مگابایت است.`);
      }

      const buffer = await file.arrayBuffer();
      const hashHex = await RelayCrypto.sha256BytesHex(buffer);
      const safeName = file.name.replace(/[^a-zA-Z0-9._-]/g, '_');
      const attId = `att_${hashHex.substring(0, 8)}_${safeName}`;
      const path = `relay_queue/attachments/${attId}.json`;

      const payload = {
        attachment_id: attId,
        filename: file.name,
        mime: file.type || 'application/octet-stream',
        size: file.size,
        sha256: hashHex,
        created_at: new Date().toISOString(),
        payload_base64: RelayCrypto.bytesToBase64(buffer),
      };

      const payloadBytes = new TextEncoder().encode(JSON.stringify(payload, null, 2));
      const b64Content = RelayCrypto.bytesToBase64(payloadBytes);

      const endpoint = `/repos/${this.owner}/${this.repo}/contents/${path}`;
      const putRes = await this.apiRequest(endpoint, {
        method: 'PUT',
        body: {
          message: `Upload attachment ${file.name} [${attId}]`,
          content: b64Content,
          branch: this.branch,
        },
      });

      if (!putRes.ok && putRes.status !== 422) {
        // 422 indicates file already exists with same sha, which is fine
        throw new Error(`خطا در آپلود فایل ${file.name}: ${putRes.data?.message || putRes.status}`);
      }

      // Cache locally
      this.attachmentCache.set(attId, {
        filename: file.name,
        mime: file.type || 'application/octet-stream',
        size: file.size,
        payload_base64: payload.payload_base64,
      });

      return {
        id: attId,
        name: file.name,
        mime: file.type || 'application/octet-stream',
        size: file.size,
        sha256: hashHex,
      };
    }

    /**
     * Fetch & download attachment bytes from relay_queue/attachments/
     */
    async getAttachmentData(attId) {
      if (this.attachmentCache.has(attId)) {
        return this.attachmentCache.get(attId);
      }

      const path = `relay_queue/attachments/${attId}.json`;
      const endpoint = `/repos/${this.owner}/${this.repo}/contents/${path}?ref=${encodeURIComponent(this.branch)}`;
      const res = await this.apiRequest(endpoint);

      if (!res.ok || !res.data || !res.data.content) {
        throw new Error('فایل پیوست در مخزن یافت نشد.');
      }

      const cleanContent = (res.data.content || '').replace(/\s+/g, '');
      const rawBytes = RelayCrypto.base64ToBytes(cleanContent);
      const dec = new TextDecoder();
      const attData = JSON.parse(dec.decode(rawBytes));

      this.attachmentCache.set(attId, attData);
      return attData;
    }

    /**
     * Trigger GitHub Actions Runner Web-Bridge or write web request queue file
     */
    async dispatchWebBridge(url, room, mode = 'markdown', maxBytes = 2097152) {
      const workflowPath = 'web-bridge.yml';
      const endpoint = `/repos/${this.owner}/${this.repo}/actions/workflows/${workflowPath}/dispatches`;

      const dispatchRes = await this.apiRequest(endpoint, {
        method: 'POST',
        body: {
          ref: this.branch,
          inputs: {
            url: url,
            room: room,
            mode: mode,
            max_bytes: String(maxBytes),
          },
        },
      });

      if (dispatchRes.ok || dispatchRes.status === 204) {
        return { success: true, method: 'workflow_dispatch' };
      }

      // Fallback: write request file to relay_queue/web_requests/
      const reqId = `req_${Date.now()}_${Math.random().toString(16).substring(2, 6)}`;
      const reqPath = `relay_queue/web_requests/${reqId}.json`;
      const reqPayload = {
        request_id: reqId,
        url: url,
        room: room,
        mode: mode,
        max_bytes: maxBytes,
        created_at: new Date().toISOString(),
        requester: 'mohammad',
      };

      const reqBytes = new TextEncoder().encode(JSON.stringify(reqPayload, null, 2));
      const putRes = await this.apiRequest(`/repos/${this.owner}/${this.repo}/contents/${reqPath}`, {
        method: 'PUT',
        body: {
          message: `Queue web fetch request ${reqId}`,
          content: RelayCrypto.bytesToBase64(reqBytes),
          branch: this.branch,
        },
      });

      if (putRes.ok) {
        return { success: true, method: 'queue_file', request_id: reqId };
      }

      throw new Error(`عدم موفقیت در اجرای وب‌بریج (${dispatchRes.status} / ${putRes.status})`);
    }

    /**
     * Send message to a room with attachments and optional encryption
     */
    async sendMessage(room, rawText, stagedFiles = []) {
      if (!VALID_ROOMS.includes(room)) {
        throw new Error(`اتاق نامعتبر است: ${room}`);
      }
      if (!this.token) {
        throw new Error('لطفاً ابتدا توکن گیت‌هاب را در بخش تنظیمات وارد کنید.');
      }

      // Step 1: Client-side secret scrubbing
      const scrubbedText = RelayCrypto.scrubSecrets(rawText);

      // Step 2: Upload staged attachments if any
      const attachmentsMeta = [];
      for (const file of stagedFiles) {
        const meta = await this.uploadAttachment(file);
        attachmentsMeta.push(meta);
      }

      // Step 3: Handle /web command trigger
      const webCmd = RelayCrypto.parseWebCommand(scrubbedText);
      if (webCmd) {
        try {
          await this.dispatchWebBridge(webCmd.url, room, webCmd.mode, webCmd.max_bytes);
        } catch (err) {
          console.warn('Web bridge dispatch notice:', err);
        }
      }

      // Step 4: Build Canonical Message Envelope
      const createdAt = new Date().toISOString();
      const nonce = Math.random().toString(16).substring(2, 10);
      let envelope = null;

      if (this.passphrase) {
        const enc = await RelayCrypto.encryptText(scrubbedText, this.passphrase);
        envelope = {
          version: 1,
          room: room,
          sender: 'mohammad',
          created_at: createdAt,
          encrypted: true,
          cipher: 'aes-256-gcm',
          iv: enc.iv,
          body: enc.body,
          attachments: attachmentsMeta,
          meta: {
            source: 'github-web-ui',
            salt: enc.salt,
            iterations: enc.iterations,
            nonce: nonce,
          },
        };
      } else {
        envelope = {
          version: 1,
          room: room,
          sender: 'mohammad',
          created_at: createdAt,
          encrypted: false,
          cipher: 'none',
          iv: null,
          body: scrubbedText,
          attachments: attachmentsMeta,
          meta: {
            source: 'github-web-ui',
            nonce: nonce,
          },
        };
      }

      envelope.msg_id = await RelayCrypto.computeMsgId(
        room,
        'mohammad',
        createdAt,
        envelope.body,
        nonce
      );

      // Step 5: Commit to relay_queue/inbox/
      const epochMs = Date.now();
      const shortId = envelope.msg_id.substring(0, 8);
      const filePath = `relay_queue/inbox/msg_${epochMs}_${shortId}.json`;
      const envBytes = new TextEncoder().encode(JSON.stringify(envelope, null, 2));
      const b64Content = RelayCrypto.bytesToBase64(envBytes);

      const endpoint = `/repos/${this.owner}/${this.repo}/contents/${filePath}`;
      const putRes = await this.apiRequest(endpoint, {
        method: 'PUT',
        body: {
          message: `Relay msg from mohammad [${room}]`,
          content: b64Content,
          branch: this.branch,
        },
      });

      if (!putRes.ok) {
        throw new Error(`خطا در ثبت پیام در گیت‌هاب (${putRes.status}): ${putRes.data?.message || 'نامشخص'}`);
      }

      // Step 6: Optimistic UI insertion
      this.seenMsgIds.add(envelope.msg_id);
      this.saveSeenIds();

      const localMsg = {
        version: 1,
        msg_id: envelope.msg_id,
        room: room,
        sender: 'mohammad',
        created_at: createdAt,
        encrypted: envelope.encrypted,
        cipher: envelope.cipher,
        iv: envelope.iv,
        raw_body: envelope.body,
        body: scrubbedText,
        is_decrypted: envelope.encrypted,
        decrypt_error: null,
        attachments: attachmentsMeta,
        meta: envelope.meta,
      };

      if (!this.messages[room]) {
        this.messages[room] = [];
      }
      this.messages[room].push(localMsg);

      return localMsg;
    }
  }

  // ==============================================================================
  // UI Controller Application
  // ==============================================================================

  class RelayApp {
    constructor() {
      this.client = null;
      this.stagedFiles = [];
      this.initDomElements();
      this.loadConfig();
      this.bindEvents();
      this.render();
      if (this.client.token) {
        this.client.startPolling();
      }
    }

    initDomElements() {
      this.dom = {
        roomTabs: document.querySelectorAll('.room-tab'),
        unreadBadges: {
          main: document.getElementById('unread-main'),
          'dm-argon': document.getElementById('unread-dm-argon'),
          workers: document.getElementById('unread-workers'),
        },
        messageStream: document.getElementById('message-stream'),
        emptyState: document.getElementById('empty-state'),
        statusPill: document.getElementById('status-pill'),
        statusDot: document.getElementById('status-dot'),
        statusText: document.getElementById('status-text'),
        rateLimitText: document.getElementById('ratelimit-text'),
        authBanner: document.getElementById('auth-banner'),
        bannerSettingsBtn: document.getElementById('banner-settings-btn'),
        manualPollBtn: document.getElementById('manual-poll-btn'),
        openSettingsBtn: document.getElementById('open-settings-btn'),
        closeSettingsBtn: document.getElementById('close-settings-btn'),
        settingsModal: document.getElementById('settings-modal'),
        settingsForm: document.getElementById('settings-form'),
        clearCredsBtn: document.getElementById('clear-creds-btn'),
        togglePatBtn: document.getElementById('toggle-pat-btn'),
        togglePassphraseBtn: document.getElementById('toggle-passphrase-btn'),
        cfgOwner: document.getElementById('cfg-owner'),
        cfgRepo: document.getElementById('cfg-repo'),
        cfgBranch: document.getElementById('cfg-branch'),
        cfgPat: document.getElementById('cfg-pat'),
        cfgRemember: document.getElementById('cfg-remember'),
        cfgPassphrase: document.getElementById('cfg-passphrase'),
        cfgInterval: document.getElementById('cfg-interval'),
        cryptoStatusIndicator: document.getElementById('crypto-status-indicator'),
        stagedTray: document.getElementById('staged-tray'),
        stagedList: document.getElementById('staged-list'),
        clearStagedBtn: document.getElementById('clear-staged-btn'),
        fileInput: document.getElementById('file-input'),
        attachBtn: document.getElementById('attach-btn'),
        messageInput: document.getElementById('message-input'),
        sendBtn: document.getElementById('send-btn'),
        quickWebHint: document.getElementById('quick-web-hint'),
        openWebBridgeBtn: document.getElementById('open-web-bridge-btn'),
        closeWebBridgeBtn: document.getElementById('close-web-bridge-btn'),
        cancelWebBridgeBtn: document.getElementById('cancel-web-bridge-btn'),
        webBridgeModal: document.getElementById('web-bridge-modal'),
        webBridgeForm: document.getElementById('web-bridge-form'),
        bridgeUrl: document.getElementById('bridge-url'),
        bridgeMode: document.getElementById('bridge-mode'),
        bridgeRoom: document.getElementById('bridge-room'),
        bridgeMaxBytes: document.getElementById('bridge-max-bytes'),
      };
    }

    loadConfig() {
      let saved = null;
      try {
        const local = localStorage.getItem(STORAGE_KEY_CONFIG);
        const session = sessionStorage.getItem(STORAGE_KEY_CONFIG);
        saved = local ? JSON.parse(local) : (session ? JSON.parse(session) : null);
      } catch (e) {
        console.warn('Failed to parse saved config:', e);
      }

      const config = saved || {
        owner: 'mohammadlali0707-stack',
        repo: 'agw-workers',
        branch: 'main',
        token: '',
        passphrase: '',
        pollInterval: 10000,
        rememberToken: false,
      };

      this.client = new GitHubRelayClient(config);

      // Populate Settings inputs
      if (this.dom.cfgOwner) this.dom.cfgOwner.value = config.owner;
      if (this.dom.cfgRepo) this.dom.cfgRepo.value = config.repo;
      if (this.dom.cfgBranch) this.dom.cfgBranch.value = config.branch;
      if (this.dom.cfgPat) this.dom.cfgPat.value = config.token;
      if (this.dom.cfgPassphrase) this.dom.cfgPassphrase.value = config.passphrase;
      if (this.dom.cfgInterval) this.dom.cfgInterval.value = String(config.pollInterval);
      if (this.dom.cfgRemember) this.dom.cfgRemember.checked = !!config.rememberToken;

      this.updateCryptoStatusIndicator();
      this.updateAuthBanner();
    }

    saveConfigFromForm() {
      const owner = this.dom.cfgOwner.value.trim() || 'mohammadlali0707-stack';
      const repo = this.dom.cfgRepo.value.trim() || 'agw-workers';
      const branch = this.dom.cfgBranch.value.trim() || 'main';
      const token = this.dom.cfgPat.value.trim();
      const passphrase = this.dom.cfgPassphrase.value.trim();
      const pollInterval = parseInt(this.dom.cfgInterval.value, 10) || 10000;
      const rememberToken = this.dom.cfgRemember.checked;

      const config = {
        owner,
        repo,
        branch,
        token,
        passphrase,
        pollInterval,
        rememberToken,
      };

      if (rememberToken) {
        localStorage.setItem(STORAGE_KEY_CONFIG, JSON.stringify(config));
        sessionStorage.removeItem(STORAGE_KEY_CONFIG);
      } else {
        sessionStorage.setItem(STORAGE_KEY_CONFIG, JSON.stringify(config));
        localStorage.removeItem(STORAGE_KEY_CONFIG);
      }

      this.client.owner = owner;
      this.client.repo = repo;
      this.client.branch = branch;
      this.client.token = token;
      this.client.passphrase = passphrase;
      this.client.pollInterval = pollInterval;

      this.updateCryptoStatusIndicator();
      this.updateAuthBanner();

      if (token) {
        this.client.startPolling();
      } else {
        this.client.stopPolling();
        this.updateStatus('offline', 'توکن ثبت نشده');
      }

      this.closeModal(this.dom.settingsModal);
    }

    clearStoredCredentials() {
      if (confirm('آیا از حذف اطلاعات اتصال و توکن از حافظه مرورگر اطمینان دارید؟')) {
        localStorage.removeItem(STORAGE_KEY_CONFIG);
        sessionStorage.removeItem(STORAGE_KEY_CONFIG);
        this.client.token = '';
        this.client.passphrase = '';
        this.client.stopPolling();
        if (this.dom.cfgPat) this.dom.cfgPat.value = '';
        if (this.dom.cfgPassphrase) this.dom.cfgPassphrase.value = '';
        this.updateCryptoStatusIndicator();
        this.updateAuthBanner();
        this.updateStatus('offline', 'اطلاعات حذف شد');
        this.closeModal(this.dom.settingsModal);
      }
    }

    updateAuthBanner() {
      if (!this.dom.authBanner) return;
      if (!this.client.token) {
        this.dom.authBanner.classList.add('visible');
      } else {
        this.dom.authBanner.classList.remove('visible');
      }
    }

    updateCryptoStatusIndicator() {
      if (!this.dom.cryptoStatusIndicator) return;
      if (this.client.passphrase) {
        this.dom.cryptoStatusIndicator.innerHTML = '🔒 رمزنگاری E2E فعال (AES-256-GCM)';
        this.dom.cryptoStatusIndicator.style.color = 'var(--color-mohammad)';
      } else {
        this.dom.cryptoStatusIndicator.innerHTML = '🔓 بدون رمزنگاری E2E';
        this.dom.cryptoStatusIndicator.style.color = 'var(--text-dim)';
      }
    }

    updateStatus(state, text) {
      if (this.dom.statusDot) {
        this.dom.statusDot.className = 'status-dot ' + state;
      }
      if (this.dom.statusText) {
        this.dom.statusText.textContent = text || state;
      }
    }

    updateRateLimit(rl) {
      if (this.dom.rateLimitText && rl) {
        this.dom.rateLimitText.textContent = `${rl.remaining}/${rl.limit}`;
        if (rl.remaining < 500) {
          this.dom.rateLimitText.style.color = 'var(--color-danger)';
        } else {
          this.dom.rateLimitText.style.color = 'var(--text-dim)';
        }
      }
    }

    bindEvents() {
      // Room Tabs Click
      this.dom.roomTabs.forEach((tab) => {
        tab.addEventListener('click', () => {
          const room = tab.dataset.room;
          if (room) this.switchRoom(room);
        });
      });

      // Settings Modal Triggers
      this.dom.openSettingsBtn?.addEventListener('click', () => this.openModal(this.dom.settingsModal));
      this.dom.bannerSettingsBtn?.addEventListener('click', () => this.openModal(this.dom.settingsModal));
      this.dom.closeSettingsBtn?.addEventListener('click', () => this.closeModal(this.dom.settingsModal));
      this.dom.settingsForm?.addEventListener('submit', (e) => {
        e.preventDefault();
        this.saveConfigFromForm();
      });
      this.dom.clearCredsBtn?.addEventListener('click', () => this.clearStoredCredentials());

      // Password Toggles
      this.dom.togglePatBtn?.addEventListener('click', () => {
        const inp = this.dom.cfgPat;
        inp.type = inp.type === 'password' ? 'text' : 'password';
      });
      this.dom.togglePassphraseBtn?.addEventListener('click', () => {
        const inp = this.dom.cfgPassphrase;
        inp.type = inp.type === 'password' ? 'text' : 'password';
      });

      // Web Bridge Modal Triggers
      this.dom.openWebBridgeBtn?.addEventListener('click', () => {
        if (this.dom.bridgeRoom) this.dom.bridgeRoom.value = this.client.activeRoom;
        this.openModal(this.dom.webBridgeModal);
      });
      this.dom.closeWebBridgeBtn?.addEventListener('click', () => this.closeModal(this.dom.webBridgeModal));
      this.dom.cancelWebBridgeBtn?.addEventListener('click', () => this.closeModal(this.dom.webBridgeModal));
      this.dom.webBridgeForm?.addEventListener('submit', async (e) => {
        e.preventDefault();
        await this.handleWebBridgeSubmit();
      });

      // Quick Web Hint click
      this.dom.quickWebHint?.addEventListener('click', () => {
        if (this.dom.messageInput) {
          this.dom.messageInput.value = '/web https://';
          this.dom.messageInput.focus();
        }
      });

      // Manual Poll Button
      this.dom.manualPollBtn?.addEventListener('click', async () => {
        if (this.client) {
          await this.client.pollActiveRoom();
        }
      });

      // Attachment handling
      this.dom.attachBtn?.addEventListener('click', () => this.dom.fileInput?.click());
      this.dom.fileInput?.addEventListener('change', (e) => this.handleFileSelection(e));
      this.dom.clearStagedBtn?.addEventListener('click', () => this.clearStagedFiles());

      // Auto-resize textarea & Enter key
      this.dom.messageInput?.addEventListener('input', () => this.autoResizeTextarea());
      this.dom.messageInput?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          this.handleSendMessage();
        }
      });

      // Send Button
      this.dom.sendBtn?.addEventListener('click', () => this.handleSendMessage());

      // Client callbacks
      this.client.onStatusChanged = (state, text) => this.updateStatus(state, text);
      this.client.onRateLimitUpdated = (rl) => this.updateRateLimit(rl);
      this.client.onMessagesUpdated = (room) => {
        if (room === this.client.activeRoom) {
          this.renderMessages();
        } else {
          this.updateUnreadBadge(room);
        }
      };

      // Drag & drop files onto chat viewport
      const viewport = document.querySelector('.chat-viewport');
      if (viewport) {
        viewport.addEventListener('dragover', (e) => {
          e.preventDefault();
          viewport.style.backgroundColor = 'rgba(2, 132, 199, 0.08)';
        });
        viewport.addEventListener('dragleave', (e) => {
          e.preventDefault();
          viewport.style.backgroundColor = '';
        });
        viewport.addEventListener('drop', (e) => {
          e.preventDefault();
          viewport.style.backgroundColor = '';
          if (e.dataTransfer && e.dataTransfer.files) {
            this.addFilesToStage(Array.from(e.dataTransfer.files));
          }
        });
      }
    }

    openModal(modal) {
      if (modal) modal.classList.add('open');
    }

    closeModal(modal) {
      if (modal) modal.classList.remove('open');
    }

    switchRoom(room) {
      if (!VALID_ROOMS.includes(room)) return;
      this.client.activeRoom = room;

      this.dom.roomTabs.forEach((tab) => {
        if (tab.dataset.room === room) {
          tab.classList.add('active');
        } else {
          tab.classList.remove('active');
        }
      });

      // Clear unread badge
      const badge = this.dom.unreadBadges[room];
      if (badge) {
        badge.textContent = '0';
        badge.classList.remove('visible');
      }

      this.renderMessages();
      this.client.pollRoom(room).then((res) => {
        if (res && res.newCount > 0) {
          this.renderMessages();
        }
      });
    }

    updateUnreadBadge(room) {
      const badge = this.dom.unreadBadges[room];
      if (!badge) return;
      const count = parseInt(badge.textContent || '0', 10) + 1;
      badge.textContent = String(count);
      badge.classList.add('visible');
    }

    handleFileSelection(e) {
      if (e.target && e.target.files) {
        this.addFilesToStage(Array.from(e.target.files));
        e.target.value = '';
      }
    }

    addFilesToStage(files) {
      for (const f of files) {
        if (f.size > 4 * 1024 * 1024) {
          alert(`حجم فایل ${f.name} بیش از سقف مجاز ۴ مگابایت است.`);
          continue;
        }
        this.stagedFiles.push(f);
      }
      this.renderStagedTray();
    }

    clearStagedFiles() {
      this.stagedFiles = [];
      this.renderStagedTray();
    }

    removeStagedFile(idx) {
      this.stagedFiles.splice(idx, 1);
      this.renderStagedTray();
    }

    renderStagedTray() {
      if (!this.dom.stagedTray || !this.dom.stagedList) return;
      if (this.stagedFiles.length === 0) {
        this.dom.stagedTray.classList.remove('visible');
        this.dom.stagedList.innerHTML = '';
        return;
      }

      this.dom.stagedTray.classList.add('visible');
      this.dom.stagedList.innerHTML = this.stagedFiles
        .map(
          (file, idx) => `
          <div class="staged-item">
            <span>📎 ${escapeHtml(file.name)} (${formatBytes(file.size)})</span>
            <button type="button" class="remove-btn" data-idx="${idx}" title="حذف">×</button>
          </div>
        `
        )
        .join('');

      this.dom.stagedList.querySelectorAll('.remove-btn').forEach((btn) => {
        btn.addEventListener('click', (e) => {
          const idx = parseInt(e.target.dataset.idx, 10);
          this.removeStagedFile(idx);
        });
      });
    }

    autoResizeTextarea() {
      const textarea = this.dom.messageInput;
      if (!textarea) return;
      textarea.style.height = 'auto';
      const newHeight = Math.min(Math.max(textarea.scrollHeight, 40), 140);
      textarea.style.height = `${newHeight}px`;
    }

    async handleSendMessage() {
      const text = this.dom.messageInput.value.trim();
      const files = [...this.stagedFiles];

      if (!text && files.length === 0) return;

      this.dom.sendBtn.disabled = true;
      this.dom.sendBtn.innerHTML = '<span>در حال ارسال...</span> ⏳';

      try {
        await this.client.sendMessage(this.client.activeRoom, text, files);
        this.dom.messageInput.value = '';
        this.autoResizeTextarea();
        this.clearStagedFiles();
        this.renderMessages();
      } catch (err) {
        alert('خطا در ارسال پیام: ' + RelayCrypto.scrubSecrets(err.message));
      } finally {
        this.dom.sendBtn.disabled = false;
        this.dom.sendBtn.innerHTML = '<span>ارسال</span> <span>🚀</span>';
      }
    }

    async handleWebBridgeSubmit() {
      const url = this.dom.bridgeUrl.value.trim();
      const mode = this.dom.bridgeMode.value;
      const room = this.dom.bridgeRoom.value;
      const maxBytes = parseInt(this.dom.bridgeMaxBytes.value, 10) || 2097152;

      if (!url) return;

      const submitBtn = document.getElementById('submit-web-bridge-btn');
      if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.textContent = 'در حال فراخوانی رانر... ⏳';
      }

      try {
        await this.client.dispatchWebBridge(url, room, mode, maxBytes);
        alert(`درخواست واکشی برای آدرس ${url} به رانر ارسال شد. پاسخ به زودی در اتاق ${room} دریافت می‌شود.`);
        this.closeModal(this.dom.webBridgeModal);
        this.dom.bridgeUrl.value = '';
      } catch (err) {
        alert('خطا در ارسال درخواست وب‌بریج: ' + RelayCrypto.scrubSecrets(err.message));
      } finally {
        if (submitBtn) {
          submitBtn.disabled = false;
          submitBtn.textContent = 'اجرای درخواست در رانر 🚀';
        }
      }
    }

    render() {
      this.renderMessages();
    }

    renderMessages() {
      if (!this.dom.messageStream) return;
      const room = this.client.activeRoom;
      const messages = this.client.messages[room] || [];

      if (messages.length === 0) {
        this.dom.messageStream.innerHTML = `
          <div class="empty-state">
            <div class="empty-icon">💬</div>
            <div class="empty-title">اتاق ${escapeHtml(room)} خالی است</div>
            <div class="empty-desc">هنوز پیامی در این اتاق تبادل نشده است. می‌توانید اولین پیام را ارسال کنید.</div>
          </div>
        `;
        return;
      }

      let html = '';
      for (const msg of messages) {
        const isOutgoing = msg.sender === 'mohammad';
        const senderClass = isOutgoing ? 'outgoing' : `incoming sender-${escapeHtml(msg.sender)}`;

        let cryptoBadge = '';
        if (msg.encrypted) {
          if (msg.decrypt_error) {
            cryptoBadge = `<span class="crypto-badge error" title="${escapeHtml(msg.decrypt_error)}">🔒 خطا در رمزگشایی</span>`;
          } else {
            cryptoBadge = `<span class="crypto-badge" title="رمزنگاری سرتاسری AES-256-GCM">🔒 امن</span>`;
          }
        }

        let senderTitle = msg.sender;
        let senderEmoji = '👤';
        if (msg.sender === 'argon') {
          senderEmoji = '🤖';
          senderTitle = 'Argon (Planner)';
        } else if (msg.sender === 'worker') {
          senderEmoji = '⚙️';
          senderTitle = 'Worker';
        } else if (msg.sender === 'web-bridge') {
          senderEmoji = '🌐';
          senderTitle = 'Web Bridge';
        } else if (msg.sender === 'mohammad') {
          senderEmoji = '👤';
          senderTitle = 'Mohammad';
        }

        let attachmentsHtml = '';
        if (msg.attachments && msg.attachments.length > 0) {
          attachmentsHtml = '<div class="bubble-attachments">' +
            msg.attachments
              .map((att) => {
                const isImage = att.mime && att.mime.startsWith('image/');
                return `
                <div class="attachment-item-wrap">
                  <button type="button" class="attachment-chip download-att-btn" data-id="${escapeHtml(att.id)}" data-name="${escapeHtml(att.name)}" data-mime="${escapeHtml(att.mime)}">
                    <span>${isImage ? '🖼️' : '📄'}</span>
                    <span class="attachment-name">${escapeHtml(att.name)}</span>
                    <span class="attachment-meta">(${formatBytes(att.size)})</span>
                    <span>⬇️</span>
                  </button>
                  <div class="preview-slot" id="preview-${escapeHtml(att.id)}"></div>
                </div>
              `;
              })
              .join('') +
            '</div>';
        }

        const formattedBody = formatMessageBody(msg.body);

        html += `
          <div class="message-row ${senderClass}" data-msg-id="${escapeHtml(msg.msg_id)}">
            <div class="message-bubble">
              <div class="bubble-header">
                <span class="sender-tag ${escapeHtml(msg.sender)}">
                  <span>${senderEmoji}</span>
                  <span>${escapeHtml(senderTitle)}</span>
                </span>
                ${cryptoBadge}
                <span class="bubble-time">${formatTimestamp(msg.created_at)}</span>
              </div>
              <div class="bubble-body" dir="auto">${formattedBody}</div>
              ${attachmentsHtml}
            </div>
          </div>
        `;
      }

      this.dom.messageStream.innerHTML = html;

      // Bind attachment download clicks
      this.dom.messageStream.querySelectorAll('.download-att-btn').forEach((btn) => {
        btn.addEventListener('click', async (e) => {
          const btnEl = e.currentTarget;
          const attId = btnEl.dataset.id;
          const name = btnEl.dataset.name;
          const mime = btnEl.dataset.mime || 'application/octet-stream';
          await this.handleDownloadAttachment(attId, name, mime);
        });
      });

      // Scroll to bottom smoothly
      this.dom.messageStream.scrollTop = this.dom.messageStream.scrollHeight;
    }

    async handleDownloadAttachment(attId, filename, mimeType) {
      try {
        const attData = await this.client.getAttachmentData(attId);
        if (!attData || !attData.payload_base64) {
          throw new Error('داده باینری فایل یافت نشد.');
        }

        const rawBytes = RelayCrypto.base64ToBytes(attData.payload_base64);
        const blob = new Blob([rawBytes], { type: mimeType });
        const blobUrl = URL.createObjectURL(blob);

        // If image, preview inline as well as downloading
        if (mimeType.startsWith('image/')) {
          const slot = document.getElementById(`preview-${attId}`);
          if (slot && !slot.hasChildNodes()) {
            const img = document.createElement('img');
            img.src = blobUrl;
            img.className = 'attachment-preview-img';
            img.alt = filename;
            img.onclick = () => window.open(blobUrl, '_blank');
            slot.appendChild(img);
          }
        }

        // Trigger browser file download
        const a = document.createElement('a');
        a.href = blobUrl;
        a.download = filename || 'attachment.bin';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
      } catch (err) {
        alert('خطا در دانلود پیوست: ' + RelayCrypto.scrubSecrets(err.message));
      }
    }
  }

  // Bootstrapping
  if (typeof window !== 'undefined') {
    window.addEventListener('DOMContentLoaded', () => {
      window.relayApp = new RelayApp();
    });
  }

  return GitHubRelayClient;
});
