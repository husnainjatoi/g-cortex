import { api, qs } from '../api.js';
import { h, clear, table, badge, tabs, modal, toast, field, select, pageHead, fmtDate } from '../ui.js';

export async function render(root, ctx) {
  root.append(pageHead('Equipment Inventory', 'Inventory, condition, custody and overdue tracking.'));
  const holder = h('div'); root.append(holder);
  tabs(holder, [{ key: 'i', label: 'Inventory & custody', render: () => inventory(ctx) }, { key: 'l', label: 'Custody log', render: log }]);
}
async function inventory(ctx) {
  const rows = await api.get('/api/req/equipment'); const wrap = h('div');
  function edit(e) {
    const isNew = !e;
    const f = { barcode: h('input', { value: e?.barcode || '', disabled: !isNew, placeholder: 'BAR-XXX-000' }), name: h('input', { value: e?.name || '' }), lab: h('input', { value: e?.lab || '' }), category: h('input', { value: e?.category || '' }), condition: select(['good', 'fair', 'damaged'], e?.condition || 'good'), status: select(['available', 'under_repair', 'retired'], e && e.status !== 'in_custody' ? e.status : 'available') };
    modal({ title: isNew ? 'Add equipment' : `Edit ${e.barcode}`, body: h('div', { class: 'form-grid' }, field('Barcode', f.barcode), field('Name', f.name), field('Lab', f.lab), field('Category', f.category), field('Condition', f.condition), isNew || e.status === 'in_custody' ? h('div') : field('Status', f.status)),
      actions: [{ label: 'Cancel', onClick: c => c() }, { label: 'Save', kind: 'primary', onClick: async (close) => {
        if (isNew) await api.post('/api/req/equipment', { barcode: f.barcode.value, name: f.name.value, lab: f.lab.value, category: f.category.value, condition: f.condition.value });
        else await api.put(`/api/req/equipment/${e.barcode}`, { name: f.name.value, lab: f.lab.value, category: f.category.value, condition: f.condition.value, ...(e.status === 'in_custody' ? {} : { status: f.status.value }) }); close(); toast('Saved.', 'ok'); ctx.refresh(); } }] });
  }
  async function force(e) { const note = h('input', { placeholder: 'Condition note' }); modal({ title: `Record return of ${e.barcode}`, body: field('Note', note), actions: [{ label: 'Record return', kind: 'primary', onClick: async (close) => { await api.post(`/api/req/equipment/${e.barcode}/return`, { condition: 'good', note: note.value }); close(); toast('Return recorded.', 'ok'); ctx.refresh(); } }] }); }
  wrap.append(h('div', { style: 'margin-bottom:12px' }, h('button', { class: 'btn primary', onclick: () => edit(null) }, '+ Add equipment')),
    table([{ label: 'Barcode', render: r => h('code', null, r.barcode) }, { label: 'Item', key: 'name' }, { label: 'Lab', key: 'lab' }, { label: 'Condition', render: r => badge(r.condition) }, { label: 'Status', render: r => badge(r.status) },
      { label: 'Custodian', render: r => r.custodian_id ? h('span', null, `${r.custodian_name} (${r.custodian_id})`, h('div', { class: 'small muted' }, `due ${fmtDate(r.due_date)}`, r.overdue ? ' · overdue' : '')) : '—' },
      { label: '', render: r => h('span', { class: 'row' }, h('button', { class: 'btn sm', onclick: () => edit(r) }, 'Edit'), r.status === 'in_custody' ? h('button', { class: 'btn sm', onclick: () => force(r) }, 'Record return') : null) }], rows)); return wrap;
}
async function log() {
  const rows = await api.get('/api/req/equipment/log');
  return table([{ label: 'When', render: r => new Date(r.at).toLocaleString() }, { label: 'Barcode', key: 'barcode' }, { label: 'Item', key: 'name' }, { label: 'Student', render: r => r.full_name ? `${r.full_name} (${r.user_id})` : '—' }, { label: 'Action', render: r => badge(r.action, 'info') }, { label: 'Condition', key: 'condition' }, { label: 'Via', key: 'channel' }, { label: 'Note', key: 'note' }], rows, { empty: 'No custody events yet.' });
}
