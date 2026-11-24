import { api, qs } from '../api.js';
import { h, clear, table, badge, tabs, modal, confirmDialog, toast, field, select, pageHead, fmtDate } from '../ui.js';

export async function render(root, ctx) {
  root.append(pageHead('Lab Equipment', 'Borrow and return equipment by barcode. Every movement is recorded in the custody trail.'));
  const holder = h('div'); root.append(holder);
  tabs(holder, [{ key: 'mine', label: 'My equipment', render: () => mine(ctx) }, { key: 'inv', label: 'Inventory', render: () => inventory(ctx) }, { key: 'log', label: 'Custody history', render: log }]);
}

async function mine(ctx) {
  const rows = await api.get('/api/req/equipment/mine');
  return table([{ label: 'Barcode', render: r => h('code', null, r.barcode) }, { label: 'Item', key: 'name' }, { label: 'Lab', key: 'lab' }, { label: 'Since', render: r => fmtDate(r.checkout_date) },
    { label: 'Due', render: r => h('span', null, fmtDate(r.due_date), ' ', r.overdue ? badge('overdue') : null) }, { label: 'Purpose', key: 'purpose' },
    { label: '', render: r => h('button', { class: 'btn sm', onclick: () => ret(r, ctx) }, 'Return') }], rows, { empty: 'You are not holding any equipment.' });
}

function ret(r, ctx) {
  const cond = select(['good', 'fair', 'damaged'], 'good'); const note = h('input', { placeholder: 'Optional note' });
  modal({ title: `Return ${r.name}`, body: h('div', null, field('Condition on return', cond, 'Damaged items are moved to repair and flagged to lab staff.'), field('Note', note)),
    actions: [{ label: 'Cancel', onClick: c => c() }, { label: 'Return item', kind: 'primary', onClick: async (close) => { const x = await api.post(`/api/req/equipment/${r.barcode}/return`, { condition: cond.value, note: note.value }); close(); toast(x.message, 'ok'); ctx.refresh(); } }] });
}

async function inventory(ctx) {
  const wrap = h('div'); const out = h('div'); const q = h('input', { type: 'search', placeholder: 'Search name, barcode or category…' }); const st = select([['', 'Any status'], 'available', 'in_custody', 'under_repair'], '');
  async function load() {
    const rows = await api.get('/api/req/equipment' + qs({ q: q.value, status: st.value }));
    clear(out).append(table([{ label: 'Barcode', render: r => h('code', null, r.barcode) }, { label: 'Item', key: 'name' }, { label: 'Lab', key: 'lab' }, { label: 'Category', key: 'category' }, { label: 'Condition', render: r => badge(r.condition) },
      { label: 'Status', render: r => h('span', null, badge(r.status), r.status === 'in_custody' && r.due_date ? h('div', { class: 'small muted' }, `due ${fmtDate(r.due_date)}`) : null) },
      { label: '', render: r => r.status === 'available' && r.condition !== 'damaged' ? h('button', { class: 'btn sm primary', onclick: () => borrow(r) }, 'Borrow') : null }], rows));
  }
  function borrow(r) {
    const purpose = h('input', { placeholder: 'e.g. Digital design lab project', maxlength: 255 }); const days = h('input', { type: 'number', min: 1, max: 14, value: 7 });
    modal({ title: `Borrow ${r.name}`, body: h('div', null, h('p', { class: 'small muted' }, `${r.barcode} · ${r.lab}`), field('Purpose', purpose), field('Loan period (days)', days, 'Limits: 3 items at a time, 14 days; overdue items block new loans.')),
      actions: [{ label: 'Cancel', onClick: c => c() }, { label: 'Borrow', kind: 'primary', onClick: async (close) => { const x = await api.post(`/api/req/equipment/${r.barcode}/checkout`, { purpose: purpose.value, days: Number(days.value) }); close(); toast(x.message, 'ok', 7000); ctx.refresh(); } }] });
  }
  q.addEventListener('input', () => { clearTimeout(q._t); q._t = setTimeout(load, 250); }); st.addEventListener('change', load);
  wrap.append(h('div', { class: 'row', style: 'margin-bottom:12px' }, h('div', { style: 'flex:1;min-width:220px' }, q), st), out); await load(); return wrap;
}

async function log() {
  const rows = await api.get('/api/req/equipment/log');
  return table([{ label: 'When', render: r => new Date(r.at).toLocaleString() }, { label: 'Barcode', render: r => h('code', null, r.barcode) }, { label: 'Item', key: 'name' }, { label: 'Action', render: r => badge(r.action, r.action === 'checkout' ? 'warn' : 'ok') }, { label: 'Condition', key: 'condition' }, { label: 'Via', key: 'channel' }, { label: 'Note', key: 'note' }], rows, { empty: 'No custody events yet.' });
}
