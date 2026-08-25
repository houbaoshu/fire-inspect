#!/bin/sh
set -eu

https_port=${FIRE_INSPECT_HTTPS_PORT:-443}
case "$https_port" in
  ""|*[!0-9]*)
    echo "FIRE_INSPECT_HTTPS_PORT must be a valid TCP port" >&2
    exit 2
    ;;
esac
if [ "$https_port" -lt 1 ] || [ "$https_port" -gt 65535 ]; then
  echo "FIRE_INSPECT_HTTPS_PORT must be a valid TCP port" >&2
  exit 2
fi

https_port_suffix=":$https_port"
if [ "$https_port" -eq 443 ]; then
  https_port_suffix=""
fi

render_root=/tmp/fire-inspect-nginx
mkdir -p "$render_root"
sed "s/__FIRE_INSPECT_HTTPS_PORT_SUFFIX__/$https_port_suffix/g" \
  /etc/nginx/templates/fire-inspect.conf.template \
  > "$render_root/default.conf"

exec nginx -g "daemon off;"
