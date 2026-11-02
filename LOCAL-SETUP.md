# Moving the board onto your own machine

Everything you need to get the family board running on hardware you own, from
an empty folder to a board on the wall. Nothing here needs the cloud.

This guide assumes you have either:

- **`dakboard.zip`** — the full source, ready to unpack; or
- the git repository (`https://github.com/gosdenj/dakboard.git`).

---

## 1. What you need

| | Minimum | Notes |
|---|---|---|
| A machine | Raspberry Pi 3 or better, any old PC, a NAS, a home server | Anything that runs Docker or Python 3.9+ |
| Disk | ~200 MB | Plus room for the album cache |
| RAM | 256 MB | The board is a single Python process |
| Network | A wired LAN connection if you can | Wi-Fi works; a wall tablet on Wi-Fi will drop the photo occasionally |
| Python | 3.9 or newer | Only if you are **not** using Docker |
| Docker + Compose | v2 | Only if you are **not** running it bare |

There is **no database**, **no build step** and **no pip install**. The whole
thing is the Python standard library.

---

## 2. Get the code

### From the ZIP

```bash
unzip dakboard.zip -d ~/dakboard
cd ~/dakboard
```

### From Git

```bash
git clone https://github.com/gosdenj/dakboard.git
cd dakboard
```

You should now have a `dakboard/` folder containing `dakboard/` (the Python
package), `static/`, `tests/`, `Dockerfile` and `docker-compose.yml`.

---

## 3. Try it first, without Docker

The fastest way to find out whether you like it. No containers, no ports to
publish, nothing to clean up.

```bash
cd ~/dakboard
python3 -B -u -m dakboard
```

You should see something like:

```
dakboard listening on http://0.0.0.0:8099
```

Open <http://localhost:8099/> and then <http://localhost:8099/settings>.

Things worth knowing:

- The board reads its config from `$DAKBOARD_DATA/config.json`, defaulting to
  `/data` — which will not be writable by a normal user. For a local run, point
  it somewhere you own:

  ```bash
  DAKBOARD_DATA=~/dakboard-data python3 -B -u -m dakboard
  ```

- `Ctrl-C` stops it. To change the port or the timezone:

  ```bash
  DAKBOARD_DATA=~/dakboard-data DAKBOARD_PORT=9000 DAKBOARD_TZ=Europe/London \
      python3 -B -u -m dakboard
  ```

- The board binds to `0.0.0.0`, so other machines on your network can reach it
  at `http://<your-ip>:8099`.

---

## 4. Run it with Docker

```bash
cd ~/dakboard
mkdir -p ./data
DAKBOARD_DATA=$PWD/data docker compose up -d --build
```

The board is now at <http://localhost:8099/>, the settings page at
<http://localhost:8099/settings>.

```bash
docker compose logs -f dakboard     # watch it
docker compose down                  # stop it
docker compose up -d --build         # rebuild after a code change
```

The config lives in `./data/config.json` on the host and is mounted into the
container at `/data`. **That file is the only thing worth backing up.**

> On a Raspberry Pi, the first `--build` takes a few minutes because it pulls
> a Python base image. After that, rebuilds take seconds.

---

## 5. Configure it

Open <http://localhost:8099/settings> and fill in each section. Every section
has a **test** button that checks the source before you save, so you find out
immediately whether a link works.

### Weather — easiest, do it first

Type a **town name** and press test. Open-Meteo needs no API key. The
gazetteer knows towns and cities, not villages, so "Welwyn Garden City" works
and "Gosden" does not. If your village is missing, use the latitude and
longitude fields instead.

### Google Photos

1. Open the album in Google Photos.
2. **Share → Create link** (or **Share → Link sharing → Anyone with the link**).
   Link sharing **must** be on — a private album will not load.
3. Copy the `photos.app.goo.gl/…` URL into the settings page and press test.

You should get a count back ("92 photos"). If the test fails, check that link
sharing is on and that you copied the whole URL.

> **Worth understanding.** Google removed the Photos Library API's access to
> shared albums on 31 March 2025 — DAKBOARD itself had to abandon the feature
> because of it. The board therefore reads the album's public share page the
> way a browser does, and pages through it with Google's own `batchexecute`
> endpoint. That is unofficial, so the board **caches the last good album list
> on disk**: if a refresh fails, it keeps rotating the photos it already has
> rather than going blank. The calendar and weather do not depend on it at all.
>
> **Known limit:** pagination past ~300 photos has not been exercised against
> a live album. If your album is larger than that, check it early — this is the
> first thing to look at if photos stop appearing part-way through.

### Google Calendar — pick one

**Option A — iCal (five minutes, no setup)**

In Google Calendar: **Settings → Integrate calendar → Secret address in iCal
format**. Paste that URL into the settings page.

One calendar, no colours. Google invalidates the URL if the account password
changes.

**Option B — OAuth (recommended; every calendar, with colours)**

You have to create a Google Cloud project yourself. There is no shortcut.

> **Do this part first, before anything else.** It is the only step that
> needs a browser and your Google account, and the only step where you can
> get properly stuck.

### Step 1 — create a project

1. Open the [Google Cloud Console](https://console.cloud.google.com/).
2. Click the project picker at the top and choose **New project**.
3. Name it something like `family-board`, and pick a location (the default
   is fine — it does not affect where your data lives).
4. **Create**.

> If Google asks you to pick an organisation, choose **None**. The project
> does not need a billing account and does not need to be linked to your
> Workspace organisation.

### Step 2 — enable the Calendar API

1. Make sure your new project is selected in the project picker.
2. Go to **APIs & Services → Library**.
3. Search for **Google Calendar API** and click it.
4. Click **Enable**.

Nothing else is needed. The board never touches Drive, Photos or Gmail.

### Step 3 — configure the consent screen

Google moved this. It is now under **Google Auth platform**, not under
**APIs & Services** (the old link still redirects, so either path works).

1. Go to **Google Auth platform → Branding**.
2. If you see *"Google Auth platform not configured yet"*, click **Get
   started** and fill in:
   - **App name** — anything you like, e.g. `Family Board`. This is what
     appears on the consent screen.
   - **User support email** — your own address.
3. **Next → Audience**. Choose:
   - **Internal** if you have a Google Workspace account and only you will
     use it. This is the tidiest option: no warning screen, no expiry, no
     verification.
   - **External** for a personal Google account. **Keep going.**
4. **Next → Contact information** — your email again.
5. **Next → Finish** → tick **I agree to the Google API Services User Data
   Policy** → **Continue** → **Create**.

### Step 4 — ⚠️ publish the app

This is the step people miss, and it is the one that matters.

1. Go to **Google Auth platform → Audience**.
2. Find **Publishing status**. If it says **Testing**, click **Publish app**
   and confirm.

**Why this matters:** Google issues refresh tokens that **expire after 7
days** to apps whose publishing status is **Testing** and whose user type is
**External**. `calendar.readonly` is not one of the trivial
name/email/profile scopes, so the expiry applies. The board would work
perfectly for a week, then quietly stop updating.

Set to **In production** and the refresh token does not expire.

**You do not need to complete verification.** Verification is Google's
process for public apps that want their name and logo shown on the consent
screen without an "unverified" warning. For a self-hosted family board it
buys you nothing but paperwork.

The one visible consequence of not verifying: because `calendar.readonly` is
a *sensitive* scope, the first sign-in shows a grey
**"Google hasn't verified this app"** screen. Click **Advanced** →
**Go to Family Board (unsafe)**. You will not see it again. That is expected
and harmless — it means "this app is asking for read access to your
calendar", which is exactly what it does.

If you leave the status in **Testing** you must also add yourself under
**Test users**. Test users in Testing mode still get tokens that expire
after 7 days, so that is a workaround for *access*, not for *expiry*.

### Step 5 — create the OAuth client

1. Go to **Google Auth platform → Clients** (or **APIs & Services →
   Credentials**).
2. Click **Create client**.
3. Pick the **application type** — see the table below. This is the part
   that decides how you finish the setup.
4. Name it `Family Board`.
5. **Create**, then copy the **Client ID** and **Client secret**.

### Step 6 — pick the right client type and redirect URI

This is where people get stuck, so it is worth being precise. Google will
**only** redirect to a URI you are allowed to register, and the rules differ
by client type.

| Application type | Redirect URIs it will accept | Use it when |
|---|---|---|
| **Desktop app** | `http://localhost:<any port>` and `http://127.0.0.1:<any port>` — nothing else. The path is ignored. | You will do the sign-in **on the same machine the board runs on** |
| **Web application** | Exact URIs you register. Must be **HTTPS**; **raw IP addresses are not allowed**; a non-standard port must be written out | The board has a real hostname over HTTPS, e.g. `https://board.longie.net` |

So work out where your board will live:

| Your setup | Client type | Redirect URI to register and to type into the settings page | Extra step |
|---|---|---|---|
| Board on a Pi/NAS at `http://<host-ip>:8099`, signing in from your laptop | **Desktop app** | `http://localhost:8099/oauth/callback` | Paste the URL back by hand — see below |
| Board behind Caddy on a real domain | **Web application** | `https://board.longie.net/oauth/callback` | None |
| Board running on the same laptop as your browser | **Desktop app** | `http://localhost:8099/oauth/callback` | None |

**A raw LAN IP cannot work, and the error is unhelpful.** If you put
`http://<host-ip>:8099/oauth/callback` in the redirect URI field, Google
rejects it with:

```
Error 400: invalid_request
```

That is Google's consent page, and there is nothing on it you can click.
Two rules cause it, and they were confirmed against Google's own servers:

- **a raw IP address is never allowed**, whatever the scheme;
- **plain HTTP is not allowed** on any host that is not loopback.

The board now checks the redirect URI before it sends you to Google and
tells you which rule you broke, so you should see a plain sentence instead
of that error page. If you still see it, the redirect URI in the settings
page is not what you think it is — check for a trailing slash or a stray
space.

**Why the raw LAN IP not working is fine.** Google will not let a Web
application client register `http://<host-ip>:8099` — HTTP is not allowed
and raw IP addresses are not allowed. A Desktop app client only accepts
loopback. So neither type can take a bare LAN address.

But that does not matter, because **OAuth only happens once**. Once the board
has stored the refresh token it refreshes silently on its own forever and
never needs a browser again. The one-time consent step can happen anywhere.

**The paste-it-back step.** With a Desktop app client and a board on another
machine, your browser gets sent to
`http://localhost:8099/oauth/callback?code=...` — which is your laptop's own
localhost, where there is no board. You will get a connection error. That is
fine:

1. Copy the whole URL out of the address bar.
2. Go back to the board's settings page.
3. Paste it into **"Paste the address you were sent to"**.
4. Press **Use this code**.

The board extracts the `code` and does the exchange itself.

### Step 7 — fill in the settings page

1. Open the board's **settings** page (`http://<the-board>:8099/settings`).
2. Set **Calendar source** to **Google sign-in (OAuth)**.
3. Paste the **Client ID**.
4. Paste the **Client secret**.
5. Paste the **Redirect URI** — exactly the one from the table above.
6. **Press Save.** The redirect URI has to be saved before you connect,
   because the board reads it from the stored config when it builds the
   consent URL.
7. Press **Connect Google Calendar**. Google's consent page opens in a new
   tab. Approve it (click past the unverified-app notice if you see it).
8. You should get **"Google Calendar connected."**

### Step 8 — choose which calendars to show

Press **List my calendars**. The board fills the **Calendars to show** field
with every calendar on the account. Delete the ones you do not want — the
family calendar, the kids' school calendar, and so on — and leave the rest
comma-separated. Then **Save and test the calendar**; it should report a
count of events.

### Step 9 — verify it actually sticks

The board polls the calendar every **5 minutes**, the weather every **15
minutes** and the album every **30 minutes**, so you do not need to reload
anything — a change you make in Google appears within five minutes.

To check it right now, press **Refresh** on the board, or restart the board.

### How many days the board shows

**Days ahead** on the settings page controls both how far ahead the board
fetches and how many days it displays. At 3 (the default) you get Today,
Tomorrow and the day after. Raise it to 7 and the board adds a group per
day, labelled with the weekday and date.

The board drops whole days and whole events when there is not enough room —
never the type. On a small screen it shows Today first, then Tomorrow, then
the rest as space allows.

Then confirm the thing that matters: **come back in eight days.** A refresh
token about to expire shows up as the board quietly going blank. If events
have stopped appearing after a week, go back to **Google Auth platform →
Audience** and check the publishing status is still **In production**.

The scope requested is `calendar.readonly`. The board never writes to your
calendar.


---

## 6. Get it onto your network

The board listens on port **8099** and binds to `0.0.0.0`, so it is reachable
at `http://<machine-ip>:8099/` from anywhere on the LAN.

Find the machine's IP:

```bash
hostname -I        # Linux
ipconfig getifaddr en0   # macOS
```

If the other devices cannot reach it, the firewall is the likely culprit:

```bash
sudo ufw allow 8099/tcp
```

### A nicer URL

If you run Caddy (or your existing reverse proxy), give it a name rather than
making people remember an IP:

```
board.longie.net {
    reverse_proxy dakboard:8099
}
```

For Caddy running on the same host in Docker, the board container has to join
the `proxy_net` network — the ready-made stack in
`docker-compose.portainer.yml` already does this.

### A hostname on your LAN

Give the machine a static lease in your router, or add a line to
`/etc/hosts` on each display device:

```
192.168.1.6    board.local
```

---

## 7. Deploy with Portainer (optional)

If you already run Portainer, there is a ready-made stack at
`docker-compose.portainer.yml`. Add it in Portainer as a **Git
repository** stack:

| Field | Value |
|---|---|
| Repository URL | `https://github.com/gosdenj/dakboard.git` |
| Branch | `refs/heads/main` |
| Compose path | `docker-compose.portainer.yml` |
| Authentication | none needed — the repository is public |
| GitOps updates | **Polling**, every `5m` |
| Re-pull image | **ON** (Portainer pulls the image the workflow published) |

Two things people get wrong:

- **Polling, not webhooks.** A webhook from GitHub cannot reach a Portainer
  running on your LAN. Polling every five minutes is the convention for this
  reason.
- **The image comes from the registry.** Portainer redeploys a stack without
  rebuilding it, so a new commit only reaches the running container if the
  image has been published. The GitHub workflow does that, and makes an
  empty "publish" commit afterwards so Portainer knows to pull it.

The stack publishes port 8099 on the host and keeps its settings in a
named Docker volume, `dakboard-config`. Nothing has to be set in
Portainer's *Environment Variables* tab unless the host port should be
something other than 8099 — set `DAKBOARD_PORT` there if it should.

**[docs/PORTAINER.md](docs/PORTAINER.md)** has the whole deployment guide:
the fields, the API equivalent, the volume, and how to roll back.

---

## 8. Kiosk mode — the display side

The board is designed to run fullscreen and unattended.

### On any machine with Chromium

```bash
chromium --kiosk --noerrdialogs --disable-infobars --disable-pinch \
         --overscroll-history-navigation=0 http://<host-ip>:8099/
```

- **Wake Lock** keeps the screen awake; it re-acquires when the tab becomes
  visible again.
- No scrollbars and no rubber-banding — the board fills the viewport exactly.
- The mouse pointer hides itself after a few seconds of stillness.
- If the server drops, the board reconnects on its own and falls back to
  polling, so it never shows an error page.

### On a Raspberry Pi

Enable HDMI in `/boot/config.txt`:

```
display_rotate=0
```

Then autostart the browser. Either a desktop autostart entry, or a
systemd unit if you are running headless:

```ini
# /etc/systemd/system/dakboard-kiosk.service
[Unit]
Description=Board kiosk
After=network-online.target docker.service
Wants=network-online.target

[Service]
User=pi
Environment=DISPLAY=:0
ExecStart=/usr/bin/chromium --kiosk --noerrdialogs --disable-infobars \
          --disable-pinch --overscroll-history-navigation=0 \
          http://<host-ip>:8099/
Restart=always
RestartSec=5

[Install]
WantedBy=graphical.target
```

```bash
sudo systemctl enable --now dakboard-kiosk
```

### On a TV

Plug the TV into the machine over HDMI and run the same Chromium command. The
board's layout is fluid rather than breakpoint-driven — the clock and agenda
are sized against the panel they live in, so the same page is correct on a
360 px phone and a 4K TV. Set the TV to **PC mode** (or disable overscan) so
it does not crop the edges.

---

## 9. Backups and upgrades

**Everything worth keeping is in one file:**

```
<data-dir>/config.json
```

That holds your calendar, album link, location and display settings. The
OAuth refresh token and the iCal URL are inside it, so **treat it as a
secret** — anyone with that file can read your calendar.

```bash
cp ~/dakboard-data/config.json ~/config-backup.json
```

To restore, copy it back and restart the board.

Upgrades:

```bash
cd ~/dakboard
git pull
DAKBOARD_DATA=$PWD/data docker compose up -d --build
```

Your `config.json` lives in the mounted data directory, not in the code, so it
survives a rebuild.

---

## 10. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| "Test failed: Failed to fetch" | The board could not write its settings file | The board now says so explicitly. Check the data volume is writable and the disk is not full — see below. |
| "Could not write the settings file" | The data directory is not writable, or the disk is full | In Docker the volume must be writable by the container's user (uid 1000). A volume Docker created as root is not. Also check `df -h`. |
| Board shows the clock but no events | Calendar not configured, or the link expired | Settings → Calendar → test. For iCal, regenerate the secret address. |
| Events stopped after exactly a week | OAuth publishing status is still **Testing** | Google Auth platform → Audience → **Publish app**. |
| `redirect_uri_mismatch` | The redirect URI is not registered on the client | Desktop app = loopback only. Web app = exact HTTPS URI, no raw IPs. See Step 6. |
| `redirect_uri_mismatch` on a LAN IP | Raw IP + HTTP cannot be registered on a Web app client | Use a Desktop app client with `http://localhost:8099/oauth/callback` and paste the URL back. |
| "Set the client id and redirect URI first" | You clicked Connect before saving | **Save** the settings first — the board reads the redirect URI from the stored config. |
| Browser shows a connection error after approving | The board is on another machine and the redirect went to your own localhost | Copy the address bar URL and paste it into **"Paste the address you were sent to"**. |
| "Google hasn't verified this app" | Normal for an unverified External app using a sensitive scope | **Advanced** → **Go to Family Board (unsafe)**. |
| "Google did not return a refresh token" | Google returned a cached consent without a new token | Revoke the app in your Google account (Security → Third-party access), then connect again. |
| `Error 401: invalid_client` | Google does not recognise the client ID or secret | The client ID must end in `.apps.googleusercontent.com`. Check you did not paste the client SECRET into the client ID field, that the two belong to the same project, and that the client has not been deleted. |
| The board shows yesterday's events | The board is in a timezone other than yours | Set `DAKBOARD_TZ` to your timezone and restart. |
| Photos never load | Link sharing is off on the album | Share → Link sharing → Anyone with the link. |
| Photos froze at the last one | The album refresh failed (Google changed the page) | The board is behaving as designed — it keeps the cached list. Check `docker compose logs`. |
| Photos stop part-way through a big album | Pagination past ~300 photos is untested | Report it with your album link; it is the known limit. |
| "List my calendars" says connect first | No refresh token yet | Connect the account, then press it again. |
| "Changes discarded" but you changed something | The page was already saved | Harmless. The button only enables when there is an unsaved edit. |
| Board works on the host but not on the TV | Firewall | `sudo ufw allow 8099/tcp` |
| Board is blank on a 4K TV | Overscan | Set the TV to PC mode / disable overscan. |
| `Address already in use` | Another board is running | `DAKBOARD_PORT=9000` for a second one. |

Check the logs first — they are usually more specific than the browser:

```bash
docker compose logs -f dakboard
```

### If every "test" button says "Failed to fetch"

That message comes from the browser, not from the board: it means the request
never got an answer. It used to happen whenever the board could not write
its settings file — a permissions problem on the data volume, or a full
disk. The board kept serving the config it had loaded at startup, so the
clock, the photos and the weather all carried on working while every save
silently failed.

The board now says what went wrong instead:

```
Could not write the settings file /data/config.json: No space left on device.
Check that /data exists and is writable by the board's user...
```

If you see that, check the two usual causes:

```bash
df -h                      # is the disk full?
docker exec dakboard ls -ld /data && docker exec dakboard touch /data/.wtest
```

A volume that Docker created as **root** cannot be written by the
container's unprivileged user (uid 1000). Fix it on the host:

```bash
docker run --rm -u 0 -v dakboard-config:/data alpine chown -R 1000:1000 /data
```

---

## 11. What to check first, in order

1. The board loads and the clock ticks.
2. The weather shows your town (not a default).
3. The album test returns a photo count.
4. The calendar test returns today's events.
5. Resize the browser window from phone-width to 4K — the layout should
   adapt, never shrink the type.
6. Only then wire it to the wall display.

---

## Appendix — the files that matter

```
dakboard/
  dakboard/
    config.py     the config store, the EDITABLE allowlist, secret masking
    calendar.py   OAuth refresh + Calendar API, and the iCal fallback
    ical.py       a small RFC 5545 parser
    photos.py     the shared-album scraper
    weather.py    the Open-Meteo client
    state.py      the state the server owns
    server.py     HTTP, the SSE stream, the settings API
  static/
    index.html    the board
    settings.html the settings page
  tests/          the test suite and the layout check
  Dockerfile
  docker-compose.yml
  docker-compose.portainer.yml  the Portainer stack
```

Run the tests:

```bash
python3 -B -m unittest discover -s tests
```

Check the layout on 13 real screen sizes across all three layouts (this
starts its own server and needs Playwright):

```bash
python3 -B tests/layout_check.py --shots /tmp/shots
```
