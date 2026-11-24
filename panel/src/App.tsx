import { QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router-dom";

import { AuthProvider } from "@/auth/AuthProvider";
import { RequireAuth } from "@/auth/RequireAuth";
import { AppLayout } from "@/layout/AppLayout";
import { createQueryClient } from "@/lib/query-client";
import { ComingSoonPage } from "@/pages/ComingSoonPage";
import { DashboardPage } from "@/pages/DashboardPage";
import { DatabasePage } from "@/pages/DatabasePage";
import { DocumentsPage } from "@/pages/DocumentsPage";
import { KeysPage } from "@/pages/KeysPage";
import { LoginPage } from "@/pages/LoginPage";
import { TestAssistantPage } from "@/pages/TestAssistantPage";
import { WidgetPage } from "@/pages/WidgetPage";

const queryClient = createQueryClient();

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<RequireAuth />}>
        <Route element={<AppLayout />}>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/documents" element={<DocumentsPage />} />
          <Route path="/database" element={<DatabasePage />} />
          <Route path="/conversations" element={<ComingSoonPage title="Conversations" />} />
          <Route path="/widget" element={<WidgetPage />} />
          <Route path="/test" element={<TestAssistantPage />} />
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
