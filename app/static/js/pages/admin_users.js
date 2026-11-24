import { api, qs } from '../api.js';
import { h, clear, table, badge, modal, confirmDialog, toast, field, select, pageHead } from '../ui.js';

const SCOPES = ['super', 'academic', 'mess', 'requisition'];
export async function render(root, ctx) {
  root.append(pageHead('Users & Roles', 'Roles and administrative scopes are stored in the database and enforced by the backend.'));
  const out = h('div'); const q = h('input', { type: 'search', placeholder: 'Search by ID, name or email…' }); const role = select([['', 'All roles'], 'student', 'faculty', 'admin'], '');
  async function load() {
    const rows = await api.get('/api/admin/users' + qs({ q: q.value, role: role.value }));
    clear(out).append(table([{ label: 'ID', render: u => h('b', null, u.user_id) }, { label: 'Name', key: 'full_name' }, { label: 'Email', key: 'email' }, { label: 'Role', render: u => badge(u.role, u.role === 'admin' ? 'bad' : u.role === 'faculty' ? 'info' : 'mute') },
      { label: 'Scopes', render: u => u.scopes || '—' }, { label: 'Password', render: u => badge(u.has_password ? 'set' : 'not set', u.has_password ? 'ok' : 'warn') }, { label: 'Active', render: u => badge(u.is_active ? 'active' : 'cancelled') },
      { label: '', render: u => h('span', { class: 'row' }, h('button', { class: 'btn sm', onclick: () => edit(u) }, 'Edit'), h('button', { class: 'btn sm', onclick: () => reset(u) }, 'Set password')) }], rows));
  }
  function edit(u) {
    const r = select(['student', 'faculty', 'admin'], u.role); const act = select([['true', 'Active'], ['false', 'Deactivated']], String(u.is_active));
    const sc = SCOPES.map(s => h('label', { style: 'display:inline-flex;gap:6px;margin-right:14px;font-weight:500' }, h('input', { type: 'checkbox', value: s, checked: (u.scopes || '').split(',').includes(s), style: 'width:auto' }), s));
    modal({ title: `Edit ${u.user_id}`, body: h('div', null, field('Role', r), field('Administrative scopes (admins only)', h('div', null, sc), 'super = everything; others are limited to one area of responsibility'), field('Account', act)),
      actions: [{ label: 'Cancel', onClick: c => c() }, { label: 'Save', kind: 'primary', onClick: async (close) => { await api.put(`/api/admin/users/${u.user_id}`, { role: r.value, scopes: sc.map(l => l.firstChild).filter(i => i.checked).map(i => i.value), is_active: act.value === 'true' }); close(); toast('User updated. Their sessions were signed out.', 'ok'); load(); } }] });
  }
  function reset(u) {
    const pw = h('input', { type: 'text', autocomplete: 'off', placeholder: 'Temporary password' });
    modal({ title: `Set temporary password for ${u.user_id}`, body: field('Temporary password', pw, 'Min 8 characters with letters and digits. The user must change it at next sign-in.'), actions: [{ label: 'Set password', kind: 'primary', onClick: async (close) => { await api.post(`/api/admin/users/${u.user_id}/password`, { password: pw.value }); close(); toast('Password set.', 'ok'); load(); } }] });
  }
  function create() {
    const f = { user_id: h('input', { maxlength: 10 }), full_name: h('input'), email: h('input', { type: 'email' }), role: select(['student', 'faculty', 'admin'], 'student'), password: h('input', { type: 'text', autocomplete: 'off' }) };
    const sc = SCOPES.map(s => h('label', { style: 'display:inline-flex;gap:6px;margin-right:14px;font-weight:500' }, h('input', { type: 'checkbox', value: s, style: 'width:auto' }), s));
    modal({ title: 'Add user', body: h('div', { class: 'form-grid' }, field('User ID / reg. no.', f.user_id), field('Full name', f.full_name), field('Email', f.email), field('Role', f.role), field('Temporary password', f.password, 'Optional now; can be set later'), field('Scopes (admins)', h('div', null, sc))),
      actions: [{ label: 'Cancel', onClick: c => c() }, { label: 'Create', kind: 'primary', onClick: async (close) => { await api.post('/api/admin/users', { user_id: f.user_id.value, full_name: f.full_name.value, email: f.email.value, role: f.role.value, password: f.password.value || null, scopes: sc.map(l => l.firstChild).filter(i => i.checked).map(i => i.value) }); close(); toast('User created.', 'ok'); load(); } }] });
  }
  q.addEventListener('input', () => { clearTimeout(q._t); q._t = setTimeout(load, 250); }); role.addEventListener('change', load);
  root.append(h('div', { class: 'row', style: 'margin-bottom:12px' }, h('div', { style: 'flex:1;min-width:220px' }, q), h('div', null, role), h('button', { class: 'btn primary', onclick: create }, '+ Add user')), out); await load();
}
