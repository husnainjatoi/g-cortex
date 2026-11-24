import { api, qs } from '../api.js';
import { h, clear, table, badge, tabs, toast, pageHead, fmtDateTime, select } from '../ui.js';

export async function render(root, ctx) {
  root.append(pageHead('Notifications & Activity', 'Updates from every module, and a record of what you (or your assistant) did.'));
  const holder = h('div'); root.append(holder);
  tabs(holder, [{ key: 'n', label: 'Notifications', render: () => notes(ctx) }, { key: 'a', label: 'Activity history', render: activity }]);
}

async function notes(ctx) {
  const r = await api.get('/api/portal/notifications');
  const wrap = h('div');
  wrap.append(h('div', { class: 'row', style: 'margin-bottom:12px' }, h('span', { class: 'muted' }, `${r.unread} unread`), h('button', { class: 'btn sm right', disabled: !r.unread, onclick: async () => { await api.post('/api/portal/notifications/read'); ctx.refresh(); } }, 'Mark all read')));
  if (!r.items.length) return h('div', { class: 'card empty' }, 'No notifications yet.');
  const kind = { success: 'ok', warning: 'warn', error: 'bad', info: 'info' };
  r.items.forEach(n => wrap.append(h('div', { class: 'card', style: `padding:12px 16px;${n.read_at ? 'opacity:.7' : 'border-left:3px solid var(--indigo)'}` },
    h('div', { class: 'row spread' }, h('div', { class: 'row' }, badge(n.kind, kind[n.level] || 'info'), h('b', null, n.title)), h('span', { class: 'small muted' }, fmtDateTime(n.created_at))),
    n.body ? h('div', { class: 'muted', style: 'margin-top:4px' }, n.body) : null,
    n.link ? h('a', { href: '#/' + n.link, class: 'small' }, 'Open →') : null)));
  return wrap;
}

async function activity() {
  const wrap = h('div'); const out = h('div'); const f = select([['', 'All outcomes'], 'success', 'failed', 'denied'], '');
  async function load() {
    const rows = await api.get('/api/portal/activity' + qs({ outcome: f.value }));
    clear(out).append(table([{ label: 'When', render: r => fmtDateTime(r.at) }, { label: 'Action', render: r => h('code', null, r.action) }, { label: 'Record', render: r => `${r.entity_type || ''} ${r.entity_id || ''}` }, { label: 'Via', key: 'channel' },
      { label: 'Outcome', render: r => badge(r.outcome) }, { label: 'Detail', render: r => r.detail && r.detail.error ? h('span', { class: 'small' }, r.detail.error) : '' }], rows, { empty: 'No activity yet.' }));
  }
  f.addEventListener('change', load); wrap.append(h('div', { style: 'width:200px;margin-bottom:12px' }, f), out); await load(); return wrap;
}
