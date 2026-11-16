// The local API. Same origin as the page, so the server's Host and Origin checks pass.

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function handle<T>(r: Response): Promise<T> {
  const text = await r.text();
  let body: any = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = { error: text };
  }
  if (!r.ok) throw new ApiError(r.status, body?.error || `HTTP ${r.status}`);
  return body as T;
}

export const get = <T>(path: string, signal?: AbortSignal) => fetch(path, { cache: "no-store", signal }).then((r) => handle<T>(r));

export const post = <T>(path: string, body: unknown = {}, signal?: AbortSignal) =>
  fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  }).then((r) => handle<T>(r));
