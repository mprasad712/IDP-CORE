import { create } from "zustand";

/**
 * The currently-processing IDP document, shared across surfaces (Playground upload + Builder-page
 * run panel). Kept in a global store (not component state) so the run KEEPS being tracked — canvas
 * highlighting + timeline — even after the Playground modal is closed. The backend run is a detached
 * background task and never stops on close; this just keeps the UI following it.
 */
interface IdpRunState {
  activeDocId: string | null;
  activeFileName: string | null;
  /** Processing job id — used to stream native per-vertex build events (/build/{jobId}/events). */
  activeJobId: string | null;
  /** Flipped true when the native event stream ends — drives the "Run another" affordance. */
  runDone: boolean;
  setActiveRun: (
    docId: string,
    fileName?: string | null,
    jobId?: string | null,
  ) => void;
  setRunDone: (done: boolean) => void;
  clearActiveRun: () => void;
}

export const useIdpRunStore = create<IdpRunState>((set) => ({
  activeDocId: null,
  activeFileName: null,
  activeJobId: null,
  runDone: false,
  setActiveRun: (docId, fileName = null, jobId = null) =>
    set({
      activeDocId: docId,
      activeFileName: fileName,
      activeJobId: jobId,
      runDone: false,
    }),
  setRunDone: (done) => set({ runDone: done }),
  clearActiveRun: () =>
    set({
      activeDocId: null,
      activeFileName: null,
      activeJobId: null,
      runDone: false,
    }),
}));
