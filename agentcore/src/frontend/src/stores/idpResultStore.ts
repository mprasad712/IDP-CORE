import { create } from "zustand";

/**
 * Shared IDP extracted-data result for the Playground.
 *
 * The document upload lives in the chat INPUT area (no-input.tsx), but the extracted
 * result is rendered in the MAIN chat area (chat-view.tsx). This store bridges the two
 * so the result shows where a chat response would, instead of docked at the bottom.
 */
interface IdpResultStore {
  /** The display-ready extracted-data object (status / headers / line_items / detected_elements), or null. */
  result: Record<string, unknown> | null;
  setResult: (result: Record<string, unknown> | null) => void;
  clearResult: () => void;
}

export const useIdpResultStore = create<IdpResultStore>((set) => ({
  result: null,
  setResult: (result) => set({ result }),
  clearResult: () => set({ result: null }),
}));
