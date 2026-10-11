/* Fetch helpers for the existing FastAPI routes. Web clients use the same-origin
 * UI cookie; desktop clients also carry their preload-provided session token. */

export class ApiError extends Error {
  /** field: the form field a refusal belongs to, when the route names one (provider forms). */
  constructor(message: string, readonly status: number, readonly field?: string) {
    super(message);
    this.name = 'ApiError';
  }
}

export function sessionToken(): string {
  return window.__SESSION_TOKEN__ ?? '';
}

export function sessionHeaders(): Record<string, string> {
  const token = sessionToken();
  return token ? { 'X-Session-Token': token } : {};
}

type Method = 'GET' | 'POST' | 'PATCH' | 'DELETE';

/** Call any /api route. A body implies POST unless a method is given. */
export async function api<T = unknown>(path: string, body?: unknown, method?: Method): Promise<T> {
  const verb: Method = method ?? (body === undefined ? 'GET' : 'POST');
  const headers = sessionHeaders();
  let payload: BodyInit | undefined;
  if (body instanceof FormData) {
    payload = body;
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    payload = JSON.stringify(body);
  }
  const response = await fetch(path, { method: verb, headers, body: payload });
  const text = await response.text();
  let data: unknown = undefined;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!response.ok) {
    let message = response.statusText || `Request failed (${response.status})`;
    let field: string | undefined;
    if (data && typeof data === 'object' && 'error' in data) {
      const body = data as { error: unknown; field?: unknown };
      message = String(body.error);
      if (typeof body.field === 'string') field = body.field;
    } else if (typeof data === 'string' && data) message = data;
    throw new ApiError(message, response.status, field);
  }
  return data as T;
}

/** Shorthand for /api/runtime routes, matching the legacy runtime.js/project.js helper. */
export function runtime<T = unknown>(path: string, body?: unknown): Promise<T> {
  return api<T>('/api/runtime' + path, body);
}

/** Image types the board accepts; SVG is excluded because it can carry script. */
export const RASTER_IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/gif', 'image/webp'];

/** Upload one image through the existing chat upload route; returns its /uploads/ url. */
export async function uploadImage(file: Blob, name: string): Promise<{ name: string; url: string }> {
  if (!RASTER_IMAGE_TYPES.includes(file.type)) throw new ApiError('Choose a PNG, JPEG, GIF or WebP image', 400);
  const form = new FormData();
  form.append('file', file, name);
  return api('/api/upload', form);
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
