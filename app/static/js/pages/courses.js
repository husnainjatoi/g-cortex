import { api, qs } from '../api.js';
import { h, clear, put, table, badge, tabs, modal, confirmDialog, toast, field, select, form, pageHead, fmtDate, errorBox } from '../ui.js';

const DAYS = ['M', 'T', 'W', 'Th', 'F', 'Sa'];
const DAYN = { M: 'Mon', T: 'Tue', W: 'Wed', Th: 'Thu', F: 'Fri', Sa: 'Sat' };

export async function render(root, ctx) {
  root.append(pageHead('Courses & Timetable', 'Browse the live catalogue, register, plan and ask GIKI.'));
  const holder = h('div'); root.append(holder);
  tabs(holder, [
    { key: 'catalog', label: 'Course catalog', render: () => catalog(ctx) },
    { key: 'mine', label: 'My registrations', render: () => mine(ctx) },
    { key: 'tt', label: 'Timetable', render: timetable },
    { key: 'plan', label: 'Planner & what-if', render: planner },
    { key: 'ask', label: 'Ask GIKI', render: ask },
    { key: 'record', label: 'Academic record', render: record },
  ]);
}

async function catalog(ctx) {
  const wrap = h('div'); const out = h('div');
  const q = h('input', { type: 'search', placeholder: 'Search code, title or topic…' });
  const cat = select([['', 'All categories'], 'core', 'elective', 'math', 'humanities'], '');
  const scope = select([['true', 'Offered this term'], ['false', 'Full official GIKI catalogue']], 'true');
  async function load() {
    const rows = await api.get('/api/courses' + qs({ q: q.value, category: cat.value, offered_only: scope.value }));
    clear(out).append(table([
      { label: 'Code', render: r => h('b', null, r.course_code) }, { label: 'Title', key: 'title' }, { label: 'Cr', key: 'credits' },
      { label: 'Timetable', render: r => h('span', null, r.slot || '—', r.lab_slot ? h('div', { class: 'small muted' }, `Lab: ${r.lab_slot}`) : null) },
      { label: 'Prerequisites', render: r => (r.prerequisites.length ? r.prerequisites.join(', ') : 'None') },
      { label: 'Seats', render: r => `${r.seats_left}/${r.capacity}` },
      { label: 'Status', render: r => r.student_status === 'not_offered' ? badge('catalogue only', 'mute') : badge(r.student_status) },
      { label: '', class: 'nowrap', render: r => h('span', { class: 'row' },
        h('button', { class: 'btn sm', onclick: () => details(r.course_code) }, 'Details'),
        r.student_status === 'available' ? h('button', { class: 'btn sm primary', onclick: () => register(r) }, 'Register') : null) },
    ], rows, { empty: 'No courses match.' }));
  }
  async function register(r) {
    let check;
    try { check = await api.get(`/api/courses/${r.course_code}/check`); } catch (e) { return toast(e.message, 'bad'); }
    const warn = [];
    if (!check.prerequisites.eligible) warn.push(h('div', { class: 'alert bad' }, '⚠ ' + check.prerequisites.message));
    if (check.schedule.has_conflicts) warn.push(h('div', { class: 'alert bad' }, '⚠ ' + check.schedule.message));
    if (!(await confirmDialog(`Register for ${r.course_code}?`, h('div', null, ...warn, h('p', null, `${r.title} · ${r.credits} credit hours · ${r.slot || 'no fixed slot'}`),
      warn.length ? h('p', { class: 'small muted' }, 'The server will reject this registration if the rules above are not met.') : null), 'Register'))) return;
    try { const res = await api.post(`/api/courses/${r.course_code}/register`); toast(res.message, 'ok'); load(); } catch (e) { toast(e.message, 'bad', 7000); }
  }
  async function details(code) {
    const c = await api.get(`/api/courses/${code}`);
    const chain = (nodes) => h('ul', null, nodes.map(n => h('li', null, `${n.course_code} – ${n.title}`, n.prerequisites.length ? chain(n.prerequisites) : null)));
    modal({ title: `${c.course_code} – ${c.title}`, body: h('div', null,
      h('p', null, c.description || 'No description provided.'),
      h('div', { class: 'grid g2' }, h('div', null, h('b', null, 'Credit hours: '), c.credits), h('div', null, h('b', null, 'Instructor: '), c.instructor || '—'),
        h('div', null, h('b', null, 'Lecture: '), c.slot || '—'), h('div', null, h('b', null, 'Lab: '), c.lab_slot || '—'), h('div', null, h('b', null, 'Seats left: '), `${c.seats_left} of ${c.capacity}`), h('div', null, h('b', null, 'Category: '), c.category)),
      h('h3', { style: 'margin-top:14px' }, 'Prerequisite chain'), c.prerequisite_chain.length ? chain(c.prerequisite_chain) : h('p', { class: 'muted' }, 'No prerequisites.'),
      c.student_status === 'blocked_prerequisites' ? h('div', { class: 'alert warn' }, `You are missing: ${c.missing_prerequisites.join(', ')}`) : null,
      c.catalog_only ? h('div', { class: 'alert info' }, 'This course is in the official GIKI catalogue but is not scheduled this term, so it cannot be registered yet.') : null,
      c.official_source ? h('p', { class: 'small muted' }, 'Official source: ', h('a', { href: c.official_source.source_url, target: '_blank', rel: 'noopener' }, c.official_source.source_title || 'giki.edu.pk'), c.official_source.source_page ? `, p. ${c.official_source.source_page}` : '', ` · retrieved ${fmtDate(c.official_source.retrieved_at)}`, c.official_source.prereq_status === 'ambiguous' ? ` · official prerequisite text: “${c.official_source.official_prereq_text}”` : '') : null) });
  }
  q.addEventListener('input', () => { clearTimeout(q._t); q._t = setTimeout(load, 250); }); cat.addEventListener('change', load); scope.addEventListener('change', load);
  wrap.append(h('div', { class: 'row', style: 'margin-bottom:12px' }, h('div', { style: 'flex:1;min-width:220px' }, q), h('div', null, cat), h('div', null, scope)), out);
  await load(); return wrap;
}

async function mine(ctx) {
  const r = await api.get('/api/courses/me/registrations');
  const wrap = h('div');
  wrap.append(h('div', { class: 'row', style: 'margin-bottom:12px' }, h('b', null, `${r.term}:`), `${r.total_credits} of ${r.max_credits} credit hours registered`));
  wrap.append(table([
    { label: 'Code', render: x => h('b', null, x.course_code) }, { label: 'Title', key: 'title' }, { label: 'Cr', key: 'credits' }, { label: 'Lecture', render: x => x.slot || '—' }, { label: 'Lab', render: x => x.lab_slot || '—' },
    { label: 'Registered', render: x => fmtDate(x.registered_at) },
    { label: '', render: x => h('button', { class: 'btn sm danger', onclick: async () => {
      if (!(await confirmDialog(`Drop ${x.course_code}?`, `This removes ${x.title} from your ${r.term} registration.`, 'Drop course', 'danger'))) return;
      try { const res = await api.post(`/api/courses/${x.course_code}/drop`); toast(res.message, 'ok'); ctx.refresh(); } catch (e) { toast(e.message, 'bad', 7000); } } }, 'Drop') }], r.courses, { empty: 'You have not registered for any courses this term.' }));
  return wrap;
}

async function timetable() {
  const t = await api.get('/api/courses/me/timetable');
  const wrap = h('div');
  if (t.has_conflicts) wrap.append(h('div', { class: 'alert bad' }, h('b', null, 'Timetable clash: '), t.conflicts.map(c => `${c.course_a} vs ${c.course_b} on ${DAYN[c.day]} (${c.overlap}, ${c.kind})`).join(' · ')));
  else if (t.courses.length) wrap.append(h('div', { class: 'alert ok' }, 'No clashes between your lectures and labs.'));
  const H0 = 8, H1 = 18, PX = 44;
  const grid = h('div', { class: 'tt' }, h('div', { class: 'hd' }, ''), DAYS.map(d => h('div', { class: 'hd' }, DAYN[d])));
  const clashKeys = new Set(t.conflicts.flatMap(c => [`${c.course_a}${c.day}`, `${c.course_b}${c.day}`]));
  for (let hr = H0; hr < H1; hr++) {
    grid.append(h('div', { class: 'tm', style: `height:${PX}px` }, `${String(hr).padStart(2, '0')}:00`));
    DAYS.forEach(d => {
      const cell = h('div', { class: 'cell', style: `height:${PX}px` });
      t.entries.filter(e => e.day === d && parseInt(e.start) === hr).forEach(e => {
        const [sh, sm] = e.start.split(':').map(Number), [eh, em] = e.end.split(':').map(Number);
        cell.append(h('div', { class: `ev ${e.kind} ${clashKeys.has(e.course_code + d) ? 'clash' : ''}`, style: `top:${(sm / 60) * PX}px;height:${((eh * 60 + em) - (sh * 60 + sm)) / 60 * PX - 3}px`, title: `${e.title} (${e.kind})` },
          h('b', null, e.course_code), ` ${e.start}-${e.end}`, h('div', null, e.kind === 'lab' ? 'Lab' : e.title)));
      });
      grid.append(cell);
    });
  }
  wrap.append(t.entries.length ? grid : h('div', { class: 'card empty' }, 'Register for courses to see your timetable.'));
  return wrap;
}

async function planner() {
  const wrap = h('div');
  const recOut = h('div'); const interests = h('input', { placeholder: 'e.g. machine learning, embedded systems' });
  const prof = await api.get('/api/courses/me/record');
  interests.value = (prof.profile && prof.profile.interests) || '';
  async function recs(save) {
    if (save) await api.put('/api/courses/me/interests', { interests: interests.value });
    const r = await api.get('/api/courses/me/recommendations' + qs({ interests: interests.value }));
    clear(recOut).append(table([{ label: 'Course', render: x => h('b', null, `${x.course_code} – ${x.title}`) }, { label: 'Why', key: 'reason' }, { label: 'Eligible now', render: x => badge(x.eligible_now ? 'available' : 'blocked_prerequisites') }], r.recommendations, { empty: 'No matching electives found — try different interests.' }));
  }
  wrap.append(h('div', { class: 'card' }, h('h2', null, 'Elective recommendations'), h('div', { class: 'row' }, h('div', { style: 'flex:1;min-width:240px' }, interests),
    h('button', { class: 'btn primary', onclick: () => recs(false).catch(e => toast(e.message, 'bad')) }, 'Recommend'), h('button', { class: 'btn', onclick: () => recs(true).catch(e => toast(e.message, 'bad')) }, 'Save as my interests')), h('div', { style: 'margin-top:12px' }, recOut)));
  recs(false);

  const minors = await api.get('/api/courses/minors'); const all = await api.get('/api/courses');
  const out = h('div'); const sel = h('select', { multiple: true, size: 3 }, minors.map(m => h('option', { value: m.minor_name, selected: true }, m.minor_name)));
  const extra = h('select', { multiple: true, size: 5 }, all.filter(c => c.student_status !== 'completed').map(c => h('option', { value: c.course_code }, `${c.course_code} – ${c.title}`)));
  wrap.append(h('div', { class: 'card' }, h('h2', null, 'What-if degree & minor simulator'),
    h('div', { class: 'grid g2' }, field('Minor(s) to evaluate', sel, 'Hold Ctrl/Cmd to compare several minors'), field('Assume I also complete…', extra, 'Hypothetical courses — nothing is registered')),
    h('button', { class: 'btn primary', onclick: async () => {
      try {
        const r = await api.post('/api/courses/me/what-if', { minors: [...sel.selectedOptions].map(o => o.value), hypothetical_courses: [...extra.selectedOptions].map(o => o.value) });
        clear(out).append(...r.minors.map(m => h('div', { class: 'card flat' }, h('div', { class: 'row spread' }, h('h3', null, m.minor), badge(`${m.progress_pct}% planned`, m.progress_pct >= 100 ? 'ok' : 'info')),
          h('div', { class: 'bar' }, h('i', { style: `width:${m.progress_pct}%` })),
          h('p', null, 'Completed: ', m.completed.join(', ') || '—', h('br'), 'In progress / assumed: ', m.in_progress_or_planned.join(', ') || '—', h('br'), 'Still needed: ', m.remaining.join(', ') || 'none', ` (${m.credits_remaining} credit hours)`),
          Object.keys(m.prerequisite_blockers).length ? h('div', { class: 'alert warn' }, 'Blocked by prerequisites: ', Object.entries(m.prerequisite_blockers).map(([c, p]) => `${c} needs ${p.join(', ')}`).join(' · ')) : null,
          m.eligible_next.length ? h('p', null, h('b', null, 'You can take next: '), m.eligible_next.join(', ')) : null)),
          Object.keys(r.shared_courses_between_minors).length ? h('div', { class: 'alert info' }, 'Shared between minors: ', Object.entries(r.shared_courses_between_minors).map(([k, v]) => `${k}: ${v.join(', ')}`).join(' · ')) : null);
      } catch (e) { toast(e.message, 'bad'); } } }, 'Simulate'), h('div', { style: 'margin-top:12px' }, out)));
  return wrap;
}

async function ask() {
  const out = h('div'); const q = h('input', { placeholder: 'e.g. What is the add/drop policy? What is the minimum CGPA to graduate?', maxlength: 500 });
  const go = async () => {
    try {
      const r = await api.post('/api/courses/ask', { question: q.value });
      put(clear(out), r.found ? [h('div', { class: 'alert ok' }, 'Answers below come from official GIKI documents (giki.edu.pk). Check the cited source for the authoritative text.'),
        r.passages.map(p => h('div', { class: 'card flat' }, h('div', { class: 'row spread' }, h('b', null, p.source_title || 'GIKI document'), badge(p.similarity ? `match ${Math.round(p.similarity * 100)}%` : 'text match', 'info')),
          h('div', { class: 'small muted' }, [p.section ? `Section: ${p.section}` : null, p.page ? `p. ${p.page}` : null, p.retrieved_at ? `retrieved ${p.retrieved_at}` : null].filter(Boolean).join(' · ')), h('p', { style: 'white-space:pre-wrap' }, p.text),
          h('a', { href: p.source_url, target: '_blank', rel: 'noopener' }, p.source_url)))] : h('div', { class: 'alert warn' }, r.answer));
    } catch (e) { clear(out).append(errorBox(e)); }
  };
  q.addEventListener('keydown', e => { if (e.key === 'Enter') go(); });
  return h('div', { class: 'card' }, h('h2', null, 'Ask GIKI'), h('p', { class: 'muted' }, 'Searches only official GIKI documents (Student Handbook, prospectus, calendar and other pages on giki.edu.pk). If the answer is not in them, it says so instead of guessing.'), h('div', { class: 'row' }, h('div', { style: 'flex:1' }, q), h('button', { class: 'btn primary', onclick: go }, 'Ask')), h('div', { style: 'margin-top:12px' }, out));
}

async function record() {
  const r = await api.get('/api/courses/me/record');
  return h('div', null, h('div', { class: 'row', style: 'margin-bottom:12px' }, h('b', null, 'CGPA: '), r.cgpa ?? '—', h('b', null, '  Credits earned: '), r.credits_earned, h('span', { class: 'muted' }, `· ${r.profile.program || ''} · Semester ${r.profile.current_semester || ''}`)),
    table([{ label: 'Code', key: 'course_code' }, { label: 'Title', key: 'title' }, { label: 'Credits', key: 'credits' }, { label: 'Grade', render: x => badge(x.grade, x.grade === 'F' ? 'bad' : 'ok') }, { label: 'Term', key: 'term' }], r.history, { empty: 'No completed courses on record.' }));
}
