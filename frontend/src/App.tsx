import { Navigate, Route, Routes } from "react-router-dom";

import { RequireAdmin } from "./auth/RequireAdmin";
import { RequireAuth } from "./auth/RequireAuth";
import { AppShell } from "./components/AppShell";
import { AdminPage } from "./pages/AdminPage";
import { InspectionFormPage } from "./pages/InspectionFormPage";
import { InspectionsPage } from "./pages/InspectionsPage";
import { LoginPage } from "./pages/LoginPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { WorkspacePage } from "./pages/WorkspacePage";

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <RequireAuth>
            <AppShell />
          </RequireAuth>
        }
      >
        <Route index element={<Navigate to="/inspections" replace />} />
        <Route path="/inspections" element={<InspectionsPage />} />
        <Route path="/inspections/new" element={<InspectionFormPage />} />
        <Route path="/inspections/:inspectionId" element={<Navigate to="materials" replace />} />
        <Route path="/inspections/:inspectionId/:section/*" element={<WorkspacePage />} />
        <Route
          path="/admin"
          element={
            <RequireAdmin>
              <Navigate to="/admin/users" replace />
            </RequireAdmin>
          }
        />
        <Route
          path="/admin/:section"
          element={
            <RequireAdmin>
              <AdminPage />
            </RequireAdmin>
          }
        />
      </Route>
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}
