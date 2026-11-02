# dakboard

A self-hosted family wall board - Google Calendar, a Google Photos album on
rotation, and today's weather, in a fullscreen web page.

## How it is deployed

This stack is deployed by **Portainer**, from the `longieirl/dakboard`
repository image (not built from source - the image is built by CI and
pushed to `ghcr.io`).

| Field | Value |
|---|---|
| Repository URL | `https://github.com/longieirl/dakboard.git` |
| Branch | `main` |
| Compose path | `stacks/dakboard/docker-compose.yml` |
| Authentication | Username + fine-grained PAT (Contents: Read-only) |
| GitOps updates mechanism | **Polling** (NOT Webhook - see the repo README) |
| Polling interval | `24h` |
| Re-pull image | OFF (Watchtower handles image updates) |

`DOCKER_DATA_HOME` and `DOCKER_SHARED_HOME` must be set in Portainer's
Environment Variables tab at stack creation/redeploy time - Portainer CE has
no global env var injection.

## Access

- Board: `http://192.168.1.6:8099/`
- Settings: `http://192.168.1.6:8099/settings`

For a hostname, add a site block to the `caddy` stack:

```
board.longie.net {
    reverse_proxy dakboard:8099
}
```

## First run

Open the settings page and:

1. **Calendar** - pick iCal (paste the secret address) or OAuth.
2. **Photos** - paste a shared album link. The album must be shared with
   "Anyone with the link".
3. **Weather** - type a town name.

Each section has a "Save and test" button, which saves what it tested and
confirms it works.

## Kiosk

On the screen itself, run Chromium in kiosk mode:

```
chromium --kiosk --noerrdialogs --disable-infobars --disable-pinch \
         --overscroll-history-navigation=0 http://192.168.1.6:8099/
```
