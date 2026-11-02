"""Read a Google Photos SHARED ALBUM without the Photos Library API.

Google removed `photoslibrary.readonly`, `photoslibrary.sharing` and every
`sharedAlbums.*` method on 31 March 2025, so a shared album now returns
403 PERMISSION_DENIED through the official API.  DAKBOARD itself had to
abandon its Google Photos integration because of it.

The way forward is to read the album's PUBLIC WEB PAGE the way a browser
does:

  1. GET https://photos.app.goo.gl/<id>  ->  redirects to
     https://photos.google.com/share/<albumKey>?key=<authKey>
  2. parse the AF_initDataCallback blobs embedded in that page.
     Page shape:  data[1] = items, data[2] = nextPageToken, data[3][1] = title
  3. for further pages, POST to Google's own batchexecute endpoint:
       https://photos.google.com/_/PhotosUi/data/batchexecute
     with rpcids=snAcKc and
       f.req = [[["snAcKc",[albumKey,pageId,null,authKey],null,"generic"]]]
     The reply is XSSI-prefixed JSON; the payload is the wrb.fr/snAcKc entry
     and has the same shape: inner[1] = items, inner[2] = next token.

Image URLs live on https://lh3.googleusercontent.com/ and are long-lived, so
they can be cached and served straight to the browser.

This is unofficial.  If Google changes the page markup the scraper breaks,
so the caller keeps the last good album list on disk and keeps serving it.
"""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept-Language": "en-US,en;q=0.9",
    # skips the EU cookie-consent interstitial
    "Cookie": "SOCS=CAI",
}

BATCH_URL = "https://photos.google.com/_/PhotosUi/data/batchexecute"
RPC_ID = "snAcKc"

# Google's page size for a shared album
PAGE_SIZE = 300
# hard ceiling, matches Google's own album item limit
MAX_ITEMS = 20000

XSSI = ")]}'"

_INIT_RE = re.compile(
    r"AF_initDataCallback\(\{key:\s*'(ds:\d+)'.*?data:(.*?), sideChannel:\s*\{\}\}\);",
    re.DOTALL,
)
# fallback if the structured parse ever fails: pull the items straight out
_FALLBACK_RE = re.compile(
    r'\["(AF1Qip[^"]+)",\["(https://lh3\.googleusercontent\.com/[^"]+)",(\d+),(\d+)'
)


class PhotosError(Exception):
    """Raised when the album cannot be read."""


def _get(url, data=None, timeout=30, headers=None):
    hdrs = dict(HEADERS)
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace"), resp.geturl()


def _parse_items(raw_list):
    """Turn Google's item arrays into plain dicts."""
    out = []
    if not isinstance(raw_list, list):
        return out
    for entry in raw_list:
        if not (isinstance(entry, list) and len(entry) >= 2
                and isinstance(entry[0], str)
                and isinstance(entry[1], list) and entry[1]
                and isinstance(entry[1][0], str)
                and entry[1][0].startswith("https://lh3.googleusercontent.com/")):
            continue
        visual = entry[1]
        width = visual[1] if len(visual) > 1 and isinstance(visual[1], int) else 0
        height = visual[2] if len(visual) > 2 and isinstance(visual[2], int) else 0
        taken = entry[2] if len(entry) > 2 and isinstance(entry[2], int) else None
        out.append({
            "id": entry[0],
            "url": visual[0],
            "w": width,
            "h": height,
            "taken": taken,
        })
    return out


def _parse_page(data):
    """data = [?, items, nextToken, albumInfo]."""
    if not isinstance(data, list):
        return [], None, None
    items = _parse_items(data[1] if len(data) > 1 else None)
    token = data[2] if len(data) > 2 and isinstance(data[2], str) and data[2] else None
    title = None
    if (len(data) > 3 and isinstance(data[3], list) and len(data[3]) > 1
            and isinstance(data[3][1], str)):
        title = data[3][1]
    return items, token, title


def _init_blocks(html):
    blocks = []
    for _key, raw in _INIT_RE.findall(html):
        try:
            blocks.append(json.loads(raw))
        except ValueError:
            continue
    return blocks


def _parse_batch(text):
    """Pull the snAcKc payload out of a batchexecute reply."""
    body = text.lstrip()
    if body.startswith(XSSI):
        body = body[len(XSSI):]
    body = body.lstrip()
    candidates = []
    try:
        candidates.append(json.loads(body))
    except ValueError:
        for line in body.splitlines():
            line = line.strip()
            if line.startswith("["):
                try:
                    candidates.append(json.loads(line))
                except ValueError:
                    continue
    for cand in candidates:
        if not isinstance(cand, list):
            continue
        for entry in cand:
            if (isinstance(entry, list) and len(entry) > 2
                    and entry[1] == RPC_ID and isinstance(entry[2], str)):
                try:
                    return json.loads(entry[2])
                except ValueError:
                    continue
    return None


def _batch(album_key, auth_key, page_id, album_url):
    inner = json.dumps([album_key, page_id, None, auth_key])
    envelope = json.dumps([[[RPC_ID, inner, None, "generic"]]])
    params = urllib.parse.urlencode({
        "rpcids": RPC_ID,
        "source-path": f"/share/{album_key}",
        "hl": "en",
        "rt": "c",
    })
    url = f"{BATCH_URL}?{params}"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        "Accept": "*/*",
        "Origin": "https://photos.google.com",
        "Referer": album_url,
        "X-Same-Domain": "1",
    }
    text, _ = _get(url, data=urllib.parse.urlencode({"f.req": envelope}).encode(),
                   headers=headers)
    return _parse_batch(text)


def _share_context(final_url):
    """Pull the album key and the auth key out of the album URL."""
    m_album = re.search(r"/share/([^/?#]+)", final_url)
    m_key = re.search(r"[?&]key=([^&#]+)", final_url)
    if not m_album or not m_key:
        return None, None
    return m_album.group(1), m_key.group(1)


def normalise_link(link):
    """Accept what a user is likely to paste."""
    link = (link or "").strip()
    if not link:
        return ""
    if link.startswith("photos.app.goo.gl/") or link.startswith("photos.google.com/"):
        link = "https://" + link
    if link.startswith("http://"):
        link = "https://" + link[len("http://"):]
    return link


def fetch_album(link, max_pages=200):
    """Fetch a shared album. Returns a dict:
         {title, items:[{id,url,w,h,taken}], pages, album_key}
    Raises PhotosError if the album cannot be read.
    """
    link = normalise_link(link)
    if not link:
        raise PhotosError("No album link given.")

    try:
        html, final_url = _get(link)
    except urllib.error.HTTPError as exc:
        raise PhotosError(
            f"Album link returned HTTP {exc.code}. Is link sharing still on?"
        ) from exc
    except urllib.error.URLError as exc:
        raise PhotosError(f"Could not reach Google Photos: {exc.reason}") from exc

    album_key, auth_key = _share_context(final_url)
    if not album_key or not auth_key:
        if "consent.google." in final_url:
            raise PhotosError(
                "Google showed a cookie-consent page instead of the album."
            )
        if "/album/" in final_url:
            raise PhotosError(
                "That is a private album URL. In Google Photos use "
                "Share -> Create link and paste the photos.app.goo.gl link."
            )
        raise PhotosError("Could not find the album id in the shared link.")

    seen = set()
    items = []
    title = None
    pages = 0
    token = None

    # page 1 comes embedded in the HTML
    for block in _init_blocks(html):
        block_items, block_token, block_title = _parse_page(block)
        if block_items and len(block_items) > len(items):
            items = block_items
            token = token or block_token
            title = title or block_title

    if not items:
        # structured parse failed - fall back to a plain regex sweep
        for pid, url, _w, _h in _FALLBACK_RE.findall(html):
            items.append({"id": pid, "url": url, "w": 0, "h": 0, "taken": None})
        title = title or _title_from_html(html)

    if not items:
        raise PhotosError(
            "The album page contained no photos. It may be empty, link sharing "
            "may be off, or Google may have changed the page format."
        )

    # deduplicate the embedded page
    unique = []
    for item in items:
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        unique.append(item)
    items = unique
    pages = 1

    # page through the rest
    if token or len(items) >= PAGE_SIZE:
        page_id = token
        while pages < max_pages and len(items) < MAX_ITEMS:
            try:
                data = _batch(album_key, auth_key, page_id, final_url)
            except Exception:
                # keep what we have rather than losing the album
                break
            if data is None:
                break
            page_items, next_token, page_title = _parse_page(data)
            added = 0
            for item in page_items:
                if item["id"] in seen:
                    continue
                seen.add(item["id"])
                items.append(item)
                added += 1
                if len(items) >= MAX_ITEMS:
                    break
            pages += 1
            title = title or page_title
            if added == 0 or not next_token:
                break
            page_id = next_token
            time.sleep(0.15)          # be gentle with Google's servers

    return {
        "title": title or "Shared album",
        "items": items,
        "pages": pages,
        "album_key": album_key,
    }


def _title_from_html(html):
    m = re.search(r'<meta\s+property="og:title"\s+content="([^"]*)"', html)
    if not m:
        return None
    import html as html_mod
    title = html_mod.unescape(m.group(1)).strip()
    title = re.sub(r"\s*-\s*Google Photos\s*$", "", title)
    title = re.sub(r"\s*·\s*[^·]*\d{4}[^·]*$", "", title)
    return title or None


def image_url(item, width=1920, height=1080, crop=True):
    """The URL to hand the browser, sized so it is not a 4K original."""
    suffix = f"=w{width}-h{height}" + ("-c" if crop else "")
    return item["url"] + suffix
