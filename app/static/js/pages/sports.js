import { api, qs } from '../api.js';
import { h, clear, table, badge, modal, confirmDialog, toast, field, select, pageHead, fmtDateTime, addDays, statCard } from '../ui.js';

export async function render(root, ctx) {
  root.append(pageHead('Sports Booking', 'Reserve courts and facilities. Fair-use quotas and the no-show rule apply.'));
  const [facs, quota] = await Promise.all([api.get('/api/req/sports/facilities'), api.get('/api/req/sports/quota')]);
  const q = h('div'); const slots = h('div'); const mine = h('div');
  const fac = select(facs.map(f => [f.venue_id, f.name]), facs[0]?.venue_id); const date = h('input', { type: 'date', value: addDays(1), min: addDays(0), max: addDays(7) });
  const quotaBox = () => h('div', { class: 'grid g4' }, statCard('Daily quota left', `${quota.daily_remaining_hours}h`, `of ${quota.daily_limit_hours}h/day`), statCard('Weekly quota left', `${quota.weekly_remaining_hours}h`, `of ${quota.weekly_limit_hours}h/week`),
    statCard('No-show strikes', `${quota.no_show_strikes}/${quota.strike_limit}`, quota.suspended ? `Suspended until ${quota.suspended_until || '—'}` : 'Good standing'), statCard('Check-in rule', '15 min', 'Unclaimed slots are auto-released'));
  root.append(quotaBox());
  if (quota.suspended) root.append(h('div', { class: 'alert bad' }, `Your booking privileges are suspended after ${quota.strike_limit} no-shows${quota.suspended_until ? ` until ${quota.suspended_until}` : ''}.`));

  async function loadSlots() {
    const a = await api.get('/api/req/sports/availability' + qs({ facility_id: fac.value, date: date.value }));
    clear(slots).append(h('div', { class: 'slots' }, a.slots.map(s => h('div', { class: `slot ${s.available ? '' : s.booked_by_me ? 'mine taken' : 'taken'}`, title: s.reason || 'Available', onclick: () => s.available && book(a, s) }, `${s.start}`, h('div', { class: 'small' }, s.available ? 'Free' : s.booked_by_me ? 'Yours' : s.reason)))));
  }
  async function book(a, s) {
    const dur = select([['1', '1 hour'], ['1.5', '1.5 hours'], ['2', '2 hours'], ['0.5', '30 minutes']], '1');
    modal({ title: `Book ${a.facility}`, body: h('div', null, h('p', null, `${a.date} from ${s.start}`), field('Duration', dur, `Remaining today: ${quota.daily_remaining_hours}h. The server enforces quotas, opening hours and slot availability.`)),
      actions: [{ label: 'Cancel', onClick: c => c() }, { label: 'Confirm booking', kind: 'primary', onClick: async (close) => {
        const r = await api.post('/api/req/sports/bookings', { facility_id: fac.value, date: a.date, start_time: s.start, duration_hours: Number(dur.value) }); close(); toast(r.message, 'ok', 9000); ctx.refresh(); } }] });
  }
  async function loadMine() {
    const rows = await api.get('/api/req/sports/bookings');
    clear(mine).append(table([{ label: 'Code', render: r => h('b', null, r.booking_code) }, { label: 'Venue', key: 'venue_name' }, { label: 'When', render: r => `${fmtDateTime(r.start_ts)} – ${fmtDateTime(r.end_ts).split(', ').pop()}` },
      { label: 'Status', render: r => badge(r.status) }, { label: 'Check-in PIN', render: r => r.checkin_token ? h('code', null, r.checkin_token) : '—' },
      { label: '', render: r => h('span', { class: 'row' },
        r.status === 'approved' && r.source === 'sports_reservation' && new Date(r.start_ts) < new Date(Date.now() + 10 * 60000) && new Date(r.end_ts) > new Date() ? h('button', { class: 'btn sm primary', onclick: () => checkin(r) }, 'Check in') : null,
        r.can_cancel ? h('button', { class: 'btn sm danger', onclick: async () => { if (!(await confirmDialog('Cancel booking?', `${r.venue_name} ${fmtDateTime(r.start_ts)}`, 'Cancel booking', 'danger'))) return;
          try { const x = await api.post(`/api/req/sports/bookings/${r.booking_code}/cancel`, {}); toast(x.message, 'ok'); ctx.refresh(); } catch (e) { toast(e.message, 'bad'); } } }, 'Cancel') : null) }], rows, { empty: 'No bookings yet.' }));
  }
  function checkin(r) {
    const pin = h('input', { placeholder: 'CHK-0000' });
    modal({ title: `Check in: ${r.venue_name}`, body: field('Check-in PIN', pin, 'Enter the PIN shown on your booking within the check-in window.'), actions: [{ label: 'Check in', kind: 'primary', onClick: async (close) => {
      const x = await api.post(`/api/req/sports/bookings/${r.booking_code}/checkin`, { pin: pin.value }); close(); toast(x.message, 'ok'); ctx.refresh(); } }] });
  }
  fac.addEventListener('change', loadSlots); date.addEventListener('change', loadSlots);
  root.append(h('div', { class: 'card' }, h('h2', null, 'Available slots'), h('div', { class: 'row', style: 'margin-bottom:12px' }, h('div', { style: 'min-width:260px' }, fac), h('div', null, date)), slots),
    h('div', { class: 'card' }, h('h2', null, 'My bookings'), mine));
  await Promise.all([loadSlots(), loadMine()]);
}
