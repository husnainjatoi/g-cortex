import { api, qs } from '../api.js';
import { h, clear, table, badge, tabs, modal, confirmDialog, toast, field, select, pageHead, fmtDateTime, addDays } from '../ui.js';

export async function render(root, ctx) {
  root.append(pageHead('Sports & Venues', 'Bookings, no-show enforcement, venue catalogue and booking quotas.'));
  const holder = h('div'); root.append(holder);
  tabs(holder, [{ key: 'b', label: 'Bookings', render: () => bookings(ctx) }, { key: 'v', label: 'Venues & facilities', render: () => venues(ctx) }, { key: 'q', label: 'Quotas & rules', render: () => rules(['sports_daily_quota_hours', 'sports_weekly_quota_hours', 'sports_open_hour', 'sports_close_hour', 'sports_advance_days', 'cancel_cutoff_minutes', 'checkin_grace_minutes', 'strike_limit', 'suspension_days', 'sports_checkin_early_minutes']) }]);
}
async function bookings(ctx) {
  const date = h('input', { type: 'date', value: addDays(0) }); const out = h('div');
  async function load() { const rows = await api.get('/api/req/admin/bookings' + qs({ date: date.value }));
    clear(out).append(table([{ label: 'Code', key: 'booking_code' }, { label: 'Venue', key: 'venue_name' }, { label: 'Student', render: r => `${r.full_name} (${r.user_id})` }, { label: 'Time', render: r => `${fmtDateTime(r.start_ts)} – ${fmtDateTime(r.end_ts).split(', ').pop()}` }, { label: 'Status', render: r => badge(r.status) }, { label: 'Type', key: 'source' },
      { label: '', render: r => ['approved', 'pending'].includes(r.status) ? h('button', { class: 'btn sm danger', onclick: async () => { if (!(await confirmDialog('Cancel booking?', r.booking_code, 'Cancel', 'danger'))) return; try { await api.post(`/api/req/sports/bookings/${r.booking_code}/cancel`, { reason: 'cancelled by administrator' }); toast('Cancelled.', 'ok'); load(); } catch (e) { toast(e.message, 'bad'); } } }, 'Cancel') : null }], rows, { empty: 'No bookings on this date.' })); }
  date.addEventListener('change', load);
  const w = h('div', null, h('div', { class: 'row', style: 'margin-bottom:12px' }, h('div', { style: 'width:200px' }, date), h('button', { class: 'btn', onclick: async () => { const r = await api.post('/api/req/admin/auto-release'); toast(`No-show sweep: ${r.released} slot(s) auto-released.`, 'ok'); load(); } }, 'Run no-show auto-release now'), h('span', { class: 'small muted' }, 'Also runs automatically every minute.')), out);
  await load(); return w;
}
async function venues(ctx) {
  const rows = await api.get('/api/req/venues?include_inactive=true');
  const wrap = h('div');
  function edit(v) {
    const isNew = !v;
    const f = { venue_id: h('input', { value: v?.venue_id || '', disabled: !isNew, placeholder: 'SEM_HALL_2' }), name: h('input', { value: v?.name || '' }), capacity: h('input', { type: 'number', min: 1, value: v?.capacity || 50 }), category: h('input', { value: v?.category || '', placeholder: 'seminar_hall, sports, lab…' }),
      facilities: h('input', { value: v?.facilities || '' }), description: h('textarea', null, v?.description || ''), requires_approval: select([['true', 'Required'], ['false', 'Not required']], String(v?.requires_approval ?? true)), is_active: select([['true', 'Active'], ['false', 'Inactive']], String(v?.is_active ?? true)), exclusive: select([['true', 'Single booking per slot'], ['false', 'Shared (up to capacity)']], String(v?.exclusive ?? true)) };
    modal({ title: isNew ? 'Add venue / facility' : `Edit ${v.name}`, wide: true, body: h('div', { class: 'form-grid' }, field('Venue ID', f.venue_id), field('Name', f.name), field('Capacity', f.capacity), field('Category', f.category, "Use 'sports' for bookable sports facilities"), field('Facilities', f.facilities), field('Approval', f.requires_approval), field('Availability', f.is_active), field('Booking mode', f.exclusive), h('div', { style: 'grid-column:1/-1' }, field('Description', f.description))),
      actions: [{ label: 'Cancel', onClick: c => c() }, { label: 'Save', kind: 'primary', onClick: async (close) => {
        const b = { name: f.name.value, capacity: +f.capacity.value, category: f.category.value, facilities: f.facilities.value, description: f.description.value, requires_approval: f.requires_approval.value === 'true', exclusive: f.exclusive.value === 'true' };
        if (isNew) await api.post('/api/req/venues', { venue_id: f.venue_id.value, ...b }); else await api.put(`/api/req/venues/${v.venue_id}`, { ...b, is_active: f.is_active.value === 'true' }); close(); toast('Saved.', 'ok'); ctx.refresh(); } }] });
  }
  wrap.append(h('div', { style: 'margin-bottom:12px' }, h('button', { class: 'btn primary', onclick: () => edit(null) }, '+ Add venue')),
    table([{ label: 'ID', render: v => h('code', null, v.venue_id) }, { label: 'Name', key: 'name' }, { label: 'Type', key: 'category' }, { label: 'Capacity', key: 'capacity' }, { label: 'Facilities', render: v => v.facilities || '—' }, { label: 'Active', render: v => badge(v.is_active ? 'active' : 'cancelled') }, { label: '', render: v => h('button', { class: 'btn sm', onclick: () => edit(v) }, 'Edit') }], rows));
  return wrap;
}
export async function rules(keys) {
  const all = await api.get('/api/admin/settings'); const rows = all.filter(s => keys.includes(s.key));
  return table([{ label: 'Rule', render: s => h('div', null, h('code', null, s.key), h('div', { class: 'small muted' }, s.description)) }, { label: 'Value', render: s => { const i = h('input', { value: s.value, style: 'width:120px' }); return h('span', { class: 'row' }, i, h('button', { class: 'btn sm primary', onclick: async () => { try { await api.put(`/api/admin/settings/${s.key}`, { value: i.value }); toast('Rule updated.', 'ok'); } catch (e) { toast(e.message, 'bad'); } } }, 'Save')); } }], rows);
}
