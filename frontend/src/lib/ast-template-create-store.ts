import { create } from "zustand";
import type { TiptapContent } from "@/lib/api";

interface CreateTemplatePayload {
  name: string;
  version: string;
  docNo: string;
  iriPattern: string;
  sampleText: string | null;
  sampleContent: TiptapContent | null;
}

interface CreateTemplateStore {
  payload: CreateTemplatePayload | null;
  setPayload: (p: CreateTemplatePayload) => void;
  clearPayload: () => void;
}

export const useCreateTemplateStore = create<CreateTemplateStore>((set) => ({
  payload: null,
  setPayload: (p) => set({ payload: p }),
  clearPayload: () => set({ payload: null }),
}));
