import { api, qs } from '../api.js';
import { h, clear, table, badge, tabs, modal, confirmDialog, toast, field, select, pageHead, fmtDate, fmtDateTime, money, addDays } from '../ui.js';

export async function render(root, ctx) {
  root.append(pageHead('Mess Administration', 'Rates, monthly invoicing, payment verification, security deposits, final clearance, attendance and menu.'));
  const holder = h('div'); root.append(holder);
  tabs(holder, [{ key: 'b', label: 'Billing & rates', render: () => billing(ctx) }, { key: 'p', label: 'Payments to verify', render: () => payments(ctx) }, { key: 'd', label: 'Security deposits', render: () => deposits(ctx) },
    { key: 'c', label: 'Final clearance', render: () => clearances(ctx) }, { key: 'a', label: 'Attendance', render: attendance }, { key: 'm', label: 'Menu & nutrition', render: menu }, { key: 'f', label: 'Demand forecast', render: forecast }, { key: 'g', label: 'Gate-pass sync', render: gate }]);
}

async function billing(ctx) {
  const [rates, sum, dues] = await Promise.all([api.get('/api/mess/admin/rates'), api.get('/api/mess/admin/summary'), api.get('/api/mess/admin/dues')]);
  const wrap = h('div');
  const mode = h('select', null, [['per_meal', 'Per meal (rate for breakfast/lunch/dinner)'], ['per_day', 'Per day (one daily rate)']].map(([v, t]) => h('option', { value: v, selected: v === rates.billing_mode }, t)));
  const meal = select(['breakfast', 'lunch', 'dinner', 'daily'], rates.billing_mode === 'per_day' ? 'daily' : 'breakfast'); const rate = h('input', { type: 'number', min: 0, step: '0.01', placeholder: 'PKR' }); const eff = h('input', { type: 'date', value: addDays(0) });
  wrap.append(h('div', { class: 'grid g4' }, h('div', { class: 'card stat' }, h('div', { class: 'l' }, 'Invoiced'), h('div', { class: 'v' }, money(sum.invoiced_pkr))), h('div', { class: 'card stat' }, h('div', { class: 'l' }, 'Collected'), h('div', { class: 'v' }, money(sum.collected_pkr))),
    h('div', { class: 'card stat' }, h('div', { class: 'l' }, 'Outstanding'), h('div', { class: 'v' }, money(sum.outstanding_pkr))), h('div', { class: 'card stat' }, h('div', { class: 'l' }, 'Deposits held'), h('div', { class: 'v' }, money(sum.deposits_held_pkr)))));
  wrap.append(h('div', { class: 'card' }, h('h2', null, 'Mess charges'), rates.configured ? null : h('div', { class: 'alert warn' }, 'Rates are not configured for the active billing mode, so estimates and invoices cannot be calculated. No prices are assumed by the system.'),
    h('div', { class: 'row', style: 'margin-bottom:10px' }, h('div', { style: 'width:340px' }, field('Billing basis', mode)), h('button', { class: 'btn', onclick: async () => { try { await api.put('/api/admin/settings/mess_billing_mode', { value: mode.value }); toast('Billing basis updated.', 'ok'); ctx.refresh(); } catch (e) { toast(e.message, 'bad'); } } }, 'Save basis')),
    table([{ label: 'Meal', render: m => m }, { label: 'Rate in force today (PKR)', render: m => rates.effective_today[m] == null ? h('span', { class: 'muted' }, 'not set') : rates.effective_today[m] }], ['breakfast', 'lunch', 'dinner', 'daily']),
    h('div', { class: 'row', style: 'margin-top:12px;align-items:flex-end' }, h('div', { style: 'width:150px' }, field('Meal', meal)), h('div', { style: 'width:140px' }, field('Rate (PKR)', rate)), h('div', { style: 'width:170px' }, field('Effective from', eff)),
      h('button', { class: 'btn primary', style: 'margin-bottom:12px', onclick: async () => { try { await api.put('/api/mess/admin/rates', { meal_type: meal.value, rate_pkr: Number(rate.value), effective_from: eff.value }); toast('Rate saved.', 'ok'); ctx.refresh(); } catch (e) { toast(e.message, 'bad', 7000); } } }, 'Set rate')),
    h('p', { class: 'small muted' }, 'Rates are versioned by effective date and cannot be changed retroactively once invoices exist for that period. Each meal is priced at the rate in force on the day it was eaten.'),
    rates.history.length ? table([{ label: 'Meal', key: 'meal_type' }, { label: 'Rate', key: 'rate_pkr' }, { label: 'Effective', render: r => fmtDate(r.effective_from) }, { label: 'Set by', key: 'set_by' }], rates.history) : null));
  const month = h('input', { type: 'month', value: addDays(-28).slice(0, 7) });
  wrap.append(h('div', { class: 'card' }, h('h2', null, 'Monthly invoices'), h('div', { class: 'row' }, h('div', { style: 'width:190px' }, month),
    h('button', { class: 'btn primary', onclick: async () => { if (!(await confirmDialog('Generate invoices?', `Creates one immutable invoice per student who ate in ${month.value}. Existing invoices are never duplicated.`, 'Generate'))) return; try { const r = await api.post('/api/mess/admin/invoices/generate', { month: month.value }); toast(`${r.created} invoice(s) created; ${r.already_invoiced_or_no_meals} skipped.`, 'ok', 7000); ctx.refresh(); } catch (e) { toast(e.message, 'bad', 8000); } } }, 'Generate invoices for month'),
    h('span', { class: 'small muted' }, 'Also generated automatically after each month ends (once rates exist).')), h('div', { style: 'margin-top:12px', id: 'inv-list' })));
  const list = wrap.querySelector('#inv-list');
  const invs = await api.get('/api/mess/admin/invoices');
  list.append(table([{ label: 'Invoice', render: i => h('b', null, i.invoice_no) }, { label: 'Student', render: i => `${i.full_name} (${i.user_id})` }, { label: 'Days', key: 'days_count' }, { label: 'Total', render: i => money(i.total_pkr) }, { label: 'Paid', render: i => money(i.paid_pkr) }, { label: 'Balance', render: i => money(i.balance_pkr) }, { label: 'Status', render: i => badge(i.status, { unpaid: 'warn', partial: 'info', paid: 'ok' }[i.status]) },
    { label: '', render: i => h('button', { class: 'btn sm', onclick: () => adjust(i) }, 'Adjust') }], invs.slice(0, 100), { empty: 'No invoices generated yet.' }));
  wrap.append(h('div', { class: 'card' }, h('h2', null, 'Outstanding dues by student'), table([{ label: 'Student', render: d => `${d.full_name} (${d.user_id})` }, { label: 'Outstanding', render: d => money(d.outstanding_pkr) }, { label: 'Overdue', render: d => money(d.overdue_pkr) }, { label: 'Invoices', key: 'invoices' }], dues, { empty: 'No outstanding dues.' })));
  function adjust(i) {
    const amt = h('input', { type: 'number', step: '0.01', placeholder: 'e.g. -120 for a credit' }); const why = h('input', { placeholder: 'Reason (required, audited)', maxlength: 255 });
    modal({ title: `Adjust ${i.invoice_no}`, body: h('div', null, h('div', { class: 'alert warn' }, 'The original invoice stays unchanged; an authorised, audited adjustment is added. Positive = extra charge, negative = credit.'), field('Adjustment (PKR)', amt), field('Reason', why)),
      actions: [{ label: 'Cancel', onClick: c => c() }, { label: 'Apply adjustment', kind: 'primary', onClick: async (close) => { await api.post(`/api/mess/admin/invoices/${i.invoice_no}/adjust`, { amount_pkr: Number(amt.value), reason: why.value }); close(); toast('Adjustment recorded.', 'ok'); ctx.refresh(); } }] });
  }
  return wrap;
}

async function payments(ctx) {
  const rows = await api.get('/api/mess/admin/payments?status=submitted');
  return table([{ label: 'Submitted', render: p => fmtDateTime(p.submitted_at) }, { label: 'Student', render: p => `${p.full_name} (${p.user_id})` }, { label: 'Invoice', key: 'invoice_no' }, { label: 'Amount', render: p => money(p.amount_pkr) }, { label: 'Reference', key: 'reference' }, { label: 'Via', key: 'channel' },
    { label: '', render: p => h('span', { class: 'row' }, h('button', { class: 'btn sm primary', onclick: () => decide(p, true) }, 'Verify'), h('button', { class: 'btn sm danger', onclick: () => decide(p, false) }, 'Reject')) }], rows, { empty: 'No receipts awaiting verification.' });
  function decide(p, approve) { const note = h('input', { placeholder: approve ? 'Note (optional)' : 'Reason shown to the student' });
    modal({ title: `${approve ? 'Verify' : 'Reject'} receipt ${p.reference}`, body: h('div', null, h('p', null, `${money(p.amount_pkr)} against ${p.invoice_no}. Confirm only after checking the bank record.`), field('Note', note)),
      actions: [{ label: 'Cancel', onClick: c => c() }, { label: approve ? 'Verify payment' : 'Reject', kind: approve ? 'primary' : 'danger', onClick: async (close) => { await api.post(`/api/mess/admin/payments/${p.payment_id}/verify`, { approve, note: note.value }); close(); toast('Recorded.', 'ok'); ctx.refresh(); } }] }); }
}

async function deposits(ctx) {
  const [rows, pend, st] = await Promise.all([api.get('/api/mess/admin/deposits'), api.get('/api/mess/admin/deposits/pending'), api.get('/api/admin/settings')]);
  const cfg = st.find(s => s.key === 'mess_deposit_amount'); const amt = h('input', { type: 'number', min: 0, value: cfg.value, style: 'width:160px' });
  const wrap = h('div');
  wrap.append(h('div', { class: 'card' }, h('h2', null, 'Security deposit amount'), h('p', { class: 'muted' }, 'Refundable deposit held for the student\'s whole stay (not prepaid meals). 0 means not configured. Changing it does not alter existing student records.'),
    h('div', { class: 'row' }, amt, h('button', { class: 'btn primary', onclick: async () => { try { await api.put('/api/admin/settings/mess_deposit_amount', { value: amt.value }); toast('Deposit amount saved.', 'ok'); ctx.refresh(); } catch (e) { toast(e.message, 'bad'); } } }, 'Save'))));
  if (pend.length) wrap.append(h('div', { class: 'card' }, h('h2', null, 'Deposit payments awaiting verification'), table([{ label: 'Student', render: t => `${t.full_name} (${t.user_id})` }, { label: 'Amount', render: t => money(t.amount_pkr) }, { label: 'Reference', key: 'reference' }, { label: 'Recorded', render: t => fmtDateTime(t.recorded_at) },
    { label: '', render: t => h('span', { class: 'row' }, h('button', { class: 'btn sm primary', onclick: async () => { await api.post(`/api/mess/admin/deposit-txns/${t.txn_id}/verify`, { approve: true }); toast('Verified.', 'ok'); ctx.refresh(); } }, 'Verify'), h('button', { class: 'btn sm danger', onclick: async () => { await api.post(`/api/mess/admin/deposit-txns/${t.txn_id}/verify`, { approve: false }); toast('Rejected.', 'ok'); ctx.refresh(); } }, 'Reject')) }], pend)));
  wrap.append(h('div', { class: 'card' }, h('h2', null, 'Student deposit records'), table([{ label: 'Student', render: d => `${d.full_name} (${d.user_id})` }, { label: 'Status', render: d => badge(d.status.replace('_', ' '), { paid: 'ok', partial: 'info', unpaid: 'warn' }[d.status]) }, { label: 'Required', render: d => money(d.original_deposit_pkr) }, { label: 'Verified paid', render: d => money(d.verified_paid_pkr) },
    { label: 'Refundable', render: d => money(d.refundable_balance_pkr) }, { label: 'Dues', render: d => money(d.outstanding_bills_pkr) },
    { label: '', render: d => h('span', { class: 'row' }, h('button', { class: 'btn sm', onclick: () => detail(d) }, 'View'), h('button', { class: 'btn sm primary', onclick: () => record(d) }, 'Record payment')) }], rows)));
  async function detail(d) { const x = await api.get(`/api/mess/admin/deposits/${d.user_id}`);
    modal({ title: `Deposit — ${d.full_name}`, wide: true, body: table([{ label: 'Date', render: t => fmtDateTime(t.recorded_at) }, { label: 'Type', render: t => t.kind.replace('_', ' ') }, { label: 'Amount', render: t => money(t.amount_pkr) }, { label: 'Reference', render: t => t.reference || '—' }, { label: 'Status', render: t => badge(t.status) }, { label: 'Verified by', render: t => t.verified_by_name || '—' }], x.transactions, { empty: 'No transactions.' }) }); }
  function record(d) { const a = h('input', { type: 'number', step: '0.01', min: 0 }); const ref = h('input', { placeholder: 'Bank / receipt reference' }); const v = select([['false', 'Pending verification'], ['true', 'Verified']], 'false');
    modal({ title: `Record deposit payment — ${d.full_name}`, body: h('div', null, field('Amount (PKR)', a), field('Reference', ref), field('Verification', v)), actions: [{ label: 'Cancel', onClick: c => c() }, { label: 'Record', kind: 'primary', onClick: async (close) => { await api.post(`/api/mess/admin/deposits/${d.user_id}/payments`, { amount_pkr: Number(a.value), reference: ref.value, verified: v.value === 'true' }); close(); toast('Recorded.', 'ok'); ctx.refresh(); } }] }); }
  return wrap;
}

async function clearances(ctx) {
  const rows = await api.get('/api/mess/admin/clearances');
  const w = h('div'); w.append(h('div', { class: 'alert info' }, 'Workflow: student requests → you review/approve → settle (unpaid dues are deducted from the deposit, refundable amount reserved) → record the refund once it is actually returned. Nothing is paid out automatically.'));
  w.append(table([{ label: '#', key: 'clearance_id' }, { label: 'Student', render: c => `${c.full_name} (${c.user_id})` }, { label: 'Requested', render: c => fmtDateTime(c.requested_at) }, { label: 'Deposit', render: c => money(c.deposit_balance_pkr) }, { label: 'Dues', render: c => money(c.outstanding_pkr) }, { label: 'Refundable', render: c => money(c.refundable_pkr) }, { label: 'Additional due', render: c => money(c.additional_due_pkr) }, { label: 'Status', render: c => badge(c.status, { requested: 'warn', approved: 'info', settled: 'info', returned: 'ok', rejected: 'bad' }[c.status]) },
    { label: '', render: c => c.status === 'requested' ? h('span', { class: 'row' }, h('button', { class: 'btn sm primary', onclick: () => review(c, true) }, 'Approve'), h('button', { class: 'btn sm danger', onclick: () => review(c, false) }, 'Reject'))
      : c.status === 'approved' ? h('button', { class: 'btn sm primary', onclick: async () => { const p = await api.get(`/api/mess/admin/clearances/preview/${c.user_id}`); if (!(await confirmDialog('Process settlement?', `Deduct PKR ${(p.deposit_balance_pkr - p.refundable_pkr).toFixed(2)} of dues from the deposit; refundable PKR ${p.refundable_pkr.toFixed(2)}; student still owes PKR ${p.additional_due_pkr.toFixed(2)}.`, 'Settle'))) return; try { const r = await api.post(`/api/mess/admin/clearances/${c.clearance_id}/settle`); toast(`Settled. Refundable PKR ${r.refundable_pkr.toFixed(2)}.`, 'ok', 7000); ctx.refresh(); } catch (e) { toast(e.message, 'bad', 8000); } } }, 'Settle')
      : c.status === 'settled' && c.refundable_pkr > 0 ? h('button', { class: 'btn sm', onclick: () => returned(c) }, 'Record refund returned') : null }], rows, { empty: 'No clearance requests.' }));
  function review(c, approve) { const n = h('input', { placeholder: 'Note' }); modal({ title: `${approve ? 'Approve' : 'Reject'} clearance #${c.clearance_id}`, body: field('Note', n), actions: [{ label: 'Cancel', onClick: x => x() }, { label: approve ? 'Approve' : 'Reject', kind: approve ? 'primary' : 'danger', onClick: async (close) => { await api.post(`/api/mess/admin/clearances/${c.clearance_id}/review`, { approve, note: n.value }); close(); toast('Recorded.', 'ok'); ctx.refresh(); } }] }); }
  function returned(c) { const r = h('input', { placeholder: 'Cheque / transfer reference' }); modal({ title: `Refund returned — clearance #${c.clearance_id}`, body: field('Refund reference', r, `Refundable amount: ${money(c.refundable_pkr)}`), actions: [{ label: 'Record', kind: 'primary', onClick: async (close) => { await api.post(`/api/mess/admin/clearances/${c.clearance_id}/return`, { return_reference: r.value }); close(); toast('Refund recorded.', 'ok'); ctx.refresh(); } }] }); }
  return w;
}

async function attendance() {
  const date = h('input', { type: 'date', value: addDays(0) }); const out = h('div');
  async function load() { const r = await api.get('/api/mess/admin/attendance' + qs({ date: date.value }));
    clear(out).append(h('div', { class: 'row', style: 'margin:10px 0' }, Object.entries(r.counts).map(([k, v]) => badge(`${k}: ${v}`, 'info'))), table([{ label: 'Time', render: e => fmtDateTime(e.created_at) }, { label: 'Student', render: e => `${e.full_name} (${e.user_id})` }, { label: 'Meal', key: 'meal_type' }, { label: 'Status', render: e => badge(e.status) }, { label: 'Source', key: 'source' }], r.entries, { empty: 'No entries for this date.' })); }
  date.addEventListener('change', load); const w = h('div', null, h('div', { style: 'width:200px' }, date), out); await load(); return w;
}

async function menu() {
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
  return h('div', { class: 'card' }, h('h2', null, 'Gate-pass synchronisation'), h('div', { class: 'alert info' }, 'Gate-passes arrive from the campus security gate controller via POST /api/mess/gate-pass-sync (X-Gate-Key). This form performs the same call manually. Gate-passes are informational records; they do not affect billing. No gate hardware is controlled from this portal.'),
    h('div', { class: 'form-grid' }, field('Student ID', f.user_id), field('Destination', f.destination), field('Leave from', f.leave_start), field('Leave to', f.leave_end), field('External reference', f.external_ref)),
    h('button', { class: 'btn primary', onclick: async () => { try { await api.post('/api/mess/gate-pass-sync', { user_id: f.user_id.value.trim(), leave_start: f.leave_start.value, leave_end: f.leave_end.value, destination: f.destination.value || null, external_ref: f.external_ref.value || null, approved: true }); toast('Gate-pass synchronised.', 'ok'); } catch (e) { toast(e.message, 'bad'); } } }, 'Sync gate-pass'));
}
