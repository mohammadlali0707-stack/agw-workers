/**
 * cluster_chat/timezone_utils.js
 * 
 * Shared JavaScript timezone utilities for Asia/Tehran (UTC+03:30) formatting (P9).
 * - parseUtcDate(ts): Safely parses ISO/timestamp into a Date object, appending 'Z' to naive UTC strings.
 * - formatTehranTime(ts): Returns 24-hour time 'HH:mm' in Asia/Tehran.
 * - formatTehranDateTime(ts): Returns 'YYYY-MM-DD HH:mm:ss' in Asia/Tehran.
 */

(function (global) {
  'use strict';

  function parseUtcDate(ts) {
    if (ts === null || ts === undefined || typeof ts === 'boolean') return null;
    if (ts instanceof Date) return isNaN(ts.getTime()) ? null : ts;
    if (typeof ts === 'number') {
      if (!Number.isFinite(ts)) return null;
      return new Date(ts > 1e11 ? ts : ts * 1000);
    }

    if (typeof ts !== 'string') return null;
    let s = ts.trim();
    if (!s) return null;

    // Disallow non-finite strings
    const lower = s.toLowerCase();
    if (lower === 'nan' || lower === 'infinity' || lower === '-infinity' || lower === 'inf' || lower === '-inf') {
      return null;
    }

    // Prioritize standard ISO date strings (starts with YYYY-MM-DD)
    if (/^\d{4}-\d{2}-\d{2}/.test(s)) {
      if (/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(s) && !/(Z|[+-]\d{2}:?\d{2})$/i.test(s)) {
        s = s.replace(' ', 'T') + 'Z';
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
    if (ts === null || ts === undefined || ts === '') return '';
    const d = parseUtcDate(ts);
    if (!d) return typeof ts === 'string' ? ts : '';
    try {
      return new Intl.DateTimeFormat('en-GB', {
        timeZone: 'Asia/Tehran',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false
      }).format(d);
    } catch (_) {
      return typeof ts === 'string' ? ts : '';
    }
  }

  function formatTehranDateTime(ts) {
    if (ts === null || ts === undefined || ts === '') return '';
    const d = parseUtcDate(ts);
    if (!d) return typeof ts === 'string' ? ts : '';
    try {
      return new Intl.DateTimeFormat('en-CA', {
        timeZone: 'Asia/Tehran',
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false
      }).format(d).replace(', ', ' ');
    } catch (_) {
      return typeof ts === 'string' ? ts : '';
    }
  }

  const TimezoneUtils = {
    parseUtcDate,
    formatTehranTime,
    formatTehranDateTime
  };

  // Node.js CommonJS export
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = TimezoneUtils;
  }

  // Browser global export
  if (typeof window !== 'undefined') {
    window.ClusterChatTimezone = TimezoneUtils;
    window.parseUtcDate = parseUtcDate;
    window.formatTehranTime = formatTehranTime;
    window.formatTehranDateTime = formatTehranDateTime;
  } else if (typeof global !== 'undefined') {
    global.ClusterChatTimezone = TimezoneUtils;
    global.parseUtcDate = parseUtcDate;
    global.formatTehranTime = formatTehranTime;
    global.formatTehranDateTime = formatTehranDateTime;
  }
})(typeof globalThis !== 'undefined' ? globalThis : this);
