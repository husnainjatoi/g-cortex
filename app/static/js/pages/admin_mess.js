import { api, qs } from '../api.js';
import { h, clear, table, badge, tabs, modal, confirmDialog, toast, field, select, pageHead, fmtDate, fmtDateTime, money, addDays, barChart } from '../ui.js';

export async function render(root, ctx) {
  root.append(pageHead('Mess Office', 'Approvals, rebates, attendance, menu & nutrition, forecasting and gate-pass sync.'));
  const holder = h('div'); root.append(holder);
  tabs(holder, [{ key: 'p', label: 'Mess-out approvals', render: () => approvals(ctx) }, { key: 'a', label: 'Attendance', render: attendance }, { key: 'm', label: 'Menu & nutrition', render: () => menu(ctx) },
    { key: 'f', label: 'Demand forecast', render: forecast }, { key: 'g', label: 'Gate-pass sync', render: gate }]);
}

async function approvals(ctx) {
  const [rows, sum] = await Promise.all([api.get('/api/mess/admin/mess-out'), api.get('/api/mess/admin/summary')]);
  const wrap = h('div'); wrap.append(h('div', { class: 'row', style: 'margin-bottom:12px' }, badge(`${sum.pending_requests} pending`, 'warn'), h('span', { class: 'muted' }, `${sum.approved} rebates · ${money(sum.total_pkr)} credited · ${sum.total_days} days`)));
  wrap.append(table([{ label: '#', key: 'request_id' }, { label: 'Student', render: r => `${r.full_name} (${r.user_id})` }, { label: 'Dates', render: r => `${fmtDate(r.start_date)} – ${fmtDate(r.end_date)} (${r.days_count}d)` }, { label: 'Rebate', render: r => money(r.est_rebate_pkr) },
    { label: 'Gate-pass', render: r => r.gate_pass_synced ? 'Verified' : 'None' }, { label: 'Reason', render: r => r.reason || '—' }, { label: 'Status', render: r => badge(r.status) },
    { label: '', render: r => r.status === 'pending' ? h('span', { class: 'row' }, h('button', { class: 'btn sm primary', onclick: () => decide(r, true) }, 'Approve'), h('button', { class: 'btn sm danger', onclick: () => decide(r, false) }, 'Reject')) : null }], rows, { empty: 'No mess-out requests.' }));
  function decide(r, approve) {
    const note = h('input', { placeholder: 'Note (optional)' });
    modal({ title: `${approve ? 'Approve' : 'Reject'} mess-out #${r.request_id}`, body: h('div', null, h('p', null, approve ? `Approving credits ${money(r.est_rebate_pkr)} to ${r.full_name}'s rebate ledger.` : 'The student will be notified.'), field('Note', note)),
      actions: [{ label: 'Cancel', onClick: c => c() }, { label: approve ? 'Approve' : 'Reject', kind: approve ? 'primary' : 'danger', onClick: async (close) => { await api.post(`/api/mess/admin/mess-out/${r.request_id}/decision`, { approve, note: note.value }); close(); toast('Decision recorded.', 'ok'); ctx.refresh(); } }] });
  }
  return wrap;
}

async function attendance() {
  const date = h('input', { type: 'date', value: addDays(0) }); const out = h('div');
  async function load() { const r = await api.get('/api/mess/admin/attendance' + qs({ date: date.value }));
    clear(out).append(h('div', { class: 'row', style: 'margin:10px 0' }, Object.entries(r.counts).map(([k, v]) => badge(`${k}: ${v}`, 'info'))), table([{ label: 'Time', render: e => fmtDateTime(e.created_at) }, { label: 'Student', render: e => `${e.full_name} (${e.user_id})` }, { label: 'Meal', key: 'meal_type' }, { label: 'Status', render: e => badge(e.status) }, { label: 'Source', key: 'source' }], r.entries, { empty: 'No entries for this date.' })); }
  date.addEventListener('change', load); const w = h('div', null, h('div', { style: 'width:200px' }, date), out); await load(); return w;
}

async function menu(ctx) {
  const r = await api.get('/api/mess/menu');
  return h('div', { class: 'grid g3' }, r.meals.map(m => {
    const f = { items: h('textarea', null, m.items.join(', ')), calories: h('input', { type: 'number', value: m.calories }), protein_g: h('input', { type: 'number', value: m.protein_g }), carbs_g: h('input', { type: 'number', value: m.carbs_g }), fat_g: h('input', { type: 'number', value: m.fat_g }), allergens: h('input', { value: m.allergens.join(', ') }) };
    return h('div', { class: 'card' }, h('h2', null, m.meal_period), field('Items', f.items), h('div', { class: 'form-grid' }, field('kcal', f.calories), field('Protein g', f.protein_g), field('Carbs g', f.carbs_g), field('Fat g', f.fat_g)), field('Allergens', f.allergens),
      h('button', { class: 'btn primary', onclick: async () => { try { await api.put(`/api/mess/admin/menu/${m.meal_period}`, { items: f.items.value, calories: +f.calories.value, protein_g: +f.protein_g.value, carbs_g: +f.carbs_g.value, fat_g: +f.fat_g.value, allergens: f.allergens.value }); toast('Menu saved.', 'ok'); } catch (e) { toast(e.message, 'bad'); } } }, 'Save'));
  }));
}

async function forecast() {
  const date = h('input', { type: 'date', value: addDays(1) }); const out = h('div');
  async function go() { const rows = await Promise.all(['breakfast', 'lunch', 'dinner'].map(m => api.get('/api/mess/forecast' + qs({ meal: m, date: date.value }))));
    clear(out).append(table([{ label: 'Meal', key: 'meal' }, { label: 'Enrolled', key: 'enrolled_students' }, { label: 'Away', key: 'approved_mess_outs' }, { label: 'Predicted', render: r => h('b', null, r.predicted_headcount) }, { label: 'Produce', key: 'recommended_production' }, { label: 'Waste avoided', render: r => `${r.estimated_overproduction_avoided_pct}%` }, { label: 'Method', render: r => h('span', { class: 'small' }, r.method) }], rows), h('p', { class: 'small muted' }, rows[0].note)); }
  date.addEventListener('change', go); const w = h('div', null, h('div', { style: 'width:200px;margin-bottom:12px' }, date), out); await go(); return w;
}

async function gate() {
  const f = { user_id: h('input', { placeholder: 'Student ID' }), leave_start: h('input', { type: 'date', value: addDays(1) }), leave_end: h('input', { type: 'date', value: addDays(4) }), destination: h('input', { placeholder: 'Destination' }), external_ref: h('input', { placeholder: 'Gate controller reference (optional)' }) };
  return h('div', { class: 'card' }, h('h2', null, 'Gate-pass synchronisation'), h('div', { class: 'alert info' }, 'Production gate-passes arrive from the campus security gate controller via POST /api/mess/gate-pass-sync using the X-Gate-Key header. This form performs the same call manually for testing or manual entry; no physical gate hardware is controlled from this portal.'),
    h('div', { class: 'form-grid' }, field('Student ID', f.user_id), field('Destination', f.destination), field('Leave from', f.leave_start), field('Leave to', f.leave_end), field('External reference', f.external_ref)),
    h('button', { class: 'btn primary', onclick: async () => { try { await api.post('/api/mess/gate-pass-sync', { user_id: f.user_id.value.trim(), leave_start: f.leave_start.value, leave_end: f.leave_end.value, destination: f.destination.value || null, external_ref: f.external_ref.value || null, approved: true }); toast('Gate-pass synchronised; matching pending mess-outs were auto-approved.', 'ok', 7000); } catch (e) { toast(e.message, 'bad'); } } }, 'Sync gate-pass'));
}
