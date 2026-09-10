"use client";

import { useQuery } from "@tanstack/react-query";
import { getBatchDemo, getIdentity } from "@/lib/api";

export function useBatchDemo(documentIri: string | undefined) {
  const { username, role } = getIdentity();
  return useQuery({
    queryKey: ["batch-demo", username, role, documentIri],
    queryFn: ({ signal }) => getBatchDemo(documentIri!, signal),
    enabled: Boolean(documentIri),
    staleTime: 30_000,
    retry: false,
    refetchOnWindowFocus: false,
  });
}
