import { api } from '../api.js';
import { h, pageHead, field, form, toast, badge, passwordInput } from '../ui.js';

export async function render(root, { user }) {
  root.append(pageHead('Profile & Account', 'Your account details and password.'));
  root.append(h('div', { class: 'grid g2' },
    h('div', { class: 'card' }, h('h2', null, 'Account'),
      h('p', null, h('b', null, 'Name: '), user.full_name), h('p', null, h('b', null, 'ID: '), user.user_id),
      h('p', null, h('b', null, 'Role: '), badge(user.role, user.role === 'admin' ? 'bad' : 'info')),
      user.role === 'admin' ? h('p', null, h('b', null, 'Administrative scopes: '), user.scopes.join(', ')) : null,
      h('p', { class: 'small muted' }, 'Your role and permissions are stored on the server and cannot be changed from this page.')),
    h('div', { class: 'card' }, h('h2', null, 'Change password'),
      form([field('Current password', passwordInput({ name: 'current_password', autocomplete: 'current-password' })),
            field('New password', passwordInput({ name: 'new_password', autocomplete: 'new-password' }), 'At least 8 characters with letters and digits'),
            field('Confirm new password', passwordInput({ name: 'confirm_password', autocomplete: 'new-password' }))],
        async (d) => { await api.post('/api/auth/change-password', d); toast('Password changed. Your session has been renewed.', 'ok'); }, 'Change password'))));
}
