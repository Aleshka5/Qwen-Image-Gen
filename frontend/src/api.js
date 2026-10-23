export class ApiError extends Error {
  constructor(message, status) {
    super(message || 'Request failed');
    this.name = 'ApiError';
    this.status = status;
  }
}

async function read(res) {
  const text = await res.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }
  if (!res.ok) {
    const message = (data && typeof data.error === 'string' && data.error) || text || res.statusText || 'Request failed';
    throw new ApiError(message, res.status);
  }
  return data;
}

function jsonHeaders() {
  return { Accept: 'application/json', 'Content-Type': 'application/json' };
}

export function get(url) {
  return fetch(url, { headers: { Accept: 'application/json' } }).then(read);
}

export function postJson(url, body) {
  return fetch(url, {
    method: 'POST',
    headers: jsonHeaders(),
    body: JSON.stringify(body),
  }).then(read);
}

export function patchJson(url, body) {
  return fetch(url, {
    method: 'PATCH',
    headers: jsonHeaders(),
    body: JSON.stringify(body),
  }).then(read);
}

export function del(url) {
  return fetch(url, { method: 'DELETE', headers: { Accept: 'application/json' } }).then(read);
}

export function postForm(url, form) {
  return fetch(url, {
    method: 'POST',
    headers: { Accept: 'application/json' },
    body: form,
  }).then(read);
}

export function asObject(data, key) {
  if (data && typeof data === 'object' && data[key] && typeof data[key] === 'object' && !Array.isArray(data[key])) {
    return data[key];
  }
  return data;
}

export function asList(data, key) {
  if (Array.isArray(data)) return data;
  if (data && Array.isArray(data[key])) return data[key];
  return [];
}

export function shapeSource(id, preview = false) {
  return preview ? `/api/shapes/${id}/source?preview=1` : `/api/shapes/${id}/source`;
}

export function sleep(ms) {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

export const api = {
  config: () => get('/api/config'),
  catalog: () => get('/api/catalog'),
  settings: () => get('/api/settings'),
  projects: () => get('/api/projects'),
  project: (id) => get(`/api/projects/${id}`),
  createProject: (form) => postForm('/api/projects', form),
  renameProject: (id, name) => patchJson(`/api/projects/${id}`, { name }),
  deleteProject: (id) => del(`/api/projects/${id}`),
  createShape: (projectId, body) => postJson(`/api/projects/${projectId}/shapes`, body),
  updateShape: (id, body) => patchJson(`/api/shapes/${id}`, body),
  deleteShape: (id) => del(`/api/shapes/${id}`),
  commit: (projectId, parentId) => postJson(`/api/projects/${projectId}/commit`, { parent_id: parentId }),
  setType: (id, type) => postJson(`/api/shapes/${id}/type`, { type }),
  shapeVariants: (id) => get(`/api/shapes/${id}/variants`),
  selectShapeVariant: (id, variantId) => postJson(`/api/shapes/${id}/select`, { variant_id: variantId }),
  projectVariants: (id) => get(`/api/projects/${id}/variants`),
  selectProjectVariant: (id, variantId) => postJson(`/api/projects/${id}/select`, { variant_id: variantId }),
  generations: (form) => postForm('/api/generations', form),
  task: (id) => get(`/api/tasks/${id}`),
  feed: () => get('/api/feed'),
  render: (body) => postJson('/api/renders', body),
  createProvider: (body) => postJson('/api/settings/providers', body),
  updateProvider: (id, body) => patchJson(`/api/settings/providers/${id}`, body),
  deleteProvider: (id) => del(`/api/settings/providers/${id}`),
  defaultProvider: (id) => fetch(`/api/settings/providers/${id}/default`, {
    method: 'POST',
    headers: { Accept: 'application/json' },
  }).then(read),
};
