export type ApiResponse<T> =
  | { ok: true; result: T; error?: null }
  | { ok: false; result?: unknown; error: { code: string; message: string; details?: unknown } };

export async function apiJson<T>(path: string, init?: RequestInit): Promise<ApiResponse<T>> {
  const resp = await fetch(path, {
    ...init,
    headers: {
      "content-type": "application/json",
      ...(init?.headers || {}),
    },
  });

  const text = await resp.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  return data as ApiResponse<T>;
}

export async function apiForm<T>(path: string, form: FormData, init?: RequestInit): Promise<ApiResponse<T>> {
  const resp = await fetch(path, {
    ...(init || {}),
    method: init?.method || "POST",
    body: form,
    headers: {
      ...(init?.headers || {}),
    },
  });

  const text = await resp.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  return data as ApiResponse<T>;
}
