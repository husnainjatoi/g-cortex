import { api } from './api.js';
import { h, clear, toast, put, passwordInput, field, form } from './ui.js';
import { createChat } from './chat.js';

const root = document.getElementById('app');
const S = { user: null };

// Top-navigation model. A leaf is [href, label]; a group is [label, [[href, label], ...]]. Only items the user may use are listed
// (the backend still enforces every permission - hiding is convenience, not security).
function navFor(u) {
  if (u.role === 'student') return [
    ['#/dashboard', 'Dashboard'], ['#/courses', 'Courses'], ['#/mess', 'Mess'], ['#/requisitions', 'Requisitions'],
    ['Facilities', [['#/sports', 'Sports Booking'], ['#/equipment', 'Lab Equipment']]], ['#/assistant', 'AI Assistant'],
  ];
  if (u.role === 'faculty') return [['#/approvals', 'Approvals'], ['#/mess-forecast', 'Mess Forecast'], ['#/assistant', 'AI Assistant']];
  const has = (s) => u.scopes.includes('super') || u.scopes.includes(s);
  const items = [['#/admin', 'Admin Dashboard']];
  if (has('academic')) items.push(['Academics', [['#/admin/courses', 'Course Management'], ['#/admin/knowledge', 'Official GIKI Data']]]);
  if (has('super')) items.push(['#/admin/users', 'Student Management']);
  if (has('mess')) items.push(['#/admin/mess', 'Mess Administration']);
  if (has('requisition')) items.push(['#/admin/requisitions', 'Requisition Approvals'], ['Resources', [['#/admin/societies', 'Society Management'], ['#/admin/sports', 'Sports & Venues'], ['#/admin/equipment', 'Equipment Inventory']]]);
  const reports = [['#/admin/reports', 'Reports & Analytics']];
  if (has('super')) reports.push(['#/admin/settings', 'Rules & Settings'], ['#/admin/audit', 'Audit & Events']);
  items.push(['Reports & System', reports]);
  return items;
}

const routes = {
  '/dashboard': () => import('./pages/dashboard.js'), '/courses': () => import('./pages/courses.js'), '/mess': () => import('./pages/mess.js'),
  '/requisitions': () => import('./pages/requisitions.js'), '/sports': () => import('./pages/sports.js'), '/equipment': () => import('./pages/equipment.js'),
  '/notifications': () => import('./pages/notifications.js'), '/assistant': () => import('./pages/assistant.js'), '/approvals': () => import('./pages/approvals.js'),
  '/mess-forecast': () => import('./pages/forecast.js'),
  '/admin': () => import('./pages/admin_dashboard.js'), '/admin/reports': () => import('./pages/admin_overview.js'), '/admin/knowledge': () => import('./pages/admin_knowledge.js'), '/profile': () => import('./pages/profile.js'), '/admin/courses': () => import('./pages/admin_courses.js'), '/admin/mess': () => import('./pages/admin_mess.js'),
  '/admin/requisitions': () => import('./pages/admin_requisitions.js'), '/admin/sports': () => import('./pages/admin_sports.js'), '/admin/societies': () => import('./pages/admin_societies.js'),
  '/admin/equipment': () => import('./pages/admin_equipment.js'), '/admin/users': () => import('./pages/admin_users.js'),
  '/admin/settings': () => import('./pages/admin_settings.js'), '/admin/audit': () => import('./pages/admin_audit.js'),
};
const home = () => ({ student: '#/dashboard', faculty: '#/approvals', admin: '#/admin' }[S.user.role]);

let content, chatDrawer, bell;
export const refresh = () => route();

async function route() {
  if (!S.user) return renderLogin();
  if (S.user.must_change_password) return document.getElementById('force-password') ? undefined : renderForcePasswordChange();   // nothing else is reachable until the password is changed
  const path = (location.hash.replace(/^#/, '') || '').split('?')[0];
  if (!routes[path]) { location.hash = home(); return; }
  document.body.classList.toggle('on-assistant', path === '/assistant');
  document.body.classList.toggle('on-assistant', path === '/assistant');
  markActive(path);
  document.getElementById('topnav')?.classList.remove('open');
  document.querySelectorAll('.dd.open').forEach(d => d.classList.remove('open'));
  clear(content).append(h('div', { class: 'muted' }, 'Loading…'));
  try {
    const mod = await routes[path]();
    const frag = h('div'); await mod.render(frag, { user: S.user, refresh: route });
    clear(content).append(frag);
  } catch (e) {
    clear(content).append(h('div', { class: 'alert bad' }, e.status === 403 ? 'You do not have permission to view this page.' : e.message));
  }
  updateBell();
}

async function updateBell() {
  try { const r = await api.get('/api/auth/me'); S.user = { ...S.user, ...r }; if (bell) bell.textContent = r.unread_notifications ? `🔔 ${r.unread_notifications}` : '🔔'; } catch (_) { /* session expired handled on next call */ }
}

function markActive(path) {
  document.querySelectorAll('.topnav a[data-href]').forEach(a => a.classList.toggle('active', a.dataset.href === '#' + path));
  document.querySelectorAll('.topnav .dd').forEach(d => d.classList.toggle('active', !!d.querySelector('a.active')));
}

function navItem(item) {
  if (typeof item[1] === 'string') return h('a', { href: item[0], 'data-href': item[0], class: 'navlink' }, item[1]);
  const [label, kids] = item;
  const dd = h('div', { class: 'dd' },
    h('button', { class: 'navlink', 'aria-haspopup': 'true', onclick: (e) => { e.stopPropagation(); const open = dd.classList.contains('open'); document.querySelectorAll('.dd.open').forEach(d => d.classList.remove('open')); dd.classList.toggle('open', !open); } }, label, h('span', { class: 'caret' }, '▾')),
    h('div', { class: 'dd-menu' }, kids.map(([href, text]) => h('a', { href, 'data-href': href }, text))));
  return dd;
}

function layout() {
  clear(root);
  const links = h('nav', { class: 'navlinks', 'aria-label': 'Main' }, navFor(S.user).map(navItem));
  bell = h('a', { href: '#/notifications', class: 'iconbtn', title: 'Notifications', 'aria-label': 'Notifications' }, '🔔');
  const initials = S.user.full_name.split(' ').map(w => w[0]).slice(0, 2).join('').toUpperCase();
  const profile = h('div', { class: 'dd profile' },
    h('button', { class: 'avatar', 'aria-haspopup': 'true', 'aria-label': 'Account menu', onclick: (e) => { e.stopPropagation(); const open = profile.classList.contains('open'); document.querySelectorAll('.dd.open').forEach(d => d.classList.remove('open')); profile.classList.toggle('open', !open); } }, initials),
    h('div', { class: 'dd-menu right' },
      h('div', { class: 'who' }, h('b', null, S.user.full_name), h('span', { class: 'small muted' }, S.user.role === 'admin' ? `Administrator · ${S.user.scopes.join(', ')}` : `${S.user.role} · ${S.user.user_id}`)),
      h('a', { href: '#/profile', 'data-href': '#/profile' }, 'Profile & password'), h('a', { href: '#/notifications', 'data-href': '#/notifications' }, 'Notifications & activity'),
      h('button', { class: 'linkbtn', onclick: async () => { await api.post('/api/auth/logout').catch(() => {}); S.user = null; sessionStorage.removeItem('gcortex_chat'); location.hash = ''; renderLogin(); } }, 'Sign out')));
  const bar = h('header', { class: 'topnav', id: 'topnav' },
    h('div', { class: 'topnav-inner' },
      h('a', { class: 'brand', href: '#', onclick: (e) => { e.preventDefault(); location.hash = home(); } }, h('img', { src: '/static/img/logo.png', alt: 'G-Cortex' }), h('span', null, 'G-Cortex', h('small', null, 'GIKI Campus Portal'))),
      links,
      h('div', { class: 'topnav-right' },
        h('button', { class: 'btn sm assistant-btn', onclick: () => { if (location.hash === '#/assistant') return; window.__chatDrawer.classList.toggle('open'); window.__chat.focus(); }, title: 'Ask G-Cortex' }, '✦', h('span', { class: 'lbl' }, ' Ask')),
        bell, profile,
        h('button', { class: 'menu-btn iconbtn', 'aria-label': 'Menu', 'aria-expanded': 'false', onclick: (e) => { const o = bar.classList.toggle('open'); e.currentTarget.setAttribute('aria-expanded', o); } }, '☰'))));
  content = h('main', { class: 'content' });
  root.append(bar, content);
  document.addEventListener('click', () => document.querySelectorAll('.dd.open').forEach(d => d.classList.remove('open')));
  const chat = createChat({ role: S.user.role, onChange: () => route() });
  chatDrawer = chat.el; window.__chat = chat; window.__chatDrawer = chatDrawer;
  const fab = h('button', { class: 'chat-fab', title: 'Ask G-Cortex', 'aria-label': 'Open assistant', onclick: () => { chatDrawer.classList.toggle('open'); chat.focus(); } }, '✦');
  root.append(chatDrawer, fab);
}

function renderLogin() {
  clear(root);
  const err = h('div');
  const email = h('input', { type: 'email', name: 'email', required: true, autocomplete: 'username', placeholder: 'u2024405@giki.edu.pk' });
  const pwWrap = passwordInput({ name: 'password', required: true, autocomplete: 'current-password' }); const pw = pwWrap.querySelector('input');
  const btn = h('button', { class: 'btn primary', type: 'submit', style: 'width:100%;justify-content:center;padding:11px' }, 'Sign in');
  const f = h('form', { onsubmit: async (e) => {
    e.preventDefault(); btn.disabled = true; clear(err);
    try { const u = await api.post('/api/auth/login', { email: email.value, password: pw.value }); S.user = u; boot(u); }
    catch (ex) { err.append(h('div', { class: 'alert bad' }, ex.message)); btn.disabled = false; }
  } },
    h('h1', null, 'Sign in'), h('p', { class: 'muted', style: 'margin:0 0 18px' }, 'Use your GIKI email and portal password. Your role is determined by your account record.'),
    err, h('div', { class: 'field' }, h('label', null, 'Email'), email), h('div', { class: 'field' }, h('label', null, 'Password'), pwWrap), btn,
    h('p', { class: 'hint', style: 'margin-top:14px' }, 'Forgot your password or no account yet? Contact the Directorate of Student Affairs / IT office to be provisioned.'));
  root.append(h('div', { class: 'login-wrap' },
    h('section', { class: 'login-hero' }, h('img', { src: '/static/img/logo.png', alt: 'G-Cortex logo' }), h('h1', null, 'G-Cortex'),
      h('p', null, 'The campus operating system for GIKI — academics, dining and campus resources in one place, with an assistant that can do the work for you.'),
      h('ul', null, h('li', null, 'Register for courses with live prerequisite and timetable checks'), h('li', null, 'Monthly mess bills, your security deposit and a dynamic dining QR'),
        h('li', null, 'Sports bookings, venue requisitions and lab equipment custody'))),
    h('section', { class: 'login-card' }, f)));
}

function boot(u) {
  S.user = u;
  if (u.must_change_password) return renderForcePasswordChange();
  layout();
  if (!location.hash || !routes[location.hash.replace(/^#/, '').split('?')[0]]) location.hash = home(); else route();
}

// Dedicated, full-page screen shown while the account still uses its temporary password (the server also refuses every other API call).
function renderForcePasswordChange() {
  clear(root); document.body.classList.remove('on-assistant');
  const f = form([
    h('div', { class: 'field', style: 'grid-column:1/-1' }, h('label', null, 'Current (temporary) password'), passwordInput({ name: 'current_password', autocomplete: 'current-password' })),
    h('div', { class: 'field', style: 'grid-column:1/-1' }, h('label', null, 'New password'), passwordInput({ name: 'new_password', autocomplete: 'new-password' }), h('div', { class: 'hint' }, 'At least 8 characters, with letters and digits. It must differ from the temporary password.')),
    h('div', { class: 'field', style: 'grid-column:1/-1' }, h('label', null, 'Confirm new password'), passwordInput({ name: 'confirm_password', autocomplete: 'new-password' }))],
    async (d) => {
      await api.post('/api/auth/change-password', d);               // the server revokes old sessions and sets a fresh cookie
      const me = await api.get('/api/auth/me');                      // proves the renewed session works
      toast('Your password has been changed. Welcome to G-Cortex!', 'ok', 6000);
      location.hash = ''; boot(me);
    }, 'Set new password');
  f.querySelector('.form-grid').style.gridTemplateColumns = '1fr';
  root.append(h('div', { class: 'force-wrap' }, h('div', { class: 'force-card', id: 'force-password' },
    h('img', { src: '/static/img/logo.png', alt: 'G-Cortex', style: 'height:44px' }),
    h('h1', { style: 'margin:10px 0 6px' }, `Welcome to G-Cortex, ${S.user.full_name.split(' ')[0]}`),
    h('p', { class: 'muted' }, 'Your account was created with a temporary password that is based on information the university already holds. To keep your account and personal records safe, please choose a private password before you continue.'),
    h('div', { class: 'alert warn' }, 'You cannot use the portal until you set a new password.'), f,
    h('p', { style: 'margin-top:14px' }, h('button', { class: 'linkbtn', type: 'button', onclick: async () => { await api.post('/api/auth/logout').catch(() => {}); S.user = null; location.hash = ''; renderLogin(); } }, 'Sign out')))));
}

window.addEventListener('hashchange', () => { if (S.user) route(); });
(async () => { try { boot(await api.get('/api/auth/me')); } catch (_) { renderLogin(); } })();
export const currentUser = () => S.user;
