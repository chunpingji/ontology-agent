import { create } from "zustand";
import type { TiptapContent } from "@/lib/api";

interface CreateTemplatePayload {
  name: string;
  version: string;
  docNo: string;
  iriPattern: string;
  sampleText: string | null;
  sampleContent: TiptapContent | null;
  // 原始示例 .docx：创建成功后作为「输出格式模板」附加到新模板（内存中传递，随客户端
  // 导航存活；硬刷新丢失时整个 payload 一并失效并回退向导，无半态）。此前创建流程只存
  // 解析后的文本/JSON，原始 docx 被 parse-sample 的临时文件删除后即丢失，导致新建模板
  // 的「套用输出模板」功能形同虚设（须再进编辑页手工「替换」一次才生效）。
  sampleFile: File | null;
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
