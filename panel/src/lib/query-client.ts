import { QueryClient } from "@tanstack/react-query";

/**
 * The panel's QueryClient. No automatic retries for queries or mutations, so a
 * 4xx or 429 never turns one click or one page view into several requests.
 * Tests use this same factory, so they exercise the real settings.
 */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false },
      mutations: { retry: false },
    },
  });
}
