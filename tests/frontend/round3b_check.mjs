// Headless-browser checks: admin societies, admin food expenditure, student mess page, degree plan / what-if, society reservation authority.
// Usage: PUPPETEER=... CHROME=... node round3b_check.mjs <baseUrl> <email> <password> <desktop|mobile> <scenario> '<json context>'
const { default: puppeteer } = await import(process.env.PUPPETEER);
const [base, email, password, mode, scenario, ctxJson] = process.argv.slice(2);
const X = JSON.parse(ctxJson || '{}');
const out = { errors: [], checks: {} };
const b = await puppeteer.launch({ executablePath: process.env.CHROME, headless: 'new', args: ['--no-sandbox'] });
const p = await b.newPage();
await p.setViewport(mode === 'mobile' ? { width: 390, height: 800, isMobile: true } : { width: 1366, height: 900 });
p.on('pageerror', e => out.errors.push('PAGEERR ' + e.message));
p.on('console', m => { if (m.type() === 'error' && !/40[0-9]|409|422|429/.test(m.text())) out.errors.push('CONSOLE ' + m.text()); });
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const check = (name, ok, detail) => { out.checks[name] = ok ? true : (detail ?? false); };
const text = (sel) => p.evaluate((s) => (document.querySelector(s) || {}).innerText || '', sel);
const TECH = /\[object|HTTP \d|Traceback|ValidationError|undefined|\{"|"detail"|loc:|Unprocessable|\bNaN\b/i;
const go = async (hash) => { await p.evaluate((h) => { location.hash = h; }, hash); await sleep(1400); };
const setVal = (sel, v) => p.evaluate((s, val) => { const e = document.querySelector(s); e.value = val; e.dispatchEvent(new Event('input', { bubbles: true })); e.dispatchEvent(new Event('change', { bubbles: true })); }, sel, v);
const clickText = async (sel, label) => { const ok = await p.evaluate((s, l) => { const e = [...document.querySelectorAll(s)].find(x => x.innerText.trim() === l || x.innerText.trim().startsWith(l)); if (e) { e.click(); return true; } return false; }, sel, label); if (!ok) throw new Error(`no ${sel} with text ${label}`); await sleep(700); };
const rowText = (needle) => p.evaluate((n) => { const r = [...document.querySelectorAll('.content tbody tr')].find(x => x.innerText.includes(n)); return r ? r.innerText : null; }, needle);
const rowClick = async (needle, label) => { const ok = await p.evaluate((n, l) => { const r = [...document.querySelectorAll('.content tbody tr')].find(x => x.innerText.includes(n)); const bt = r && [...r.querySelectorAll('button')].find(x => x.innerText.trim() === l); if (bt) { bt.click(); return true; } return false; }, needle, label); if (!ok) throw new Error(`row ${needle} has no ${label}`); await sleep(900); };
const toastText = () => p.evaluate(() => [...document.querySelectorAll('#toasts .toast')].map(t => t.innerText).join(' | '));
const modalFill = async (name, v) => setVal(`.modal [name="${name}"]`, v);

await p.goto(base + '/', { waitUntil: 'networkidle0' });
await p.type('input[name=email]', email); await p.type('input[name=password]', password); await p.click('button[type=submit]');
await p.waitForSelector('.topnav', { timeout: 15000 }); await sleep(1000);

if (scenario === 'admin_societies') {
  const nav = await p.evaluate(() => [...document.querySelectorAll('.navlinks a, .navlinks .dd-menu a')].map(a => a.textContent.trim()));
  check('nav_has_society_management', nav.includes('Society Management'), nav);
  await go('#/admin/societies');
  check('title', (await text('.content h1')) === 'Society Management');
  const rows = await p.evaluate(() => document.querySelectorAll('.content tbody tr').length);
  check('lists_societies', rows >= 3, rows);
  check('shows_source_and_status', /active/.test(await text('.content table')) && /legacy|official|manual/.test(await text('.content table')));
  await setVal('.content input[type=search]', 'Test Society'); await sleep(900);
  check('search_filters', (await p.evaluate(() => document.querySelectorAll('.content tbody tr').length)) === 2);
  // add
  await clickText('.content button', '+ Add society');
  await modalFill('name', 'Browser Test Society'); await modalFill('description', 'Created from the browser test'); await modalFill('category', 'technical');
  await clickText('.modal footer button', 'Save'); await sleep(900);
  await setVal('.content input[type=search]', 'Browser Test'); await sleep(900);
  check('added_visible', !!(await rowText('Browser Test Society')));
  // edit
  await rowClick('Browser Test Society', 'Edit');
  await modalFill('name', 'Browser Test Society 2'); await clickText('.modal footer button', 'Save'); await sleep(900);
  check('edited', !!(await rowText('Browser Test Society 2')) && /edited/.test(await rowText('Browser Test Society 2')));
  // validation message is friendly
  await rowClick('Browser Test Society 2', 'Edit'); await modalFill('name', 'ab'); await clickText('.modal footer button', 'Save'); await sleep(700);
  const t = await toastText(); check('validation_friendly', /name/i.test(t) && !TECH.test(t), t);
  await clickText('.modal footer button', 'Cancel');
  // details + members
  await setVal('.content input[type=search]', 'Test Society A'); await sleep(900);
  await rowClick('Test Society A', 'Details');
  check('details_shows_source', /Source:/.test(await text('.modal')) && /Members who may reserve/.test(await text('.modal')));
  await setVal('.modal input[placeholder^="Student ID"]', X.add_member); await clickText('.modal button', 'Add member'); await sleep(900);
  check('member_added', (await text('.modal')).includes(X.add_member));
  await clickText('.modal .x', '×');
  // deactivate
  await setVal('.content input[type=search]', 'Browser Test'); await sleep(900);
  await rowClick('Browser Test Society 2', 'Deactivate'); await clickText('.modal footer button', 'Deactivate'); await sleep(900);
  check('deactivated', /inactive/.test((await rowText('Browser Test Society 2')) || ''));
  check('incomplete_filter', await p.evaluate(() => { const s = [...document.querySelectorAll('.content select')][0]; return [...s.options].some(o => o.value === 'incomplete'); }));
}

if (scenario === 'admin_society_denied') {
  const nav = await p.evaluate(() => [...document.querySelectorAll('.navlinks a, .navlinks .dd-menu a')].map(a => a.textContent.trim()));
  check('no_society_menu_for_this_role', !nav.includes('Society Management'), nav);
  await go('#/admin/societies');
  check('blocked_page', /permission|scope/i.test(await text('.content')) || (await p.evaluate(() => !!document.querySelector('.content .alert.bad'))), await text('.content'));
}

if (scenario === 'admin_mess') {
  await go('#/admin/mess');
  check('expenditure_tab_first', /Record monthly food expenditure/.test(await text('.content')));
  const month = X.month;
  await setVal('.content [name=month]', month); await setVal('.content [name=amount_pkr]', ''); await clickText('.content button', 'Record expenditure'); await sleep(800);
  const e1 = await p.evaluate(() => [...document.querySelectorAll('.field-error')].map(e => e.innerText).join(' | ')) + ' ' + await toastText();
  check('empty_amount_friendly', e1.trim().length > 3 && !TECH.test(e1), e1);
  await setVal('.content [name=amount_pkr]', '3000000'); await clickText('.content button', 'Record expenditure'); await sleep(1500);
  await go('#/admin/mess'); await sleep(500);
  const row = await rowText(X.label);
  check('recorded_row', !!row && /3,000,000/.test(row) && /recorded/.test(row), row);
  check('denominator_from_backend', new RegExp(`\\b${X.users}\\b`).test(row || ''), row);
  check('per_student_from_backend', (row || '').includes(X.per_student), row);
  await setVal('.content [name=month]', month); await setVal('.content [name=amount_pkr]', '1000'); await clickText('.content button', 'Record expenditure'); await sleep(900);
  const dup = await toastText(); check('duplicate_blocked_friendly', /already been recorded/.test(dup) && !TECH.test(dup), dup);
  await rowClick(X.label, 'Correct');
  await modalFill('amount_pkr', '3600000'); await clickText('.modal footer button', 'Save correction'); await sleep(900);
  const reasonErr = await p.evaluate(() => [...document.querySelectorAll('.modal .field-error')].map(e => e.innerText).join(' | ')) + await toastText();
  check('reason_required', /reason/i.test(reasonErr) && !TECH.test(reasonErr), reasonErr);
  await modalFill('reason', 'Supplier invoice corrected'); await clickText('.modal footer button', 'Save correction'); await sleep(1500);
  await go('#/admin/mess'); await sleep(500);
  const t2 = await text('.content');
  check('history_keeps_superseded', /superseded/.test(t2) && /active/.test(t2) && /3,000,000/.test(t2) && /3,600,000/.test(t2) && /Supplier invoice corrected/.test(t2));
  check('shows_recorded_by', /Mess Office/.test(t2));
}

if (scenario === 'student_mess') {
  await go('#/mess');
  const dis = await text('#estimate-disclaimer');
  check('disclaimer_exact', dis.includes(X.disclaimer), dis);
  check('says_estimate_not_invoice', /ESTIMATE/.test(dis) && /not an invoice/i.test(dis));
  check('estimate_amount', (await text('#bill-amount')).includes(X.estimate), await text('#bill-amount'));
  const body = await text('.content');
  check('calculation_visible', body.includes('3,000') && /÷/.test(body) && /distinct/.test(body));
  check('deposit_separate_note', /security deposit is separate/i.test(body));
  await setVal('.content input[type=month]', X.empty_month); await clickText('.content button', 'Show this month'); await sleep(900);
  check('no_number_invented', /Not available yet/.test(await text('#bill-amount')) && /not recorded|Not available/.test(await text('.content')), await text('.content'));
  await clickText('.tabs button', 'Invoices & payments');
  const inv = await text('.content');
  check('invoice_number_shown', inv.includes(X.invoice_no), X.invoice_no);
  check('invoice_status_unpaid', /unpaid/.test(inv)); check('invoice_summary_outstanding', /outstanding/i.test(inv)); check('invoice_summary_pending', /Pending verification/i.test(inv), inv.slice(0, 400));
  await clickText('.content button', 'Details'); await sleep(900);
  check('invoice_details', /Finalized/.test(await text('.modal')) && /balance/.test(await text('.modal')));
  await p.evaluate(() => document.querySelector('.modal .x').click()); await sleep(300);
  await clickText('.tabs button', 'Security deposit');
  const dep = await text('.content');
  check('deposit_15000', /15,000/.test(dep) && /paid/i.test(dep) && /refundable balance/i.test(dep), dep.slice(0, 900));
  await clickText('.tabs button', 'Meal attendance');
  check('attendance_rows', (await p.evaluate(() => document.querySelectorAll('.content tbody tr').length)) >= 3 && /lunch/.test(await text('.content')));
}

if (scenario === 'student_planner') {
  await go('#/courses'); await clickText('.tabs button', 'Degree plan'); await sleep(1500);
  check('own_programme_selected', (await p.evaluate(() => document.querySelector('#degree-programme').selectedOptions[0].textContent)) === X.own_programme);
  const t = await text('.content');
  check('shows_semesters_and_status', /Semester 1/.test(t) && /completed/.test(t) && /in progress/.test(t) && !TECH.test(t), t.slice(0, 900));
  check('demo_assumption_disclosed', /Demo account/.test(t));
  check('source_cited', /Undergraduate Prospectus 2025/.test(t) && /page \d+/.test(t));
  await p.select('#degree-programme', X.other_code); await sleep(1400);
  const t2 = await text('.content');
  check('what_if_other_programme', /What-if: this is/.test(t2) && /Semester 1/.test(t2), t2.slice(0, 200));
  await clickText('.tabs button', 'Planner & what-if'); await sleep(1500);
  const opts = await p.evaluate(() => [...document.querySelectorAll('.content select[multiple]')][0] ? [...[...document.querySelectorAll('.content select[multiple]')][0].options].map(o => o.textContent) : []);
  check('only_official_minors_listed', opts.length === 1 && opts[0] === 'Management for Engineers', opts);
  check('unverified_not_presented_as_official', /unverified/.test(await text('.content')) && !/Software Systems/.test(await text('.content select')));
  await clickText('.content button', 'Simulate'); await sleep(1500);
  const sim = await text('.content');
  check('official_minor_result', /Management for Engineers/.test(sim) && /does not state/.test(sim) && /Undergraduate Prospectus 2025/.test(sim), sim.slice(-400));
}

if (scenario === 'society_reservation') {
  await go('#/requisitions?tab=new'); await sleep(800);
  await p.click('.seg button[data-t=society]'); await sleep(300);
  const pick = async (name) => p.select('select[name=society_id]', String(X.societies[name]));
  const fill = async () => { await p.select('select[name=venue_id]', 'SEM_HALL_1'); await setVal('input[name=event_title]', 'Auth test'); await setVal('input[name=expected_attendees]', '20'); await setVal('input[name=booking_date]', X.date); };
  await fill();
  if (X.expect === 'member') {
    await pick('with_members');
    await p.click('form button[type=submit]'); await sleep(1500);
    const r = await text('#reservation-result'); const all = await text('.content');
    check('verified_member_submitted', /submitted/.test(r) && !/could not be verified/.test(all), all.slice(0, 300));
    check('member_next_approver_shown', /Next approver: \S+/.test(all));
  } else if (X.expect === 'unverified') {
    await pick('no_members');
    await p.click('form button[type=submit]'); await sleep(1500);
    const all = await text('.content');
    check('unverified_submitted_with_warning', /submitted/.test(all) && /could not be verified from membership records/.test(all) && /administrator will confirm/.test(all), all.slice(0, 400));
  } else if (X.expect === 'non_member') {
    await pick('with_members');
    await p.click('form button[type=submit]'); await sleep(1500);
    const all = await text('.content');
    check('non_member_refused_friendly', /not recorded as a member/.test(all) && !/submitted/.test(await text('#reservation-result')) && !TECH.test(all), all.slice(0, 400));
    const direct = await p.evaluate(async (sid, d) => { const r = await fetch('/api/req/requisitions', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reservation_type: 'society', society_id: sid, venue_id: 'SEM_HALL_2', event_title: 'bypass', booking_date: d, start_time: '15:00', duration_hours: 2, expected_attendees: 5 }) }); return r.status; }, X.societies.with_members, X.date);
    check('backend_blocks_bypass', direct === 403, direct);
  }
  // find-a-venue flow still works on the same page
  await go('#/requisitions?tab=venues'); await setVal('input[name=description]', 'seminar with projector'); await setVal('input[name=attendees]', '40'); await clickText('.content button', 'Recommend'); await sleep(1500);
  const vid = await p.evaluate(() => (document.querySelector('.venue-card button[data-venue]') || {}).dataset?.venue);
  check('recommendations_still_render', !!vid && !/\[object/.test(await text('.content')));
  await p.click('.venue-card button[data-venue]'); await sleep(1500);
  check('reserve_this_venue_still_prefills', (await p.evaluate(() => document.querySelector('select[name=venue_id]')?.value)) === vid);
}

if (scenario === 'admin_reviews_reservations') {
  await go('#/admin/societies'); await setVal('.content input[type=search]', 'Test Society B'); await sleep(900);
  await rowClick('Test Society B', 'Details');
  const m = await text('.modal');
  check('request_listed_with_authority_flag', /REQ-/.test(m) && /to confirm/.test(m), m.slice(-300));
  await p.evaluate(() => document.querySelector('.modal .x').click());
  await go('#/admin/requisitions');
  check('approvals_page_lists_it', /Test Society B/.test(await text('.content')) || /Auth test/.test(await text('.content')), (await text('.content')).slice(0, 300));
}

check('no_technical_text_anywhere', !TECH.test(await text('body')), (await text('body')).match(TECH)?.[0]);
out.overflow = await p.evaluate(() => document.documentElement.scrollWidth > innerWidth + 2);
console.log(JSON.stringify(out));
await b.close();
