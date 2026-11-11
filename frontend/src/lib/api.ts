export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(options.headers ?? {}) },
    ...options,
  })
  const contentType = response.headers.get('content-type') ?? ''
  const data: unknown = contentType.includes('application/json')
    ? await response.json()
    : await response.text()
  if (!response.ok) {
    const payload = data as { detail?: string; error?: string }
    throw new ApiError(
      payload?.detail || payload?.error || `HTTP ${response.status}`,
      response.status,
    )
  }
  return data as T
}

export const pretty = (value: unknown): string => JSON.stringify(value, null, 2)
