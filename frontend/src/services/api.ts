import type { DashboardData, UploadResponse } from "../types";

const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

async function fetchJSON<T>(url: string, fallback: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) {
    try {
      const error = await response.json();
      throw new Error(error.detail || fallback);
    } catch {
      throw new Error(`${fallback} (HTTP ${response.status})`);
    }
  }
  return response.json();
}

export async function uploadDiveLog(
  file: File,
  sessionId?: string,
  donate?: boolean,
): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append("file", file);
  if (sessionId) {
    formData.append("session_id", sessionId);
  }
  if (donate) {
    formData.append("donate", "true");
  }

  const response = await fetch(`${API_BASE}/api/upload`, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Upload failed");
  }

  return response.json();
}

export function fetchDashboard(sessionId: string): Promise<DashboardData> {
  return fetchJSON<DashboardData>(
    `${API_BASE}/api/dashboard/${sessionId}`,
    "Failed to load dashboard"
  );
}

export function fetchSharedDashboard(shareId: string): Promise<DashboardData> {
  return fetchJSON<DashboardData>(
    `${API_BASE}/api/shared/${shareId}`,
    "Shared results not found"
  );
}

export async function saveRoastSummary(sessionId: string, roast: string): Promise<void> {
  await fetch(`${API_BASE}/api/sessions/${sessionId}/roast`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ roast }),
  });
}

export function createChatStream(
  message: string,
  sessionId: string,
  onChunk: (content: string) => void,
  onDone: () => void,
  onError: (error: string) => void
): AbortController {
  const controller = new AbortController();

  fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, session_id: sessionId }),
    signal: controller.signal,
  })
    .then((response) => {
      const reader = response.body?.getReader();
      const decoder = new TextDecoder();

      function read() {
        reader?.read().then(({ done, value }) => {
          if (done) {
            onDone();
            return;
          }

          const text = decoder.decode(value, { stream: true });
          const lines = text.split("\n");

          for (const line of lines) {
            if (line.startsWith("data: ")) {
              try {
                const data = JSON.parse(line.slice(6));
                if (data.content) {
                  onChunk(data.content);
                }
                if (data.error) {
                  onError(data.error);
                }
              } catch {
                // Skip non-JSON lines
              }
            }
          }

          read();
        });
      }

      read();
    })
    .catch((err) => {
      if (err.name !== "AbortError") {
        onError(err.message);
      }
    });

  return controller;
}
