import type {
  ApiBox,
  BoxStatus,
  BootstrapPayload,
  DirectoryListing,
  FavoriteToggle,
  FilePreview,
  PinToggle,
  SessionInfo,
  TerminalHandshake,
  SimpleMessage,
} from "./types";

const API_BASE = "/api/v1";

async function handle<T>(response: Response, originalRequest?: { url: string, options: RequestInit }): Promise<T> {
  if (!response.ok) {
    // Auto-retry token issues once
    if (response.status === 403 && !response.url.includes('/bootstrap') && originalRequest) {
      console.warn('Token invalid, fetching fresh token...');
      try {
        const { useBootstrapStore } = await import('@/stores/bootstrap');
        const bootstrapStore = useBootstrapStore();
        
        // Clear old token and fetch fresh one
        bootstrapStore.setToken(null);
        await bootstrapStore.bootstrap();
        
        // Retry the original request with new token
        const token = bootstrapStore.token;
        if (token) {
          const newHeaders = { ...originalRequest.options.headers, ...buildHeaders(token) };
          const retryResponse = await fetch(originalRequest.url, {
            ...originalRequest.options,
            headers: newHeaders
          });
          if (retryResponse.ok) {
            return retryResponse.json() as Promise<T>;
          }
        }
      } catch (retryError) {
        console.error('Token refresh failed:', retryError);
      }
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
  return headers;
}

export async function fetchBootstrap(): Promise<BootstrapPayload> {
  const res = await fetch(`${API_BASE}/bootstrap?_t=${Date.now()}`, {
    headers: buildHeaders(),
    cache: 'no-cache'
  });
  return handle<BootstrapPayload>(res);
}

export async function fetchBoxes(token: string | null): Promise<ApiBox[]> {
  const url = `${API_BASE}/boxes`;
  const options = { headers: buildHeaders(token) };
  const res = await fetch(url, options);
  return handle<ApiBox[]>(res, { url, options });
}

export async function fetchBox(name: string, token: string | null): Promise<ApiBox> {
  const res = await fetch(`${API_BASE}/boxes/${encodeURIComponent(name)}`, {
    headers: buildHeaders(token),
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
  });
  return handle<FilePreview>(res);
}

export async function fetchSessions(token: string | null): Promise<SessionInfo> {
  const res = await fetch(`${API_BASE}/sessions`, { headers: buildHeaders(token) });
  return handle<SessionInfo>(res);
}

export async function fetchTerminalHandshake(token: string | null): Promise<TerminalHandshake> {
  const res = await fetch(`${API_BASE}/terminal/handshake`, { headers: buildHeaders(token) });
  return handle<TerminalHandshake>(res);
}

export async function togglePin(name: string, token: string | null): Promise<PinToggle> {
  const res = await fetch(`${API_BASE}/boxes/${encodeURIComponent(name)}/pin`, {
    method: "POST",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
  });
  return handle<PinToggle>(res);
}

export async function toggleFavorite(
  name: string,
  path: string,
  favorite: boolean,
  token: string | null,
): Promise<FavoriteToggle> {
  const res = await fetch(`${API_BASE}/boxes/${encodeURIComponent(name)}/fav`, {
    method: "POST",
    headers: { ...buildHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ path, favorite }),
  });
  return handle<FavoriteToggle>(res);
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

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}/boxes/${encodeURIComponent(name)}/upload`, true);
    xhr.setRequestHeader("Accept", "application/json");
    if (token) {
      xhr.setRequestHeader("X-SSHLER-TOKEN", token);
    }

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
  });
  return handle<BoxStatus>(res);
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
  });
  if (!res.ok) {
    throw new Error(`download failed ${res.status}`);
  }
  return res.blob();
}
