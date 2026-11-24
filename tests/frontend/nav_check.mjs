// Headless-browser checks of the top navigation + assistant access.
// Usage: PUPPETEER=<path to puppeteer-core entry> CHROME=<chrome binary> node nav_check.mjs <baseUrl> <email> <password> <desktop|mobile> [screenshotDir]
const { default: puppeteer } = await import(process.env.PUPPETEER);
const [base, email, password, mode, shotDir] = process.argv.slice(2);
const out = { errors: [] };
const b = await puppeteer.launch({ executablePath: process.env.CHROME, headless: 'new', args: ['--no-sandbox'] });
const p = await b.newPage();
await p.setViewport(mode === "mobile" ? { width: 390, height: 800, isMobile: true } : { width: Number(process.env.VIEWPORT_W || 1366), height: 800 });
p.on('pageerror', e => out.errors.push('PAGEERR ' + e.message));
p.on('console', m => { if (m.type() === 'error' && !/401|403/.test(m.text())) out.errors.push('CONSOLE ' + m.text()); });
await p.goto(base + '/', { waitUntil: 'networkidle0' });
out.loginHasSidebar = !!(await p.$('.sidebar'));
await p.type('input[name=email]', email); await p.type('input[name=password]', password); await p.click('button[type=submit]');
await p.waitForSelector('.topnav', { timeout: 15000 });
await new Promise(r => setTimeout(r, 800));
out.hasSidebar = !!(await p.$('.sidebar'));
out.navPosition = await p.evaluate(() => { const r = document.querySelector('.topnav').getBoundingClientRect(); return { top: r.top, width: Math.round(r.width), vw: innerWidth }; });
const visible = (sel) => p.evaluate((s) => [...document.querySelectorAll(s)].filter(e => e.offsetParent !== null).map(e => e.textContent.trim().replace(/▾/, '').trim()), sel);
if (mode === 'mobile') {
  out.hamburgerVisible = (await visible('.menu-btn')).length > 0;
  out.linksHiddenBeforeOpen = (await visible('.navlinks > a.navlink')).length === 0;
  await p.click('.menu-btn'); await new Promise(r => setTimeout(r, 300));
}
out.topLevel = await visible('.navlinks > a.navlink, .navlinks > .dd > button.navlink');
out.activeLabel = await p.evaluate(() => (document.querySelector('.navlinks a.navlink.active, .navlinks .dd.active > .navlink') || {}).textContent?.replace(/▾/, '').trim());
// horizontal overflow check (layout must not scroll sideways)
out.navOverlap = await p.evaluate(() => {
  const links = [...document.querySelectorAll('.navlinks > *')].filter(e => e.getBoundingClientRect().width > 0); const right = document.querySelector('.topnav-right').getBoundingClientRect();
  if (!links.length) return false; const last = links[links.length - 1].getBoundingClientRect(); return last.right > right.left + 1 && getComputedStyle(document.querySelector('.navlinks')).display !== 'none' && last.top < right.bottom && last.bottom > right.top;
});
out.horizontalOverflow = await p.evaluate(() => document.documentElement.scrollWidth > innerWidth + 2);
// follow every top-level link and every dropdown child; each must render a page with no error alert
out.pages = [];
const hrefs = await p.evaluate(() => [...document.querySelectorAll('.navlinks a[data-href]')].map(a => a.dataset.href));
for (const h of [...new Set(hrefs)]) {
  await p.evaluate((x) => { location.hash = x; }, h); await new Promise(r => setTimeout(r, 1500));
  out.pages.push({ href: h, title: await p.evaluate(() => (document.querySelector('.content h1') || {}).textContent), alert: await p.evaluate(() => (document.querySelector('.content .alert.bad') || {}).textContent || null), active: await p.evaluate((x) => { const a = document.querySelector(`.navlinks a[data-href="${x}"]`); return !!a && a.classList.contains('active'); }, h) });
}
// dropdown opens on click
const dd = await p.$('.navlinks .dd > button.navlink');
if (dd && mode !== 'mobile') { await dd.click(); out.dropdownOpens = await p.evaluate(() => !!document.querySelector('.dd.open .dd-menu') && document.querySelector('.dd.open .dd-menu').getBoundingClientRect().width > 0); }
// the assistant is reachable from the current page
await p.evaluate(() => { location.hash = '#/' + (document.querySelector('.navlinks a[data-href]').dataset.href.slice(2)); }); await new Promise(r => setTimeout(r, 800));
const shown = `(el) => !!el && getComputedStyle(el).display !== 'none' && el.getBoundingClientRect().width > 0`;
out.assistantFabVisible = await p.evaluate(`(${shown})(document.querySelector('.chat-fab'))`);
if (out.assistantFabVisible) { await p.click('.chat-fab'); await new Promise(r => setTimeout(r, 400)); out.chatOpens = await p.evaluate(`(${shown})(document.querySelector('.chat-panel.open'))`); }
// profile menu
await p.click('.avatar'); out.profileMenu = await p.evaluate(() => [...document.querySelectorAll('.dd.profile .dd-menu a, .dd.profile .dd-menu button')].map(e => e.textContent.trim()));
if (shotDir) { await p.screenshot({ path: `${shotDir}/${mode}_${email.split('@')[0]}.png` }); }
console.log(JSON.stringify(out));
await b.close();
