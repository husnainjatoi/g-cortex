import { h, pageHead } from '../ui.js';
import { createChat } from '../chat.js';

export async function render(root, { user, refresh }) {
  root.append(pageHead('G-Cortex Assistant', 'Ask questions or tell it what to do. It uses the same rules and data as the portal pages and always asks before changing anything.'));
  root.append(createChat({ full: true, role: user.role }).el,
    h('p', { class: 'small muted', style: 'margin-top:10px' }, 'Anything the assistant does appears immediately in the matching page and in your Activity history (marked “assistant”). If it cannot do something, the portal backend has refused it and the assistant will explain why.'));
}
