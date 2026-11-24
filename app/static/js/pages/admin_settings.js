import { api } from '../api.js';
import { h, pageHead, toast, table } from '../ui.js';

export async function render(root) {
  root.append(pageHead('Rules & Settings', 'Institutional rules enforced by the backend for both the portal and the assistant. Each change is audited.'));
  const rows = await api.get('/api/admin/settings');
  root.append(table([{ label: 'Rule', render: s => h('div', null, h('code', null, s.key), h('div', { class: 'small muted' }, s.description)) }, { label: 'Value', render: s => { const i = h('input', { value: s.value, style: 'width:140px' }); return h('span', { class: 'row' }, i, h('button', { class: 'btn sm primary', onclick: async () => { try { await api.put(`/api/admin/settings/${s.key}`, { value: i.value }); toast('Updated.', 'ok'); } catch (e) { toast(e.message, 'bad'); } } }, 'Save')); } }, { label: 'Updated', render: s => new Date(s.updated_at).toLocaleString() }], rows));
}
