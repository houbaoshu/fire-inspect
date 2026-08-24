FROM node:22.23.2-bookworm-slim AS frontend
WORKDIR /build
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend ./
RUN pnpm build

FROM nginx:1.27.5-alpine
COPY deploy/nginx-main.conf /etc/nginx/nginx.conf
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=frontend /build/dist /usr/share/nginx/html
USER nginx
EXPOSE 8080 8443
ENTRYPOINT ["nginx", "-g", "daemon off;"]
