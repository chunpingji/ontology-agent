"use client";

import { useCallback, useSyncExternalStore } from "react";
import { getIdentity, setIdentity as persistIdentity, type Identity } from "@/lib/api";

/** Roles recognised by the platform (aligns with backend VALID_ROLES). */
export type Role = "senior_analyst" | "operator" | "qa";

/**
 * Reactive identity/role hook (T002, Foundational).
 *
 * Wraps the existing `getIdentity()` / `setIdentity()` localStorage mechanism in
 * a `useSyncExternalStore` so that nav visibility (US1) and the approvals gate
 * (US3) re-render the moment the identity switcher writes a new role — across
 * components and across tabs.
 *
 * SSR-safe: the server snapshot is the default identity, and React reconciles
 * the client snapshot after hydration without a mismatch warning. Using an
 * external store (not `useEffect` + `setState`) also keeps us clear of React 19's
 * `react-hooks/set-state-in-effect` rule.
 */

const STORAGE_KEY = "slpra.identity";
const CHANGE_EVENT = "slpra:identity-change";

// useSyncExternalStore requires getSnapshot to return a stable reference between
// real changes; getIdentity() parses JSON into a fresh object every call, so we
// cache by raw string and only mint a new object when the stored value changes.
let cachedRaw: string | null = null;
let cachedIdentity: Identity = getIdentity();

function readSnapshot(): Identity {
  if (typeof window === "undefined") return cachedIdentity;
  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (raw !== cachedRaw) {
    cachedRaw = raw;
    cachedIdentity = getIdentity();
  }
  return cachedIdentity;
}

const SERVER_IDENTITY: Identity = { username: "analyst", role: "senior_analyst" };
const getServerSnapshot = (): Identity => SERVER_IDENTITY;

function subscribe(onStoreChange: () => void): () => void {
  window.addEventListener("storage", onStoreChange);
  window.addEventListener(CHANGE_EVENT, onStoreChange);
  return () => {
    window.removeEventListener("storage", onStoreChange);
    window.removeEventListener(CHANGE_EVENT, onStoreChange);
  };
}

export interface UseIdentity {
  identity: Identity;
  role: string;
  setIdentity: (next: Identity) => void;
}

export function useIdentity(): UseIdentity {
  const identity = useSyncExternalStore(subscribe, readSnapshot, getServerSnapshot);

  const setIdentity = useCallback((next: Identity) => {
    persistIdentity(next);
    cachedRaw = null; // invalidate cache so the next snapshot re-reads
    // `storage` events don't fire in the same tab — notify local subscribers.
    window.dispatchEvent(new Event(CHANGE_EVENT));
  }, []);

  return { identity, role: identity.role, setIdentity };
}
