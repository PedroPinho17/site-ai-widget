import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

import { clearSession } from "@/lib/session";

// Vitest runs without globals, so Testing Library's auto-cleanup is off:
// unmount every render so one test's DOM never leaks into the next.
afterEach(() => {
  cleanup();
  clearSession();
  sessionStorage.clear();
  localStorage.clear();
});
