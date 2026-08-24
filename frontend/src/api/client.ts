export interface RequestOptions {
  signal?: AbortSignal;
  revision?: number;
  idempotencyKey?: string;
  headers?: HeadersInit;
}

interface ApiErrorBody {
  code?: string;
  message?: string;
  detail?: string;
  errors?: Record<string, string[] | string>;
  error?: {
    code?: string;
    message?: string;
    details?: Record<string, string[] | string>;
  };
}

const DEFAULT_ERROR_MESSAGES: Record<number, string> = {
  400: "提交内容有误，请检查后重试",
  401: "登录状态已失效，请重新登录",
  403: "你没有执行此操作的权限",
  404: "请求的内容不存在或无权访问",
  409: "数据已发生变化，请刷新后重试",
  413: "文件或请求内容超过允许大小",
  429: "操作过于频繁，请稍后重试",
  500: "服务暂时不可用，请稍后重试",
};

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly errors?: Record<string, string[] | string>;

  constructor(status: number, code: string, message: string, errors?: Record<string, string[] | string>) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.errors = errors;
  }
}

export function getCookie(name: string): string | undefined {
  if (typeof document === "undefined") return undefined;
  const prefix = `${encodeURIComponent(name)}=`;
  for (const part of document.cookie.split(";")) {
    const value = part.trim();
    if (value.startsWith(prefix)) return decodeURIComponent(value.slice(prefix.length));
  }
  return undefined;
}

function safeMessage(status: number, body: ApiErrorBody | null): string {
  const candidate = body?.error?.message ?? body?.message ?? body?.detail;
  if (typeof candidate === "string" && candidate.length > 0 && candidate.length <= 500) {
    return candidate;
  }
  return DEFAULT_ERROR_MESSAGES[status] ?? "请求失败，请稍后重试";
}

function normalizePath(path: string): string {
  if (!path.startsWith("/")) return `/${path}`;
  return path;
}

export class ApiClient {
  readonly baseUrl: string;

  constructor(baseUrl = "/api/v1") {
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  async request<T>(method: string, path: string, body?: unknown, options: RequestOptions = {}): Promise<T> {
    const headers = new Headers(options.headers);
    headers.set("Accept", "application/json");
    const upperMethod = method.toUpperCase();
    const isWrite = !["GET", "HEAD", "OPTIONS"].includes(upperMethod);
    const isForm = typeof FormData !== "undefined" && body instanceof FormData;

    if (body !== undefined && !isForm) headers.set("Content-Type", "application/json");
    if (isWrite) {
      const csrf = getCookie("csrftoken");
      if (csrf) headers.set("X-CSRFToken", csrf);
    }
    if (options.revision !== undefined) headers.set("If-Match", `"${options.revision}"`);
    if (options.idempotencyKey) headers.set("Idempotency-Key", options.idempotencyKey);

    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}${normalizePath(path)}`, {
        method: upperMethod,
        credentials: "same-origin",
        headers,
        signal: options.signal,
        body: body === undefined ? undefined : isForm ? (body as FormData) : JSON.stringify(body),
      });
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") throw error;
      throw new ApiError(0, "NETWORK_ERROR", "无法连接到服务，请检查内网连接后重试");
    }

    if (!response.ok) {
      let errorBody: ApiErrorBody | null = null;
      const contentType = response.headers.get("content-type") ?? "";
      if (contentType.includes("application/json")) {
        try {
          errorBody = (await response.json()) as ApiErrorBody;
        } catch {
          errorBody = null;
        }
      }
      if (response.status === 401 && typeof window !== "undefined") {
        window.dispatchEvent(new CustomEvent("fire-inspect:unauthorized"));
      }
      throw new ApiError(
        response.status,
        errorBody?.error?.code ?? errorBody?.code ?? `HTTP_${response.status}`,
        safeMessage(response.status, errorBody),
        errorBody?.error?.details ?? errorBody?.errors,
      );
    }

    if (response.status === 204) return undefined as T;
    const contentType = response.headers.get("content-type") ?? "";
    if (!contentType.includes("application/json")) return (await response.blob()) as T;
    return (await response.json()) as T;
  }

  get<T>(path: string, options?: RequestOptions) {
    return this.request<T>("GET", path, undefined, options);
  }

  post<T>(path: string, body?: unknown, options?: RequestOptions) {
    return this.request<T>("POST", path, body, options);
  }

  patch<T>(path: string, body: unknown, options?: RequestOptions) {
    return this.request<T>("PATCH", path, body, options);
  }

  put<T>(path: string, body: unknown, options?: RequestOptions) {
    return this.request<T>("PUT", path, body, options);
  }

  delete<T>(path: string, body?: unknown, options?: RequestOptions) {
    return this.request<T>("DELETE", path, body, options);
  }
}

export const api = new ApiClient(import.meta.env.VITE_API_BASE_URL || "/api/v1");

export function createIdempotencyKey(prefix: string): string {
  const id = typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `${prefix}-${id}`;
}

export function getErrorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return "操作未完成，请稍后重试";
}
