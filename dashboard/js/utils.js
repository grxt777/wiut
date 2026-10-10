/** Shared dashboard utilities — XSS-safe rendering helpers. */
(function (global) {
  'use strict';

  function escapeHtml(str) {
    if (str == null) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function cleanBranchName(raw) {
    if (!raw) return '—';
    let s = String(raw).trim();
    s = s.replace(/^\d{5}\s*-\s*[0-9A-Za-z]{4,5}\s*/, '');
    s = s.replace(/^[0-9A-Za-z]{4,5}\s*-\s*/, '');
    s = s.replace(/^\d{5}\s*/, '');
    s = s.replace(/["']O[^\s]*zsanoatqurilishbank["']?\s*ATB\s*/gi, '');
    s = s.replace(/ATB\s*/gi, '');
    s = s.replace(/^[-"'\s\xa0]+|[-"'\s\xa0]+$/g, '');
    s = s.replace(/bank\s+xizmatlar[i]?\s+markaz[i]?/gi, 'BXM');
    s = s.replace(/bank\s+xizmatlar[i]?\s+ofis[i]?/gi, 'BXO');
    s = s.replace(/центр\s+банковских\s+услуг/gi, 'BXM');
    s = s.replace(/офис\s+банковских\s+услуг/gi, 'BXO');
    return s.trim() || '—';
  }

  global.escapeHtml = escapeHtml;
  global.cleanBranchName = cleanBranchName;
})(typeof window !== 'undefined' ? window : globalThis);
