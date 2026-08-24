import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, createIdempotencyKey, getErrorMessage } from "../../api/client";
import { asList, type PaginatedResponse, type User } from "../../api/types";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { EmptyState, ErrorState, LoadingState } from "../../components/States";
import { StatusPill } from "../../components/StatusPill";
import { formatDateTime } from "../../utils/format";

export function UsersAdminPanel() {
  const queryClient = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const [username, setUsername] = useState("");
  const [role, setRole] = useState<User["role"]>("inspector");
  const [password, setPassword] = useState("");
  const [statusTarget, setStatusTarget] = useState<User | null>(null);
  const [editTarget, setEditTarget] = useState<User | null>(null);
  const [editRole, setEditRole] = useState<User["role"]>("inspector");
  const [editPassword, setEditPassword] = useState("");

  const usersQuery = useQuery({
    queryKey: ["admin-users"],
    queryFn: () => api.get<User[] | PaginatedResponse<User>>("/users/"),
  });
  const users = asList(usersQuery.data);

  const createMutation = useMutation({
    mutationFn: () => api.post("/users/", {
      username: username.trim(),
      role,
      password,
    }, { idempotencyKey: createIdempotencyKey("create-user") }),
    onSuccess: async () => {
      setUsername("");
      setPassword("");
      setRole("inspector");
      setCreateOpen(false);
      await queryClient.invalidateQueries({ queryKey: ["admin-users"] });
    },
  });

  const statusMutation = useMutation({
    mutationFn: () => api.patch(`/users/${statusTarget!.id}/`, {
      status: statusTarget!.status === "active" ? "disabled" : "active",
    }),
    onSuccess: async () => {
      setStatusTarget(null);
      await queryClient.invalidateQueries({ queryKey: ["admin-users"] });
    },
  });

  const editMutation = useMutation({
    mutationFn: () => api.patch(`/users/${editTarget!.id}/`, {
      role: editRole,
      ...(editPassword ? { password: editPassword } : {}),
    }),
    onSuccess: async () => {
      setEditTarget(null);
      setEditPassword("");
      await queryClient.invalidateQueries({ queryKey: ["admin-users"] });
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (username.trim() && password.length >= 10) createMutation.mutate();
  };

  if (usersQuery.isLoading) return <LoadingState label="正在加载用户" />;
  if (usersQuery.isError) return <ErrorState message={getErrorMessage(usersQuery.error)} onRetry={() => void usersQuery.refetch()} />;

  return (
    <section aria-labelledby="users-admin-title">
      <div className="section-heading">
        <div><h2 id="users-admin-title">用户管理</h2><p>用户原则上停用而不物理删除，以保留历史操作人语义。</p></div>
        <button className="button button-primary" type="button" onClick={() => setCreateOpen((value) => !value)}>＋ 新建用户</button>
      </div>

      {createOpen ? (
        <form className="panel inline-create-panel" onSubmit={submit}>
          <div className="form-grid two-columns">
            <label className="field"><span>用户名</span><input autoComplete="off" value={username} onChange={(event) => setUsername(event.target.value)} required maxLength={150} /></label>
            <label className="field"><span>角色</span><select value={role} onChange={(event) => setRole(event.target.value as User["role"])}><option value="inspector">检查员</option><option value="admin">管理员</option></select></label>
            <label className="field field-wide"><span>初始密码</span><input type="password" autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)} minLength={10} required /><small>至少 10 位；不提供默认密码。</small></label>
          </div>
          {createMutation.isError ? <div className="notice notice-error">{getErrorMessage(createMutation.error)}</div> : null}
          <div className="form-actions compact"><button className="button button-ghost" type="button" onClick={() => setCreateOpen(false)}>取消</button><button className="button button-primary" type="submit" disabled={!username.trim() || password.length < 10 || createMutation.isPending}>创建用户</button></div>
        </form>
      ) : null}

      {editTarget ? (
        <form className="panel inline-create-panel" onSubmit={(event) => { event.preventDefault(); editMutation.mutate(); }}>
          <div className="subsection-heading"><div><h3>编辑用户：{editTarget.username}</h3><p>可以调整角色或设置新密码；留空密码表示保持不变。</p></div></div>
          <div className="form-grid two-columns">
            <label className="field"><span>角色</span><select value={editRole} onChange={(event) => setEditRole(event.target.value as User["role"])}><option value="inspector">检查员</option><option value="admin">管理员</option></select></label>
            <label className="field"><span>新密码（可选）</span><input type="password" autoComplete="new-password" value={editPassword} onChange={(event) => setEditPassword(event.target.value)} minLength={10} /><small>填写时至少 10 位。</small></label>
          </div>
          {editMutation.isError ? <div className="notice notice-error">{getErrorMessage(editMutation.error)}</div> : null}
          <div className="form-actions compact"><button className="button button-ghost" type="button" onClick={() => { setEditTarget(null); setEditPassword(""); }}>取消</button><button className="button button-primary" type="submit" disabled={editMutation.isPending || (editRole === editTarget.role && !editPassword) || (Boolean(editPassword) && editPassword.length < 10)}>保存用户</button></div>
        </form>
      ) : null}

      {users.length === 0 ? (
        <EmptyState title="还没有用户" description="通过一次性初始化命令建立首个管理员后，可在这里添加检查员。" />
      ) : (
        <div className="panel table-wrap">
          <table>
            <thead><tr><th>用户</th><th>角色</th><th>状态</th><th>创建时间</th><th><span className="sr-only">操作</span></th></tr></thead>
            <tbody>{users.map((user) => (
              <tr key={user.id}>
                <td><strong>{user.display_name || user.username}</strong><small>{user.username}</small></td>
                <td>{user.role === "admin" ? "管理员" : "检查员"}</td>
                <td><StatusPill status={user.status === "active" ? "succeeded" : "cancelled"} label={user.status === "active" ? "启用" : "已停用"} /></td>
                <td>{formatDateTime(user.created_at)}</td>
                <td><div className="table-actions"><button className="button button-secondary button-small" type="button" onClick={() => { setEditTarget(user); setEditRole(user.role); setEditPassword(""); }}>编辑</button><button className={user.status === "active" ? "button button-danger-outline button-small" : "button button-secondary button-small"} type="button" onClick={() => setStatusTarget(user)}>{user.status === "active" ? "停用" : "启用"}</button></div></td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}

      <ConfirmDialog
        open={Boolean(statusTarget)}
        title={statusTarget?.status === "active" ? "停用这个用户？" : "重新启用这个用户？"}
        description={<><p>{statusTarget?.status === "active" ? "停用后，该用户的现有会话应失效且无法继续访问业务数据；历史操作人信息会保留。" : "启用后，该用户将恢复其角色对应的最小权限。"}</p>{statusMutation.isError ? <span className="form-error">{getErrorMessage(statusMutation.error)}</span> : null}</>}
        tone={statusTarget?.status === "active" ? "warning" : "default"}
        confirmLabel={statusTarget?.status === "active" ? "确认停用" : "确认启用"}
        busy={statusMutation.isPending}
        onCancel={() => setStatusTarget(null)}
        onConfirm={() => statusMutation.mutate()}
      />
    </section>
  );
}
