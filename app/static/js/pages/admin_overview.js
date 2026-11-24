import { api } from '../api.js';
import { h, statCard, barChart, stackBar, table, badge, pageHead, money, fmtDate } from '../ui.js';

export async function render(root) {
  const r = await api.get('/api/admin/reports/overview');
  root.append(pageHead('Reports & Overview', `${r.term} · generated ${new Date(r.generated_at).toLocaleString()}`));
  if (r.courses) {
    const t = r.courses.totals;
    root.append(h('h2', null, 'Course registration'), h('div', { class: 'grid g4' }, statCard('Courses', t.courses, `${t.active} active`), statCard('Registrations', t.registrations, 'this term'), statCard('Students registered', `${t.students_registered}/${t.students}`), statCard('Via assistant', (r.courses.by_channel.find(c => c.channel === 'assistant') || { n: 0 }).n, 'registrations/drops')));
    root.append(h('div', { class: 'grid g2' }, h('div', { class: 'card' }, h('h3', null, 'Enrolment by course'), barChart(r.courses.by_course.filter(c => c.is_active).slice(0, 10).map(c => ({ label: c.course_code, value: c.registered, cap: c.capacity })), { label: 'label', value: 'value', suffix: '' })),
      h('div', { class: 'card' }, h('h3', null, 'Seat utilisation'), table([{ label: 'Course', key: 'course_code' }, { label: 'Reg / Cap', render: c => `${c.registered}/${c.capacity}` }, { label: 'Fill', render: c => `${Math.round(100 * c.registered / Math.max(1, c.capacity))}%` }, { label: 'Dropped', key: 'dropped' }], r.courses.by_course.slice(0, 8)))));
  }
  if (r.mess) {
    const days = {}; r.mess.attendance_14d.forEach(x => { days[x.day] = (days[x.day] || 0) + x.n; });
    root.append(h('h2', null, 'Mess'), h('div', { class: 'grid g4' }, statCard('Rebates credited', money(r.mess.rebates.total_pkr), `${r.mess.rebates.rebates} transactions · ${r.mess.rebates.days} days`), statCard('Entries today', r.mess.entries_today), statCard('Mess-outs', r.mess.mess_out_by_status.map(s => `${s.status} ${s.n}`).join(' · ') || '—')));
    root.append(h('div', { class: 'card' }, h('h3', null, 'Dining hall entries (last 14 days)'), barChart(Object.entries(days).map(([d, n]) => ({ label: fmtDate(d), value: n })), { label: 'label', value: 'value' })));
  }
  if (r.requisitions) {
    root.append(h('h2', null, 'Requisitions, sports & equipment'), h('div', { class: 'grid g3' },
      h('div', { class: 'card' }, h('h3', null, 'Requisitions by status'), stackBar(r.requisitions.by_status.map(s => ({ label: s.status.replace(/_/g, ' '), n: s.n }))), h('p', { class: 'small muted' }, `${r.requisitions.pending_admin} awaiting Tier-2 approval`)),
      h('div', { class: 'card' }, h('h3', null, 'Equipment status'), stackBar(r.equipment.by_status.map(s => ({ label: s.status.replace(/_/g, ' '), n: s.n }))), h('p', { class: 'small muted' }, `${r.equipment.overdue.length} overdue loan(s)`)),
      h('div', { class: 'card' }, h('h3', null, 'Requisitions by society'), barChart(r.requisitions.by_society.map(s => ({ label: s.society_name, value: s.n })), { label: 'label', value: 'value' }))));
    root.append(h('div', { class: 'grid g2' }, h('div', { class: 'card' }, h('h3', null, 'Sports facility utilisation (30 days)'), table([{ label: 'Facility', key: 'name' }, { label: 'Hours booked', key: 'booked_hours' }, { label: 'Checked in', key: 'checked_in' }, { label: 'No-shows', key: 'no_shows' }, { label: 'Cancelled', key: 'cancelled' }], r.sports.utilization), h('p', { class: 'small muted' }, `${r.sports.suspended_students} student(s) currently suspended`)),
      h('div', { class: 'card' }, h('h3', null, 'Equipment custody by lab'), table([{ label: 'Lab', key: 'lab' }, { label: 'Items', key: 'total' }, { label: 'In custody', key: 'in_custody' }], r.equipment.by_lab), r.equipment.overdue.length ? h('div', { class: 'alert bad', style: 'margin-top:10px' }, 'Overdue: ', r.equipment.overdue.map(o => `${o.name} (${o.full_name}, due ${fmtDate(o.due_date)})`).join(' · ')) : null)));
  }
  if (r.activity) {
    const a = r.activity;
    root.append(h('h2', null, 'Platform activity'), h('div', { class: 'grid g3' }, statCard('Active students (7d)', a.active_students_7d), statCard('Failed event handlers', a.failed_event_handlers, a.failed_event_handlers ? 'Retry from Audit & Events' : 'Pipeline healthy'),
      h('div', { class: 'card' }, h('h3', null, 'Actions by channel (30d)'), stackBar(a.by_channel.map(c => ({ label: c.channel, n: c.n }))))),
      h('div', { class: 'card' }, h('h3', null, 'Daily operations (accepted vs rejected)'), barChart(a.daily_14d.map(d => ({ label: fmtDate(d.day), value: d.ok + d.rejected })), { label: 'label', value: 'value' })));
  }
}
