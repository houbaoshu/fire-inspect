# 前端

本目录是 React + TypeScript + Vite 单页应用。运行时资源全部由构建产物本地提供，不使用 CDN、在线字体或浏览器长期令牌。

## 命令

```bash
pnpm install --frozen-lockfile
pnpm test
pnpm typecheck
pnpm build
```

开发服务器默认把 `/api` 和 `/protected-media` 转发到 `127.0.0.1:8000`。启动 Django 前应设置 `FIRE_INSPECT_CSRF_TRUSTED_ORIGINS=http://127.0.0.1:5173`，否则跨端口的开发 Origin 会被 CSRF 校验正确拒绝。生产环境通过同源 `/api/v1/` 使用会话 Cookie 与 CSRF；可以在构建时通过 `VITE_API_BASE_URL` 修改 API 前缀，但不得写入密钥。

## SPA 路由

生产 Nginx 应在 API 与受保护文件规则之后包含 `nginx-spa.conf`。它只对前端路由回退到 `index.html`，静态资源缺失仍返回 404。

主要直达路由包括：

- `/inspections`
- `/inspections/new`
- `/inspections/:id/materials|transcripts|facts|regulations|record|photo-document|interview|history`
- `/admin/users|knowledge|templates|jobs|audit`

## 安全边界

- UI 权限隐藏只改善体验；所有访问仍由 API 与文件接口校验。
- AI 助手只提交查询并显示文本、来源和类型化导航动作。前端拒绝跨检查、外部 URL、未知类型和非安全 ID，不实现写操作。
- React 以文本节点显示用户、转写、知识与 AI 内容，不注入 HTML。
- 修改请求自动携带同源 CSRF Cookie；修订号和幂等键由命令调用显式传入。
