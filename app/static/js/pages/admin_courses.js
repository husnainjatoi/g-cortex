import { api, qs } from '../api.js';
import { h, clear, table, badge, tabs, modal, confirmDialog, toast, field, select, pageHead, fmtDate } from '../ui.js';

export async function render(root, ctx) {
  root.append(pageHead('Course Administration', 'Changes are saved to the database and appear instantly in the student portal and the assistant.'));
  const holder = h('div'); root.append(holder);
  tabs(holder, [{ key: 'c', label: 'Courses', render: () => courses(ctx) }, { key: 'g', label: 'Student records', render: records }]);
}

async function courses(ctx) {
  const wrap = h('div'); const out = h('div'); const q = h('input', { type: 'search', placeholder: 'Search courses…' });
  async function load() {
    const rows = await api.get('/api/admin/courses' + qs({ q: q.value }));
    clear(out).append(table([{ label: 'Code', render: r => h('b', null, r.course_code) }, { label: 'Title', key: 'title' }, { label: 'Cr', key: 'credits' }, { label: 'Lecture / Lab', render: r => `${r.slot || '—'}${r.lab_slot ? ' / ' + r.lab_slot : ''}` },
      { label: 'Prereqs', render: r => r.prerequisites.join(', ') || '—' }, { label: 'Registered', render: r => `${r.seats_taken}/${r.capacity}` }, { label: 'Active', render: r => r.catalog_only ? badge('catalogue only', 'mute') : badge(r.is_active ? 'active' : 'cancelled') },
      { label: '', class: 'nowrap', render: r => h('span', { class: 'row' }, h('button', { class: 'btn sm', onclick: () => edit(r) }, 'Edit'), h('button', { class: 'btn sm', onclick: () => students(r) }, 'Students'),
        h('button', { class: 'btn sm danger', onclick: async () => { if (!(await confirmDialog(`Remove ${r.course_code}?`, 'Courses with registrations, history or dependants are deactivated rather than deleted.', 'Remove', 'danger'))) return;
          try { const x = await api.del(`/api/admin/courses/${r.course_code}`); toast(x.message, 'ok', 6000); load(); } catch (e) { toast(e.message, 'bad'); } } }, 'Remove')) }], rows));
  }
  function edit(c) {
    const isNew = !c;
    const f = {
      course_code: h('input', { value: c?.course_code || '', disabled: !isNew, placeholder: 'CS401', maxlength: 10 }), title: h('input', { value: c?.title || '', maxlength: 100 }), credits: h('input', { type: 'number', min: 1, max: 6, value: c?.credits || 3 }),
      slot: h('input', { value: c?.slot || '', placeholder: 'MWF 10:00-11:00' }), lab_slot: h('input', { value: c?.lab_slot || '', placeholder: 'W 14:00-16:00 (optional)' }), capacity: h('input', { type: 'number', min: 0, value: c?.capacity ?? 60 }),
      category: select(['core', 'elective', 'math', 'humanities', 'science'], c?.category || 'elective'), instructor: h('input', { value: c?.instructor || '' }), domains: h('input', { value: c?.domains || '', placeholder: 'comma separated topics' }),
      prerequisites: h('input', { value: (c?.prerequisites || []).join(', '), placeholder: 'CS101, MT101' }), description: h('textarea', null, c?.description || ''), is_active: select([['true', 'Active'], ['false', 'Inactive']], String(c?.is_active ?? true)), catalog_only: select([['false', 'Offered this term (open for registration)'], ['true', 'Catalogue only (not scheduled)']], String(c?.catalog_only ?? false)) };
    modal({ title: isNew ? 'Add course' : `Edit ${c.course_code}`, wide: true, body: h('div', { class: 'form-grid' },
      field('Course code', f.course_code, 'Format: 2-4 letters + 3 digits'), field('Title', f.title), field('Credit hours', f.credits), field('Category', f.category), field('Lecture slot', f.slot, 'Days M T W Th F Sa + time range'), field('Lab slot', f.lab_slot),
      field('Registration limit', f.capacity), field('Status', f.is_active), field('Availability this term', f.catalog_only, 'Imported official courses start as catalogue-only; set a slot/limit and mark offered to open them'), field('Instructor', f.instructor), field('Topics', f.domains), field('Prerequisites', f.prerequisites, 'Course codes; circular chains are rejected'), h('div'),
      h('div', { style: 'grid-column:1/-1' }, field('Description', f.description))),
      actions: [{ label: 'Cancel', onClick: x => x() }, { label: isNew ? 'Create course' : 'Save changes', kind: 'primary', onClick: async (close) => {
        const body = { title: f.title.value, credits: Number(f.credits.value), slot: f.slot.value, lab_slot: f.lab_slot.value, capacity: Number(f.capacity.value), category: f.category.value, instructor: f.instructor.value, domains: f.domains.value, description: f.description.value,
          prerequisites: f.prerequisites.value.split(',').map(s => s.trim().toUpperCase()).filter(Boolean) };
        if (isNew) await api.post('/api/admin/courses', { course_code: f.course_code.value, ...body }); else await api.put(`/api/admin/courses/${c.course_code}`, { ...body, is_active: f.is_active.value === 'true', catalog_only: f.catalog_only.value === 'true' });
        close(); toast(isNew ? 'Course created.' : 'Course updated.', 'ok'); load(); } }] });
  }
  async function students(c) {
    const rows = await api.get(`/api/admin/courses/${c.course_code}/students`);
    const sid = h('input', { placeholder: 'Student ID, e.g. 2024405' });
    const m = modal({ title: `${c.course_code} – registered students (${rows.length}/${c.capacity})`, wide: true, body: h('div', null,
      table([{ label: 'ID', key: 'user_id' }, { label: 'Name', key: 'full_name' }, { label: 'Email', key: 'email' }, { label: 'Registered', render: r => fmtDate(r.registered_at) }, { label: 'Via', key: 'channel' },
        { label: '', render: r => h('button', { class: 'btn sm danger', onclick: async () => { try { await api.post(`/api/admin/courses/${c.course_code}/drop`, { student_id: r.user_id }); toast('Student dropped.', 'ok'); m.close(); load(); } catch (e) { toast(e.message, 'bad'); } } }, 'Drop') }], rows, { empty: 'No registrations.' }),
      h('div', { class: 'row', style: 'margin-top:14px' }, h('div', { style: 'flex:1' }, sid), h('button', { class: 'btn primary', onclick: async () => { try { await api.post(`/api/admin/courses/${c.course_code}/register`, { student_id: sid.value.trim() }); toast('Student registered (all rules apply).', 'ok'); m.close(); load(); } catch (e) { toast(e.message, 'bad', 7000); } } }, 'Register student'))) });
  }
  q.addEventListener('input', () => { clearTimeout(q._t); q._t = setTimeout(load, 250); });
  wrap.append(h('div', { class: 'row', style: 'margin-bottom:12px' }, h('div', { style: 'flex:1;min-width:220px' }, q), h('button', { class: 'btn primary', onclick: () => edit(null) }, '+ Add course')), out); await load(); return wrap;
}

async function records() {
  const sid = h('input', { placeholder: 'Student ID' }); const code = h('input', { placeholder: 'Course code' }); const grade = select(['', 'A+', 'A', 'A-', 'B+', 'B', 'B-', 'C+', 'C', 'C-', 'D', 'F', 'W'], ''); const term = h('input', { placeholder: 'Term, e.g. Spring 2026' });
  return h('div', { class: 'card' }, h('h2', null, 'Record a completed course / grade'), h('p', { class: 'muted' }, 'Updates prerequisite eligibility immediately. Choose the blank grade to remove a record.'),
    h('div', { class: 'form-grid' }, field('Student ID', sid), field('Course code', code), field('Grade', grade), field('Term', term)),
    h('button', { class: 'btn primary', onclick: async () => { try { await api.post('/api/admin/grades', { student_id: sid.value.trim(), course_code: code.value.trim(), grade: grade.value || null, term: term.value || null }); toast('Academic record updated.', 'ok'); } catch (e) { toast(e.message, 'bad'); } } }, 'Save'));
}
