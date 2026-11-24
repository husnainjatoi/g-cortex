// Unified G-Cortex assistant widget (floating drawer + full page share one conversation).
import { api } from './api.js';
import { h, esc, toast, clear } from './ui.js';

const state = { sessionId: sessionStorage.getItem('gcortex_chat') || null, started: false, nodes: new Set() };

export function md(text) {
  let s = esc(text);
  s = s.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/`([^`]+)`/g, '<code>$1</code>');
  const lines = s.split('\n'); const out = []; let list = false;
  for (const ln of lines) {
    const m = ln.match(/^\s*[*\-•]\s+(.*)/);
    if (m) { if (!list) { out.push('<ul>'); list = true; } out.push(`<li>${m[1]}</li>`); }
    else { if (list) { out.push('</ul>'); list = false; } out.push(ln.trim() ? `<p>${ln}</p>` : ''); }
  }
  if (list) out.push('</ul>');
  return out.join('');
}

const SUGGESTIONS = ['Which courses can I register for?', 'Show my mess-out history', 'Book badminton court 1 tomorrow at 17:00', 'I need a venue for a society event with 100 participants', 'What is the status of my requisition?'];

export function createChat({ full = false, role = 'student', onChange } = {}) {
  const log = h('div', { class: 'chat-log' });
  const input = h('input', { type: 'text', placeholder: 'Ask G-Cortex or tell it what to do…', maxlength: 2000, autocomplete: 'off' });
  const send = h('button', { class: 'btn primary', type: 'submit' }, 'Send');
  const panel = h('div', { class: `chat-panel ${full ? 'chat-full open' : ''}` },
    h('div', { class: 'chat-head' }, h('img', { src: '/static/img/logo.png', alt: '' }),
      h('div', null, h('b', null, 'G-Cortex Assistant'), h('div', { class: 'small', style: 'opacity:.75' }, 'One assistant for courses, mess and campus resources')),
      h('button', { onclick: newChat }, 'New chat')),
    log,
    h('div', { class: 'chips' }, (role === 'student' ? SUGGESTIONS : ['What can you do?']).slice(0, 4).map(s => h('span', { class: 'chip', onclick: () => ask(s) }, s))),
    h('form', { class: 'chat-form', onsubmit: (e) => { e.preventDefault(); const v = input.value.trim(); if (v) { input.value = ''; ask(v); } } }, input, send));

  function add(role, html, node) {
    const m = h('div', { class: `msg ${role}` });
    if (role === 'bot') m.innerHTML = html; else m.textContent = html;
    if (node) m.append(node);
    log.append(m); log.scrollTop = log.scrollHeight; return m;
  }

  function pendingBox(actions) {
    const box = h('div');
    actions.forEach(a => {
      const row = h('div', { class: 'confirm-row' },
        h('button', { class: 'btn primary sm', onclick: async () => {
          row.querySelectorAll('button').forEach(b => b.disabled = true);
          try {
            const r = await api.post(`/api/orchestrator/actions/${a.action_id}/confirm`);
            add('bot', md((r.ok ? '✅ ' : '⚠️ ') + r.message)); row.remove(); if (r.ok && onChange) onChange();
          } catch (e) { add('bot', md('⚠️ ' + e.message)); row.remove(); }
        } }, 'Confirm'),
        h('button', { class: 'btn sm', onclick: async () => {
          try { await api.post(`/api/orchestrator/actions/${a.action_id}/cancel`); add('bot', md('Cancelled. Nothing was changed.')); } catch (e) { /* already resolved */ }
          row.remove();
        } }, 'Cancel'));
      box.append(row);
    });
    return box;
  }

  async function ask(text) {
    add('user', text);
    const typing = add('bot', '<span class="typing">Working on it…</span>');
    send.disabled = input.disabled = true;
    try {
      const r = await api.post('/api/orchestrator/chat', { message: text, session_id: state.sessionId });
      state.sessionId = r.session_id; sessionStorage.setItem('gcortex_chat', r.session_id);
      typing.remove();
      add('bot', md(r.reply), r.pending_actions && r.pending_actions.length ? pendingBox(r.pending_actions) : null);
      if (r.executed && r.executed.ok && onChange) onChange();
    } catch (e) { typing.remove(); add('bot', md('⚠️ ' + e.message)); }
    finally { send.disabled = input.disabled = false; input.focus(); }
  }

  async function newChat() {
    state.sessionId = null; sessionStorage.removeItem('gcortex_chat'); clear(log);
    add('bot', md('Hi! I can look things up **and** do them for you — register for courses, submit mess-outs, book sports slots, file requisitions or borrow equipment. I always ask you to confirm before changing anything.'));
  }

  async function restore() {
    if (state.sessionId) {
      try {
        const hst = await api.get(`/api/orchestrator/session/${state.sessionId}/history`);
        if (hst.messages.length) {
          hst.messages.forEach(m => add(m.role === 'user' ? 'user' : 'bot', m.role === 'user' ? m.content : md(m.content)));
          if (hst.pending_actions.length) log.lastChild.append(pendingBox(hst.pending_actions));
          return;
        }
      } catch (_) { state.sessionId = null; sessionStorage.removeItem('gcortex_chat'); }
    }
    newChat();
  }
  restore();
  return { el: panel, focus: () => input.focus() };
}
