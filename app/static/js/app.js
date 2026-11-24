import { api } from './api.js';
import { h, clear, toast } from './ui.js';
import { createChat } from './chat.js';

const root = document.getElementById('app');
const S = { user: null };

const ICON = { dash: '▦', course: '🎓', mess: '🍽', req: '📋', sport: '🏸', equip: '🔧', bell: '🔔', bot: '✦', admin: '⚙', users: '👥', audit: '🛡', chart: '📊', check: '✔' };
function navFor(u) {
  if (u.role === 'student') return [
    ['Overview', [['#/dashboard', 'Dashboard', ICON.dash]]],
    ['Academics', [['#/courses', 'Courses & Timetable', ICON.course]]],
    ['Campus Services', [['#/mess', 'Mess', ICON.mess], ['#/requisitions', 'Requisitions', ICON.req], ['#/sports', 'Sports Booking', ICON.sport], ['#/equipment', 'Lab Equipment', ICON.equip]]],
    ['Assistant', [['#/assistant', 'G-Cortex Assistant', ICON.bot], ['#/notifications', 'Notifications & Activity', ICON.bell]]],
  ];
  if (u.role === 'faculty') return [
    ['Faculty', [['#/approvals', 'Approvals', ICON.check], ['#/mess-forecast', 'Mess Forecast', ICON.chart]]],
    ['Assistant', [['#/assistant', 'G-Cortex Assistant', ICON.bot], ['#/notifications', 'Notifications & Activity', ICON.bell]]],
  ];
  const has = (s) => u.scopes.includes('super') || u.scopes.includes(s);
  const adm = [['#/admin', 'Overview & Reports', ICON.chart]];
  if (has('academic')) adm.push(['#/admin/courses', 'Course Administration', ICON.course]);
  if (has('mess')) adm.push(['#/admin/mess', 'Mess Office', ICON.mess]);
  if (has('requisition')) adm.push(['#/admin/requisitions', 'Requisition Approvals', ICON.req], ['#/admin/sports', 'Sports & Venues', ICON.sport], ['#/admin/equipment', 'Equipment Inventory', ICON.equip]);
  const sys = [];
  if (has('super')) sys.push(['#/admin/users', 'Users & Roles', ICON.users], ['#/admin/settings', 'Rules & Settings', ICON.admin], ['#/admin/audit', 'Audit & Events', ICON.audit]);
  sys.push(['#/notifications', 'Notifications & Activity', ICON.bell], ['#/assistant', 'G-Cortex Assistant', ICON.bot]);
  return [['Administration', adm], ['System', sys]];
}

const routes = {
  '/dashboard': () => import('./pages/dashboard.js'), '/courses': () => import('./pages/courses.js'), '/mess': () => import('./pages/mess.js'),
  '/requisitions': () => import('./pages/requisitions.js'), '/sports': () => import('./pages/sports.js'), '/equipment': () => import('./pages/equipment.js'),
  '/notifications': () => import('./pages/notifications.js'), '/assistant': () => import('./pages/assistant.js'), '/approvals': () => import('./pages/approvals.js'),
  '/mess-forecast': () => import('./pages/forecast.js'),
  '/admin': () => import('./pages/admin_overview.js'), '/admin/courses': () => import('./pages/admin_courses.js'), '/admin/mess': () => import('./pages/admin_mess.js'),
  '/admin/requisitions': () => import('./pages/admin_requisitions.js'), '/admin/sports': () => import('./pages/admin_sports.js'),
  '/admin/equipment': () => import('./pages/admin_equipment.js'), '/admin/users': () => import('./pages/admin_users.js'),
  '/admin/settings': () => import('./pages/admin_settings.js'), '/admin/audit': () => import('./pages/admin_audit.js'),
};
const home = () => ({ student: '#/dashboard', faculty: '#/approvals', admin: '#/admin' }[S.user.role]);

let content, chatDrawer, bell;
export const refresh = () => route();

async function route() {
  if (!S.user) return renderLogin();
  const path = (location.hash.replace(/^#/, '') || '').split('?')[0];
  if (!routes[path]) { location.hash = home(); return; }
  document.body.classList.toggle('on-assistant', path === '/assistant');
  document.querySelectorAll('.nav a').forEach(a => a.classList.toggle('active', a.getAttribute('href') === '#' + path));
  document.getElementById('sidebar')?.classList.remove('open');
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

function layout() {
  clear(root);
  const sidebar = h('aside', { class: 'sidebar', id: 'sidebar' },
    h('div', { class: 'brand' }, h('img', { src: '/static/img/logo.png', alt: 'G-Cortex' }), h('div', null, 'G-Cortex', h('small', null, 'GIKI Campus Portal'))),
    h('nav', { class: 'nav' }, navFor(S.user).map(([title, items]) => [h('h4', null, title), items.map(([href, label, ico]) => h('a', { href }, h('span', { class: 'ico' }, ico), label))])),
    h('div', { class: 'side-foot' }, `${S.user.role === 'admin' ? 'Administrator' : S.user.role === 'faculty' ? 'Faculty' : 'Student'} · ${S.user.user_id}`));
  bell = h('a', { href: '#/notifications', class: 'btn sm', title: 'Notifications' }, '🔔');
  const topbar = h('div', { class: 'topbar' },
    h('button', { class: 'btn sm menu-btn', onclick: () => sidebar.classList.toggle('open'), 'aria-label': 'Menu' }, '☰'),
    h('div', { class: 'right row' }, bell,
      h('div', { class: 'who' }, h('b', null, S.user.full_name), h('span', { class: 'small muted' }, S.user.role === 'admin' ? `Admin · ${S.user.scopes.join(', ')}` : S.user.role)),
      h('button', { class: 'btn sm', onclick: async () => { await api.post('/api/auth/logout').catch(() => {}); S.user = null; sessionStorage.removeItem('gcortex_chat'); location.hash = ''; renderLogin(); } }, 'Sign out')));
  content = h('div', { class: 'content' });
  root.append(h('div', { class: 'shell' }, sidebar, h('div', { class: 'main' }, topbar, content)));
  if (S.user.role !== 'admin' || true) {
    const chat = createChat({ role: S.user.role, onChange: () => route() });
    chatDrawer = chat.el;
    const fab = h('button', { class: 'chat-fab', title: 'Ask G-Cortex', 'aria-label': 'Open assistant', onclick: () => { chatDrawer.classList.toggle('open'); chat.focus(); } }, '✦');
    root.append(chatDrawer, fab);
    window.__chatDrawer = chatDrawer;
  }
}

function renderLogin() {
  clear(root);
  const err = h('div');
  const email = h('input', { type: 'email', name: 'email', required: true, autocomplete: 'username', placeholder: 'u2024405@giki.edu.pk' });
  const pw = h('input', { type: 'password', name: 'password', required: true, autocomplete: 'current-password' });
  const btn = h('button', { class: 'btn primary', type: 'submit', style: 'width:100%;justify-content:center;padding:11px' }, 'Sign in');
  const f = h('form', { onsubmit: async (e) => {
    e.preventDefault(); btn.disabled = true; clear(err);
    try { const u = await api.post('/api/auth/login', { email: email.value, password: pw.value }); S.user = u; boot(u); }
    catch (ex) { err.append(h('div', { class: 'alert bad' }, ex.message)); btn.disabled = false; }
  } },
    h('h1', null, 'Sign in'), h('p', { class: 'muted', style: 'margin:0 0 18px' }, 'Use your GIKI email and portal password. Your role is determined by your account record.'),
    err, h('div', { class: 'field' }, h('label', null, 'Email'), email), h('div', { class: 'field' }, h('label', null, 'Password'), pw), btn,
    h('p', { class: 'hint', style: 'margin-top:14px' }, 'Forgot your password or no account yet? Contact the Directorate of Student Affairs / IT office to be provisioned.'));
  root.append(h('div', { class: 'login-wrap' },
    h('section', { class: 'login-hero' }, h('img', { src: '/static/img/logo.png', alt: 'G-Cortex logo' }), h('h1', null, 'G-Cortex'),
      h('p', null, 'The campus operating system for GIKI — academics, dining and campus resources in one place, with an assistant that can do the work for you.'),
      h('ul', null, h('li', null, 'Register for courses with live prerequisite and timetable checks'), h('li', null, 'Mess-out requests, rebates and your dynamic dining QR'),
        h('li', null, 'Sports bookings, venue requisitions and lab equipment custody'))),
    h('section', { class: 'login-card' }, f)));
}

function boot(u) {
  S.user = u; layout();
  if (u.must_change_password) return forcePasswordChange();
  if (!location.hash || !routes[location.hash.replace(/^#/, '').split('?')[0]]) location.hash = home(); else route();
}

async function forcePasswordChange() {
  const { modal, field, form } = await import('./ui.js');
  const body = form([field('Current password', h('input', { type: 'password', name: 'current_password', required: true })), field('New password', h('input', { type: 'password', name: 'new_password', required: true, minlength: 8 }), 'At least 8 characters with letters and digits')],
    async (d) => { await api.post('/api/auth/change-password', d); toast('Password changed. Please sign in again.', 'ok'); S.user = null; location.hash = ''; renderLogin(); },
    'Change password');
  modal({ title: 'Set a new password', body: h('div', null, h('div', { class: 'alert warn' }, 'Your account uses a temporary password. Choose a new one to continue.'), body) });
}

window.addEventListener('hashchange', () => { if (S.user) route(); });
(async () => { try { boot(await api.get('/api/auth/me')); } catch (_) { renderLogin(); } })();
export const currentUser = () => S.user;
