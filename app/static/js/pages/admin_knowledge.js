import { api } from '../api.js';
import { h, clear, table, badge, tabs, toast, confirmDialog, pageHead, fmtDateTime, statCard } from '../ui.js';

export async function render(root, ctx) {
  root.append(pageHead('Official GIKI Data', 'Ask GIKI knowledge base and course catalogue, imported only from giki.edu.pk with source tracking.'));
  const st = await api.get('/api/admin/knowledge/status');
  root.append(h('div', { class: 'grid g4' }, statCard('Official sources', st.totals.sources), statCard('Indexed passages', st.totals.chunks, `${st.totals.embedded} with vector embeddings`), statCard('Courses with source', st.totals.courses_with_source), statCard('Open review flags', st.totals.open_flags)));
  root.append(h('div', { class: 'card' }, h('div', { class: 'row' }, h('button', { class: 'btn primary', disabled: st.running, onclick: async () => { if (!(await confirmDialog('Refresh official data?', 'Re-crawls the configured pages and documents on giki.edu.pk only (robots.txt honoured, rate limited), re-indexes changed documents and re-validates the course catalogue. This can take several minutes.', 'Start refresh'))) return; try { await api.post('/api/admin/knowledge/refresh'); toast('Refresh started in the background.', 'ok'); setTimeout(ctx.refresh, 1500); } catch (e) { toast(e.message, 'bad'); } } }, st.running ? 'Refresh running…' : 'Refresh from giki.edu.pk'),
    st.last_run ? h('span', { class: 'muted' }, `Last run #${st.last_run.run_id}: ${st.last_run.status}${st.last_run.finished_at ? ' · ' + fmtDateTime(st.last_run.finished_at) : ''}`) : h('span', { class: 'muted' }, 'Never run'))));
  const holder = h('div'); root.append(holder);
  tabs(holder, [{ key: 'f', label: 'Course review flags', render: () => flags(ctx) }, { key: 'p', label: 'Degree programmes', render: () => programmes(ctx) }, { key: 's', label: 'Sources', render: sources }]);
}

async function flags(ctx) {
  const rows = await api.get('/api/admin/knowledge/flags?status=open');
  return h('div', null, h('div', { class: 'alert info' }, 'Imported data never overwrites existing courses. Differences, ambiguous or unresolvable prerequisites are listed here for your decision. “Apply official” updates the course from the verified source; “Dismiss” keeps the current data.'),
    table([{ label: 'Course', render: f => h('b', null, f.course_code) }, { label: 'Issue', render: f => badge(f.kind.replace(/_/g, ' '), 'warn') }, { label: 'Details', render: f => h('span', { class: 'small' }, JSON.stringify(f.detail)) }, { label: 'Source', render: f => f.source_url ? h('a', { href: f.source_url, target: '_blank', rel: 'noopener' }, 'giki.edu.pk') : '' },
      { label: '', render: f => h('span', { class: 'row' }, ['title_mismatch', 'credits_mismatch', 'prereq_mismatch'].includes(f.kind) ? h('button', { class: 'btn sm primary', onclick: () => act(f, 'apply') }, 'Apply official') : null, h('button', { class: 'btn sm', onclick: () => act(f, 'dismiss') }, 'Dismiss')) }], rows, { empty: 'No open flags.' }));
  async function act(f, a) { try { await api.post(`/api/admin/knowledge/flags/${f.flag_id}/${a}`); toast('Done.', 'ok'); ctx.refresh(); } catch (e) { toast(e.message, 'bad'); } }
}

async function sources() {
  const rows = await api.get('/api/admin/knowledge/sources');
  return table([{ label: 'Document', render: s => h('a', { href: s.url, target: '_blank', rel: 'noopener' }, s.title || s.url) }, { label: 'Type', key: 'doc_type' }, { label: 'Passages', key: 'chunks' }, { label: 'Fetched', render: s => fmtDateTime(s.fetched_at) }, { label: 'Last checked', render: s => fmtDateTime(s.last_checked_at) }, { label: 'Version', render: s => s.version_label ? `${s.series || ''} ${s.version_label}`.trim() : '—' }, { label: 'Embedded', render: s => `${s.embedded}/${s.chunks}` }, { label: 'Status', render: s => badge(s.status, s.status === 'active' ? 'ok' : s.status === 'superseded' ? 'mute' : 'bad') }, { label: 'Note', render: s => s.error || '' }], rows, { empty: 'No sources ingested yet. Run a refresh.' });
}

async function programmes(ctx) {
  const rows = await api.get('/api/admin/knowledge/programs');
  return h('div', null, h('div', { class: 'alert info' }, 'Semester-wise degree plans imported from the Undergraduate Prospectus 2025. A plan marked “needs review” has rows that could not be read unambiguously (or whose credit total does not match the printed total); nothing was guessed. The import is repeatable and never overwrites hand-made courses.'),
    h('p', null, h('button', { class: 'btn primary', onclick: async () => { if (!(await confirmDialog('Re-run the curriculum import?', 'Re-reads the stored prospectus. It is idempotent: unchanged data stays unchanged.', 'Import'))) return; try { const r = await api.post('/api/admin/knowledge/curriculum-import'); toast(`Done: ${r.programs} programmes, ${r.program_rows} plan rows, ${r.courses_created} new catalogue courses.`, 'ok', 7000); ctx.refresh(); } catch (e) { toast(e.message, 'bad', 7000); } } }, 'Re-run curriculum import')),
    table([{ label: 'Programme', render: p => h('div', null, h('b', null, p.name), h('div', { class: 'small muted' }, p.faculty)) }, { label: 'Plan rows', key: 'rows' }, { label: 'Tracks', key: 'tracks' }, { label: 'Page', key: 'source_page' },
      { label: 'Status', render: p => badge(p.plan_status.replace('_', ' '), p.plan_status === 'parsed' ? 'ok' : 'warn') },
      { label: 'Review notes', render: p => p.review_notes.length ? h('ul', { class: 'small' }, p.review_notes.map(n => h('li', null, n.kind === 'unparsed_row' ? `Row not read (p. ${n.page}): ${n.line}` : `Semester ${n.semester}: parsed ${n.parsed} credit hours, printed total ${n.declared}`))) : '—' }], rows, { empty: 'No programmes imported yet.' }));
}
