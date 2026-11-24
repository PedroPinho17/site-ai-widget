import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router-dom";

import { AuthProvider } from "@/auth/AuthProvider";
import { RequireAuth } from "@/auth/RequireAuth";
import { AppLayout } from "@/layout/AppLayout";
import { ComingSoonPage, DashboardPage } from "@/pages/ComingSoonPage";
import { KeysPage } from "@/pages/KeysPage";
import { LoginPage } from "@/pages/LoginPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: false, refetchOnWindowFocus: false },
  },
});

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<RequireAuth />}>
        <Route element={<AppLayout />}>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/documents" element={<ComingSoonPage title="Documents" />} />
          <Route path="/database" element={<ComingSoonPage title="Database" />} />
          <Route path="/conversations" element={<ComingSoonPage title="Conversations" />} />
          <Route path="/widget" element={<ComingSoonPage title="Widget" />} />
          <Route path="/keys" element={<KeysPage />} />
        </Route>
      </Route>
    </Routes>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <AppRoutes />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
