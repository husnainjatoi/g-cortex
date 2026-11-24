import { api } from '../api.js';
import { h, table, badge, modal, toast, field, pageHead, fmtDate } from '../ui.js';
import { stages } from './requisitions.js';

export async function render(root, ctx) {
  root.append(pageHead('Faculty Approvals', 'Tier-1 endorsements for the societies you advise.'));
  const rows = await api.get('/api/req/requisitions?scope=assigned');
  root.append(table([{ label: 'ID', render: r => h('b', null, r.requisition_id) }, { label: 'Event', render: r => h('div', null, r.event_title, h('div', { class: 'small muted' }, `${r.society_name} · by ${r.applicant_name}`)) },
    { label: 'Venue & time', render: r => `${r.venue_name} · ${fmtDate(r.booking_date)} ${r.start_time} · ${r.expected_attendees} people` }, { label: 'Justification', render: r => r.justification || '—' },
    { label: 'Progress', render: r => stages(r) }, { label: 'Status', render: r => badge(r.status) },
    { label: '', render: r => r.status === 'pending_advisor_approval' ? h('span', { class: 'row' }, h('button', { class: 'btn sm primary', onclick: () => decide(r, 'approve', ctx) }, 'Approve'), h('button', { class: 'btn sm danger', onclick: () => decide(r, 'reject', ctx) }, 'Reject')) : null }], rows, { empty: 'No requisitions assigned to you.' }));
}
export function decide(r, action, ctx) {
  const c = h('textarea', { placeholder: action === 'reject' ? 'Reason (required)' : 'Comments (optional)', maxlength: 500 });
  modal({ title: `${action === 'approve' ? 'Approve' : 'Reject'} ${r.requisition_id}`, body: h('div', null, h('p', null, `${r.event_title} — ${r.venue_name}`), field('Comments', c)),
    actions: [{ label: 'Cancel', onClick: x => x() }, { label: action === 'approve' ? 'Approve' : 'Reject', kind: action === 'approve' ? 'primary' : 'danger', onClick: async (close) => {
      const x = await api.post(`/api/req/requisitions/${r.requisition_id}/review`, { action, comments: c.value }); close(); toast(x.message, 'ok', 6000); ctx.refresh(); } }] });
}
