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
    { key: 'degree', label: 'Degree plan', render: degree },
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

const STATUS = { completed: ['completed', 'ok'], in_progress: ['in progress', 'info'], eligible: ['eligible', 'ok'], blocked: ['blocked by prerequisites', 'bad'], open_slot: ['elective slot', 'mute'] };

// Semester-wise curriculum from the official prospectus, with the student's progress. Choosing another programme is the what-if for changing degree.
async function degree() {
  const wrap = h('div'); const body = h('div');
  const progs = await api.get('/api/courses/programs'); const mine = await api.get('/api/courses/me/degree-progress');
  if (!progs.length) return h('div', { class: 'alert warn' }, 'The official degree plans have not been imported yet. An administrator can import them from Official GIKI Data.');
  const prog = select(progs.map(p => [p.code, p.name]), mine.program ? mine.program.code : progs[0].code, { id: 'degree-programme' }); const trackSel = h('select', { id: 'degree-track' });
  const draw = async (code, track) => {
    try {
      const d = await api.get('/api/courses/me/degree-progress' + qs({ program: code, track }));
      put(trackSel, d.tracks.map(t => h('option', { value: t, selected: t === d.selected_track }, t))); trackSel.parentElement.style.display = d.tracks.length ? '' : 'none';
      const c = d.credits;
      put(body,
        d.program.plan_status !== 'parsed' ? h('div', { class: 'alert warn' }, 'Part of this programme\'s plan could not be read unambiguously from the prospectus and is under administrator review.') : null,
        d.is_students_own_programme ? null : h('div', { class: 'alert info' }, `What-if: this is ${d.program.name}, not your programme. Courses you have already passed count wherever the same course code appears. Nothing is changed.`),
        d.demo_assumption && d.is_students_own_programme ? h('div', { class: 'alert info' }, 'Demo account: earlier semesters are assumed passed and the current semester assumed in progress (no grades are recorded for these).') : null,
        h('div', { class: 'grid g4' }, statCard2('Completed', c.completed), statCard2('In progress', c.in_progress), statCard2('Remaining', c.remaining), statCard2('Programme total', c.total)),
        d.timetable_conflicts.length ? h('div', { class: 'alert warn' }, 'Timetable clashes with your registered courses: ', d.timetable_conflicts.map(x => `${x.course} vs ${x.conflicts_with}`).join(', ')) : null,
        d.semesters.map(sem => h('div', { class: 'card' }, h('h3', null, `Semester ${sem.semester}`, h('span', { class: 'small muted' }, `  ${sem.credits_completed} credit hours completed`)),
          table([{ label: 'Code', render: x => x.course_code ? h('b', null, x.course_code) : '—' }, { label: 'Course', key: 'title' }, { label: 'Cr', key: 'credit_hours' },
            { label: 'Prerequisites', render: x => x.prerequisites || '—' }, { label: 'Status', render: x => h('span', null, badge(...STATUS[x.status]), x.missing_prerequisites.length ? h('div', { class: 'small muted' }, `needs ${x.missing_prerequisites.join(', ')}`) : null) },
            { label: 'Offered now', render: x => x.course_code ? (x.offered_this_term ? 'yes' : 'not scheduled') : '—' }], sem.courses))),
        h('p', { class: 'small muted' }, 'Source: ', h('a', { href: d.program.source_url, target: '_blank', rel: 'noopener' }, 'GIKI Undergraduate Prospectus 2025'), d.program.source_page ? ` · page ${d.program.source_page}` : '', ' · ', d.notes.join(' ')));
    } catch (e) { put(body, errorBox(e)); }
  };
  prog.addEventListener('change', () => draw(prog.value)); trackSel.addEventListener('change', () => draw(prog.value, trackSel.value));
  wrap.append(h('div', { class: 'card' }, h('div', { class: 'row' }, h('div', { style: 'min-width:260px' }, field('Degree programme', prog)), h('div', { style: 'min-width:260px' }, field('Track (semesters 5-8)', trackSel)))), body);
  await draw(mine.program ? mine.program.code : progs[0].code, mine.selected_track);
  return wrap;
}
const statCard2 = (l, v) => h('div', { class: 'card stat' }, h('div', { class: 'l' }, `${l} (credit hours)`), h('div', { class: 'v' }, v));

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
  const official = minors.filter(m => m.status === 'official'), unverified = minors.filter(m => m.status !== 'official');
  const out = h('div'); const sel = h('select', { multiple: true, size: Math.max(2, official.length) }, official.map(m => h('option', { value: m.minor_name, selected: true }, m.minor_name)));
  const extra = h('select', { multiple: true, size: 5 }, all.filter(c => c.student_status !== 'completed').map(c => h('option', { value: c.course_code }, `${c.course_code} – ${c.title}`)));
  wrap.append(h('div', { class: 'card' }, h('h2', null, 'What-if degree & minor simulator'),
    h('div', { class: 'grid g2' }, field('Official minor(s) to evaluate', sel, unverified.length ? `Only officially published minors are listed. ${unverified.length} other minor(s) in the system are unverified and under administrator review.` : 'Hold Ctrl/Cmd to compare several minors'), field('Assume I also complete…', extra, 'Hypothetical courses — nothing is registered')),
    h('button', { class: 'btn primary', onclick: async () => {
      try {
        const r = await api.post('/api/courses/me/what-if', { minors: [...sel.selectedOptions].map(o => o.value), hypothetical_courses: [...extra.selectedOptions].map(o => o.value) });
        put(clear(out), r.minors.map(m => h('div', { class: 'card flat' }, h('div', { class: 'row spread' }, h('h3', null, m.minor), m.progress_pct == null ? badge('official · no fixed requirement', 'info') : badge(`${m.progress_pct}% planned`, m.progress_pct >= 100 ? 'ok' : 'info')),
          m.progress_pct != null ? h('div', { class: 'bar' }, h('i', { style: `width:${m.progress_pct}%` })) : null,
          m.requirement_note ? h('div', { class: 'alert info' }, m.requirement_note) : null,
          m.course_pool ? h('p', null, h('b', null, 'Courses the prospectus lists for this minor: '), m.course_pool.join(', '), h('br'), 'You have completed: ', m.pool_completed.join(', ') || '—', h('br'), 'In progress / assumed: ', m.pool_in_progress.join(', ') || '—') : null,
          m.required_courses.length ? h('p', null, 'Completed: ', m.completed.join(', ') || '—', h('br'), 'In progress / assumed: ', m.in_progress_or_planned.join(', ') || '—', h('br'), 'Still needed: ', m.remaining.join(', ') || 'none', ` (${m.credits_remaining} credit hours)`) : null,
          m.source_url ? h('p', { class: 'small muted' }, 'Source: ', h('a', { href: m.source_url, target: '_blank', rel: 'noopener' }, 'Undergraduate Prospectus 2025'), m.offered_by ? ` · ${m.offered_by}` : '') : null,
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
