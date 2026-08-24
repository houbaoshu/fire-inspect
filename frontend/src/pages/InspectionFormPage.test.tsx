import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import { AuthContext, type AuthContextValue } from "../auth/AuthContext";
import { InspectionFormPage } from "./InspectionFormPage";

function LocationProbe() {
  return <output data-testid="location">{useLocation().pathname}</output>;
}

describe("InspectionFormPage", () => {
  it("creates the seven-type Shanghai-time inspection payload and enters its workspace", async () => {
    const post = vi.spyOn(api, "post").mockResolvedValue({
      id: "i-1",
      unit_name: "示例商场",
    } as never);
    const context: AuthContextValue = {
      user: { id: "u-1", username: "检查员甲", role: "inspector", status: "active" },
      status: "authenticated",
      login: async () => undefined,
      logout: async () => undefined,
      refresh: async () => undefined,
    };
    const queryClient = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    render(
      <AuthContext.Provider value={context}>
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={["/inspections/new"]}>
            <Routes>
              <Route path="/inspections/new" element={<InspectionFormPage />} />
              <Route path="*" element={<LocationProbe />} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>
      </AuthContext.Provider>,
    );
    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/单位（场所）名称/), "示例商场");
    await user.type(screen.getByLabelText(/^地址/), "振兴路 1 号");
    fireEvent.change(screen.getByLabelText(/检查时间/), { target: { value: "2026-08-24T09:30" } });
    await user.click(screen.getByLabelText("申请解除临时查封的检查"));
    await user.click(screen.getByRole("button", { name: "创建并进入工作台" }));

    await waitFor(() => expect(post).toHaveBeenCalledWith(
      "/inspections/",
      expect.objectContaining({
        unit_name: "示例商场",
        unit_address: "振兴路 1 号",
        inspection_at: "2026-08-24T01:30:00.000Z",
        inspection_type: "lift_seal",
        personnel: [{ name: "检查员甲", role: "inspector", sequence: 0 }],
      }),
      expect.objectContaining({ idempotencyKey: expect.any(String) }),
    ));
    expect(await screen.findByTestId("location")).toHaveTextContent("/inspections/i-1/materials");
  });
});
