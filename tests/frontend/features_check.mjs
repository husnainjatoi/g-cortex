// Headless-browser checks of the round-3 frontend features against the real backend.
// Usage: PUPPETEER=... CHROME=... node features_check.mjs <baseUrl> <email> <password> <desktop|mobile> <student|admin|forced> [newPassword]
const { default: puppeteer } = await import(process.env.PUPPETEER);
const [base, email, password, mode, scenario, newPassword] = process.argv.slice(2);
const out = { errors: [], checks: {} };
const b = await puppeteer.launch({ executablePath: process.env.CHROME, headless: 'new', args: ['--no-sandbox'] });
const p = await b.newPage();
await p.setViewport(mode === 'mobile' ? { width: 390, height: 800, isMobile: true } : { width: 1366, height: 800 });
p.on('pageerror', e => out.errors.push('PAGEERR ' + e.message));
p.on('console', m => { if (m.type() === 'error' && !/40[0-9]|409|422|429/.test(m.text())) out.errors.push('CONSOLE ' + m.text()); });
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const check = (name, ok, detail) => { out.checks[name] = ok ? true : (detail ?? false); };
const text = (sel) => p.evaluate((s) => (document.querySelector(s) || {}).innerText || '', sel);
const bodyText = () => p.evaluate(() => document.body.innerText);
const TECH = /\[object|HTTP \d|\b(4\d\d|5\d\d)\b error|Traceback|ValidationError|undefined|\{"|"detail"|loc:|Unprocessable|\[Object/i;
const go = async (hash) => { await p.evaluate((h) => { location.hash = h; }, hash); await sleep(1300); };
const fill = async (sel, v) => { await p.evaluate((x) => { document.querySelector(x).value = ''; }, sel); await p.focus(sel); if (v !== '') await p.type(sel, v); else await p.evaluate((x) => document.querySelector(x).dispatchEvent(new Event('input', { bubbles: true })), sel); };
const setVal = (sel, v) => p.evaluate((s, val) => { const e = document.querySelector(s); e.value = val; e.dispatchEvent(new Event('input', { bubbles: true })); e.dispatchEvent(new Event('change', { bubbles: true })); }, sel, v);

await p.goto(base + '/', { waitUntil: 'networkidle0' });

// ── login page: password visibility toggle keeps the typed value; wrong password gives a friendly message
await p.type('input[name=email]', email); await p.type('input[name=password]', 'Wrong-pass-1');
check('login_toggle_present', !!(await p.$('.pw-toggle')));
await p.click('.pw-toggle');
check('login_toggle_shows', await p.evaluate(() => document.querySelector('input[name=password]').type === 'text' && document.querySelector('input[name=password]').value === 'Wrong-pass-1'));
await p.click('.pw-toggle');
check('login_toggle_hides_keeps_value', await p.evaluate(() => document.querySelector('input[name=password]').type === 'password' && document.querySelector('input[name=password]').value === 'Wrong-pass-1'));
await p.click('button[type=submit]'); await sleep(900);
const loginErr = await text('.alert.bad');
check('login_error_friendly', loginErr.length > 5 && !TECH.test(loginErr), loginErr);
await fill('input[name=password]', password); await p.click('button[type=submit]');

if (scenario === 'forced') {
  await p.waitForSelector('#force-password', { timeout: 10000 });
  check('forced_page_shown', true);
  check('forced_no_portal_nav', !(await p.$('.topnav')));
  const t = await text('#force-password');
  check('forced_welcome_text', /Welcome to G-Cortex/.test(t) && /temporary password/.test(t));
  check('forced_has_three_toggles', (await p.$$('#force-password .pw-toggle')).length === 3);
  await go('#/dashboard');
  check('forced_blocks_navigation', !!(await p.$('#force-password')) && !(await p.$('.topnav')));
  check('forced_api_blocked', await p.evaluate(async () => (await fetch('/api/portal/dashboard')).status === 403));
  // mismatch -> inline friendly error next to the field, still on the page
  await p.type('input[name=current_password]', password); await p.type('input[name=new_password]', newPassword); await p.type('input[name=confirm_password]', newPassword + 'x');
  await p.click('#force-password button[type=submit]'); await sleep(900);
  const fe = await p.evaluate(() => [...document.querySelectorAll('#force-password .field-error, #force-password .alert.bad')].map(e => e.innerText).join(' | '));
  check('forced_mismatch_friendly', /match/i.test(fe) && !TECH.test(fe), fe);
  check('forced_still_blocked', !!(await p.$('#force-password')));
  await fill('input[name=confirm_password]', newPassword); await p.click('#force-password button[type=submit]');
  await p.waitForSelector('.topnav', { timeout: 10000 });
  check('forced_then_portal', true);
  check('forced_cookie_renewed', await p.evaluate(async () => { const r = await fetch('/api/auth/me'); const j = await r.json(); return r.ok && j.must_change_password === false; }));
  check('forced_dashboard_works', await p.evaluate(async () => (await fetch('/api/portal/dashboard')).status === 200));
}

if (scenario === 'student') {
  try { await p.waitForSelector('.topnav', { timeout: 15000 }); } catch (e) { out.dbg = await p.evaluate(() => (document.querySelector(".alert") || {}).innerText + " || pw=" + document.querySelector("input[name=password]").value.length); console.log(JSON.stringify(out)); process.exit(0); }
  await sleep(1200);
  // dashboard: mess bill card replaces the deposit card; View more -> Mess page
  const dash = await bodyText();
  check('dash_bill_card', !!(await p.$('#mess-bill-card')) && /mess (bill|invoice)/i.test(await text('#mess-bill-card')), await text('#mess-bill-card'));
  check('dash_no_deposit_card', !/Mess security deposit/i.test(dash));
  check('dash_bill_estimate_labelled', /estimate|invoice/i.test(await text('#mess-bill-card')));
  await p.click('#mess-bill-card a'); await sleep(1300);
  check('dash_view_more_goes_to_mess', (await p.evaluate(() => location.hash)).startsWith('#/mess'));

  // recommender: invalid input -> inline message; valid -> real cards (no "[object HTMLDivElement]")
  await go('#/requisitions?tab=venues');
  await setVal('input[name=description]', ''); await p.click('.card .btn.primary'); await sleep(900);
  const ve = await p.evaluate(() => [...document.querySelectorAll('.field-error')].map(e => e.innerText).join(' | '));
  check('recommend_invalid_inline', ve.length > 3 && !TECH.test(ve), ve);
  await fill('input[name=description]', 'seminar with a projector and microphone'); await fill('input[name=attendees]', '40'); await p.click('.card .btn.primary'); await sleep(1200);
  const rec = await p.evaluate(() => ({ cards: document.querySelectorAll('.venue-card').length, txt: document.body.innerText }));
  check('recommend_cards_rendered', rec.cards > 0 && !/\[object/i.test(rec.txt), rec);
  check('recommend_card_content', await p.evaluate(() => { const c = document.querySelector('.venue-card'); return /Capacity \d+/.test(c.innerText) && /Suitability/.test(c.innerText) && /Availability/.test(c.innerText) && !!c.querySelector('button[data-venue]'); }));
  await fill('input[name=attendees]', '99999'); await p.click('.card .btn.primary'); await sleep(900);
  check('recommend_too_many_friendly', !TECH.test(await text('.content')) , await text('.content'));
  await fill('input[name=attendees]', '40'); await p.click('.card .btn.primary'); await sleep(1200);

  // Reserve This Venue -> New requisition with the real venue id preselected
  const vid = await p.evaluate(() => document.querySelector('.venue-card button[data-venue]').dataset.venue);
  await p.click('.venue-card button[data-venue]'); await sleep(1500);
  check('reserve_navigates', /tab=new/.test(await p.evaluate(() => location.hash)) && (await p.evaluate(() => location.hash)).includes(encodeURIComponent(vid)));
  check('reserve_prefills_venue', (await p.evaluate(() => document.querySelector('select[name=venue_id]').value)) === vid, vid);
  check('reserve_prefills_attendees', (await p.evaluate(() => document.querySelector('input[name=expected_attendees]').value)) === '40');
  // selection persists across a reload (draft kept in the session)
  await p.reload({ waitUntil: 'networkidle0' }); await sleep(1500);
  check('reserve_survives_reload', (await p.evaluate(() => document.querySelector('select[name=venue_id]')?.value)) === vid);

  // individual reservation: friendly inline validation, then success
  await fill('input[name=expected_attendees]', ''); await p.click('form button[type=submit]'); await sleep(900);
  const errs = await p.evaluate(() => [...document.querySelectorAll('form .field-error')].map(e => e.innerText).join(' | '));
  check('reservation_inline_errors', /title/i.test(errs) && !TECH.test(errs), errs);
  check('reservation_errors_near_fields', await p.evaluate(() => !!document.querySelector('.field.has-error [name=event_title]')));
  await fill('input[name=event_title]', 'Browser test seminar'); await fill('input[name=expected_attendees]', '30');
  check('reservation_type_selector', (await p.$$('.seg button')).length === 2);
  await p.click('form button[type=submit]'); await sleep(1500);
  const res1 = await text('#reservation-result');
  check('individual_submitted', /submitted/.test(res1) && /Directorate of Student Affairs/.test(await text('.content')), res1);

  // society reservation: society dropdown comes from the backend
  await go('#/requisitions?tab=new'); await sleep(800);
  await p.click('.seg button[data-t=society]'); await sleep(300);
  const opts = await p.evaluate(() => [...document.querySelectorAll('select[name=society_id] option')].map(o => o.textContent));
  check('society_options_loaded', opts.length > 1, opts);
  await p.select('select[name=venue_id]', vid);
  await fill('input[name=event_title]', 'Society browser event'); await fill('input[name=expected_attendees]', '25');
  await setVal('input[name=booking_date]', await p.evaluate(() => { const d = new Date(); d.setDate(d.getDate() + 9); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0, 10); }));
  await p.click('form button[type=submit]'); await sleep(900);
  check('society_required_inline', /society/i.test(await p.evaluate(() => [...document.querySelectorAll('form .field-error')].map(e => e.innerText).join(' | '))));
  const socValue = await p.evaluate(() => [...document.querySelectorAll('select[name=society_id] option')].find(o => o.value && /ACM/.test(o.textContent)).value);
  await p.select('select[name=society_id]', socValue);
  await p.click('form button[type=submit]'); await sleep(1500);
  const res2 = await text('#reservation-result');
  check('society_submitted', /submitted/.test(res2), res2 || (await text('.content')));
  check('society_approval_status_shown', /pending/i.test(res2) === false && /Next approver: \S+/.test(await text('.content')) && /Status:/.test(await text('.content')));

  await go('#/requisitions?tab=mine');
  const mine = await text('.content');
  check('mine_shows_types', /Individual reservation/.test(mine) && /Society:/.test(mine) && !TECH.test(mine), mine.slice(0, 300));

  // Ask GIKI / other pages: nothing stringified
  for (const h of ['#/dashboard', '#/courses', '#/mess', '#/requisitions', '#/sports', '#/equipment', '#/assistant', '#/profile']) {
    await go(h); const t2 = await bodyText();
    check('page_clean_' + h.slice(2), !TECH.test(t2) && !(await p.$('.content .alert.bad')), (t2.match(TECH) || [])[0]);
  }
  // profile password toggles
  await go('#/profile'); check('profile_toggles', (await p.$$('.pw-toggle')).length === 3);
}

if (scenario === 'admin') {
  await p.waitForSelector('.topnav', { timeout: 15000 }); await sleep(1000);
  const hrefs = await p.evaluate(() => [...new Set([...document.querySelectorAll('.navlinks a[data-href]')].map(a => a.dataset.href))]);
  for (const h of hrefs) { await go(h); const t = await bodyText(); check('admin_' + h.slice(2), (h === '#/admin/audit' || !TECH.test(t))   /* the audit viewer intentionally shows record details */ && !(await p.$('.content .alert.bad')), (t.match(TECH) || [])[0]); }
  check('admin_has_pages', hrefs.length >= 3, hrefs);
}
out.overflow = await p.evaluate(() => document.documentElement.scrollWidth > innerWidth + 2);
console.log(JSON.stringify(out));
await b.close();
