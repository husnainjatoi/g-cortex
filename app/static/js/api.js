// Thin fetch wrapper. Authentication is a server-side HttpOnly session cookie; no tokens or secrets live in JS.
export class ApiError extends Error {
  constructor(status, detail, code, details, fields) { super(detail); this.status = status; this.code = code; this.details = details; this.fields = fields || null; this.friendly = true; }
}

async function request(method, path, body) {
  let res;
  try {
    res = await fetch(path, {
      method, credentials: 'same-origin',
      headers: body !== undefined ? { 'Content-Type': 'application/json' } : {},
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (_) { throw new ApiError(0, 'We could not reach the server. Please check your connection and try again.', 'network'); }
  let data = null;
  try { data = await res.json(); } catch (_) { /* empty body */ }
  if (!res.ok) {
    // The backend sends plain-language messages (and per-field messages for forms); never surface raw validation objects or status codes.
    const detail = data && typeof data.detail === 'string' ? data.detail : null;
    const fallback = res.status >= 500 ? 'Something went wrong on our side. Please try again later.' : res.status === 401 ? 'Please sign in to continue.' : res.status === 403 ? "You don't have permission to do that." : res.status === 404 ? "We couldn't find what you were looking for." : 'Some of the information you entered is missing or invalid. Please check and try again.';
    throw new ApiError(res.status, detail || fallback, data && data.code, data && data.details, data && data.fields);
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
