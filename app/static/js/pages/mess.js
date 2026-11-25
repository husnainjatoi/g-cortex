import { api, qs } from '../api.js';
import { h, put, clear, table, badge, tabs, modal, confirmDialog, toast, field, select, form, pageHead, fmtDate, fmtDateTime, money, addDays, errorBox } from '../ui.js';

export async function render(root, ctx) {
  root.append(pageHead('Mess', 'Your estimated and finalized monthly bills, payments, meal attendance and refundable security deposit.'));
  const holder = h('div'); root.append(holder);
  tabs(holder, [
    { key: 'bill', label: 'My bill', render: () => bill(ctx) },
    { key: 'inv', label: 'Invoices & payments', render: () => invoices(ctx) },
    { key: 'dep', label: 'Security deposit', render: () => deposit(ctx) },
    { key: 'qr', label: 'Dining QR', render: qr },
    { key: 'meals', label: 'Meal attendance', render: meals },
    { key: 'menu', label: 'Menu, macros & allergens', render: menu },
    { key: 'out', label: 'Mess-out', render: () => messOut(ctx) },
  ]);
}

const statusKind = { unpaid: 'warn', partial: 'info', paid: 'ok' };

async function bill(ctx) {
  const b = await api.get('/api/mess/me/bill');
  const wrap = h('div'); const body = h('div');
  const fin = b.kind === 'finalized';
  const draw = (b) => {
    const fin = b.kind === 'finalized';
    put(body,
      fin ? h('div', { class: 'alert ok' }, h('b', null, `FINALIZED invoice for ${b.month}. `), b.explanation)
          : [h('div', { class: 'alert info', id: 'estimate-disclaimer' }, h('b', null, `ESTIMATE for ${b.month} — not an invoice, nothing is payable. `), b.disclaimer), b.amount_pkr == null ? h('div', { class: 'alert warn' }, b.explanation) : null],
      h('div', { class: 'grid g4' },
        h('div', { class: 'card stat' }, h('div', { class: 'l' }, fin ? 'Invoice total' : 'Estimated bill per student'), h('div', { class: 'v', id: 'bill-amount' }, b.amount_pkr == null ? 'Not available yet' : money(b.amount_pkr)),
          h('div', { class: 's' }, fin ? `${b.invoice.status} · balance ${money(b.invoice.balance_pkr)}` : 'estimate only')),
        h('div', { class: 'card stat' }, h('div', { class: 'l' }, 'Total food expenditure'), h('div', { class: 'v' }, b.expense_entries ? money(b.total_food_expenditure_pkr) : '—'), h('div', { class: 's' }, b.expense_entries ? `recorded ${fmtDate(b.expenditure_updated_at)}` : 'not recorded by the Mess Office yet')),
        h('div', { class: 'card stat' }, h('div', { class: 'l' }, 'Students using the mess'), h('div', { class: 'v' }, b.students_using_mess), h('div', { class: 's' }, 'distinct students with verified entries')),
        h('div', { class: 'card stat' }, h('div', { class: 'l' }, 'Your meals this month'), h('div', { class: 'v' }, b.days_consumed), h('div', { class: 's' }, `day(s) · ${b.meals.breakfast} breakfast · ${b.meals.lunch} lunch · ${b.meals.dinner} dinner`))),
      h('div', { class: 'card' }, h('h2', null, 'How this is calculated'),
        h('p', null, h('b', null, 'Estimated bill per student = total food expenditure ÷ number of distinct verified students using the mess.')),
        b.expense_entries && b.students_using_mess ? h('p', null, `${money(b.total_food_expenditure_pkr)} ÷ ${b.students_using_mess} students = ${money(b.estimated_per_student_pkr)}${b.days_consumed ? '' : ' (you have no verified meals this month, so your estimate is PKR 0)'}`) : h('p', { class: 'muted' }, 'The calculation appears once the Mess Office has recorded the month\'s food expenditure and at least one student has a verified meal entry.'),
        h('p', { class: 'small muted' }, 'Only verified entries are counted. This figure is calculated by the server; the portal does not compute bills itself.'), b.expenditure_updated_at ? h('p', { class: 'small muted' }, `Expenditure last updated ${fmtDateTime(b.expenditure_updated_at)}.`) : null,
        h('p', { class: 'small' }, h('b', null, 'Your PKR 15,000 security deposit is separate: '), 'it is refundable, is not a prepaid balance and is never used to pay monthly bills automatically.')),
      b.previous_invoice ? h('div', { class: 'card flat' }, h('b', null, 'Previous invoice: '), `${b.previous_invoice.billing_month_label} · ${money(b.previous_invoice.total_pkr)} · ${b.previous_invoice.status} · balance ${money(b.previous_invoice.balance_pkr)}`) : null,
      h('div', { class: 'card flat' }, h('b', null, 'Outstanding on all finalized invoices: '), money(b.outstanding_total_pkr), ' ', b.outstanding_total_pkr > 0 ? h('a', { href: '#/mess', onclick: (e) => { e.preventDefault(); document.querySelectorAll('.tabs button')[1].click(); } }, 'Pay / submit receipt') : null));
  };
  draw(b);
  const month = h('input', { type: 'month', value: b.month });
  wrap.append(h('div', { class: 'row', style: 'margin-bottom:12px' }, h('div', { style: 'width:190px' }, month), h('button', { class: 'btn', onclick: async () => { try { draw(await api.get('/api/mess/me/bill?month=' + month.value)); } catch (e) { toast(e.message, 'bad'); } } }, 'Show this month')), body);
  return wrap;
}

function challanView(c) {
  modal({ title: `Payment challan ${c.challan_no}`, body: h('div', null,
    h('div', { class: 'card flat' }, h('p', null, h('b', null, 'Student: '), `${c.student} (${c.student_id})`), h('p', null, h('b', null, 'Invoice: '), `${c.invoice_no} (${c.month})`),
      h('p', null, h('b', null, 'Amount to pay: '), money(c.amount_pkr)), h('p', null, h('b', null, 'Due date: '), fmtDate(c.due_date)), h('p', null, h('b', null, 'Issued: '), fmtDateTime(c.issued_at))),
    h('div', { class: 'alert info' }, c.instructions)), actions: [{ label: 'Print', onClick: () => { window.print(); return false; } }, { label: 'Close', kind: 'primary', onClick: (x) => x() }] });
}

async function invoices(ctx) {
  const rows = await api.get('/api/mess/me/invoices'); const pays = await api.get('/api/mess/me/payments');
  const wrap = h('div');
  const due = rows.reduce((a, i) => a + Number(i.balance_pkr), 0), pend = rows.reduce((a, i) => a + Number(i.pending_pkr || 0), 0);
  wrap.append(h('div', { class: 'grid g3' }, h('div', { class: 'card stat' }, h('div', { class: 'l' }, 'Outstanding'), h('div', { class: 'v' }, money(due))), h('div', { class: 'card stat' }, h('div', { class: 'l' }, 'Pending verification'), h('div', { class: 'v' }, money(pend)), h('div', { class: 's' }, 'receipts awaiting the Mess Office')), h('div', { class: 'card stat' }, h('div', { class: 'l' }, 'Paid invoices'), h('div', { class: 'v' }, rows.filter(i => i.status === 'paid').length))));
  wrap.append(h('div', { class: 'card' }, h('h2', null, 'Monthly invoices'), table([
    { label: 'Invoice', render: i => h('b', null, i.invoice_no) }, { label: 'Month', key: 'billing_month_label' }, { label: 'Generated', render: i => fmtDate(i.generated_at) }, { label: 'Days', key: 'days_count' }, { label: 'Amount', render: i => money(i.total_pkr) },
    { label: 'Paid', render: i => money(i.paid_pkr) }, { label: 'Balance', render: i => money(i.balance_pkr) }, { label: 'Due', render: i => h('span', null, fmtDate(i.due_date), ' ', i.overdue ? badge('overdue') : null) },
    { label: 'Status', render: i => badge(i.status, statusKind[i.status]) },
    { label: '', render: i => h('span', { class: 'row' }, h('button', { class: 'btn sm', onclick: () => detail(i) }, 'Details'),
        i.balance_pkr > 0 ? h('button', { class: 'btn sm', onclick: async () => { try { challanView(await api.post(`/api/mess/invoices/${i.invoice_no}/challan`)); } catch (e) { toast(e.message, 'bad'); } } }, 'Challan') : null,
        i.balance_pkr - i.pending_pkr > 0 ? h('button', { class: 'btn sm primary', onclick: () => receipt(i) }, 'Submit receipt') : null) }], rows, { empty: 'No invoices yet. Invoices are generated after month end from the meals you ate.' })));
  wrap.append(h('div', { class: 'card' }, h('h2', null, 'Payment history'), table([{ label: 'Submitted', render: p => fmtDateTime(p.submitted_at) }, { label: 'Invoice', key: 'invoice_no' }, { label: 'Amount', render: p => money(p.amount_pkr) }, { label: 'Reference', key: 'reference' }, { label: 'Method', render: p => p.method.replace('_', ' ') }, { label: 'Status', render: p => badge(p.status) }, { label: 'Note', render: p => p.verify_note || '' }], pays, { empty: 'No payments submitted.' })));
  async function detail(i) {
    const d = await api.get(`/api/mess/invoices/${i.invoice_no}`);
    modal({ title: d.invoice_no, wide: true, body: h('div', null, h('p', { class: 'muted' }, `Finalized ${fmtDateTime(d.generated_at)} · ${d.billing_mode.replace('_', ' ')} billing`),
      table([{ label: 'Item', key: 'label' }, { label: 'Count', key: 'count' }, { label: 'Rate', key: 'rate_pkr' }, { label: 'Amount', render: l => money(l.amount_pkr) }], d.lines),
      h('p', null, h('b', null, 'Invoice amount: '), money(d.amount_pkr), d.adjustments_pkr ? ` · adjustments ${money(d.adjustments_pkr)}` : '', ` · total ${money(d.total_pkr)} · paid ${money(d.paid_pkr)} · balance ${money(d.balance_pkr)}`),
      d.adjustment_history.length ? h('div', null, h('h3', null, 'Authorised adjustments'), table([{ label: 'When', render: a => fmtDateTime(a.at) }, { label: 'Amount', render: a => money(a.amount_pkr) }, { label: 'Reason', key: 'reason' }], d.adjustment_history)) : null) });
  }
  function receipt(i) {
    const ref = h('input', { placeholder: 'Bank transaction / receipt reference', maxlength: 80 }); const amt = h('input', { type: 'number', step: '0.01', value: (i.balance_pkr - i.pending_pkr).toFixed(2) });
    modal({ title: `Submit payment receipt for ${i.invoice_no}`, body: h('div', null, h('div', { class: 'alert info' }, 'G-Cortex does not take payments. Pay with the bank challan, then enter the bank\'s reference here. The Mess Office verifies it before your balance changes.'),
      field('Amount paid (PKR)', amt), field('Receipt / transaction reference', ref)),
      actions: [{ label: 'Cancel', onClick: c => c() }, { label: 'Submit receipt', kind: 'primary', onClick: async (close) => { const r = await api.post(`/api/mess/invoices/${i.invoice_no}/payments`, { amount_pkr: Number(amt.value), reference: ref.value }); close(); toast(r.message, 'ok', 7000); ctx.refresh(); } }] });
  }
  return wrap;
}

async function deposit(ctx) {
  const d = await api.get('/api/mess/me/deposit'); const wrap = h('div');
  wrap.append(h('div', { class: 'alert info' }, d.note));
  wrap.append(h('div', { class: 'grid g4' },
    h('div', { class: 'card stat' }, h('div', { class: 'l' }, 'Original deposit'), h('div', { class: 'v' }, d.status === 'not_configured' ? '—' : money(d.original_deposit_pkr)), h('div', { class: 's' }, badge(d.status.replace('_', ' '), { paid: 'ok', partial: 'info', unpaid: 'warn', refunded: 'mute', not_configured: 'mute' }[d.status]))),
    h('div', { class: 'card stat' }, h('div', { class: 'l' }, 'Verified paid'), h('div', { class: 'v' }, money(d.verified_paid_pkr)), h('div', { class: 's' }, d.pending_verification_pkr ? `${money(d.pending_verification_pkr)} awaiting verification` : 'Payment verified by the Mess Office')),
    h('div', { class: 'card stat' }, h('div', { class: 'l' }, 'Refundable balance'), h('div', { class: 'v' }, money(d.refundable_balance_pkr)), h('div', { class: 's' }, d.deductions_pkr ? `${money(d.deductions_pkr)} deducted at settlement` : 'No deductions')),
    h('div', { class: 'card stat' }, h('div', { class: 'l' }, 'Outstanding bills'), h('div', { class: 'v' }, money(d.outstanding_bills_pkr)), h('div', { class: 's' }, 'Settle monthly; deducted from the deposit only at final clearance'))));
  wrap.append(h('div', { class: 'card' }, h('h2', null, 'Deposit transactions'), table([{ label: 'Date', render: t => fmtDateTime(t.recorded_at) }, { label: 'Type', render: t => t.kind.replace('_', ' ') }, { label: 'Amount', render: t => money(t.amount_pkr) }, { label: 'Reference', render: t => t.reference || '—' }, { label: 'Status', render: t => badge(t.status) }, { label: 'Note', render: t => t.note || '' }], d.transactions, { empty: 'No deposit transactions recorded.' })));
  const clr = d.clearance;
  const box = h('div', { class: 'card' }, h('h2', null, 'Final clearance (graduation)'));
  if (clr) box.append(h('p', null, 'Status: ', badge(clr.status), ` · requested ${fmtDateTime(clr.requested_at)}`), clr.status !== 'requested' ? h('p', null, `Deposit PKR ${Number(clr.deposit_balance_pkr).toFixed(2)} · deductions PKR ${Number(clr.deductions_pkr || 0).toFixed(2)} · refundable PKR ${Number(clr.refundable_pkr || 0).toFixed(2)} · additional dues PKR ${Number(clr.additional_due_pkr || 0).toFixed(2)}`) : null, clr.return_reference ? h('p', null, `Refund reference: ${clr.return_reference}`) : null, clr.note ? h('p', { class: 'muted' }, clr.note) : null);
  const p = await api.get('/api/mess/me/clearance/preview');
  box.append(h('div', { class: 'alert info' }, h('b', null, 'If you cleared today: '), `deposit PKR ${p.deposit_balance_pkr.toFixed(2)} − unpaid bills PKR ${p.outstanding_invoices_pkr.toFixed(2)} − unbilled meals PKR ${p.uninvoiced_consumption_pkr.toFixed(2)} = refundable PKR ${p.refundable_pkr.toFixed(2)}`, p.additional_due_pkr > 0 ? ` (you would still owe PKR ${p.additional_due_pkr.toFixed(2)})` : '', p.rates_missing.length ? ' — rates are not configured, so unbilled meals cannot be priced yet' : ''));
  if (!clr || ['rejected', 'returned'].includes(clr.status)) box.append(h('button', { class: 'btn primary', onclick: async () => { if (!(await confirmDialog('Request final mess clearance?', 'The Mess Office will review your deposit and dues. No refund is issued automatically.', 'Request clearance'))) return; try { const r = await api.post('/api/mess/me/clearance'); toast(r.message, 'ok', 7000); ctx.refresh(); } catch (e) { toast(e.message, 'bad'); } } }, 'Request final clearance'));
  wrap.append(box); return wrap;
}

async function messOut(ctx) {
  const wrap = h('div'); const start = h('input', { type: 'date', value: addDays(1) }); const end = h('input', { type: 'date', value: addDays(3) }); const reason = h('input', { maxlength: 255, placeholder: 'Optional reason' });
  wrap.append(h('div', { class: 'card' }, h('h2', null, 'Declare a mess-out'), h('div', { class: 'alert info' }, 'A mess-out tells the kitchen you will be away so it can plan portions. It does not change your bill — you are charged only for meals you eat.'),
    h('form', { onsubmit: async (e) => { e.preventDefault(); try { const r = await api.post('/api/mess/mess-out', { start_date: start.value, end_date: end.value, reason: reason.value }); toast(r.message, 'ok', 6000); ctx.refresh(); } catch (er) { toast(er.message, 'bad', 7000); } } },
      h('div', { class: 'form-grid' }, field('From', start), field('To (inclusive)', end)), field('Reason', reason), h('button', { class: 'btn primary', type: 'submit' }, 'Record mess-out'))));
  const rows = await api.get('/api/mess/mess-out');
  wrap.append(h('div', { class: 'card' }, h('h2', null, 'My mess-out records'), table([{ label: '#', key: 'request_id' }, { label: 'Dates', render: r => `${fmtDate(r.start_date)} – ${fmtDate(r.end_date)}` }, { label: 'Days', key: 'days_count' }, { label: 'Status', render: r => h('span', null, badge(r.status === 'approved' ? 'recorded' : r.status, r.status === 'approved' ? 'ok' : undefined), ' ', r.phase ? badge(r.phase) : null) }, { label: 'Via', key: 'channel' },
    { label: '', render: r => (['approved', 'pending'].includes(r.status) && new Date(r.start_date) > new Date()) ? h('button', { class: 'btn sm danger', onclick: async () => { try { await api.post(`/api/mess/mess-out/${r.request_id}/cancel`); toast('Cancelled.', 'ok'); ctx.refresh(); } catch (e) { toast(e.message, 'bad'); } } }, 'Cancel') : null }], rows, { empty: 'No mess-out records.' })));
  return wrap;
}

async function qr() {
  const wrap = h('div'); const out = h('div'); let timer, meal = '';
  const sel = select([['', 'Current meal'], 'breakfast', 'lunch', 'dinner'], '');
  async function load() {
    clearInterval(timer);
    try {
      const r = await api.get('/api/mess/qr' + qs({ meal: sel.value }));
      let left = r.expires_in_seconds; const cd = h('b', null, `${left}s`); const bar = h('i', { style: 'width:100%' });
      clear(out).append(h('div', { class: 'qr-box' }, h('div', { html: r.svg }), h('div', { class: 'col' },
        h('div', { class: 'muted small' }, `${r.meal.toUpperCase()} · single-use code`), h('div', { class: 'code' }, r.code.replace(/(\d{4})(\d{4})/, '$1 $2')),
        h('div', null, 'Refreshes in ', cd), h('div', { class: 'bar', style: 'width:230px' }, bar),
        h('p', { class: 'small muted', style: 'max-width:300px' }, 'Show this at the turnstile. The code rotates every 30 seconds and can be redeemed once, so screenshots and card-sharing do not work.'))));
      timer = setInterval(() => { left -= 1; if (!document.body.contains(out)) return clearInterval(timer); cd.textContent = `${Math.max(left, 0)}s`; bar.style.width = `${Math.max(0, 100 * left / r.step_seconds)}%`; if (left <= 0) load(); }, 1000);
    } catch (e) { clear(out).append(h('div', { class: 'alert warn' }, e.message)); }
  }
  sel.addEventListener('change', load);
  wrap.append(h('div', { class: 'card' }, h('div', { class: 'row', style: 'margin-bottom:14px' }, h('div', { style: 'width:200px' }, sel), h('button', { class: 'btn', onclick: load }, 'Refresh code')), out));
  load(); return wrap;
}

async function meals() {
  const r = await api.get('/api/mess/me/meals?days=30');
  return h('div', null,
    h('div', { class: 'grid g2' }, h('div', { class: 'card stat' }, h('div', { class: 'l' }, 'Meals eaten (30d)'), h('div', { class: 'v' }, r.summary.meals_consumed)),
      h('div', { class: 'card stat' }, h('div', { class: 'l' }, 'Avg daily calories'), h('div', { class: 'v' }, r.summary.avg_daily_calories))),
    h('div', { class: 'grid g2' }, h('div', null, h('h3', null, 'Daily nutrition (from turnstile entries)'), table([{ label: 'Date', render: d => fmtDate(d.date) }, { label: 'Meals', key: 'meals' }, { label: 'kcal', key: 'calories' }, { label: 'Protein g', key: 'protein_g' }, { label: 'Carbs g', key: 'carbs_g' }, { label: 'Fat g', key: 'fat_g' }], r.daily_nutrition, { empty: 'No consumed meals yet.' })),
      h('div', null, h('h3', null, 'Meal log'), table([{ label: 'Date', render: d => fmtDate(d.meal_date) }, { label: 'Meal', key: 'meal_type' }, { label: 'Status', render: d => badge(d.status) }, { label: 'Source', key: 'source' }], r.records, { empty: 'No records.' }))));
}

async function menu() {
  const r = await api.get('/api/mess/menu');
  const al = h('input', { value: r.student_allergies && r.student_allergies !== 'none' ? r.student_allergies : '', placeholder: 'e.g. dairy, eggs, gluten (comma separated)' });
  const wrap = h('div');
  wrap.append(h('div', { class: 'card' }, h('h2', null, 'My dietary profile'), h('div', { class: 'row' }, h('div', { style: 'flex:1;min-width:240px' }, al),
    h('button', { class: 'btn primary', onclick: async () => { try { await api.put('/api/mess/me/allergies', { allergies: al.value }); toast('Allergy profile saved.', 'ok'); const m = document.querySelector('.tabs .active'); m && m.click(); } catch (e) { toast(e.message, 'bad'); } } }, 'Save allergies')),
    h('p', { class: 'hint' }, 'The allergen shield compares each meal against this list.')));
  wrap.append(h('div', { class: 'grid g3' }, r.meals.map(m => h('div', { class: 'card' }, h('h2', null, m.meal_period[0].toUpperCase() + m.meal_period.slice(1)),
    h('p', null, m.items.join(' · ')), h('div', { class: 'row small' }, badge(`${m.calories} kcal`, 'info'), badge(`P ${m.protein_g}g`, 'mute'), badge(`C ${m.carbs_g}g`, 'mute'), badge(`F ${m.fat_g}g`, 'mute')),
    h('p', { class: 'small muted' }, 'Allergens: ', m.allergens.join(', ') || 'none'), h('div', { class: `alert ${m.allergen_shield.safe ? 'ok' : 'bad'}`, style: 'margin:0' }, m.allergen_shield.message)))));
  return wrap;
}
