import { api } from '../api.js';
import { h, statCard, badge, fmtDate, fmtDateTime, money, table, pageHead } from '../ui.js';

export async function render(root, { user }) {
  const d = await api.get('/api/portal/dashboard');
  root.append(pageHead(`Welcome, ${user.full_name.split(' ')[0]}`, `${d.courses.term} · your campus at a glance`));
  if (d.courses.has_conflicts) root.append(h('div', { class: 'alert warn' }, '⚠ Your registered courses have a timetable clash. ', h('a', { href: '#/courses' }, 'Review your timetable')));
  root.append(h('div', { class: 'grid g4' },
    statCard('Registered courses', d.courses.count, `${d.courses.credits}/${d.courses.max_credits} credit hours`),
    statCard(d.mess.bill.kind === 'estimate' ? 'Mess bill (estimate)' : 'Mess invoice', d.mess.bill.amount_pkr == null ? '—' : money(d.mess.bill.amount_pkr), `${d.mess.bill.days_consumed} day(s) eaten in ${d.mess.bill.month} · outstanding ${money(d.mess.bill.outstanding_total_pkr)}`),
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
    h('div', { class: 'card' }, h('h2', null, 'Mess security deposit'),
      h('p', null, badge(d.mess.deposit.status.replace('_', ' ')), ' ', d.mess.deposit.status === 'not_configured' ? 'Deposit amount not configured yet.' : `Refundable balance ${money(d.mess.deposit.refundable_balance_pkr)} of ${money(d.mess.deposit.original_deposit_pkr)}`), h('p', null, h('a', { href: '#/mess' }, 'View bill & deposit →'))),
    h('div', { class: 'card' }, h('h2', null, 'Equipment in your custody'),
      table([{ label: 'Item', key: 'name' }, { label: 'Barcode', key: 'barcode' }, { label: 'Due', render: r => h('span', null, fmtDate(r.due_date), ' ', r.overdue ? badge('overdue') : null) }], d.equipment, { empty: 'You are not holding any equipment.' }))));
  root.append(h('div', { class: 'card flat' }, h('b', null, 'Tip: '), 'press the ✦ button to ask the assistant to do any of this for you — it uses the same rules as these pages.'));
}
