import { create } from "zustand";

/**
 * Persisted history of IDP document runs ("sessions") for the Playground.
 *
 * Each upload becomes a session that OUTLIVES the Playground modal (and a page reload) — the backend
 * run is a detached background task, so closing the modal never stops it; this keeps the frontend able
 * to re-attach and show the result. The user can revisit any past session and see its extracted data.
 *
 * Stored in localStorage (no zustand persist middleware is used elsewhere in this app, so we do it by
 * hand). Only the display-ready result is kept — small and safe to serialize.
 */
export interface IdpSession {
  id: string; // document_id
  jobId: string | null;
  fileName: string;
  agentId: string | null;
  status: "processing" | "done" | "error";
  /** buildIdpDisplay() output — what the main chat area renders. */
  result: Record<string, unknown> | null;
  error?: string | null;
  createdAt: number;
}

interface IdpSessionState {
  sessions: IdpSession[]; // newest first
  activeId: string | null;
  /** Create or patch a session by id (merges into an existing one). */
  upsertSession: (patch: Partial<IdpSession> & { id: string }) => void;
  /** Select the session to view (null = show the fresh upload area). */
  selectSession: (id: string | null) => void;
  removeSession: (id: string) => void;
}

const KEY = "idp-sessions-v1";
const MAX = 30; // cap history so localStorage can't grow unbounded

function loadPersisted(): { sessions: IdpSession[]; activeId: string | null } {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed?.sessions)) {
        return { sessions: parsed.sessions, activeId: parsed.activeId ?? null };
      }
    }
  } catch {
    // ignore corrupt/absent storage — start empty
  }
  return { sessions: [], activeId: null };
}

function persist(sessions: IdpSession[], activeId: string | null) {
  try {
    localStorage.setItem(
      KEY,
      JSON.stringify({ sessions: sessions.slice(0, MAX), activeId }),
    );
  } catch {
    // storage full / unavailable — history just won't survive reload; not fatal
  }
}

const initial = loadPersisted();

export const useIdpSessionStore = create<IdpSessionState>((set) => ({
  sessions: initial.sessions,
  activeId: initial.activeId,

  upsertSession: (patch) =>
    set((st) => {
      const existing = st.sessions.find((s) => s.id === patch.id);
      let sessions: IdpSession[];
      if (existing) {
        sessions = st.sessions.map((s) =>
          s.id === patch.id ? { ...s, ...patch } : s,
        );
      } else {
        const created: IdpSession = {
          id: patch.id,
          jobId: patch.jobId ?? null,
          fileName: patch.fileName ?? "document",
          agentId: patch.agentId ?? null,
          status: patch.status ?? "processing",
          result: patch.result ?? null,
          error: patch.error ?? null,
          createdAt: patch.createdAt ?? Date.now(),
        };
        sessions = [created, ...st.sessions].slice(0, MAX);
      }
      persist(sessions, st.activeId);
      return { sessions };
    }),

  selectSession: (id) =>
    set((st) => {
      persist(st.sessions, id);
      return { activeId: id };
    }),

  removeSession: (id) =>
    set((st) => {
      const sessions = st.sessions.filter((s) => s.id !== id);
      const activeId = st.activeId === id ? null : st.activeId;
      persist(sessions, activeId);
      return { sessions, activeId };
    }),
}));
