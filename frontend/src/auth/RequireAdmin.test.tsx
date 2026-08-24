import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { AuthContext, type AuthContextValue } from "./AuthContext";
import { RequireAdmin } from "./RequireAdmin";

const inspectorContext: AuthContextValue = {
  user: { id: "u-1", username: "检查员甲", role: "inspector", status: "active" },
  status: "authenticated",
  login: async () => undefined,
  logout: async () => undefined,
  refresh: async () => undefined,
};

describe("RequireAdmin", () => {
  it("does not render administrator content for an inspector", () => {
    render(
      <AuthContext.Provider value={inspectorContext}>
        <MemoryRouter>
          <RequireAdmin>
            <div>用户管理机密内容</div>
          </RequireAdmin>
        </MemoryRouter>
      </AuthContext.Provider>,
    );

    expect(screen.queryByText("用户管理机密内容")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "无权访问" })).toBeVisible();
  });
});
