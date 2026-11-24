import { api } from '../api.js';
import { h, table, badge, tabs, modal, toast, field, select, pageHead, fmtDate, clear } from '../ui.js';
import { stages } from './requisitions.js';
import { decide } from './approvals.js';

export async function render(root, ctx) {
  root.append(pageHead('Requisition Approvals', 'Tier-2 clearance by the Directorate of Student Affairs.'));
  const holder = h('div'); root.append(holder);
  tabs(holder, [{ key: 'q', label: 'Awaiting Tier-2', render: () => list(ctx, 'pending_admin_approval') }, { key: 'adv', label: 'Awaiting advisor', render: () => list(ctx, 'pending_advisor_approval') }, { key: 'all', label: 'All requisitions', render: () => list(ctx, '') }]);
}
async function list(ctx, status) {
  const rows = await api.get('/api/req/requisitions?scope=all' + (status ? `&status=${status}` : ''));
  return table([{ label: 'ID', render: r => h('b', null, r.requisition_id) }, { label: 'Event', render: r => h('div', null, r.event_title, h('div', { class: 'small muted' }, `${r.society_name} · ${r.applicant_name}`)) }, { label: 'Venue & time', render: r => `${r.venue_name} · ${fmtDate(r.booking_date)} ${r.start_time} (${r.duration_hours}h) · ${r.expected_attendees}` },
    { label: 'Progress', render: r => stages(r) }, { label: 'Status', render: r => badge(r.status) },
    { label: '', render: r => ['pending_admin_approval', 'pending_advisor_approval'].includes(r.status) ? h('span', { class: 'row' }, h('button', { class: 'btn sm primary', onclick: () => decide(r, 'approve', ctx) }, 'Approve'), h('button', { class: 'btn sm danger', onclick: () => decide(r, 'reject', ctx) }, 'Reject')) : null }], rows, { empty: 'Nothing here.' });
}
