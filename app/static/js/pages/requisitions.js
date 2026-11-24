import { api, qs } from '../api.js';
import { h, clear, table, badge, tabs, modal, confirmDialog, toast, field, select, form, pageHead, fmtDate, addDays, errorBox } from '../ui.js';

export function stages(r) { return h('div', { class: 'stages' }, r.stages.map((s, i) => [i ? h('span', { class: 'muted' }, '→') : null, h('span', { class: `stage ${s.state}`, title: s.comments || '' }, `${s.tier}. ${s.label}${s.state === 'approved' ? ' ✓' : s.state === 'rejected' ? ' ✗' : s.state === 'current' ? ' …' : ''}`)])); }

export async function render(root, ctx) {
  root.append(pageHead('Requisitions', 'Society event requisitions with multi-tier digital approval, and venue search.'));
  const holder = h('div'); root.append(holder);
  tabs(holder, [{ key: 'mine', label: 'My requisitions', render: () => mine(ctx) }, { key: 'new', label: 'New requisition', render: () => create(ctx) }, { key: 'venues', label: 'Find a venue', render: venues }]);
}

async function mine(ctx) {
  const rows = await api.get('/api/req/requisitions?scope=mine');
  return table([{ label: 'ID', render: r => h('b', null, r.requisition_id) }, { label: 'Event', render: r => h('div', null, r.event_title, h('div', { class: 'small muted' }, r.society_name)) },
    { label: 'Venue & time', render: r => h('div', null, r.venue_name, h('div', { class: 'small muted' }, `${fmtDate(r.booking_date)} ${r.start_time} · ${r.duration_hours}h · ${r.expected_attendees} people`)) },
    { label: 'Approval progress', render: r => h('div', null, stages(r), r.pending_authority ? h('div', { class: 'small muted' }, `Waiting on ${r.pending_authority}`) : null,
        (r.stages.find(s => s.state === 'rejected') || {}).comments ? h('div', { class: 'small', style: 'color:var(--bad)' }, `Reason: ${r.stages.find(s => s.state === 'rejected').comments}`) : null) },
    { label: 'Status', render: r => badge(r.status) },
    { label: '', render: r => ['rejected', 'cancelled'].includes(r.status) ? null : h('button', { class: 'btn sm danger', onclick: async () => {
      if (!(await confirmDialog(`Cancel ${r.requisition_id}?`, 'Any reserved venue slot will be released.', 'Cancel requisition', 'danger'))) return;
      try { const x = await api.post(`/api/req/requisitions/${r.requisition_id}/cancel`); toast(x.message, 'ok'); ctx.refresh(); } catch (e) { toast(e.message, 'bad'); } } }, 'Cancel') }], rows, { empty: 'You have not filed any requisitions.' });
}

async function create(ctx) {
  const [soc, ven] = await Promise.all([api.get('/api/req/societies'), api.get('/api/req/venues')]);
  const venues = ven.filter(v => v.category !== 'sports');
  if (!soc.mine.length) return h('div', { class: 'alert warn' }, 'You are not a registered member of any society, so you cannot file a requisition. Ask your society president or the Directorate of Student Affairs to add you.');
  const f = form([
    field('Society', select(soc.mine.map(s => [s.society_name, `${s.society_name} (${s.member_role.replace('_', ' ')})`]), '', { name: 'society_name' })),
    field('Venue', select(venues.map(v => [v.venue_id, `${v.name} — up to ${v.capacity}`]), '', { name: 'venue_id' })),
    field('Event title', h('input', { name: 'event_title', required: true, maxlength: 150 })),
    field('Expected attendees', h('input', { name: 'expected_attendees', type: 'number', min: 1, required: true })),
    field('Date', h('input', { name: 'booking_date', type: 'date', required: true, min: addDays(2), value: addDays(7) }), 'Minimum 2 days notice'),
    field('Start time', h('input', { name: 'start_time', type: 'time', required: true, value: '14:00' })),
    field('Duration (hours)', h('input', { name: 'duration_hours', type: 'number', step: '0.5', min: 0.5, max: 8, value: 2, required: true })),
    h('div', { class: 'field', style: 'grid-column:1/-1' }, h('label', null, 'Justification'), h('textarea', { name: 'justification', maxlength: 1000, placeholder: 'Purpose and relevance of the event' }))],
    async (d) => {
      if (!(await confirmDialog('Submit requisition?', `It will be routed to your society's Faculty Advisor (Tier 1), then the Directorate of Student Affairs (Tier 2).`, 'Submit'))) return;
      d.expected_attendees = Number(d.expected_attendees); d.duration_hours = Number(d.duration_hours);
      const r = await api.post('/api/req/requisitions', d); toast(r.message, 'ok', 7000); ctx.refresh();
    }, 'Submit requisition');
  return h('div', { class: 'card' }, h('p', { class: 'muted' }, 'Not sure which venue fits? Use “Find a venue” first.'), f);
}

async function venues() {
  const wrap = h('div'); const out = h('div');
  const desc = h('input', { placeholder: 'Describe your event, e.g. “hackathon with live demos and a projector”' });
  const n = h('input', { type: 'number', min: 1, value: 50, style: 'width:110px' }); const fac = h('input', { placeholder: 'Needs: projector, stage…' });
  const q = h('input', { type: 'search', placeholder: 'Search venues by name or facility' }); const list = h('div');
  async function loadList() { const v = await api.get('/api/req/venues' + qs({ q: q.value })); clear(list).append(table([{ label: 'Venue', render: x => h('b', null, x.name) }, { label: 'Type', key: 'category' }, { label: 'Capacity', key: 'capacity' }, { label: 'Facilities', render: x => x.facilities || '—' }, { label: 'Approval', render: x => x.requires_approval ? 'Required' : 'Not required' }], v)); }
  wrap.append(h('div', { class: 'card' }, h('h2', null, 'Natural-language venue recommender'), h('div', { class: 'row' }, h('div', { style: 'flex:1;min-width:260px' }, desc), n, h('div', { style: 'min-width:180px' }, fac),
    h('button', { class: 'btn primary', onclick: async () => {
      try { const r = await api.post('/api/req/venues/recommend', { description: desc.value, attendees: Number(n.value), facilities: fac.value });
        clear(out).append(r.recommendations.length ? r.recommendations.map(v => h('div', { class: 'card flat' }, h('div', { class: 'row spread' }, h('b', null, `${v.name} (${v.venue_id})`), badge(`${v.match_score}% match`, 'info')), h('div', { class: 'small muted' }, `Capacity ${v.capacity} · ${v.facilities.join(', ')}`), h('ul', null, v.reasons.map(x => h('li', null, x))))) : h('div', { class: 'alert warn' }, r.guidance)); } catch (e) { clear(out).append(errorBox(e)); } } }, 'Recommend')), h('div', { style: 'margin-top:12px' }, out)));
  q.addEventListener('input', () => { clearTimeout(q._t); q._t = setTimeout(loadList, 250); });
  wrap.append(h('div', { class: 'card' }, h('h2', null, 'Campus venues'), q, h('div', { style: 'margin-top:10px' }, list))); loadList(); return wrap;
}
