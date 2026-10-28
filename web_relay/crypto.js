/**
 * web_relay/crypto.js
 *
 * Client-side cryptographic helper library for GitHub Relay Mode.
 * Implements WebCrypto (window.crypto.subtle) AES-256-GCM encryption with
 * PBKDF2 key derivation, deterministic SHA-256 msg_id computation,
 * and secret sanitization compatible with cluster_chat/relay_protocol.py.
 */

(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.RelayCrypto = factory();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  // Get subtle crypto instance across Browser and Node.js runtimes
  const getSubtle = () => {
    if (typeof globalThis !== 'undefined' && globalThis.crypto && globalThis.crypto.subtle) {
      return globalThis.crypto.subtle;
    }
    if (typeof window !== 'undefined' && window.crypto && window.crypto.subtle) {
      return window.crypto.subtle;
    }
    throw new Error('WebCrypto API (crypto.subtle) is not available in this environment');
  };

  const getRandomBytes = (length) => {
    const bytes = new Uint8Array(length);
    if (typeof globalThis !== 'undefined' && globalThis.crypto && globalThis.crypto.getRandomValues) {
      globalThis.crypto.getRandomValues(bytes);
    } else if (typeof window !== 'undefined' && window.crypto && window.crypto.getRandomValues) {
      window.crypto.getRandomValues(bytes);
    } else {
      // Fallback for older environments
      for (let i = 0; i < length; i++) {
        bytes[i] = Math.floor(Math.random() * 256);
      }
    }
    return bytes;
  };

  /**
   * Base64 encoding & decoding helpers compatible with standard ArrayBuffer / Uint8Array
   */
  function bytesToBase64(bytes) {
    if (!(bytes instanceof Uint8Array)) {
      bytes = new Uint8Array(bytes);
    }
    if (typeof Buffer !== 'undefined') {
      return Buffer.from(bytes.buffer, bytes.byteOffset, bytes.byteLength).toString('base64');
    }
    let binary = '';
    const len = bytes.byteLength;
    for (let i = 0; i < len; i++) {
      binary += String.fromCharCode(bytes[i]);
    }
    return window.btoa(binary);
  }

  function base64ToBytes(b64) {
    if (!b64 || typeof b64 !== 'string') {
      return new Uint8Array(0);
    }
    // Clean whitespace/newlines
    const cleanB64 = b64.replace(/\s+/g, '');
    if (typeof Buffer !== 'undefined') {
      const buf = Buffer.from(cleanB64, 'base64');
      return new Uint8Array(buf.buffer, buf.byteOffset, buf.byteLength);
    }
    const binary = window.atob(cleanB64);
    const len = binary.length;
    const bytes = new Uint8Array(len);
    for (let i = 0; i < len; i++) {
      bytes[i] = binary.charCodeAt(i);
    }
    return bytes;
  }

  /**
   * Secret Scrubbing Regex Rules matching cluster_chat/relay_protocol.py
   */
  const SCRUB_RULES = [
    // PEM Private Keys (RSA, EC, DSA, OPENSSH, or generic PRIVATE KEY)
    {
      regex: /-----\s*BEGIN\s+[A-Z0-9_\- ]*PRIVATE\s+KEY\s*-----[\s\S]*?-----\s*END\s+[A-Z0-9_\- ]*PRIVATE\s+KEY\s*-----/g,
      replace: '[REDACTED_SECRET]',
    },
    // GitHub Personal Access Tokens (classic, fine-grained, OAuth, etc.)
    {
      regex: /\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{20,}\b/g,
      replace: '[REDACTED_SECRET]',
    },
    {
      regex: /\bgithub_pat_[A-Za-z0-9_]{20,}\b/g,
      replace: '[REDACTED_SECRET]',
    },
    // Google / Gemini API Keys
    {
      regex: /\bAIza[0-9A-Za-z\-_]{30,45}\b/g,
      replace: '[REDACTED_SECRET]',
    },
    // Telegram Bot Tokens (e.g. 123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ123456789)
    {
      regex: /\b[0-9]{8,10}:[A-Za-z0-9_\-]{35}\b/g,
      replace: '[REDACTED_SECRET]',
    },
    // Bearer auth headers
    {
      regex: /\bBearer\s+[A-Za-z0-9\-_\.=]{20,}\b/gi,
      replace: 'Bearer [REDACTED_SECRET]',
    },
    // Slack tokens
    {
      regex: /\bxox[baprs]-[0-9A-Za-z_\-]{10,}\b/g,
      replace: '[REDACTED_SECRET]',
    },
    // OpenAI API keys
    {
      regex: /\bsk-[A-Za-z0-9]{20,}\b/g,
      replace: '[REDACTED_SECRET]',
    },
    // AWS Access Key ID
    {
      regex: /\bAKIA[0-9A-Z]{16}\b/g,
      replace: '[REDACTED_SECRET]',
    },
    // Generic key assignments
    {
      regex: /((?:api[_-]?key|secret[_-]?key|token|auth[_-]?token)\s*[:=]\s*["']?)([A-Za-z0-9\-_\.]{16,})(["']?)/gi,
      replace: '$1[REDACTED_SECRET]$3',
    },
  ];

  /**
   * RelayCrypto API implementation
   */
  const RelayCrypto = {
    // Protocol cryptographic parameters
    SALT_BYTES: 16,
    IV_BYTES: 12,
    TAG_BYTES: 16,
    KEY_BITS: 256,
    DEFAULT_ITERATIONS: 100000,
    CIPHER_NAME: 'aes-256-gcm',

    bytesToBase64,
    base64ToBytes,

    /**
     * Compute deterministic SHA-256 digest hex string from input string.
     * @param {string} str
     * @returns {Promise<string>} 64-char lowercase hex string
     */
    async sha256Hex(str) {
      if (typeof str !== 'string') {
        str = String(str || '');
      }
      const subtle = getSubtle();
      const enc = new TextEncoder();
      const buf = await subtle.digest('SHA-256', enc.encode(str));
      const bytes = new Uint8Array(buf);
      let hex = '';
      for (let i = 0; i < bytes.length; i++) {
        hex += bytes[i].toString(16).padStart(2, '0');
      }
      return hex;
    },

    /**
     * Compute SHA-256 hash of an ArrayBuffer or Uint8Array.
     * @param {ArrayBuffer|Uint8Array} buffer
     * @returns {Promise<string>} 64-char lowercase hex string
     */
    async sha256BytesHex(buffer) {
      const subtle = getSubtle();
      const buf = await subtle.digest('SHA-256', buffer);
      const bytes = new Uint8Array(buf);
      let hex = '';
      for (let i = 0; i < bytes.length; i++) {
        hex += bytes[i].toString(16).padStart(2, '0');
      }
      return hex;
    },

    /**
     * Derive an AES-GCM 256-bit CryptoKey using PBKDF2-HMAC-SHA256.
     * @param {string} passphrase - User shared key
     * @param {Uint8Array} saltBytes - 16-byte random salt
     * @param {number} [iterations=100000] - Iteration count
     * @returns {Promise<CryptoKey>}
     */
    async deriveKey(passphrase, saltBytes, iterations = 100000) {
      if (!passphrase || typeof passphrase !== 'string') {
        throw new Error('Passphrase must be a non-empty string');
      }
      if (!(saltBytes instanceof Uint8Array)) {
        saltBytes = new Uint8Array(saltBytes);
      }
      const subtle = getSubtle();
      const enc = new TextEncoder();
      const baseKey = await subtle.importKey(
        'raw',
        enc.encode(passphrase),
        { name: 'PBKDF2' },
        false,
        ['deriveKey']
      );

      return await subtle.deriveKey(
        {
          name: 'PBKDF2',
          salt: saltBytes,
          iterations: iterations || this.DEFAULT_ITERATIONS,
          hash: 'SHA-256',
        },
        baseKey,
        { name: 'AES-GCM', length: this.KEY_BITS },
        false,
        ['encrypt', 'decrypt']
      );
    },

    /**
     * Encrypt a plaintext string using AES-256-GCM with PBKDF2 key derivation.
     * Returns encrypted payload matching cluster_chat/relay_protocol.py.
     * @param {string} plaintext
     * @param {string} passphrase
     * @param {number} [iterations=100000]
     * @returns {Promise<{cipher: string, salt: string, iv: string, body: string, ciphertext: string, iterations: number}>}
     */
    async encryptText(plaintext, passphrase, iterations = 100000) {
      if (typeof plaintext !== 'string') {
        plaintext = String(plaintext || '');
      }
      if (!passphrase || typeof passphrase !== 'string') {
        throw new Error('Passphrase is required for encryption');
      }

      const subtle = getSubtle();
      const salt = getRandomBytes(this.SALT_BYTES);
      const iv = getRandomBytes(this.IV_BYTES);
      const derivedKey = await this.deriveKey(passphrase, salt, iterations);

      const enc = new TextEncoder();
      const ptBytes = enc.encode(plaintext);

      // WebCrypto AES-GCM append 16-byte tag to ciphertext directly
      const ctBuffer = await subtle.encrypt(
        { name: 'AES-GCM', iv: iv },
        derivedKey,
        ptBytes
      );

      const saltB64 = bytesToBase64(salt);
      const ivB64 = bytesToBase64(iv);
      const ctB64 = bytesToBase64(ctBuffer);

      return {
        cipher: this.CIPHER_NAME,
        salt: saltB64,
        iv: ivB64,
        body: ctB64,
        ciphertext: ctB64,
        iterations: iterations,
      };
    },

    /**
     * Decrypt an encrypted envelope or payload dictionary using AES-256-GCM.
     * @param {Object} envelope - Object containing iv, body (or ciphertext), and salt in envelope or envelope.meta
     * @param {string} passphrase
     * @returns {Promise<string>} Decrypted plaintext UTF-8 string
     */
    async decryptText(envelope, passphrase) {
      if (!envelope || typeof envelope !== 'object') {
        throw new Error('Envelope must be an object');
      }
      if (!passphrase || typeof passphrase !== 'string') {
        throw new Error('Passphrase is required for decryption');
      }

      // If envelope is marked as unencrypted or cipher is 'none', return body
      if (envelope.encrypted === false || (envelope.cipher && envelope.cipher.toLowerCase() === 'none')) {
        return envelope.body || '';
      }

      const ivB64 = envelope.iv;
      if (!ivB64) {
        throw new Error("Missing 'iv' in encrypted envelope");
      }

      const saltB64 = envelope.salt || (envelope.meta && envelope.meta.salt);
      if (!saltB64) {
        throw new Error("Missing 'salt' in encrypted envelope (checked top-level and meta.salt)");
      }

      const ctB64 = envelope.ciphertext || envelope.body;
      if (!ctB64) {
        throw new Error("Missing 'ciphertext' or 'body' in encrypted envelope");
      }

      const iterations = (envelope.meta && envelope.meta.iterations) || envelope.iterations || this.DEFAULT_ITERATIONS;

      const saltBytes = base64ToBytes(saltB64);
      const ivBytes = base64ToBytes(ivB64);
      const ctBytes = base64ToBytes(ctB64);

      if (ctBytes.length < this.TAG_BYTES) {
        throw new Error('Ciphertext payload too short to contain authentication tag');
      }

      const subtle = getSubtle();
      const derivedKey = await this.deriveKey(passphrase, saltBytes, iterations);

      try {
        const ptBuffer = await subtle.decrypt(
          { name: 'AES-GCM', iv: ivBytes },
          derivedKey,
          ctBytes
        );
        const dec = new TextDecoder();
        return dec.decode(ptBuffer);
      } catch (err) {
        throw new Error('Decryption failed: authentication tag mismatch or invalid passphrase (' + err.message + ')');
      }
    },

    /**
     * Deterministic SHA-256 msg_id generation matching cluster_chat/relay_protocol.py:
     *   sha256(room:sender:created_at:nonce:sha256(body))
     *
     * @param {string} room - "main", "dm-argon", or "workers"
     * @param {string} sender - "mohammad", "argon", etc.
     * @param {string} createdAt - ISO 8601 UTC string
     * @param {string} body - Plaintext or Base64 ciphertext
     * @param {string} [nonce=""] - Nonce string
     * @returns {Promise<string>} Deterministic 64-character SHA-256 hex digest
     */
    async computeMsgId(room, sender, createdAt, body, nonce = '') {
      if (room === 'workers-room') {
        room = 'workers';
      }
      if (!room || typeof room !== 'string') {
        throw new Error('Room must be a non-empty string');
      }
      if (!sender || typeof sender !== 'string') {
        throw new Error('Sender must be a non-empty string');
      }
      if (!createdAt || typeof createdAt !== 'string') {
        throw new Error('createdAt must be a non-empty string');
      }

      const bodyStr = typeof body === 'string' ? body : String(body || '');
      const bodyHashHex = await this.sha256Hex(bodyStr);
      const canonical = `${room}:${sender}:${createdAt}:${nonce}:${bodyHashHex}`;
      return await this.sha256Hex(canonical);
    },

    /**
     * Redact sensitive credentials, tokens, and private keys into [REDACTED_SECRET].
     * @param {string} text
     * @returns {string} Sanitized string
     */
    scrubSecrets(text) {
      if (text === null || text === undefined) {
        return '';
      }
      let sanitized = typeof text === 'string' ? text : String(text);
      for (const rule of SCRUB_RULES) {
        sanitized = sanitized.replace(rule.regex, rule.replace);
      }
      return sanitized;
    },

    /**
     * Parse /web or !web chat command string into structured object.
     * Matches cluster_chat/relay_protocol.parse_web_command.
     * @param {string} text
     * @returns {{url: string, mode: string, max_bytes: number, raw_command: string}|null}
     */
    parseWebCommand(text) {
      if (!text || typeof text !== 'string') {
        return null;
      }
      const stripped = text.trim();
      const match = stripped.match(/^(?:\/|!)web(?:\s+([\s\S]*)|$)/i);
      if (!match) {
        return null;
      }
      let rest = match[1];
      if (!rest || !rest.trim()) {
        return null;
      }
      rest = rest.trim();

      // Extract mode
      let mode = null;
      const modeMatch = rest.match(/--mode=([a-zA-Z0-9_\-]+)/i);
      if (modeMatch) {
        const m = modeMatch[1].toLowerCase();
        if (m === 'raw') {
          mode = 'file';
        } else if (['markdown', 'html', 'file'].includes(m)) {
          mode = m;
        }
        rest = (rest.slice(0, modeMatch.index) + ' ' + rest.slice(modeMatch.index + modeMatch[0].length)).trim();
      }

      // Extract max_bytes
      let maxBytes = null;
      const bytesMatch = rest.match(/--(?:max[\-_]bytes|bytes|max[\-_]size)=([0-9]+)/i);
      if (bytesMatch) {
        const parsedInt = parseInt(bytesMatch[1], 10);
        if (!isNaN(parsedInt)) {
          maxBytes = parsedInt;
        }
        rest = (rest.slice(0, bytesMatch.index) + ' ' + rest.slice(bytesMatch.index + bytesMatch[0].length)).trim();
      }

      // Extract URL:
      // 1. Markdown link: [text](https://...)
      let url = null;
      const mdMatch = rest.match(/\[([^\]]*)\]\((https?:\/\/[^\s\)]+)\)/);
      if (mdMatch) {
        url = mdMatch[2].trim();
      } else {
        // 2. Angle brackets: <https://...>
        const angleMatch = rest.match(/<(https?:\/\/[^>]+)>/);
        if (angleMatch) {
          url = angleMatch[1].trim();
        } else {
          // 3. Bare URL: https?://...
          const bareMatch = rest.match(/https?:\/\/[^\s]+/);
          if (bareMatch) {
            url = bareMatch[0].replace(/[.,;:)]>]+$/, '').trim();
          }
        }
      }

      if (!url) {
        return null;
      }

      // Default mode based on extension if not set
      if (!mode) {
        const fileExts = [
          '.pdf', '.zip', '.tar.gz', '.tgz', '.png', '.jpg',
          '.jpeg', '.csv', '.bin', '.mp4', '.mp3', '.docx',
          '.xlsx', '.tar', '.gz', '.7z'
        ];
        const cleanUrl = url.toLowerCase().split('?')[0].split('#')[0];
        if (fileExts.some(ext => cleanUrl.endsWith(ext))) {
          mode = 'file';
        } else {
          mode = 'markdown';
        }
      }

      if (!maxBytes) {
        maxBytes = mode === 'file' ? 5242880 : 2097152;
      }

      return {
        url: url,
        mode: mode,
        max_bytes: maxBytes,
        raw_command: stripped,
      };
    },
  };

  return RelayCrypto;
});
