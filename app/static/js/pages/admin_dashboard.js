import { api } from '../api.js';
import { h, statCard, pageHead, badge, table, fmtDate, money } from '../ui.js';

export async function render(root, { user }) {
  const has = (s) => user.scopes.includes('super') || user.scopes.includes(s);
  root.append(pageHead('Admin Dashboard', 'Pending work for your areas of responsibility.'));
  const cards = [], lists = [];
  const tryGet = async (p) => { try { return await api.get(p); } catch (_) { return null; } };
  if (has('mess')) {
    const [sum, pays, clr] = await Promise.all([tryGet('/api/mess/admin/summary'), tryGet('/api/mess/admin/payments?status=submitted'), tryGet('/api/mess/admin/clearances?status=requested')]);
    if (sum) cards.push(statCard('Outstanding mess dues', money(sum.outstanding_pkr), `${money(sum.collected_pkr)} collected · ${sum.invoices} invoices`), statCard('Payments to verify', sum.payments_awaiting_verification, h('a', { href: '#/admin/mess' }, 'Open Mess Administration')));
    if (clr) cards.push(statCard('Clearances to review', clr.length, 'Final mess clearance requests'));
  }
  if (has('requisition')) {
    const q = await tryGet('/api/req/requisitions?scope=all&status=pending_admin_approval');
    if (q) { cards.push(statCard('Requisitions awaiting Tier-2', q.length, h('a', { href: '#/admin/requisitions' }, 'Review'))); lists.push(h('div', { class: 'card' }, h('h2', null, 'Awaiting your approval'), table([{ label: 'ID', key: 'requisition_id' }, { label: 'Event', key: 'event_title' }, { label: 'Society', key: 'society_name' }, { label: 'Date', render: r => fmtDate(r.booking_date) }], q.slice(0, 6), { empty: 'Nothing pending.' }))); }
  }
  if (has('academic')) {
    const k = await tryGet('/api/admin/knowledge/status');
    if (k) cards.push(statCard('Official GIKI data', `${k.totals.sources} sources`, `${k.totals.chunks} passages · ${k.totals.open_flags} course flags to review`));
  }
  if (has('super')) {
    const r = await tryGet('/api/admin/reports/overview');
    if (r && r.activity) cards.push(statCard('Failed event handlers', r.activity.failed_event_handlers, h('a', { href: '#/admin/audit' }, 'Audit & Events')), statCard('Active students (7d)', r.activity.active_students_7d));
  }
  root.append(h('div', { class: 'grid g4' }, cards), ...lists, h('p', { class: 'muted' }, 'Charts and tables: ', h('a', { href: '#/admin/reports' }, 'Reports & Analytics')));
}
