import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "../api/client";
import { AuthContext, type AuthContextValue } from "../auth/AuthContext";
import type { User } from "../api/types";
import { AppShell } from "./AppShell";

function renderShell(user: User, logout = vi.fn().mockResolvedValue(undefined)) {
  const context: AuthContextValue = {
    user,
    status: "authenticated",
    login: async () => undefined,
    logout,
    refresh: async () => undefined,
  };
  render(
    <AuthContext.Provider value={context}>
      <MemoryRouter initialEntries={["/inspections"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/inspections" element={<h1>检查列表内容</h1>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </AuthContext.Provider>,
  );
  return logout;
}

const inspector: User = { id: "u-1", username: "检查员甲", role: "inspector", status: "active" };

describe("AppShell permission navigation", () => {
  it("does not expose administrator navigation to an inspector", () => {
    renderShell(inspector);
    expect(screen.getByRole("link", { name: /检查管理/ })).toBeVisible();
    expect(screen.queryByRole("link", { name: /用户管理/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /模板映射/ })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "检查列表内容" })).toBeVisible();
  });

  it("shows all administrator destinations only to an administrator", () => {
    renderShell({ ...inspector, id: "u-admin", username: "管理员", role: "admin" });
    for (const label of ["用户管理", "知识库", "模板映射", "任务中心", "审计记录"]) {
      expect(screen.getByRole("link", { name: new RegExp(label) })).toBeVisible();
    }
  });

  it("keeps the local session visible and reports a safe logout failure", async () => {
    const logout = vi.fn().mockRejectedValue(new ApiError(503, "logout_failed", "退出服务暂时不可用。"));
    renderShell(inspector, logout);
    await userEvent.setup().click(screen.getByRole("button", { name: "退出登录" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("退出服务暂时不可用。");
    expect(screen.getByText("检查员甲")).toBeVisible();
  });
});
