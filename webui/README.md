# nanobot webui

The browser front-end for the nanobot gateway. It is built with Vite + React 18 +
TypeScript + Tailwind 3 + shadcn/ui, talks to the gateway over the WebSocket
multiplex protocol, and reads session metadata from the embedded REST surface
on the same port.

For the project overview, install guide, and general docs map, see the root
[`README.md`](../README.md).

## Current status

> [!NOTE]
> The standalone WebUI development workflow currently requires a source
> checkout.
>
> WebUI changes in the GitHub repository may land before they are included in
> the next packaged release, so source installs and published package versions
> are not yet guaranteed to move in lockstep.

## Layout

```text
webui/                 source tree (this directory)
nanobot/web/dist/      build output served by the gateway
```

## Develop from source

### 1. Install nanobot from source

From the repository root:

```bash
pip install -e .
```

### 2. Enable the WebSocket channel

In `~/.nanobot/config.json`:

```json
{ "channels": { "websocket": { "enabled": true } } }
```

### 3. Start the gateway

In one terminal:

```bash
nanobot gateway
```

### 4. Start the WebUI dev server

In another terminal:

```bash
cd webui
bun install            # npm install also works
bun run dev
```

Then open `http://127.0.0.1:5173`.

By default, the dev server proxies `/api`, `/webui`, `/auth`, and WebSocket
traffic to `http://127.0.0.1:8765`.

If your gateway listens on a non-default port, point the dev server at it:

```bash
NANOBOT_API_URL=http://127.0.0.1:9000 bun run dev
```

## Build for packaged runtime

```bash
cd webui
bun run build
```

This writes the production assets to `../nanobot/web/dist`, which is the
directory served by `nanobot gateway` and bundled into the Python wheel.

If you are cutting a release, run the build before packaging so the published
wheel contains the current WebUI assets.

## Deploy to GitHub Pages

The WebUI can be deployed as a static site on GitHub Pages, connecting to a
remote nanobot gateway over HTTPS + WebSocket. A GitHub Actions workflow
(`.github/workflows/deploy-webui.yml`) is included for automatic deployment on
pushes to the `webui` branch.

### Ask Nanobot to Configure Itself

Send this message to your Nanobot (via your existing channel, e.g. Feishu, terminal, etc.):

```
Please configure my nanobot gateway for GitHub Pages deployment. Here's what I need:

1. My domain is: (your domain, e.g. nanobot.example.com)
2. My GitHub Pages URL is: (e.g. https://your-username.github.io)
3. My SSL certificate is at: (path to fullchain.pem)
4. My SSL key is at: (path to privkey.pem)

Do the following:

1. Update the websocket channel in my config.json:
   - Set `host` to `127.0.0.1` and `port` to `18765` (nginx will proxy to this)
   - Set `token_issue_secret` to a strong random string
   - Leave `sslCertfile` and `sslKeyfile` empty
2. Generate an nginx config at `/etc/nginx/sites-available/nanobot-proxy.conf` that:
   - Listens on port 8765 with SSL using my cert
   - Handles CORS preflight (OPTIONS) for my GitHub Pages origin
   - Adds CORS headers to all proxied responses
   - Proxies everything (including WebSocket upgrade) to 127.0.0.1:18765
3. Show me the sudo commands to enable the nginx site and reload nginx
4. Give me the Server URL and the `token_issue_secret` value to enter in the WebUI

Reference: https://github.com/HKUDS/nanobot/blob/main/docs/WEBSOCKET.md
```

After Nanobot updates the config and generates the nginx config, run the
sudo commands it shows you, then restart the gateway with `/restart`.
Open `https://<username>.github.io/nanobot/` and enter the Server URL and Secret.

### Manual Configuration (Optional)

If you prefer to configure everything manually, follow the steps below.

### Prerequisites

1. A fork of the nanobot repository on GitHub
2. A domain name with an SSL certificate (e.g. Let's Encrypt)
3. nginx installed on the server hosting the nanobot gateway
4. The gateway accessible from the public internet

### 1. Configure the nanobot gateway

In `~/.nanobot/config.json`, set the websocket channel to listen on localhost
only (nginx will handle TLS):

```json
{
  "channels": {
    "websocket": {
      "enabled": true,
      "host": "127.0.0.1",
      "port": 18765,
      "token_issue_secret": "your-secret-here",
      "sslCertfile": "",
      "sslKeyfile": ""
    }
  }
}
```

- **`host`** — Bind to `127.0.0.1` so only nginx can reach the gateway.
- **`token_issue_secret`** — Set a secret to require authentication for WebUI
  access. When configured, the WebUI must provide this secret to connect.
  **Important:** when deploying behind nginx, you MUST set this — nginx makes all
  connections appear as localhost, so the default localhost-only check is
  effectively bypassed. Leave empty only for local development without nginx.
- **`sslCertfile`/`sslKeyfile`** — Leave empty. nginx handles TLS.

### 2. Restart the gateway

Config changes take effect after a restart:

```bash
nanobot gateway
# or if already running, use /restart in any channel
```

### 3. Configure nginx reverse proxy

Create `/etc/nginx/sites-available/nanobot-proxy.conf`:

```nginx
server {
    listen 8765 ssl;
    server_name your-domain.example.com;

    ssl_certificate     /path/to/fullchain.pem;
    ssl_certificate_key /path/to/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;

    location / {
        # CORS preflight — handled by nginx because the websockets library
        # rejects OPTIONS requests before they reach process_request.
        if ($request_method = OPTIONS) {
            add_header Access-Control-Allow-Origin  https://<username>.github.io always;
            add_header Access-Control-Allow-Methods  "GET, OPTIONS" always;
            add_header Access-Control-Allow-Headers  "Authorization, X-Nanobot-Auth, Content-Type" always;
            add_header Access-Control-Allow-Credentials true always;
            return 204;
        }

        proxy_pass http://127.0.0.1:18765;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;

        # CORS headers on proxied responses
        add_header Access-Control-Allow-Origin  https://<username>.github.io always;
        add_header Access-Control-Allow-Methods  "GET, OPTIONS" always;
        add_header Access-Control-Allow-Headers  "Authorization, X-Nanobot-Auth, Content-Type" always;
        add_header Access-Control-Allow-Credentials true always;
    }
}
```

Replace:
- `your-domain.example.com` — your domain matching the SSL certificate
- `/path/to/fullchain.pem`, `/path/to/privkey.pem` — your SSL cert paths
- `https://<username>.github.io` — your GitHub Pages origin

Then enable the site and reload nginx:

```bash
sudo ln -s /etc/nginx/sites-available/nanobot-proxy.conf /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### 4. Enable GitHub Pages

1. Go to your fork's **Settings > Pages**
2. Set **Source** to "GitHub Actions"
3. Push to the `webui` branch — the `deploy-webui.yml` workflow will build and deploy

### 5. Connect from the WebUI

Open `https://<username>.github.io/nanobot/` and enter:

- **Server URL**: `https://your-domain.example.com:8765`
- **Secret**: The `token_issue_secret` value from your config (required)

### How it works

```
Browser (GitHub Pages, HTTPS)
  │
  │  fetch /webui/bootstrap  (GET)
  │  WebSocket /ws?token=...
  │
  ▼
nginx (TLS termination + CORS)
  │
  │  proxy_pass (plain HTTP)
  │
  ▼
nanobot gateway (127.0.0.1:18765)
```

- **nginx** terminates TLS, handles CORS preflight (OPTIONS), and adds CORS
  headers to all responses. It also proxies WebSocket upgrade requests.
- **nanobot** listens on localhost only. When `token_issue_secret` is set in
  config, the WebUI must authenticate with the secret; otherwise only
  localhost connections are allowed.

### Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `401 Unauthorized` on bootstrap | `token_issue_secret` is set but the WebUI didn't provide the correct secret | Enter the secret in the WebUI connection form |
| `ERR_CERT_COMMON_NAME_INVALID` | SSL cert domain doesn't match the URL | Use the domain that matches your cert, not the raw IP |
| `Mixed Content` error | Accessing `http://` from `https://` page | Always use `https://` for the Server URL |
| Duplicate `Access-Control-Allow-Origin` | nginx adding CORS headers in multiple location blocks | Check nginx config for duplicate `add_header` directives |
| `net::ERR_EMPTY_RESPONSE` on OPTIONS | nanobot's websockets library rejects OPTIONS | Ensure nginx is proxying (not connecting directly to nanobot) |

## Test

```bash
cd webui
bun run test
```

## Acknowledgements

- [`agent-chat-ui`](https://github.com/langchain-ai/agent-chat-ui) for UI and
  interaction inspiration across the chat surface.
