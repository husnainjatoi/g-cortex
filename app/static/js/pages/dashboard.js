import { api } from '../api.js';
import { h, statCard, badge, fmtDate, fmtDateTime, money, table, pageHead } from '../ui.js';

export async function render(root, { user }) {
  const d = await api.get('/api/portal/dashboard');
  const deg = await api.get('/api/courses/me/degree-progress').catch(() => null);
  root.append(pageHead(`Welcome, ${user.full_name.split(' ')[0]}`, `${d.courses.term} · your campus at a glance`));
  if (d.courses.has_conflicts) root.append(h('div', { class: 'alert warn' }, '⚠ Your registered courses have a timetable clash. ', h('a', { href: '#/courses' }, 'Review your timetable')));
  root.append(h('div', { class: 'grid g4' },
    statCard('Registered courses', d.courses.count, `${d.courses.credits}/${d.courses.max_credits} credit hours`),
    messBillCard(d.mess.bill),
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
    h('div', { class: 'card', id: 'degree-card' }, h('h2', null, 'Degree progress'), deg
      ? [h('p', null, h('b', null, deg.program.name), ` · semester ${deg.current_semester}`), h('div', { class: 'bar' }, h('i', { style: `width:${Math.round(100 * deg.credits.completed / Math.max(1, deg.credits.total))}%` })),
         h('p', { class: 'small muted' }, `${deg.credits.completed} of ${deg.credits.total} credit hours completed · ${deg.credits.in_progress} in progress${deg.demo_assumption ? ' (demo assumption)' : ''}`), h('p', null, h('a', { href: '#/courses' }, 'Open degree plan →'))]
      : h('p', { class: 'muted' }, 'Your degree programme is not set up yet.')),
    h('div', { class: 'card' }, h('h2', null, 'Equipment in your custody'),
      table([{ label: 'Item', key: 'name' }, { label: 'Barcode', key: 'barcode' }, { label: 'Due', render: r => h('span', null, fmtDate(r.due_date), ' ', r.overdue ? badge('overdue') : null) }], d.equipment, { empty: 'You are not holding any equipment.' }))));
  root.append(h('div', { class: 'card flat' }, h('b', null, 'Tip: '), 'press the ✦ button to ask the assistant to do any of this for you — it uses the same rules as these pages.'));
}

// Estimated vs finalized: an estimate is a projection (shared food cost / students using the mess); a finalized invoice is the confirmed, payable amount.
function messBillCard(b) {
  const fin = b.kind === 'finalized' && b.invoice;
  const status = fin ? b.invoice.status : null;
  const sub = fin
    ? h('div', { class: 's' }, badge(status, { unpaid: 'warn', partial: 'info', paid: 'ok' }[status]), ` · balance ${money(b.invoice.balance_pkr)}${b.invoice.due_date ? ' · due ' + fmtDate(b.invoice.due_date) : ''}`)
    : h('div', { class: 's' }, badge('estimate – not an invoice', 'info'), b.amount_pkr == null ? ' Not available yet — food expenditure not entered.' : ` · ${b.days_consumed} day(s) eaten in ${b.month}`);
  return h('div', { class: 'card stat', id: 'mess-bill-card' }, h('div', { class: 'l' }, fin ? `Mess invoice · ${b.month}` : `Estimated mess bill · ${b.month}`),
    h('div', { class: 'v' }, b.amount_pkr == null ? '—' : money(b.amount_pkr)), sub,
    !fin && b.disclaimer ? h('div', { class: 'small muted', style: 'margin-top:4px' }, b.disclaimer) : null,
    h('div', { class: 's', style: 'margin-top:6px' }, h('a', { href: '#/mess' }, 'View more →')));
}
