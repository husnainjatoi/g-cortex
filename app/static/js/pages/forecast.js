import { api, qs } from '../api.js';
import { h, clear, table, select, pageHead, toast, addDays, errorBox, barChart } from '../ui.js';

export async function render(root) {
  root.append(pageHead('Kitchen Demand Forecast', 'Predicted headcount from turnstile history and approved mess-outs.'));
  const date = h('input', { type: 'date', value: addDays(1) }); const out = h('div');
  async function go() {
    clear(out);
    try {
      const rows = await Promise.all(['breakfast', 'lunch', 'dinner'].map(m => api.get('/api/mess/forecast' + qs({ meal: m, date: date.value }))));
      out.append(table([{ label: 'Meal', key: 'meal' }, { label: 'Enrolled', key: 'enrolled_students' }, { label: 'Away (mess-out)', key: 'approved_mess_outs' }, { label: 'Predicted', render: r => h('b', null, r.predicted_headcount) }, { label: 'Produce', key: 'recommended_production' }, { label: 'Over-production avoided', render: r => `${r.estimated_overproduction_avoided_pct}%` }, { label: 'Method', render: r => h('span', { class: 'small' }, r.method) }], rows),
        h('p', { class: 'small muted' }, rows[0].note));
    } catch (e) { out.append(errorBox(e)); }
  }
  date.addEventListener('change', go); root.append(h('div', { style: 'width:200px;margin-bottom:12px' }, date), out); go();
}
