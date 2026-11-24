import { api, qs } from '../api.js';
import { h, clear, table, badge, tabs, toast, pageHead, fmtDateTime, select } from '../ui.js';

export async function render(root, ctx) {
  root.append(pageHead('Audit & Events', 'The audit log is append-only and cannot be edited from the application.'));
  const holder = h('div'); root.append(holder);
  tabs(holder, [{ key: 'a', label: 'Audit trail', render: audit }, { key: 'e', label: 'Event pipeline', render: () => events(ctx) }]);
}
async function audit() {
  const out = h('div'); const outcome = select([['', 'All outcomes'], 'success', 'denied', 'failed'], ''); const action = h('input', { placeholder: 'Action prefix, e.g. course.' }); const actor = h('input', { placeholder: 'Actor ID' });
  async function load() { const rows = await api.get('/api/admin/audit' + qs({ outcome: outcome.value, action: action.value, actor_id: actor.value, limit: 200 }));
    clear(out).append(table([{ label: 'When', render: r => fmtDateTime(r.at) }, { label: 'Actor', render: r => `${r.actor_id || '—'} (${r.actor_role || '—'})` }, { label: 'Via', key: 'channel' }, { label: 'Action', render: r => h('code', null, r.action) }, { label: 'Record', render: r => `${r.entity_type || ''} ${r.entity_id || ''}` }, { label: 'Outcome', render: r => badge(r.outcome) },
      { label: 'Detail', render: r => h('span', { class: 'small' }, JSON.stringify(r.detail).slice(0, 140)) }], rows)); }
  [outcome, action, actor].forEach(e => e.addEventListener('change', load));
  const w = h('div', null, h('div', { class: 'row', style: 'margin-bottom:12px' }, h('div', { style: 'width:170px' }, outcome), h('div', { style: 'width:230px' }, action), h('div', { style: 'width:160px' }, actor)), out); await load(); return w;
}
async function events(ctx) {
  const rows = await api.get('/api/admin/events');
  return h('div', null, h('div', { class: 'row', style: 'margin-bottom:12px' }, h('button', { class: 'btn', onclick: async () => { const r = await api.post('/api/admin/events/retry'); toast(`Retried ${r.retried}, recovered ${r.recovered}.`, 'ok'); ctx.refresh(); } }, 'Retry failed handlers'), h('span', { class: 'small muted' }, 'Each event is delivered to each handler exactly once; failures are retried safely.')),
    table([{ label: 'When', render: r => fmtDateTime(r.created_at) }, { label: 'Event', render: r => h('code', null, r.event_type) }, { label: 'Handlers', render: r => r.handlers.map(x => badge(`${x.handler}: ${x.status}`, x.status === 'done' ? 'ok' : 'bad')) }, { label: 'Payload', render: r => h('span', { class: 'small' }, JSON.stringify(r.payload).slice(0, 120)) }], rows));
}
