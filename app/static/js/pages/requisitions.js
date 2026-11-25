import { api, qs } from '../api.js';
import { h, clear, put, clearFieldErrors, showFieldErrors, table, badge, tabs, modal, confirmDialog, toast, field, select, form, pageHead, fmtDate, addDays, errorBox } from '../ui.js';

export function stages(r) { return h('div', { class: 'stages' }, r.stages.map((s, i) => [i ? h('span', { class: 'muted' }, '→') : null, h('span', { class: `stage ${s.state}`, title: s.comments || '' }, `${s.tier}. ${s.label}${s.state === 'approved' ? ' ✓' : s.state === 'rejected' ? ' ✗' : s.state === 'current' ? ' …' : ''}`)])); }

const DRAFT_KEY = 'gcortex_reservation_draft';
const loadDraft = () => { try { return JSON.parse(sessionStorage.getItem(DRAFT_KEY) || '{}'); } catch (_) { return {}; } };
const saveDraft = (patch) => sessionStorage.setItem(DRAFT_KEY, JSON.stringify({ ...loadDraft(), ...patch }));
const hashParams = () => new URLSearchParams((location.hash.split('?')[1]) || '');

export async function render(root, ctx) {
  root.append(pageHead('Requisitions', 'Venue reservations for individuals and societies, with digital approval, and venue search.'));
  const holder = h('div'); root.append(holder);
  const want = hashParams().get('tab');
  tabs(holder, [{ key: 'mine', label: 'My requisitions', render: () => mine(ctx) }, { key: 'new', label: 'New requisition', render: () => create(ctx) }, { key: 'venues', label: 'Find a venue', render: () => venues(ctx) }],
    ['mine', 'new', 'venues'].includes(want) ? want : undefined);
}

// "Reserve This Venue": remember the venue (and what the user already told the recommender) and open the New Requisition form.
function reserve(venue_id, extra = {}) {
  saveDraft({ venue_id, ...extra });
  const target = `#/requisitions?tab=new&venue=${encodeURIComponent(venue_id)}`;
  if (location.hash === target) window.dispatchEvent(new HashChangeEvent('hashchange')); else location.hash = target;
}

async function mine(ctx) {
  const rows = await api.get('/api/req/requisitions?scope=mine');
  return table([{ label: 'ID', render: r => h('b', null, r.requisition_id) }, { label: 'Event', render: r => h('div', null, r.event_title, h('div', { class: 'small muted' }, r.reservation_type === 'individual' ? 'Individual reservation' : `Society: ${r.society_display || r.society_name}`, r.reservation_type === 'society' && r.authority_verified === false ? h('span', null, ' ', badge('authority to be confirmed', 'warn')) : null)) },
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
  const draft = loadDraft(); const wanted = hashParams().get('venue');
  let notice = null;
  if (wanted) {                                                  // trusted lookup by database ID; never trust the URL alone
    try { const v = await api.get(`/api/req/venues/${encodeURIComponent(wanted)}`); if (venues.some(x => x.venue_id === v.venue_id)) draft.venue_id = v.venue_id; else notice = 'That venue cannot be reserved here.'; saveDraft({ venue_id: draft.venue_id }); }
    catch (e) { notice = e.message; }
  }
  const mineIds = new Map(soc.mine.map(m => [m.society_id, m.member_role]));
  let type = draft.reservation_type === 'society' ? 'society' : 'individual';
  const socSel = select([['', 'Select a society…'], ...soc.all.map(s => [s.society_id, mineIds.has(s.society_id) ? `${s.name} (you: ${mineIds.get(s.society_id).replace('_', ' ')})` : s.name])], draft.society_id || '', { name: 'society_id' });
  const socField = field('Society', socSel, 'Official GIKI societies. If members are recorded for a society, only they may reserve on its behalf.');
  const route = h('div', { class: 'alert info', style: 'grid-column:1/-1' });
  const seg = h('div', { class: 'seg', role: 'tablist' });
  const info = () => {
    const s = soc.all.find(x => String(x.society_id) === String(socSel.value));
    socField.style.display = type === 'society' ? '' : 'none';
    put(route, type === 'society' ? [h('b', null, 'Society reservation: '), s && s.advisor_id ? 'goes to the society’s Faculty Advisor first, then the Directorate of Student Affairs.' : 'goes to the Directorate of Student Affairs for approval (an administrator also confirms that you may act for the society if it is not in the membership records).']
      : [h('b', null, 'Individual reservation: '), 'goes to the Directorate of Student Affairs for approval.']);
    seg.querySelectorAll('button').forEach(b => { b.classList.toggle('active', b.dataset.t === type); b.setAttribute('aria-selected', String(b.dataset.t === type)); });
  };
  [['individual', 'Individual Reservation'], ['society', 'Society Reservation']].forEach(([k, l]) => seg.append(h('button', { type: 'button', role: 'tab', 'data-t': k, onclick: () => { type = k; saveDraft({ reservation_type: k }); info(); } }, l)));
  socSel.addEventListener('change', () => { saveDraft({ society_id: socSel.value }); info(); });
  const dateIn = h('input', { name: 'booking_date', type: 'date', min: addDays(2), value: draft.booking_date || addDays(7) });
  const fld = (label, el, hint) => field(label, el, hint);
  const venueSel = select([['', 'Select a venue…'], ...venues.map(v => [v.venue_id, `${v.name} — up to ${v.capacity}`])], draft.venue_id || '', { name: 'venue_id' });
  const inputs = {
    venue_id: venueSel, event_title: h('input', { name: 'event_title', maxlength: 150, value: draft.event_title || '' }),
    expected_attendees: h('input', { name: 'expected_attendees', type: 'number', min: 1, value: draft.expected_attendees || '' }),
    start_time: h('input', { name: 'start_time', type: 'time', value: draft.start_time || '14:00' }),
    duration_hours: h('input', { name: 'duration_hours', type: 'number', step: '0.5', min: 0.5, max: 8, value: draft.duration_hours || 2 }),
    justification: h('textarea', { name: 'justification', maxlength: 1000, placeholder: 'Purpose and relevance of the event' }, ''),
  };
  inputs.justification.value = draft.justification || '';
  const venueInfo = h('div', { class: 'hint' });
  const showVenue = () => { const v = venues.find(x => x.venue_id === venueSel.value); put(venueInfo, v ? `${v.name}: ${v.description || v.category} · capacity ${v.capacity}${v.facilities ? ' · ' + v.facilities : ''}` : ''); };
  showVenue();
  const f = form([
    h('div', { style: 'grid-column:1/-1' }, seg), route, socField,
    fld('Venue', venueSel, null), fld('Event title', inputs.event_title),
    fld('Expected participants', inputs.expected_attendees), fld('Date', dateIn, 'Minimum 2 days’ notice'),
    fld('Start time', inputs.start_time), fld('Duration (hours)', inputs.duration_hours),
    h('div', { class: 'field', style: 'grid-column:1/-1' }, h('label', null, 'Purpose / justification'), inputs.justification)],
    async (d) => {
      if (type === 'society' && !d.society_id) { const e = new Error('Please choose a society.'); e.fields = { society_id: 'Please choose the society you are reserving for.' }; throw e; }
      const body = { reservation_type: type, venue_id: d.venue_id, event_title: d.event_title, booking_date: d.booking_date, start_time: d.start_time,
        duration_hours: d.duration_hours === '' ? null : Number(d.duration_hours), expected_attendees: d.expected_attendees === '' ? null : Number(d.expected_attendees), justification: d.justification || '' };
      if (type === 'society') body.society_id = Number(d.society_id);
      const r = await api.post('/api/req/requisitions', body);
      sessionStorage.removeItem(DRAFT_KEY);
      toast('Reservation request submitted.', 'ok', 5000);
      put(holderCard, h('div', { class: 'alert ok', id: 'reservation-result' }, h('b', null, `${r.requisition_id} submitted. `), r.message),
        h('p', null, 'Status: ', badge(r.status), ' · Next approver: ', h('b', null, r.next_approver)),
        r.reservation_type === 'society' && !r.authority_verified ? h('div', { class: 'alert warn' }, 'Your authority to reserve for this society could not be verified from membership records. An administrator will confirm it during approval.') : null,
        h('p', null, h('a', { href: '#/requisitions?tab=mine', onclick: () => setTimeout(() => ctx.refresh(), 0) }, 'View my requisitions →')));
    }, 'Submit reservation request');
  f.addEventListener('input', (e) => { const n = e.target.name; if (n) saveDraft({ [n]: e.target.value }); if (n === 'venue_id') showVenue(); });
  venueSel.after(venueInfo);
  info();
  const holderCard = h('div', { class: 'card' });
  put(holderCard, notice ? h('div', { class: 'alert warn' }, notice) : null, h('p', { class: 'muted' }, 'Not sure which venue fits? Use “Find a venue” first, then choose “Reserve This Venue”. Your selections are kept while you fill in the form.'), f);
  return holderCard;
}

async function venues(ctx) {
  const wrap = h('div'); const out = h('div'); const all = await api.get('/api/req/venues');
  const cats = [...new Set(all.filter(v => v.category !== 'sports').map(v => v.category))];
  const desc = h('input', { name: 'description', placeholder: 'e.g. “hackathon with live demos and a projector”' });
  const typeSel = select([['', 'Any type'], ...cats.map(c => [c, c.replace('_', ' ')])], '', { name: 'venue_type' });
  const n = h('input', { name: 'attendees', type: 'number', min: 1, value: 50 }); const fac = h('input', { name: 'facilities', placeholder: 'projector, stage…' });
  const dt = h('input', { name: 'date', type: 'date' }); const tm = h('input', { name: 'start_time', type: 'time' });
  const q = h('input', { type: 'search', placeholder: 'Search venues by name or facility' }); const list = h('div');
  const card = (v, hint) => h('div', { class: 'card flat venue-card' },
    h('div', { class: 'row spread' }, h('b', null, v.name), v.match_score != null ? badge(`${v.match_score}% match`, 'info') : badge(v.category.replace('_', ' '), 'mute')),
    v.description ? h('div', null, v.description) : null,
    h('div', { class: 'small muted' }, `Capacity ${v.capacity}`, v.requires_approval === false ? ' · no approval needed' : ' · approval required'),
    h('div', { class: 'tags' }, (Array.isArray(v.facilities) ? v.facilities : String(v.facilities || '').split(',').map(x => x.trim()).filter(Boolean)).map(x => badge(x, 'mute'))),
    v.suitability ? h('div', { class: 'small' }, h('b', null, 'Suitability: '), v.suitability) : null,
    v.availability ? h('div', { class: 'small' }, h('b', null, 'Availability: '), v.availability) : null,
    h('div', null, h('button', { class: 'btn primary sm', 'data-venue': v.venue_id, onclick: () => reserve(v.venue_id, hint) }, 'Reserve This Venue')));
  async function loadList() {
    try { const v = (await api.get('/api/req/venues' + qs({ q: q.value }))).filter(x => x.category !== 'sports'); put(list, v.length ? h('div', { class: 'grid g2' }, v.map(x => card(x))) : h('div', { class: 'empty' }, 'No venues match your search.')); }
    catch (e) { put(list, errorBox(e)); }
  }
  const go = async () => {
    clearFieldErrors(wrap); put(out);
    try {
      const body = { description: desc.value, venue_type: typeSel.value, attendees: n.value === '' ? null : Number(n.value), facilities: fac.value, date: dt.value || null, start_time: tm.value || null };
      const r = await api.post('/api/req/venues/recommend', body);
      put(out, r.recommendations.length ? [h('div', { class: 'alert ok' }, r.guidance), r.recommendations.map(v => card(v, { expected_attendees: body.attendees, ...(body.date ? { booking_date: body.date } : {}), ...(body.start_time ? { start_time: body.start_time } : {}) }))] : h('div', { class: 'alert warn' }, r.guidance));
    } catch (e) { if (e.fields && showFieldErrors(wrap, e.fields)) return; put(out, errorBox(e)); }
  };
  wrap.append(h('div', { class: 'card' }, h('h2', null, 'Natural-language venue recommender'),
    h('div', { class: 'form-grid' }, field('Describe your event', desc), field('Venue type', typeSel), field('Participants', n), field('Needed facilities', fac), field('Date (optional)', dt), field('Start time (optional)', tm)),
    h('button', { class: 'btn primary', onclick: go }, 'Recommend'), h('div', { style: 'margin-top:12px' }, out)));
  q.addEventListener('input', () => { clearTimeout(q._t); q._t = setTimeout(loadList, 250); });
  wrap.append(h('div', { class: 'card' }, h('h2', null, 'Campus venues'), q, h('div', { style: 'margin-top:10px' }, list))); loadList(); return wrap;
}
