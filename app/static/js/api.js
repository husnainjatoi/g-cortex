// Thin fetch wrapper. Authentication is a server-side HttpOnly session cookie; no tokens or secrets live in JS.
export class ApiError extends Error {
  constructor(status, detail, code, details) { super(detail); this.status = status; this.code = code; this.details = details; }
}

async function request(method, path, body) {
  const res = await fetch(path, {
    method, credentials: 'same-origin',
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch (_) { /* empty body */ }
  if (!res.ok) {
    let detail = data && data.detail;
    if (Array.isArray(detail)) detail = detail.map(d => `${(d.loc || []).slice(-1)[0]}: ${d.msg}`).join('; ');
    throw new ApiError(res.status, detail || `Request failed (${res.status})`, data && data.code, data && data.details);
  }
  return data;
}

export const api = {
  get: (p) => request('GET', p),
  post: (p, b = {}) => request('POST', p, b),
  put: (p, b = {}) => request('PUT', p, b),
  del: (p) => request('DELETE', p),
};
export const qs = (o) => {
  const p = new URLSearchParams();
  Object.entries(o).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== '') p.set(k, v); });
  const s = p.toString();
  return s ? `?${s}` : '';
};
