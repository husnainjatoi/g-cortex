import { api, qs } from '../api.js';
import { h, put, clearFieldErrors, showFieldErrors, table, badge, modal, confirmDialog, toast, field, select, pageHead, fmtDate, fmtDateTime, errorBox } from '../ui.js';

const incomplete = (s) => [!s.description && 'description', !s.logo_url && 'logo', !s.advisor_id && 'faculty advisor'].filter(Boolean);
const logo = (s, size = 36) => s.logo_url ? h('img', { src: s.logo_url, alt: `${s.name} logo`, style: `height:${size}px;width:${size}px;object-fit:contain;border-radius:6px;background:#fff;border:1px solid var(--line)`, loading: 'lazy', onerror: (e) => { e.target.replaceWith(h('span', { class: 'muted small' }, 'no logo')); } }) : h('span', { class: 'muted small' }, 'no logo');

export async function render(root, ctx) {
  root.append(pageHead('Society Management', 'Official GIKI societies used by the reservation form and the assistant. Your edits are kept when the official list is re-imported.'));
  const out = h('div'); const q = h('input', { type: 'search', placeholder: 'Search societies' });
  const st = select([['', 'All statuses'], ['active', 'Active'], ['inactive', 'Inactive'], ['incomplete', 'Incomplete information']], '');
  const bar = h('div', { class: 'row', style: 'margin-bottom:12px' }, h('div', { style: 'flex:1;min-width:220px' }, q), h('div', { style: 'width:220px' }, st),
    h('button', { class: 'btn primary', onclick: () => edit(null) }, '+ Add society'),
    h('button', { class: 'btn', onclick: async () => {
      if (!(await confirmDialog('Import the official list?', 'Fetches the societies page on giki.edu.pk. New societies are added; societies you edited keep your changes.', 'Import'))) return;
      try { const r = await api.post('/api/req/admin/societies/import'); toast(`Import finished: ${r.created ?? 0} added, ${r.updated ?? 0} refreshed, ${r.kept_manual_edits ?? 0} kept as edited.`, 'ok', 7000); load(); } catch (e) { toast(e.message, 'bad', 7000); } } }, 'Import official list'));
  root.append(bar, out);
  let all = [];
  async function load() {
    try { all = await api.get('/api/req/admin/societies' + qs({ q: q.value })); draw(); } catch (e) { put(out, errorBox(e)); }
  }
  function draw() {
    const f = st.value; const rows = all.filter(s => !f || (f === 'incomplete' ? incomplete(s).length : s.status === f));
    put(out, h('p', { class: 'small muted' }, `${rows.length} of ${all.length} societies · ${all.filter(s => s.status === 'active').length} active`),
      table([{ label: '', render: s => logo(s) }, { label: 'Society', render: s => h('div', null, h('b', null, s.name), h('div', { class: 'small muted' }, (s.description || 'No description yet').slice(0, 110) + ((s.description || '').length > 110 ? '…' : ''))) },
        { label: 'Status', render: s => badge(s.status, s.status === 'active' ? 'ok' : 'mute') },
        { label: 'Source', render: s => h('span', null, badge(s.origin, 'mute'), s.manually_edited ? h('span', null, ' ', badge('edited', 'info')) : null) },
        { label: 'Members', key: 'members' }, { label: 'Missing', render: s => incomplete(s).length ? h('span', { class: 'small', style: 'color:var(--warn)' }, incomplete(s).join(', ')) : '—' },
        { label: '', render: s => h('span', { class: 'row' }, h('button', { class: 'btn sm', onclick: () => details(s) }, 'Details'), h('button', { class: 'btn sm', onclick: () => edit(s) }, 'Edit'),
          h('button', { class: `btn sm ${s.status === 'active' ? 'danger' : ''}`, onclick: () => toggle(s) }, s.status === 'active' ? 'Deactivate' : 'Activate')) }], rows, { empty: 'No societies match.' }));
  }
  async function toggle(s) {
    const to = s.status === 'active' ? 'inactive' : 'active';
    if (!(await confirmDialog(`${to === 'inactive' ? 'Deactivate' : 'Activate'} ${s.name}?`, to === 'inactive' ? 'It will disappear from the reservation form and the assistant. Existing requests are not affected.' : 'It will be available for reservations again.', to === 'inactive' ? 'Deactivate' : 'Activate', to === 'inactive' ? 'danger' : 'primary'))) return;
    try { await api.put(`/api/req/admin/societies/${s.society_id}`, { status: to }); toast(`${s.name} is now ${to}.`, 'ok'); load(); } catch (e) { toast(e.message, 'bad'); }
  }
  function edit(s) {
    const i = {
      name: h('input', { name: 'name', value: s?.name || '', maxlength: 200 }), category: h('input', { name: 'category', value: s?.category || '', maxlength: 50 }),
      advisor_id: h('input', { name: 'advisor_id', value: s?.advisor_id || '', maxlength: 10, placeholder: 'Faculty ID, e.g. FAC001' }),
      logo_url: h('input', { name: 'logo_url', value: s?.logo_url || '', maxlength: 500, placeholder: 'https://… (image address)' }), description: h('textarea', { name: 'description', maxlength: 4000 }, ''),
    };
    i.description.value = s?.description || '';
    const box = h('div', null, field('Society name', i.name), field('Category', i.category), field('Faculty advisor ID', i.advisor_id, 'Optional. The advisor is the first approver of society reservations.'),
      field('Logo address', i.logo_url, 'Logos are referenced by address; uploading image files is not supported.'), field('Description', i.description));
    modal({ title: s ? `Edit ${s.name}` : 'Add society', body: box, actions: [{ label: 'Cancel', onClick: c => c() }, { label: 'Save', kind: 'primary', onClick: async (close) => {
      clearFieldErrors(box);
      const body = Object.fromEntries(Object.entries(i).map(([k, el]) => [k, el.value.trim()]));
      try { if (s) await api.put(`/api/req/admin/societies/${s.society_id}`, body); else await api.post('/api/req/admin/societies', body); close(); toast('Society saved.', 'ok'); load(); }
      catch (e) { if (e.fields && showFieldErrors(box, e.fields)) return false; throw e; } } }] });
  }
  async function details(s) {
    const [mem, reqs] = await Promise.all([api.get(`/api/req/admin/societies/${s.society_id}/members`), api.get('/api/req/requisitions?scope=all')]);
    const mine = reqs.filter(r => r.society_id === s.society_id);
    const uid = h('input', { placeholder: 'Student ID, e.g. 2024405', style: 'width:180px' }); const role = select([['member', 'Member'], ['office_bearer', 'Office bearer']], 'member');
    const memBox = h('div');
    const drawMem = (rows) => put(memBox, table([{ label: 'Student', render: m => `${m.full_name} (${m.user_id})` }, { label: 'Role', render: m => m.member_role.replace('_', ' ') },
      { label: '', render: m => h('button', { class: 'btn sm danger', onclick: async () => { try { await api.del(`/api/req/admin/societies/${s.society_id}/members/${m.user_id}`); drawMem(await api.get(`/api/req/admin/societies/${s.society_id}/members`)); load(); } catch (e) { toast(e.message, 'bad'); } } }, 'Remove') }], rows,
      { empty: 'No members recorded. Until members are recorded, society reservations cannot be verified automatically and an administrator must confirm each request.' }));
    drawMem(mem);
    modal({ title: s.name, wide: true, body: h('div', null,
      h('div', { class: 'row' }, logo(s, 64), h('div', null, badge(s.status, s.status === 'active' ? 'ok' : 'mute'), ' ', s.category ? badge(s.category, 'mute') : null)),
      h('p', null, s.description || h('span', { class: 'muted' }, 'No description recorded.')),
      incomplete(s).length ? h('div', { class: 'alert warn' }, `Incomplete information: ${incomplete(s).join(', ')}.`) : null,
      h('p', { class: 'small muted' }, 'Source: ', s.source_url ? h('a', { href: s.source_url, target: '_blank', rel: 'noopener' }, s.source_url) : s.origin, ` · retrieved ${fmtDateTime(s.retrieved_at)} · ${s.manually_edited ? 'edited by an administrator (preserved on re-import)' : 'not edited'}`),
      h('h3', null, 'Members who may reserve on behalf of this society'), memBox,
      h('div', { class: 'row', style: 'margin:8px 0' }, uid, role, h('button', { class: 'btn sm primary', onclick: async () => { try { await api.post(`/api/req/admin/societies/${s.society_id}/members`, { user_id: uid.value.trim(), role: role.value }); uid.value = ''; drawMem(await api.get(`/api/req/admin/societies/${s.society_id}/members`)); load(); } catch (e) { toast(e.message, 'bad'); } } }, 'Add member')),
      h('h3', null, 'Reservation requests for this society'),
      table([{ label: 'ID', key: 'requisition_id' }, { label: 'Event', key: 'event_title' }, { label: 'Date', render: r => fmtDate(r.booking_date) }, { label: 'Status', render: r => badge(r.status) },
        { label: 'Authority', render: r => r.authority_verified ? badge('verified', 'ok') : badge('to confirm', 'warn') }], mine, { empty: 'No reservation requests yet.' }),
      h('p', null, h('a', { href: '#/admin/requisitions', onclick: () => document.querySelector('.modal-back')?.remove() }, 'Open Requisition Approvals →'))) });
  }
  q.addEventListener('input', () => { clearTimeout(q._t); q._t = setTimeout(load, 250); });
  st.addEventListener('change', draw);
  await load();
}
