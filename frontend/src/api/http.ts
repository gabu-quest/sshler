import type {
  ApiBox,
  ApiSnippet,
  ApiTunnel,
  BatchResult,
  BoxStats,
  BoxStatus,
  BootstrapPayload,
  DirectoryListing,
  FavoriteToggle,
  FilePreview,
  GitInfo,
  GrepResponse,
  LostSession,
  PinToggle,
  SnapshotStatus,
  SearchResponse,
  SessionInfo,
  TerminalHandshake,
  SimpleMessage,
} from "./types";

const API_BASE = "/api/v1";

async function handle<T>(response: Response, originalRequest?: { url: string, options: RequestInit }): Promise<T> {
  if (!response.ok) {
    // Auto-retry token issues once
    if (response.status === 403 && !response.url.includes('/bootstrap') && originalRequest) {
      console.warn('[API] 403 received, refreshing token...');
      try {
        const { useBootstrapStore } = await import('@/stores/bootstrap');
        const bootstrapStore = useBootstrapStore();

        // Clear old token and fetch fresh one
        const oldToken = bootstrapStore.token;
        bootstrapStore.setToken(null);
        await bootstrapStore.bootstrap();

        // Retry the original request with new token
        const newToken = bootstrapStore.token;
        if (newToken) {
          // Convert existing headers to plain object if needed
          const existingHeaders: Record<string, string> = {};
          const origHeaders = originalRequest.options.headers;
          if (origHeaders) {
            if (origHeaders instanceof Headers) {
              origHeaders.forEach((v, k) => { existingHeaders[k] = v; });
            } else if (Array.isArray(origHeaders)) {
              origHeaders.forEach(([k, v]) => { existingHeaders[k] = v; });
            } else {
              Object.assign(existingHeaders, origHeaders);
            }
          }
          
          // Build new headers with fresh token
          const newHeaders = { ...existingHeaders, ...buildHeaders(newToken) };
          const retryResponse = await fetch(originalRequest.url, {
            ...originalRequest.options,
            headers: newHeaders,
            credentials: 'include',
          });
          
          if (retryResponse.ok) {
            return retryResponse.json() as Promise<T>;
          }
          // Log the retry failure details
          const retryError = await safeParseError(retryResponse);
          console.error('[API] Retry also failed:', retryResponse.status, retryError);
        }
      } catch (retryError) {
        console.error('[API] Token refresh failed:', retryError);
      }
    }

    // Handle 401 Unauthorized
    if (response.status === 401) {
      await handleAuthErrors(response);
    }

    const detail = await safeParseError(response);
    throw new Error(detail || `request failed with ${response.status}`);
  }
  return response.json() as Promise<T>;
}

async function safeParseError(response: Response): Promise<string | null> {
  try {
    const data = await response.json();
    if (data?.detail) {
      if (Array.isArray(data.detail)) {
        return data.detail.map((item: any) => item?.msg || String(item)).join(", ");
      }
      return String(data.detail);
    }
  } catch (err) {
    return null;
  }
  return null;
}

export function buildHeaders(token?: string | null): HeadersInit {
  const headers: HeadersInit = {
    Accept: "application/json",
  };
  if (token) {
    headers["X-SSHLER-TOKEN"] = token;
  }
  // NOTE: Do NOT include Authorization header - we use httpOnly cookies
  return headers;
}

/**
 * Axios-style HTTP client with automatic cookie-based auth
 * All requests include credentials: 'include' for httpOnly session cookies
 */
export const http = {
  async get<T = any>(url: string, config?: { headers?: HeadersInit }): Promise<{ data: T }> {
    const headers = { ...config?.headers };
    const response = await fetch(url, {
      method: 'GET',
      headers,
      credentials: 'include' // Include httpOnly cookies
    });

    if (!response.ok) {
      await handleAuthErrors(response);
      const detail = await safeParseError(response);
      throw createHttpError(response.status, detail || `GET ${url} failed with ${response.status}`);
    }

    const data = await response.json();
    return { data };
  },

  async post<T = any>(url: string, body?: any, config?: { headers?: HeadersInit }): Promise<{ data: T }> {
    const headers = {
      'Content-Type': 'application/json',
      ...config?.headers
    };
    const response = await fetch(url, {
      method: 'POST',
      headers,
      body: body ? JSON.stringify(body) : undefined,
      credentials: 'include' // Include httpOnly cookies
    });

    if (!response.ok) {
      await handleAuthErrors(response);
      const detail = await safeParseError(response);
      throw createHttpError(response.status, detail || `POST ${url} failed with ${response.status}`);
    }

    const data = await response.json();
    return { data };
  },

  async put<T = any>(url: string, body?: any, config?: { headers?: HeadersInit }): Promise<{ data: T }> {
    const headers = {
      'Content-Type': 'application/json',
      ...config?.headers
    };
    const response = await fetch(url, {
      method: 'PUT',
      headers,
      body: body ? JSON.stringify(body) : undefined,
      credentials: 'include' // Include httpOnly cookies
    });

    if (!response.ok) {
      await handleAuthErrors(response);
      const detail = await safeParseError(response);
      throw createHttpError(response.status, detail || `PUT ${url} failed with ${response.status}`);
    }

    const data = await response.json();
    return { data };
  },

  async delete<T = any>(url: string, config?: { headers?: HeadersInit }): Promise<{ data: T }> {
    const headers = { ...config?.headers };
    const response = await fetch(url, {
      method: 'DELETE',
      headers,
      credentials: 'include' // Include httpOnly cookies
    });

    if (!response.ok) {
      await handleAuthErrors(response);
      const detail = await safeParseError(response);
      throw createHttpError(response.status, detail || `DELETE ${url} failed with ${response.status}`);
    }

    const data = await response.json();
    return { data };
  },
};

/**
 * Handle authentication-specific errors
 */
async function handleAuthErrors(response: Response): Promise<void> {
  if (response.status === 401) {
    // Unauthorized - clear auth state and redirect to login
    try {
      const { useAuthStore } = await import('@/stores/auth');
      const authStore = useAuthStore();
      authStore.clearUser();

      // Redirect to login page with return URL
      const currentPath = window.location.pathname;
      if (!currentPath.startsWith('/login')) {
        window.location.href = `/login?redirect=${encodeURIComponent(currentPath)}`;
      }
    } catch (err) {
      console.error('Failed to handle 401 error:', err);
    }
  }
}

/**
 * Create HTTP error with response details
 */
function createHttpError(status: number, message: string): Error {
  const error = new Error(message) as any;
  error.response = { status };
  return error;
}

export async function fetchBootstrap(): Promise<BootstrapPayload> {
  const res = await fetch(`${API_BASE}/bootstrap?_t=${Date.now()}`, {
    headers: buildHeaders(),
    cache: 'no-cache',
    credentials: 'include' // Include httpOnly cookies
  });
  return handle<BootstrapPayload>(res);
}

export async function fetchBoxes(token: string | null): Promise<ApiBox[]> {
  const url = `${API_BASE}/boxes`;
  const options = {
    headers: buildHeaders(token),
    credentials: 'include' as RequestCredentials
  };
  const res = await fetch(url, options);
  return handle<ApiBox[]>(res, { url, options });
}

export async function fetchBox(name: string, token: string | null): Promise<ApiBox> {
  const res = await fetch(`${API_BASE}/boxes/${encodeURIComponent(name)}`, {
    headers: buildHeaders(token),
    credentials: 'include'
  });
  return handle<ApiBox>(res);
}

export async function fetchDirectory(
  box: string,
  directory: string,
  token: string | null,
): Promise<DirectoryListing> {
  const url = new URL(`${API_BASE}/boxes/${encodeURIComponent(box)}/ls`, window.location.origin);
  url.searchParams.set("directory", directory || "/");
  const res = await fetch(url.toString().replace(window.location.origin, ""), {
    headers: buildHeaders(token),
    credentials: 'include'
  });
  return handle<DirectoryListing>(res);
}

export async function fetchFilePreview(
  box: string,
  path: string,
  token: string | null,
): Promise<FilePreview> {
  const url = new URL(`${API_BASE}/boxes/${encodeURIComponent(box)}/file`, window.location.origin);
  url.searchParams.set("path", path);
  const res = await fetch(url.toString().replace(window.location.origin, ""), {
    headers: buildHeaders(token),
    credentials: 'include'
  });
  return handle<FilePreview>(res);
}

export async function fetchSessions(token: string | null): Promise<SessionInfo> {
  const res = await fetch(`${API_BASE}/sessions`, {
    headers: buildHeaders(token),
    credentials: 'include'
  });
  return handle<SessionInfo>(res);
}

export async function fetchTerminalHandshake(token: string | null): Promise<TerminalHandshake> {
  const res = await fetch(`${API_BASE}/terminal/handshake`, {
    headers: buildHeaders(token),
    credentials: 'include'
  });
  return handle<TerminalHandshake>(res);
}

export async function togglePin(name: string, token: string | null): Promise<PinToggle> {
  const res = await fetch(`${API_BASE}/boxes/${encodeURIComponent(name)}/pin`, {
    method: "POST",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
    credentials: 'include'
  });
  return handle<PinToggle>(res);
}

export async function toggleFavorite(
  name: string,
  path: string,
  favorite: boolean,
  token: string | null,
): Promise<FavoriteToggle> {
  const url = `${API_BASE}/boxes/${encodeURIComponent(name)}/fav`;
  const options: RequestInit = {
    method: "POST",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ path, favorite }),
    credentials: 'include'
  };
  const res = await fetch(url, options);
  return handle<FavoriteToggle>(res, { url, options });
}

export async function touchFile(
  name: string,
  directory: string,
  filename: string,
  token: string | null,
): Promise<SimpleMessage> {
  const res = await fetch(`${API_BASE}/boxes/${encodeURIComponent(name)}/touch`, {
    method: "POST",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ directory, filename }),
    credentials: 'include'
  });
  return handle<SimpleMessage>(res);
}

export async function deleteFile(
  name: string,
  path: string,
  token: string | null,
): Promise<SimpleMessage> {
  const res = await fetch(`${API_BASE}/boxes/${encodeURIComponent(name)}/delete`, {
    method: "POST",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
    credentials: 'include'
  });
  return handle<SimpleMessage>(res);
}

export async function renameFile(
  name: string,
  path: string,
  new_name: string,
  token: string | null,
): Promise<SimpleMessage> {
  const res = await fetch(`${API_BASE}/boxes/${encodeURIComponent(name)}/rename`, {
    method: "POST",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ path, new_name }),
    credentials: 'include'
  });
  return handle<SimpleMessage>(res);
}

export async function moveFile(
  name: string,
  source: string,
  destination: string,
  token: string | null,
): Promise<SimpleMessage> {
  const res = await fetch(`${API_BASE}/boxes/${encodeURIComponent(name)}/move`, {
    method: "POST",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ source, destination }),
    credentials: 'include'
  });
  return handle<SimpleMessage>(res);
}

export async function copyFile(
  name: string,
  source: string,
  destination: string,
  new_name: string | null,
  token: string | null,
): Promise<SimpleMessage> {
  const res = await fetch(`${API_BASE}/boxes/${encodeURIComponent(name)}/copy`, {
    method: "POST",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ source, destination, new_name }),
    credentials: 'include'
  });
  return handle<SimpleMessage>(res);
}

export async function writeFile(
  name: string,
  path: string,
  content: string,
  token: string | null,
): Promise<SimpleMessage> {
  const res = await fetch(`${API_BASE}/boxes/${encodeURIComponent(name)}/write`, {
    method: "POST",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ path, content }),
    credentials: 'include'
  });
  return handle<SimpleMessage>(res);
}

export async function uploadFile(
  name: string,
  directory: string,
  file: File,
  token: string | null,
  onProgress?: (percent: number) => void,
): Promise<SimpleMessage> {
  const form = new FormData();
  form.append("directory", directory);
  form.append("file", file);

  // Build headers (excluding Content-Type for FormData)
  const headers = buildHeaders(token);

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}/boxes/${encodeURIComponent(name)}/upload`, true);

    // Set headers
    Object.entries(headers).forEach(([key, value]) => {
      xhr.setRequestHeader(key, String(value));
    });

    // IMPORTANT: Include credentials for cookie-based auth
    xhr.withCredentials = true;

    if (xhr.upload && onProgress) {
      xhr.upload.onprogress = (event) => {
        if (!event.lengthComputable) return;
        const percent = Math.round((event.loaded / event.total) * 100);
        onProgress(percent);
      };
    }

    xhr.onerror = () => reject(new Error("upload failed: network error"));
    xhr.onload = async () => {
      const status = xhr.status;
      const body = xhr.responseText || "{}";
      if (status >= 200 && status < 300) {
        try {
          resolve(JSON.parse(body) as SimpleMessage);
        } catch (err) {
          reject(new Error(`upload parse failed: ${err}`));
        }
        return;
      }
      try {
        const parsed = JSON.parse(body);
        if (parsed?.detail) {
          reject(new Error(Array.isArray(parsed.detail) ? parsed.detail.join(", ") : String(parsed.detail)));
          return;
        }
      } catch {
        // ignore parse errors
      }
      reject(new Error(`upload failed with ${status}`));
    };

    xhr.send(form);
  });
}

export async function boxStatus(name: string, token: string | null) {
  const res = await fetch(`${API_BASE}/boxes/${encodeURIComponent(name)}/status`, {
    headers: buildHeaders(token),
    credentials: 'include'
  });
  return handle<BoxStatus>(res);
}

export async function boxStats(name: string, token: string | null) {
  const res = await fetch(`${API_BASE}/boxes/${encodeURIComponent(name)}/stats`, {
    headers: buildHeaders(token),
    credentials: 'include'
  });
  return handle<BoxStats>(res);
}

export async function gitInfo(name: string, directory: string, token: string | null) {
  const url = `${API_BASE}/boxes/${encodeURIComponent(name)}/git?directory=${encodeURIComponent(directory)}`;
  const res = await fetch(url, {
    headers: buildHeaders(token),
    credentials: 'include'
  });
  return handle<GitInfo>(res);
}

export async function chmodFile(
  name: string,
  path: string,
  mode: string,
  token: string | null,
): Promise<SimpleMessage> {
  const res = await fetch(`${API_BASE}/boxes/${encodeURIComponent(name)}/chmod`, {
    method: "POST",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ path, mode }),
    credentials: 'include'
  });
  return handle<SimpleMessage>(res);
}

export async function setBoxTerminalTheme(
  name: string,
  theme: string,
  token: string | null,
): Promise<SimpleMessage> {
  const res = await fetch(`${API_BASE}/boxes/${encodeURIComponent(name)}/theme`, {
    method: "PUT",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ terminal_theme: theme }),
    credentials: 'include'
  });
  return handle<SimpleMessage>(res);
}

export async function fetchBoxSessions(
  name: string,
  token: string | null,
  activeOnly = false,
): Promise<import("./types").ApiSession[]> {
  const params = new URLSearchParams();
  if (activeOnly) params.set("active_only", "true");
  const qs = params.toString() ? `?${params.toString()}` : "";
  const res = await fetch(`${API_BASE}/boxes/${encodeURIComponent(name)}/sessions${qs}`, {
    headers: buildHeaders(token),
    credentials: 'include'
  });
  return handle<import("./types").ApiSession[]>(res);
}

export async function syncBoxSessions(
  name: string,
  token: string | null,
): Promise<import("./types").ApiSession[]> {
  const res = await fetch(`${API_BASE}/boxes/${encodeURIComponent(name)}/sessions/sync`, {
    method: "POST",
    headers: buildHeaders(token),
    credentials: 'include'
  });
  return handle<import("./types").ApiSession[]>(res);
}

export async function deleteSession(
  boxName: string,
  sessionId: string,
  token: string | null,
  killTmux = false,
): Promise<SimpleMessage> {
  const qs = killTmux ? "?kill_tmux=true" : "";
  const res = await fetch(`${API_BASE}/boxes/${encodeURIComponent(boxName)}/sessions/${encodeURIComponent(sessionId)}${qs}`, {
    method: "DELETE",
    headers: buildHeaders(token),
    credentials: 'include'
  });
  return handle<SimpleMessage>(res);
}

export async function renameSession(
  boxName: string,
  sessionId: string,
  newName: string,
  token: string | null,
): Promise<import("./types").ApiSession> {
  const url = `${API_BASE}/boxes/${encodeURIComponent(boxName)}/sessions/${encodeURIComponent(sessionId)}`;
  const options: RequestInit = {
    method: "PATCH",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ session_name: newName }),
    credentials: 'include'
  };
  const res = await fetch(url, options);
  return handle<import("./types").ApiSession>(res, { url, options });
}

export async function downloadFile(
  name: string,
  path: string,
  token: string | null,
): Promise<Blob> {
  const url = new URL(`${API_BASE}/boxes/${encodeURIComponent(name)}/download`, window.location.origin);
  url.searchParams.set("path", path);
  const res = await fetch(url.toString().replace(window.location.origin, ""), {
    headers: buildHeaders(token),
    credentials: 'include'
  });
  if (!res.ok) {
    throw new Error(`download failed ${res.status}`);
  }
  return res.blob();
}

export async function directorySize(
  name: string,
  path: string,
  token: string | null,
): Promise<{ size_bytes: number }> {
  const res = await fetch(
    `${API_BASE}/boxes/${encodeURIComponent(name)}/dir-size?path=${encodeURIComponent(path)}`,
    { headers: buildHeaders(token), credentials: 'include' },
  );
  return handle<{ size_bytes: number }>(res);
}

export async function downloadDirectory(
  name: string,
  path: string,
  token: string | null,
): Promise<Blob> {
  const url = new URL(`${API_BASE}/boxes/${encodeURIComponent(name)}/download-dir`, window.location.origin);
  url.searchParams.set("path", path);
  const res = await fetch(url.toString().replace(window.location.origin, ""), {
    headers: buildHeaders(token),
    credentials: 'include'
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => '');
    throw new Error(`download failed ${res.status}: ${detail}`);
  }
  return res.blob();
}

export async function statPath(
  name: string,
  path: string,
  token: string | null,
): Promise<{ exists: boolean; is_directory: boolean; is_file: boolean }> {
  const url = `${API_BASE}/boxes/${encodeURIComponent(name)}/stat?path=${encodeURIComponent(path)}`;
  const res = await fetch(url, {
    headers: buildHeaders(token),
    credentials: 'include'
  });
  return handle<{ exists: boolean; is_directory: boolean; is_file: boolean }>(res);
}

export async function batchDelete(
  name: string,
  paths: string[],
  token: string | null,
): Promise<BatchResult> {
  const res = await fetch(`${API_BASE}/boxes/${encodeURIComponent(name)}/batch/delete`, {
    method: "POST",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ paths }),
    credentials: 'include'
  });
  return handle<BatchResult>(res);
}

export async function batchMove(
  name: string,
  paths: string[],
  destination: string,
  token: string | null,
): Promise<BatchResult> {
  const res = await fetch(`${API_BASE}/boxes/${encodeURIComponent(name)}/batch/move`, {
    method: "POST",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ paths, destination }),
    credentials: 'include'
  });
  return handle<BatchResult>(res);
}

export async function batchCopy(
  name: string,
  paths: string[],
  destination: string,
  token: string | null,
): Promise<BatchResult> {
  const res = await fetch(`${API_BASE}/boxes/${encodeURIComponent(name)}/batch/copy`, {
    method: "POST",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ paths, destination }),
    credentials: 'include'
  });
  return handle<BatchResult>(res);
}

export async function grepContent(
  name: string,
  pattern: string,
  directory: string,
  token: string | null,
  caseSensitive: boolean = false,
  limit: number = 100,
): Promise<GrepResponse> {
  const url = new URL(`${API_BASE}/boxes/${encodeURIComponent(name)}/grep`, window.location.origin);
  url.searchParams.set("pattern", pattern);
  url.searchParams.set("directory", directory);
  url.searchParams.set("case_sensitive", String(caseSensitive));
  url.searchParams.set("limit", String(limit));
  const res = await fetch(url.toString().replace(window.location.origin, ""), {
    headers: buildHeaders(token),
    credentials: 'include'
  });
  return handle<GrepResponse>(res);
}

export async function createArchive(
  name: string,
  paths: string[],
  destination: string,
  archiveName: string,
  format: string,
  token: string | null,
): Promise<SimpleMessage> {
  const res = await fetch(`${API_BASE}/boxes/${encodeURIComponent(name)}/archive/create`, {
    method: "POST",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ paths, destination, archive_name: archiveName, format }),
    credentials: 'include'
  });
  return handle<SimpleMessage>(res);
}

export async function extractArchive(
  name: string,
  archivePath: string,
  destination: string,
  token: string | null,
): Promise<SimpleMessage> {
  const res = await fetch(`${API_BASE}/boxes/${encodeURIComponent(name)}/archive/extract`, {
    method: "POST",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ archive_path: archivePath, destination }),
    credentials: 'include'
  });
  return handle<SimpleMessage>(res);
}

export async function searchDirectories(
  name: string,
  query: string,
  token: string | null,
  limit: number = 20,
): Promise<SearchResponse> {
  const url = new URL(`${API_BASE}/boxes/${encodeURIComponent(name)}/search`, window.location.origin);
  url.searchParams.set("q", query);
  url.searchParams.set("limit", String(limit));
  const res = await fetch(url.toString().replace(window.location.origin, ""), {
    headers: buildHeaders(token),
    credentials: 'include'
  });
  return handle<SearchResponse>(res);
}

// Snippets

export async function fetchSnippets(
  box: string,
  token: string | null,
): Promise<ApiSnippet[]> {
  const url = `${API_BASE}/snippets?box=${encodeURIComponent(box)}`;
  const res = await fetch(url, {
    headers: buildHeaders(token),
    credentials: 'include'
  });
  return handle<ApiSnippet[]>(res);
}

export async function createSnippet(
  box: string,
  label: string,
  command: string,
  category: string,
  token: string | null,
): Promise<ApiSnippet> {
  const res = await fetch(`${API_BASE}/snippets`, {
    method: "POST",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ box, label, command, category }),
    credentials: 'include'
  });
  return handle<ApiSnippet>(res);
}

export async function updateSnippet(
  snippetId: string,
  data: { label?: string; command?: string; category?: string; sort_order?: number },
  token: string | null,
): Promise<ApiSnippet> {
  const res = await fetch(`${API_BASE}/snippets/${encodeURIComponent(snippetId)}`, {
    method: "PUT",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify(data),
    credentials: 'include'
  });
  return handle<ApiSnippet>(res);
}

export async function deleteSnippet(
  snippetId: string,
  token: string | null,
): Promise<SimpleMessage> {
  const res = await fetch(`${API_BASE}/snippets/${encodeURIComponent(snippetId)}`, {
    method: "DELETE",
    headers: buildHeaders(token),
    credentials: 'include'
  });
  return handle<SimpleMessage>(res);
}

// Tunnels

export async function fetchTunnels(
  box: string,
  token: string | null,
): Promise<ApiTunnel[]> {
  const res = await fetch(`${API_BASE}/boxes/${encodeURIComponent(box)}/tunnels`, {
    headers: buildHeaders(token),
    credentials: 'include'
  });
  return handle<ApiTunnel[]>(res);
}

export async function createTunnel(
  box: string,
  data: { tunnel_type: string; local_host: string; local_port: number; remote_host: string; remote_port: number },
  token: string | null,
): Promise<ApiTunnel> {
  const res = await fetch(`${API_BASE}/boxes/${encodeURIComponent(box)}/tunnels`, {
    method: "POST",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify(data),
    credentials: 'include'
  });
  return handle<ApiTunnel>(res);
}

export async function deleteTunnel(
  box: string,
  tunnelId: string,
  token: string | null,
): Promise<SimpleMessage> {
  const res = await fetch(`${API_BASE}/boxes/${encodeURIComponent(box)}/tunnels/${encodeURIComponent(tunnelId)}`, {
    method: "DELETE",
    headers: buildHeaders(token),
    credentials: 'include'
  });
  return handle<SimpleMessage>(res);
}

// Recovery

export async function fetchSnapshotStatus(
  token: string | null,
): Promise<SnapshotStatus> {
  const res = await fetch(`${API_BASE}/snapshot/status`, {
    headers: buildHeaders(token),
    credentials: 'include'
  });
  return handle<SnapshotStatus>(res);
}

export async function fetchRecovery(
  token: string | null,
): Promise<LostSession[]> {
  const res = await fetch(`${API_BASE}/recovery`, {
    headers: buildHeaders(token),
    credentials: 'include'
  });
  return handle<LostSession[]>(res);
}

export async function recreateSession(
  sessionId: string,
  token: string | null,
): Promise<SimpleMessage> {
  const res = await fetch(`${API_BASE}/recovery/${encodeURIComponent(sessionId)}/recreate`, {
    method: "POST",
    headers: buildHeaders(token),
    credentials: 'include'
  });
  return handle<SimpleMessage>(res);
}

export async function dismissRecovery(
  token: string | null,
): Promise<SimpleMessage> {
  const res = await fetch(`${API_BASE}/recovery/dismiss`, {
    method: "POST",
    headers: buildHeaders(token),
    credentials: 'include'
  });
  return handle<SimpleMessage>(res);
}
