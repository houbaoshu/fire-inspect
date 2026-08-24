import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { AuthContext, type AuthContextValue } from "../auth/AuthContext";
import { LoginPage } from "./LoginPage";

function LocationProbe() {
  return <output>{useLocation().pathname}</output>;
}

describe("LoginPage", () => {
  it("contains no default credentials and authenticates with a session login", async () => {
    const login = vi.fn().mockResolvedValue(undefined);
    const context: AuthContextValue = {
      user: null,
      status: "anonymous",
      login,
      logout: async () => undefined,
      refresh: async () => undefined,
    };
    render(
      <AuthContext.Provider value={context}>
        <MemoryRouter initialEntries={["/login"]}>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/inspections" element={<LocationProbe />} />
          </Routes>
        </MemoryRouter>
      </AuthContext.Provider>,
    );

    const username = screen.getByLabelText("用户名");
    const password = screen.getByLabelText("密码");
    expect(username).toHaveValue("");
    expect(password).toHaveValue("");

    const user = userEvent.setup();
    await user.type(username, "inspector_a");
    await user.type(password, "a-secure-password");
    await user.click(screen.getByRole("button", { name: "登录" }));

    expect(login).toHaveBeenCalledWith("inspector_a", "a-secure-password");
    expect(await screen.findByText("/inspections")).toBeVisible();
  });

  it("rejects a protocol-relative login return target", async () => {
    const login = vi.fn().mockResolvedValue(undefined);
    const context: AuthContextValue = {
      user: null,
      status: "anonymous",
      login,
      logout: async () => undefined,
      refresh: async () => undefined,
    };
    render(
      <AuthContext.Provider value={context}>
        <MemoryRouter initialEntries={[{ pathname: "/login", state: { from: "//evil.example/steal" } }]}>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/inspections" element={<LocationProbe />} />
          </Routes>
        </MemoryRouter>
      </AuthContext.Provider>,
    );
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("用户名"), "inspector_a");
    await user.type(screen.getByLabelText("密码"), "a-secure-password");
    await user.click(screen.getByRole("button", { name: "登录" }));

    expect(await screen.findByText("/inspections")).toBeVisible();
  });
});
