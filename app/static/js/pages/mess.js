import { api, qs } from '../api.js';
import { h, clear, table, badge, tabs, modal, confirmDialog, toast, field, select, form, pageHead, fmtDate, fmtDateTime, money, addDays, errorBox } from '../ui.js';

export async function render(root, ctx) {
  root.append(pageHead('Mess', 'Mess-out requests, rebates, your dining QR, meals and menu.'));
  const holder = h('div'); root.append(holder);
  tabs(holder, [
    { key: 'out', label: 'Mess-out & rebate', render: () => messOut(ctx) },
    { key: 'qr', label: 'Dining QR', render: qr },
    { key: 'ledger', label: 'Rebate ledger', render: ledger },
    { key: 'meals', label: 'Meal records', render: meals },
    { key: 'menu', label: 'Menu, macros & allergens', render: menu },
  ]);
}

async function messOut(ctx) {
  const prof = await api.get('/api/mess/me/profile');
  const rules = prof.rules;
  const wrap = h('div');
  const start = h('input', { type: 'date', name: 'start_date', required: true, min: addDays(Number(rules.mess_notice_days || 1)), value: addDays(Number(rules.mess_notice_days || 1) + 1) });
  const end = h('input', { type: 'date', name: 'end_date', required: true, value: addDays(Number(rules.mess_notice_days || 1) + 3) });
  const reason = h('input', { name: 'reason', maxlength: 255, placeholder: 'e.g. Going home for the weekend' });
  const prev = h('div');
  async function preview() {
    if (!start.value || !end.value) return;
    try {
      const p = await api.post('/api/mess/mess-out/preview', { start_date: start.value, end_date: end.value });
      clear(prev).append(h('div', { class: `alert ${p.eligible ? 'ok' : 'bad'}` },
        h('b', null, `${p.days} day(s) · rebate ${money(p.rebate_pkr)}`), ` (PKR ${p.daily_rate_pkr}/day)`, h('br'),
        p.eligible ? `If submitted: ${p.outcome_if_submitted}.${p.gate_pass_verified ? ` Gate-pass to ${p.gate_pass_destination || 'destination'} verified.` : ''}` : p.problems.join(' ')));
    } catch (e) { clear(prev).append(errorBox(e)); }
  }
  start.addEventListener('change', preview); end.addEventListener('change', preview);
  wrap.append(h('div', { class: 'grid g2' },
    h('div', { class: 'card' }, h('h2', null, 'Request a mess-out'),
      h('p', { class: 'muted small' }, `Rules: at least ${rules.mess_min_days} consecutive days, ${rules.mess_notice_days} day(s) notice, PKR ${rules.mess_daily_rate}/day rebate. Requests covered by a security gate-pass are approved automatically; others go to the Mess Office.`),
      h('form', { onsubmit: async (e) => {
        e.preventDefault();
        if (!(await confirmDialog('Submit mess-out request?', `${start.value} to ${end.value}. The server re-checks every rule when you submit.`, 'Submit'))) return;
        try { const r = await api.post('/api/mess/mess-out', { start_date: start.value, end_date: end.value, reason: reason.value }); toast(r.message, 'ok', 7000); ctx.refresh(); } catch (er) { toast(er.message, 'bad', 7000); }
      } }, h('div', { class: 'form-grid' }, field('From', start), field('To (inclusive)', end)), field('Reason (optional)', reason), prev, h('button', { class: 'btn primary', type: 'submit' }, 'Submit request'))),
    h('div', { class: 'card' }, h('h2', null, 'Security gate-passes'), table([{ label: 'Destination', render: g => g.destination || '—' }, { label: 'Leave', render: g => g.leave_start ? `${fmtDate(g.leave_start)} – ${fmtDate(g.leave_end)}` : '—' }, { label: 'Approved', render: g => badge(g.approved ? 'approved' : 'pending') }], prof.gate_passes, { empty: 'No gate-pass synchronised from campus security.' }),
      h('p', { class: 'small muted' }, 'Gate-passes are pushed by the campus security gate controller (REST integration); you cannot create them here.'))));
  preview();
  const rows = await api.get('/api/mess/mess-out');
  wrap.append(h('div', { class: 'card' }, h('h2', null, 'My mess-out periods'), table([
    { label: '#', key: 'request_id' }, { label: 'Dates', render: r => `${fmtDate(r.start_date)} – ${fmtDate(r.end_date)}` }, { label: 'Days', key: 'days_count' },
    { label: 'Rebate', render: r => r.credited_pkr != null ? `${money(r.credited_pkr)} credited` : `${money(r.est_rebate_pkr)} est.` },
    { label: 'Status', render: r => h('span', null, badge(r.status), ' ', r.phase ? badge(r.phase) : null) }, { label: 'Via', render: r => r.channel },
    { label: 'Note', render: r => r.decision_note || r.reason || '' },
    { label: '', render: r => (['pending', 'approved'].includes(r.status) && new Date(r.start_date) > new Date()) ? h('button', { class: 'btn sm danger', onclick: async () => {
      if (!(await confirmDialog('Cancel mess-out?', 'Any credited rebate will be reversed.', 'Cancel request', 'danger'))) return;
      try { const x = await api.post(`/api/mess/mess-out/${r.request_id}/cancel`); toast(x.message, 'ok'); ctx.refresh(); } catch (e) { toast(e.message, 'bad'); } } }, 'Cancel') : null }], rows, { empty: 'No mess-out requests yet.' })));
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

async function ledger() {
  const r = await api.get('/api/mess/me/rebates');
  return h('div', null, h('div', { class: 'row', style: 'margin-bottom:12px' }, h('b', null, 'Total credited: '), money(r.total_credited_pkr), h('span', { class: 'muted' }, `· ${r.total_days} rebated day(s)`)),
    table([{ label: 'Rebate', key: 'rebate_id' }, { label: 'Period', render: x => `${fmtDate(x.start_date)} – ${fmtDate(x.end_date)}` }, { label: 'Days', key: 'days_count' }, { label: 'Amount', render: x => money(x.rebate_amount_pkr) },
      { label: 'Gate-pass synced', render: x => x.gate_pass_synced ? 'Yes' : 'No' }, { label: 'Status', render: x => badge(x.status) }, { label: 'Posted', render: x => fmtDateTime(x.applied_at) }], r.transactions, { empty: 'No rebate transactions yet.' }));
}

async function meals() {
  const r = await api.get('/api/mess/me/meals?days=30');
  return h('div', null,
    h('div', { class: 'grid g3' }, h('div', { class: 'card stat' }, h('div', { class: 'l' }, 'Meals eaten (30d)'), h('div', { class: 'v' }, r.summary.meals_consumed)),
      h('div', { class: 'card stat' }, h('div', { class: 'l' }, 'Meals rebated'), h('div', { class: 'v' }, r.summary.meals_rebated)), h('div', { class: 'card stat' }, h('div', { class: 'l' }, 'Avg daily calories'), h('div', { class: 'v' }, r.summary.avg_daily_calories))),
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
