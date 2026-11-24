import { api } from '../api.js';
import { h, statCard, badge, fmtDate, fmtDateTime, money, table, pageHead } from '../ui.js';

export async function render(root, { user }) {
  const d = await api.get('/api/portal/dashboard');
  root.append(pageHead(`Welcome, ${user.full_name.split(' ')[0]}`, `${d.courses.term} · your campus at a glance`));
  if (d.courses.has_conflicts) root.append(h('div', { class: 'alert warn' }, '⚠ Your registered courses have a timetable clash. ', h('a', { href: '#/courses' }, 'Review your timetable')));
  root.append(h('div', { class: 'grid g4' },
    statCard('Registered courses', d.courses.count, `${d.courses.credits}/${d.courses.max_credits} credit hours`),
    statCard('Rebate credited', money(d.mess.rebate_pkr), d.mess.upcoming_mess_out.length ? `${d.mess.upcoming_mess_out.length} mess-out(s) upcoming/pending` : 'No upcoming mess-out'),
    statCard('Sports hours left today', `${d.quota.daily_remaining_hours}h`, `${d.quota.weekly_remaining_hours}h left this week · ${d.quota.no_show_strikes}/${d.quota.strike_limit} strikes`),
    statCard('Unread notifications', d.unread_notifications, h('a', { href: '#/notifications' }, 'Open inbox'))));
  root.append(h('div', { class: 'grid g2' },
    h('div', { class: 'card' }, h('h2', null, 'Upcoming bookings'),
      table([{ label: 'Facility', render: r => r.venue_name }, { label: 'When', render: r => fmtDateTime(r.start_ts) }, { label: 'Status', render: r => badge(r.status) }], d.bookings, { empty: 'No upcoming bookings.' }),
      h('p', null, h('a', { href: '#/sports' }, 'Book a slot →'))),
    h('div', { class: 'card' }, h('h2', null, 'Open requisitions'),
      table([{ label: 'ID', key: 'requisition_id' }, { label: 'Event', key: 'event_title' }, { label: 'Waiting on', render: r => r.pending_authority || '—' }], d.requisitions, { empty: 'No requisitions awaiting approval.' }),
      h('p', null, h('a', { href: '#/requisitions' }, 'Manage requisitions →')))));
  root.append(h('div', { class: 'grid g2' },
    h('div', { class: 'card' }, h('h2', null, 'Mess-outs'),
      table([{ label: 'Dates', render: r => `${fmtDate(r.start_date)} – ${fmtDate(r.end_date)}` }, { label: 'Rebate', render: r => money(r.est_rebate_pkr) }, { label: 'Status', render: r => badge(r.status) }], d.mess.upcoming_mess_out, { empty: 'Nothing scheduled.' })),
    h('div', { class: 'card' }, h('h2', null, 'Equipment in your custody'),
      table([{ label: 'Item', key: 'name' }, { label: 'Barcode', key: 'barcode' }, { label: 'Due', render: r => h('span', null, fmtDate(r.due_date), ' ', r.overdue ? badge('overdue') : null) }], d.equipment, { empty: 'You are not holding any equipment.' }))));
  root.append(h('div', { class: 'card flat' }, h('b', null, 'Tip: '), 'press the ✦ button to ask the assistant to do any of this for you — it uses the same rules as these pages.'));
}
