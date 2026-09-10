const API_BASE = "/api";

const ACCESS_TOKEN_KEY = "token";
const REFRESH_TOKEN_KEY = "refresh_token";

/** Single-flight refresh: concurrent 401s share one /auth/refresh call. */
let refreshPromise: Promise<boolean> | null = null;

function getAccessToken(): string | null {
  return localStorage.getItem(ACCESS_TOKEN_KEY);
}

function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_TOKEN_KEY);
}

export function setTokens(access: string, refresh: string): void {
  localStorage.setItem(ACCESS_TOKEN_KEY, access);
  localStorage.setItem(REFRESH_TOKEN_KEY, refresh);
}

export function clearTokens(): void {
  localStorage.removeItem(ACCESS_TOKEN_KEY);
  localStorage.removeItem(REFRESH_TOKEN_KEY);
}

export function hasTokens(): boolean {
  return getAccessToken() !== null;
}

/**
 * Exchange the stored refresh token for a new token pair (rotation).
 * Returns false when there is no refresh token or rotation is rejected
 * (expired / already used token): the caller should force re-login.
 */
export async function refreshTokens(): Promise<boolean> {
  const refreshToken = getRefreshToken();
  if (!refreshToken) {
    return false;
  }

  // Refresh is unauthenticated and must never recurse into apiFetchAuth.
  const response = await fetch(`${API_BASE}/auth/refresh`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ refresh_token: refreshToken }),
  });

  if (!response.ok) {
    // Reuse detection revokes the whole token family — drop everything.
    clearTokens();
    return false;
  }

  const data = (await response.json()) as {
    access_token: string;
    refresh_token: string;
  };
  setTokens(data.access_token, data.refresh_token);
  return true;
}

async function refreshOnce(): Promise<boolean> {
  if (!refreshPromise) {
    refreshPromise = refreshTokens().finally(() => {
      refreshPromise = null;
    });
  }
  return refreshPromise;
}

/** Callback fired when the session is definitively dead (refresh failed). */
let onSessionExpired: (() => void) | null = null;

export function setSessionExpiredHandler(handler: (() => void) | null): void {
  onSessionExpired = handler;
}

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const doFetch = async (): Promise<Response> => {
    const url = `${API_BASE}${path}`;
    const headers: Record<string, string> = {
      Accept: "application/json",
    };
    const token = getAccessToken();
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }
    if (options?.body && !(options.body instanceof FormData)) {
      headers["Content-Type"] = "application/json";
    }
    return fetch(url, {
      ...options,
      headers: {
        ...headers,
        ...(options?.headers as Record<string, string> | undefined),
      },
    });
  };

  let response = await doFetch();

  // Access token expired (15 min): rotate via refresh token and retry once.
  if (response.status === 401 && getRefreshToken()) {
    const refreshed = await refreshOnce();
    if (refreshed) {
      response = await doFetch();
    }
  }

  if (response.status === 401) {
    clearTokens();
    onSessionExpired?.();
  }

  if (!response.ok) {
    const error = await response.text();
    throw new Error(`HTTP ${response.status}: ${error}`);
  }
  // 204 carries no body, and response.json() would throw on it. DELETE
  // /stream-configs/{name} is the only such route today.
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

export interface FileListResponse {
  files: string[];
}

export interface FileUploadResponse {
  filename: string;
  message: string;
}

export interface FileUrlResponse {
  filename: string;
  url: string;
}

export interface FileDeleteResponse {
  filename: string;
  message: string;
}

export interface FileStat {
  filename: string;
  size_bytes: number;
  size_human?: string;
}

export interface StatsResponse {
  total_files: number;
  total_size_bytes: number;
  total_size_human: string;
  average_size_bytes: number;
  largest_file: FileStat | null;
}

export const filesApi = {
  list: () => apiFetch<FileListResponse>("/files/"),
  upload: (file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return apiFetch<FileUploadResponse>("/files/upload", {
      method: "POST",
      body: formData,
    });
  },
  getUrl: (filename: string) =>
    apiFetch<FileUrlResponse>(`/files/${encodeURIComponent(filename)}`),
  delete: (filename: string) =>
    apiFetch<FileDeleteResponse>(`/files/${encodeURIComponent(filename)}`, {
      method: "DELETE",
    }),
  stats: () => apiFetch<StatsResponse>("/files/stats"),
};

export interface StreamConfig {
  name: string;
  source: string;
  record_enabled: boolean;
  crop_enabled: boolean;
  crop_top_percent: number;
  crop_bottom_percent: number;
  created_at: string;
  updated_at: string;
}

export type StreamConfigUpdate = Partial<
  Pick<
    StreamConfig,
    "source" | "record_enabled" | "crop_enabled" | "crop_top_percent" | "crop_bottom_percent"
  >
>;

export type StreamConfigCreate = StreamConfigUpdate & { name: string };

/**
 * Stream configuration stored by Tacitus, as opposed to the live MediaMTX view
 * under /streams. Creating a configuration here does not provision a MediaMTX
 * path: which streams exist is decided by the MediaMTX configuration file.
 */
export const streamConfigsApi = {
  list: () => apiFetch<StreamConfig[]>("/stream-configs"),
  get: (name: string) =>
    apiFetch<StreamConfig>(`/stream-configs/${encodeURIComponent(name)}`),
  create: (payload: StreamConfigCreate) =>
    apiFetch<StreamConfig>("/stream-configs", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  update: (name: string, payload: StreamConfigUpdate) =>
    apiFetch<StreamConfig>(`/stream-configs/${encodeURIComponent(name)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  remove: (name: string) =>
    apiFetch<void>(`/stream-configs/${encodeURIComponent(name)}`, {
      method: "DELETE",
    }),
};

/** Mirrors MAX_CROP_PERCENT / MAX_CROP_TOTAL_PERCENT in app/schemas/streams.py. */
export const MAX_CROP_PERCENT = 90;
export const MAX_CROP_TOTAL_PERCENT = 90;

export const authApi = {
  login: (username: string, password: string) =>
    apiFetch<TokenResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  register: (username: string, password: string) =>
    apiFetch<{ username: string; message: string }>("/auth/register", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  me: () => apiFetch<{ username: string; message: string | null }>("/auth/me"),
  logout: () =>
    apiFetch<{ message: string }>("/auth/logout", {
      method: "POST",
      body: JSON.stringify({ refresh_token: getRefreshToken() ?? "" }),
    }),
};

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;

/** Human-readable reason for a failed upload, or null if size is fine. */
export function checkUploadSize(file: File): string | null {
  if (file.size > MAX_UPLOAD_BYTES) {
    return `File too large: ${file.name} (${formatBytes(file.size)}). Maximum is 10 MB.`;
  }
  return null;
}

export function formatBytes(bytes: number): string {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit++;
  }
  return `${value.toFixed(value < 10 && unit > 0 ? 1 : 0)} ${units[unit]}`;
}

/**
 * One application setting as reported by GET /settings.
 *
 * Credentials never leave the backend: for those, `value` is null and
 * `sensitive` is true, while `is_set` still tells whether something is
 * configured. Connection URLs are returned with the password replaced by
 * `***`.
 */
export interface SettingItem {
  name: string;
  group: string;
  value: string | null;
  sensitive: boolean;
  is_set: boolean;
}

export interface SettingsResponse {
  items: SettingItem[];
}

/** Version of the running backend, as reported by GET /settings/version. */
export interface VersionResponse {
  version: string;
}

export const settingsApi = {
  list: () => apiFetch<SettingsResponse>("/settings"),
  version: () => apiFetch<VersionResponse>("/settings/version"),
};
