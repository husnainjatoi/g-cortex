// Tiny DOM helpers + shared UI components (tables, badges, modals, toasts, charts).
export function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  if (attrs) for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'html') el.innerHTML = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'value') el.value = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const clear = (el) => { el.replaceChildren(); return el; };
// Element.append(array) would stringify the array ("[object HTMLDivElement],..."), so anything dynamic goes through put(), which flattens and skips empties.
export const put = (el, ...kids) => { el.replaceChildren(...kids.flat(Infinity).filter(k => k !== null && k !== undefined && k !== false).map(k => (k.nodeType ? k : document.createTextNode(String(k))))); return el; };

// Show/hide password control. The same <input> is kept (only its type flips) so what the user typed is never cleared.
export function passwordInput(attrs = {}) {
  const input = h('input', { type: 'password', autocomplete: 'current-password', ...attrs });
  const btn = h('button', { type: 'button', class: 'pw-toggle', 'aria-label': 'Show password', 'aria-pressed': 'false', title: 'Show password',
    onmousedown: (e) => e.preventDefault(),
    onclick: () => { const show = input.type === 'password'; input.type = show ? 'text' : 'password'; btn.textContent = show ? 'Hide' : 'Show';
      btn.setAttribute('aria-pressed', String(show)); const l = show ? 'Hide password' : 'Show password'; btn.setAttribute('aria-label', l); btn.title = l; } }, 'Show');
  return h('div', { class: 'pw-wrap' }, input, btn);
}

// Inline, plain-language field errors. `fields` maps input names to messages (from the API's `fields`).
export function clearFieldErrors(scope) {
  scope.querySelectorAll('.field-error').forEach(n => n.remove());
  scope.querySelectorAll('.has-error').forEach(n => n.classList.remove('has-error'));
}
export function showFieldErrors(scope, fields) {
  let unmatched = false, first = null;
  for (const [name, msg] of Object.entries(fields || {})) {
    const input = scope.querySelector(`[name="${CSS.escape(name)}"]`);
    const box = input && (input.closest('.field') || input.parentElement);
    if (!box) { unmatched = true; continue; }
    box.classList.add('has-error'); input.setAttribute('aria-invalid', 'true');
    box.append(h('div', { class: 'field-error', role: 'alert' }, msg)); first = first || input;
  }
  if (first) first.focus();
  return !unmatched;
}

export function toast(msg, kind = 'info', ms = 4500) {
  const t = h('div', { class: `toast ${kind}` }, msg);
  document.getElementById('toasts').append(t);
  setTimeout(() => t.remove(), ms);
}

const BADGE = {
  approved: 'ok', registered: 'ok', available: 'ok', checked_in: 'ok', active: 'ok', completed: 'info', success: 'ok', good: 'ok', done: 'ok', consumed: 'ok',
  pending: 'warn', pending_advisor_approval: 'warn', pending_admin_approval: 'warn', in_custody: 'warn', ongoing: 'info', upcoming: 'info', fair: 'warn', full: 'warn',
  rejected: 'bad', failed: 'bad', denied: 'bad', blocked_prerequisites: 'bad', damaged: 'bad', 'auto-released': 'bad', under_repair: 'bad', overdue: 'bad', cancelled: 'mute',
  dropped: 'mute', rebated: 'info', reversed: 'mute', retired: 'mute', past: 'mute',
};
export function badge(text, kind) {
  const label = String(text ?? '').replace(/_/g, ' ');
  return h('span', { class: `badge b-${kind || BADGE[String(text)] || 'mute'}` }, label);
}

export function fmtDate(v) { if (!v) return '—'; const d = new Date(v); return isNaN(d) ? String(v) : d.toLocaleDateString(undefined, { day: '2-digit', month: 'short', year: 'numeric' }); }
export function fmtDateTime(v) { if (!v) return '—'; const d = new Date(v); return isNaN(d) ? String(v) : d.toLocaleString(undefined, { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }); }
export const money = (n) => `PKR ${Number(n || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
export const isoDate = (d) => { const x = new Date(d); x.setMinutes(x.getMinutes() - x.getTimezoneOffset()); return x.toISOString().slice(0, 10); };
export const addDays = (n) => { const d = new Date(); d.setDate(d.getDate() + n); return isoDate(d); };

export function table(cols, rows, opts = {}) {
  if (!rows || !rows.length) return h('div', { class: 'tbl-wrap' }, h('div', { class: 'empty' }, opts.empty || 'Nothing to show yet.'));
  return h('div', { class: 'tbl-wrap' }, h('table', null,
    h('thead', null, h('tr', null, cols.map(c => h('th', null, c.label)))),
    h('tbody', null, rows.map(r => h('tr', null, cols.map(c => h('td', { class: c.class }, c.render ? c.render(r) : (r[c.key] ?? '—'))))))));
}

export function tabs(container, items, initial) {
  const bar = h('div', { class: 'tabs' });
  const body = h('div');
  const show = async (key) => {
    bar.querySelectorAll('button').forEach(b => b.classList.toggle('active', b.dataset.k === key));
    clear(body).append(h('div', { class: 'muted' }, 'Loading…'));
    try { const node = await items.find(i => i.key === key).render(); clear(body).append(node); }
    catch (e) { clear(body).append(errorBox(e)); }
  };
  items.forEach(i => bar.append(h('button', { 'data-k': i.key, onclick: () => show(i.key) }, i.label)));
  container.append(bar, body);
  show(initial || items[0].key);
  return { show };
}

export function modal({ title, body, actions = [], wide = false }) {
  const root = document.getElementById('modal-root');
  const close = () => back.remove();
  const back = h('div', { class: 'modal-back', onclick: (e) => { if (e.target === back) close(); } },
    h('div', { class: `modal ${wide ? 'wide' : ''}`, role: 'dialog', 'aria-modal': 'true' },
      h('header', null, h('h2', null, title), h('button', { class: 'x', 'aria-label': 'Close', onclick: close }, '×')),
      h('div', { class: 'body' }, body),
      actions.length ? h('footer', null, actions.map(a => h('button', {
        class: `btn ${a.kind || ''}`, onclick: async (e) => {
          e.target.disabled = true;
          try { const keep = await a.onClick(close); if (keep === false) e.target.disabled = false; }
          catch (err) { e.target.disabled = false; toast(err.message, 'bad', 6500); }
        } }, a.label))) : null));
  root.append(back);
  return { close };
}

export function confirmDialog(title, message, confirmLabel = 'Confirm', kind = 'primary') {
  return new Promise((resolve) => {
    let done = false;
    const m = modal({ title, body: typeof message === 'string' ? h('p', null, message) : message, actions: [
      { label: 'Cancel', onClick: (close) => { done = true; close(); resolve(false); } },
      { label: confirmLabel, kind, onClick: (close) => { done = true; close(); resolve(true); } }] });
    new MutationObserver((_, o) => { if (!document.body.contains(document.querySelector('.modal-back')) && !done) { done = true; resolve(false); o.disconnect(); } })
      .observe(document.getElementById('modal-root'), { childList: true });
  });
}

export function field(label, input, hint) { return h('div', { class: 'field' }, h('label', null, label), input, hint ? h('div', { class: 'hint' }, hint) : null); }
export function select(options, value, attrs) { return h('select', attrs, options.map(o => { const [v, t] = Array.isArray(o) ? o : [o, o]; return h('option', { value: v, selected: String(v) === String(value) }, t); })); }

export function barChart(rows, { label, value, max, suffix = '' }) {
  const m = max ?? Math.max(1, ...rows.map(r => Number(r[value]) || 0));
  if (!rows.length) return h('div', { class: 'muted' }, 'No data yet.');
  return h('div', { class: 'bars' }, rows.map(r => h('div', { class: 'br' },
    h('div', { class: 'lab', title: String(r[label]) }, String(r[label])),
    h('div', { class: 'bar' }, h('i', { style: `width:${Math.min(100, 100 * (Number(r[value]) || 0) / m)}%` })),
    h('div', { class: 'small muted' }, `${r[value]}${suffix}`))));
}
export function stackBar(parts) {
  const total = parts.reduce((a, p) => a + p.n, 0) || 1;
  const colors = ['#5b4bff', '#1fb6ff', '#0f9d6a', '#b7791f', '#c53030', '#8a94b8'];
  return h('div', null, h('div', { class: 'stack' }, parts.map((p, i) => h('i', { style: `width:${100 * p.n / total}%;background:${colors[i % colors.length]}`, title: `${p.label}: ${p.n}` }))),
    h('div', { class: 'row small muted', style: 'margin-top:6px' }, parts.map((p, i) => h('span', null, h('span', { style: `display:inline-block;width:9px;height:9px;border-radius:2px;background:${colors[i % colors.length]};margin-right:5px` }), `${p.label} ${p.n}`))));
}
export const statCard = (label, value, sub) => h('div', { class: 'card stat' }, h('div', { class: 'l' }, label), h('div', { class: 'v' }, value), sub ? h('div', { class: 's' }, sub) : null);
export const pageHead = (title, sub, ...actions) => h('div', { class: 'page-head' }, h('div', null, h('h1', null, title), sub ? h('p', null, sub) : null), h('div', { class: 'right row' }, actions));
export function errorBox(e) { return h('div', { class: 'alert bad', role: 'alert' }, e && e.friendly ? e.message : (e && e.message) || 'Something went wrong. Please try again.'); }
export function form(fields, onSubmit, submitLabel = 'Submit', extra) {
  const banner = h('div');
  const f = h('form', { novalidate: true, onsubmit: async (ev) => {
    ev.preventDefault(); const btn = f.querySelector('button[type=submit]'); btn.disabled = true; clearFieldErrors(f); clear(banner);
    try { await onSubmit(Object.fromEntries(new FormData(f).entries())); }
    catch (e) {
      const named = e.fields && Object.keys(e.fields).length;
      const allShown = named && showFieldErrors(f, e.fields);
      if (!named) banner.append(h('div', { class: 'alert bad', role: 'alert' }, e.message));
      else if (!allShown || e.fields && Object.keys(e.fields).length > 1) banner.append(h('div', { class: 'alert bad', role: 'alert' }, 'Please correct the highlighted fields and try again.'));
    } finally { btn.disabled = false; }
  } }, banner, h('div', { class: 'form-grid' }, fields), extra, h('button', { class: 'btn primary', type: 'submit' }, submitLabel));
  return f;
}
