const API_BASE = "/api";

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const url = `${API_BASE}${path}`;
  const headers: Record<string, string> = {
    Accept: "application/json",
  };
  const token = localStorage.getItem("token");
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  if (options?.body && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(url, {
    ...options,
    headers: {
      ...headers,
      ...(options?.headers as Record<string, string> || {}),
    },
  });
  if (!response.ok) {
    const error = await response.text();
    throw new Error(`HTTP ${response.status}: ${error}`);
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
};

export const authApi = {
  login: (username: string, password: string) =>
    apiFetch<{ access_token: string; token_type: string }>("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ username, password }),
    }),
};
