export type User = { id: number; username: string; role: "admin" | "user"; is_active: boolean };
export type UiConfig = { kb_mode: "demo" | "real_ai"; max_upload_bytes: number; chat_max_question_chars: number };
export type Document = {
  id: number; filename: string; file_type: string; status: "pending" | "ready" | "failed";
  error: string | null; generation: number; index_current: boolean; created_at: string; updated_at: string;
};
export type Conversation = { id: number; title: string; created_at: string; updated_at: string };
export type Source = {
  id: number; citation_id: string | null; filename: string; page_number: number | null;
  line_start: number | null; line_end: number | null; excerpt: string | null; deleted: boolean; anchor: string;
};
export type ChatMessage = {
  id: number; role: "user" | "assistant";
  kind: "question" | "answer" | "demo" | "insufficient" | "index_unavailable";
  text: string; reply_to_id: number | null; created_at: string; sources: Source[];
};
export type ConversationDetail = Conversation & { messages: ChatMessage[] };
export type QuestionPair = { user: ChatMessage; assistant: ChatMessage };

export class ApiError extends Error {
  constructor(public status: number) {
    super(`API request failed: ${status}`);
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api${path}`, { ...options, credentials: "same-origin", cache: "no-store" });
  if (!response.ok) throw new ApiError(response.status);
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

function json(method: "POST" | "PUT" | "PATCH", value: unknown): RequestInit {
  return { method, headers: { "content-type": "application/json" }, body: JSON.stringify(value) };
}

export const api = {
  me: () => request<User>("/auth/me"),
  login: (username: string, password: string) => request<User>("/auth/login", json("POST", { username, password })),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
  config: () => request<UiConfig>("/ui/config"),
  documents: (signal?: AbortSignal) => request<Document[]>("/admin/documents", { signal }),
  uploadDocument: (file: File) => {
    const body = new FormData();
    body.set("file", file);
    return request<Document>("/admin/documents", { method: "POST", body });
  },
  deleteDocument: (id: number) => request<void>(`/admin/documents/${id}`, { method: "DELETE" }),
  reindexDocument: (id: number) => request<Document>(`/admin/documents/${id}/reindex`, { method: "POST" }),
  reindexAll: () => request<{ queued: number }>("/admin/documents/reindex", { method: "POST" }),
  users: () => request<User[]>("/admin/users"),
  createUser: (username: string, password: string) => request<User>("/admin/users", json("POST", { username, password, role: "user" })),
  disableUser: (id: number) => request<User>(`/admin/users/${id}/disable`, { method: "POST" }),
  resetPassword: (id: number, password: string) => request<User>(`/admin/users/${id}/reset-password`, json("POST", { password })),
  conversations: () => request<Conversation[]>("/conversations"),
  conversation: (id: number, signal?: AbortSignal) => request<ConversationDetail>(`/conversations/${id}`, { signal }),
  createConversation: (title: string) => request<Conversation>("/conversations", json("POST", { title })),
  deleteConversation: (id: number) => request<void>(`/conversations/${id}`, { method: "DELETE" }),
  ask: (id: number, question: string) => request<QuestionPair>(`/conversations/${id}/messages`, json("POST", { question })),
};
