import hashlib
import html
import json
import logging
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger("event_collector")

USER_AGENT = "Mozilla/5.0 (compatible; PuneEventBot/1.0; +https://example.com/bot)"
SEARCH_KEYWORDS = '(hackathon OR meetup OR "conference" OR "workshop" OR "tech talk" OR "webinar" OR "seminar")'
SERPAPI_URL = "https://serpapi.com/search.json"

CITY_LIST = [
    "Pune", "Mumbai", "Bengaluru", "Bangalore", "Delhi", "New Delhi",
    "Chennai", "Hyderabad", "Kolkata", "Ahmedabad", "Noida", "Gurgaon", "Gurugram"
]

FOLDER_MAP = {
    "hackathon": "Hackathons",
    "coding challenge": "Hackathons",
    "webinar": "Webinars",
    "online seminar": "Webinars",
    "conference": "Conferences",
    "summit": "Conferences",
    "workshop": "Workshops",
    "training": "Workshops",
    "meetup": "Meetups",
    "community": "Meetups",
}


@dataclass
class CollectorConfig:
    city: str
    num_results: int
    interval_minutes: int


class CollectorState:
    running = False
    last_run: Optional[str] = None
    last_error: Optional[str] = None
    last_result_count: int = 0


DEFAULT_CONFIG = CollectorConfig(
    city=settings.DEFAULT_CITY,
    num_results=settings.DEFAULT_NUM_RESULTS,
    interval_minutes=settings.DEFAULT_INTERVAL_MINUTES,
)

CURRENT_CONFIG = DEFAULT_CONFIG


def ensure_dirs():
    os.makedirs(settings.DATA_DIR, exist_ok=True)
    os.makedirs(settings.HTML_DIR, exist_ok=True)


def init_db(path=settings.DB_PATH):
    ensure_dirs()
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()
    cur.execute("""
      CREATE TABLE IF NOT EXISTS events (
        id TEXT PRIMARY KEY,
        url TEXT UNIQUE,
        domain TEXT,
        title TEXT,
        description TEXT,
        start_time TEXT,
        fetched_at TEXT,
        serp_source TEXT,
        html_path TEXT,
        notes TEXT
      )
    """)
    cur.execute("""
      CREATE TABLE IF NOT EXISTS folders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL
      )
    """)
    cur.execute("""
      CREATE TABLE IF NOT EXISTS event_folders (
        event_id TEXT,
        folder_id INTEGER,
        PRIMARY KEY(event_id, folder_id),
        FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE,
        FOREIGN KEY(folder_id) REFERENCES folders(id) ON DELETE CASCADE
      )
    """)
    conn.commit()
    ensure_event_columns(conn, ["location_name", "address", "area", "normalized_address", "lat", "lon"])
    ensure_interest_tables(conn)
    return conn


def ensure_event_columns(conn, columns):
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(events)")
    existing = {row[1] for row in cur.fetchall()}
    for col in columns:
        if col not in existing:
            logger.info("Adding missing column to events table: %s", col)
            cur.execute(f"ALTER TABLE events ADD COLUMN {col} TEXT")
    conn.commit()


def ensure_interest_tables(db_conn):
    cur = db_conn.cursor()
    cur.execute("""
      CREATE TABLE IF NOT EXISTS user_event_interests (
        user_id INTEGER NOT NULL,
        user_email TEXT NOT NULL,
        event_title TEXT NOT NULL,
        event_title_norm TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(user_id, event_title_norm)
      )
    """)
    cur.execute("""
      CREATE TABLE IF NOT EXISTS event_interest_notifications (
        user_id INTEGER NOT NULL,
        event_id TEXT NOT NULL,
        notified_at TEXT NOT NULL,
        PRIMARY KEY(user_id, event_id)
      )
    """)
    db_conn.commit()


def _normalize_title(text: str) -> str:
    value = re.sub(r"\s+", " ", (text or "").strip().lower())
    return value[:240]


conn = init_db()

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})


def make_id(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]


def exists_url(db_conn, url: str) -> bool:
    cur = db_conn.cursor()
    cur.execute("SELECT 1 FROM events WHERE url = ? LIMIT 1", (url,))
    return cur.fetchone() is not None


def insert_event(ev: dict):
    cur = conn.cursor()
    cur.execute("""
      INSERT OR IGNORE INTO events
      (id, url, domain, title, description, start_time, fetched_at, serp_source, html_path, notes,
       location_name, address, area, normalized_address, lat, lon)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        ev['id'],
        ev['url'],
        ev.get('domain'),
        ev.get('title'),
        ev.get('description'),
        ev.get('start_time'),
        ev.get('fetched_at'),
        ev.get('serp_source'),
        ev.get('html_path'),
        ev.get('notes'),
        ev.get('location_name'),
        ev.get('address'),
        ev.get('area'),
        ev.get('normalized_address'),
        str(ev.get('lat') or ""),
        str(ev.get('lon') or ""),
    ))
    conn.commit()
    return cur.rowcount > 0


def _send_interest_notifications_for_event(ev: dict):
    title = (ev.get("title") or "").strip()
    title_norm = _normalize_title(title)
    event_id = (ev.get("id") or "").strip()
    if not title_norm or not event_id:
        return

    cur = conn.cursor()
    cur.execute("""
      SELECT user_id, user_email
      FROM user_event_interests
      WHERE event_title_norm = ?
    """, (title_norm,))
    subscribers = cur.fetchall()
    if not subscribers:
        return

    subject = f"New event update: {title}"
    body = (
        f"Hello,\n\n"
        f"A new event was added for a title you marked as 'I'm Interested'.\n\n"
        f"Event: {title}\n"
        f"Link: {ev.get('url') or 'N/A'}\n"
        f"Area/City: {ev.get('area') or 'N/A'}\n"
        f"Description: {ev.get('description') or 'N/A'}\n\n"
        f"Regards,\nEventCart"
    )

    for user_id, user_email in subscribers:
        if not user_email:
            continue
        cur.execute(
            "SELECT 1 FROM event_interest_notifications WHERE user_id = ? AND event_id = ? LIMIT 1",
            (user_id, event_id),
        )
        if cur.fetchone():
            continue
        try:
            send_mail(
                subject=subject,
                message=body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user_email],
                fail_silently=False,
            )
            cur.execute(
                """
                INSERT OR IGNORE INTO event_interest_notifications(user_id, event_id, notified_at)
                VALUES (?, ?, ?)
                """,
                (user_id, event_id, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
        except Exception as exc:
            logger.warning("Failed to send interest notification for event %s to user %s: %s", event_id, user_id, exc)


def safe_json_load(text: str):
    try:
        return json.loads(text)
    except Exception:
        matches = re.findall(r'(\{.*\})', text, flags=re.S)
        for m in matches:
            try:
                return json.loads(m)
            except Exception:
                continue
    return None


def pick_city_from_text(text: str) -> Optional[str]:
    if not text:
        return None
    for city in CITY_LIST:
        if city.lower() in text.lower():
            return city
    return None

# scrape the event location from the HTML content using multiple strategies:
def extract_title_from_html(html_text: str) -> Optional[str]:
    try:
        soup = BeautifulSoup(html_text, "html.parser")
        og = soup.find("meta", property="og:title")
        if og and og.get("content"):
            return og["content"].strip()
        tw = soup.find("meta", property="twitter:title")
        if tw and tw.get("content"):
            return tw["content"].strip()
        t = soup.find("title")
        if t:
            return t.get_text(strip=True)
        h1 = soup.find("h1")
        if h1:
            return h1.get_text(" ", strip=True)
    except Exception:
        pass
    return None


def save_html(url: str, html_text: str) -> str:
    uid = make_id(url)
    filename = uid + ".html"
    path = os.path.join(settings.HTML_DIR, filename)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        f.write(html_text)
    os.replace(path + ".tmp", path)
    return path


def extract_event_location(html_text: str, url: str, snippet: str = None) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    location_name = None
    address = None
    area = None
    try:
        soup = BeautifulSoup(html_text, "html.parser")
        scripts = soup.find_all("script", type="application/ld+json")
        for s in scripts:
            txt = s.string
            if not txt:
                continue
            data = safe_json_load(txt)
            if not data:
                continue
            items = data if isinstance(data, list) else [data]
            expanded = []
            for it in items:
                if isinstance(it, dict) and "@graph" in it:
                    g = it.get("@graph") or []
                    if isinstance(g, list):
                        expanded.extend(g)
                expanded.append(it)
            for it in expanded:
                if not isinstance(it, dict):
                    continue
                typ = it.get("@type") or it.get("type")
                if isinstance(typ, list):
                    is_event = "Event" in typ or any("Event" in t for t in typ if isinstance(t, str))
                else:
                    is_event = isinstance(typ, str) and "Event" in typ
                if not is_event:
                    continue
                loc = it.get("location") or it.get("place")
                if not loc:
                    continue
                if isinstance(loc, dict):
                    if loc.get("name"):
                        location_name = location_name or loc.get("name")
                    addr = loc.get("address") or loc.get("addressLocality") or loc.get("addressRegion")
                    if isinstance(addr, dict):
                        parts = []
                        for k in ("streetAddress", "addressLocality", "addressRegion", "postalCode", "addressCountry"):
                            v = addr.get(k)
                            if v:
                                parts.append(str(v).strip())
                        if parts:
                            address = ", ".join(parts)
                    elif isinstance(addr, str):
                        address = address or addr.strip()
                    if not address:
                        parts = []
                        for k in ("streetAddress", "addressLocality", "addressRegion", "postalCode", "addressCountry"):
                            v = loc.get(k)
                            if v:
                                parts.append(str(v).strip())
                        if parts:
                            address = ", ".join(parts)
                elif isinstance(loc, str):
                    if not location_name:
                        location_name = loc.strip()
                    if "," in loc and not address:
                        address = loc.strip()
                if not area:
                    area = pick_city_from_text(" ".join(filter(None, [location_name, address, snippet or ""])))
                if location_name or address:
                    return (location_name, address, area)
        addr_nodes = soup.select('[itemprop*=address], address')
        for node in addr_nodes:
            text = node.get_text(" ", strip=True)
            if text:
                text = html.unescape(text)
                if len(text) > (len(address or "")):
                    address = text
                parent = node.parent
                if parent:
                    name_node = parent.find(attrs={"itemprop": "name"})
                    if name_node and not location_name:
                        location_name = name_node.get_text(" ", strip=True)
                if not area:
                    area = pick_city_from_text(text)
        if address:
            return (location_name, address, area)
        addr_tag = soup.find("address")
        if addr_tag:
            text = addr_tag.get_text(" ", strip=True)
            if text:
                address = text
                if not area:
                    area = pick_city_from_text(text)
                return (location_name, address, area)
        hint_keywords = ["address", "venue", "location", "place", "map", "directions", "where", "addr"]
        candidates = []
        for kw in hint_keywords:
            nodes = soup.find_all(lambda tag: (
                (tag.has_attr("id") and kw in tag.get("id").lower()) or
                (tag.has_attr("class") and any(kw in c.lower() for c in tag.get("class")))
            ))
            for n in nodes:
                text = n.get_text(" ", strip=True)
                if text:
                    text = html.unescape(text)
                    if len(text) > 10:
                        candidates.append(text)
        if candidates:
            candidates = sorted(set(candidates), key=lambda s: -len(s))
            address = candidates[0]
            if not area:
                area = pick_city_from_text(address)
            if not location_name:
                first_line = address.split("\n")[0].split(",")[0].strip()
                if first_line and len(first_line) < 120:
                    location_name = first_line
            return (location_name, address, area)
        text = soup.get_text("\n", strip=True)
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        paragraphs = []
        i = 0
        while i < len(lines):
            para = lines[i]
            j = i + 1
            while j < len(lines) and j < i + 4:
                para = para + ", " + lines[j]
                j += 1
            paragraphs.append(para)
            i += 1
        pin_re = re.compile(r'\b[1-9][0-9]{5}\b')
        street_words = ["road","rd","street","st","lane","ln","market","building","bldg","floor","fl","venue","hall","center","centre","complex","plot","sector","block"]
        best = None
        for p in paragraphs:
            if pin_re.search(p) or any(sw in p.lower() for sw in street_words):
                score = (5 if pin_re.search(p) else 0) + p.count(",")
                if not best or score > best[0]:
                    best = (score, p)
        if best:
            address = best[1].strip()
            if not area:
                area = pick_city_from_text(address)
            if not location_name:
                first = address.split(",")[0].strip()
                if len(first) < 120:
                    location_name = first
            return (location_name, address, area)
        if snippet and not address:
            if "," in snippet or pin_re.search(snippet):
                address = snippet
                area = area or pick_city_from_text(snippet)
                return (location_name, address, area)
        return (location_name, address, area)
    except Exception as ex:
        logger.debug("extract_event_location error: %s", ex)
        return (location_name, address, area)


def fetch_eventbrite_venue(event_url: str) -> Tuple[Optional[str], Optional[str]]:
    if not settings.EVENTBRITE_TOKEN:
        return None, None
    m = re.search(r'-([0-9]{8,})($|[/?])', event_url) or re.search(r'/e/([0-9]{8,})($|[/?])', event_url)
    if not m:
        return None, None
    event_id = m.group(1)
    api_url = f"https://www.eventbriteapi.com/v3/events/{event_id}/?expand=venue"
    headers = {"Authorization": f"Bearer {settings.EVENTBRITE_TOKEN}"}
    try:
        resp = requests.get(api_url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        venue = data.get("venue") or {}
        venue_name = venue.get("name")
        addr = venue.get("address") or {}
        parts = []
        for k in ("address_1", "address_2", "city", "region", "postal_code", "country"):
            v = addr.get(k)
            if v:
                parts.append(str(v).strip())
        formatted = ", ".join(parts) if parts else None
        return venue_name, formatted
    except Exception as e:
        logger.debug("Eventbrite API error for %s: %s", event_url, e)
        return None, None


def fetch_meetup_venue(event_url: str) -> Tuple[Optional[str], Optional[str]]:
    if not settings.MEETUP_TOKEN:
        return None, None
    m = re.search(r'/events/([0-9]{6,})', event_url)
    if not m:
        return None, None
    event_id = m.group(1)
    api_url = f"https://api.meetup.com/2/events/{event_id}?fields=venue"
    headers = {"Authorization": f"Bearer {settings.MEETUP_TOKEN}"}
    try:
        resp = requests.get(api_url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        venue = data.get("venue") or {}
        venue_name = venue.get("name")
        addr_parts = []
        for k in ("address_1", "address_2", "city", "state", "zip", "country"):
            v = venue.get(k)
            if v:
                addr_parts.append(str(v).strip())
        formatted = ", ".join(addr_parts) if addr_parts else None
        return venue_name, formatted
    except Exception as e:
        logger.debug("Meetup API error for %s: %s", event_url, e)
        return None, None


def geocode_with_opencage(address: str) -> Tuple[Optional[str], Optional[float], Optional[float]]:
    if not settings.OPENCAGE_KEY or not address:
        return None, None, None
    try:
        params = {"q": address, "key": settings.OPENCAGE_KEY, "no_annotations": 1, "limit": 1}
        r = requests.get("https://api.opencagedata.com/geocode/v1/json", params=params, timeout=8)
        r.raise_for_status()
        js = r.json()
        results = js.get("results") or []
        if not results:
            return None, None, None
        top = results[0]
        formatted = top.get("formatted")
        geometry = top.get("geometry") or {}
        lat = geometry.get("lat")
        lng = geometry.get("lng")
        return formatted, lat, lng
    except Exception as e:
        logger.debug("OpenCage geocode error for %s: %s", address, e)
        return None, None, None


def geocode_with_nominatim(address: str) -> Tuple[Optional[str], Optional[float], Optional[float]]:
    if not address:
        return None, None, None
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={
                "q": address,
                "format": "jsonv2",
                "limit": 1,
                "addressdetails": 1,
            },
            headers={"User-Agent": "EventAtlas/1.0"},
            timeout=8,
        )
        resp.raise_for_status()
        rows = resp.json() or []
        if not rows:
            return None, None, None
        top = rows[0]
        lat = _to_float(top.get("lat"))
        lon = _to_float(top.get("lon"))
        if lat is None or lon is None:
            return None, None, None
        formatted = top.get("display_name") or address
        return formatted, lat, lon
    except Exception as e:
        logger.debug("Nominatim geocode error for %s: %s", address, e)
        return None, None, None


def _to_float(value):
    try:
        if value is None:
            return None
        value = str(value).strip()
        if not value:
            return None
        return float(value)
    except Exception:
        return None


def _clean_geocode_query(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    cleaned = html.unescape(str(text))
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.-")
    cleaned = re.sub(r"^(address|venue|location)\s*[:\-]\s*", "", cleaned, flags=re.I)

    at_match = re.search(r"\bat\s+(.+)", cleaned, flags=re.I)
    if at_match:
        cleaned = at_match.group(1).strip()

    cleaned = re.sub(
        r"\bon\s+\d{1,2}(?:st|nd|rd|th)?(?:[,\s/-]+\d{1,2})?(?:[,\s/-]+[A-Za-z]+)?(?:[,\s/-]+\d{2,4})?.*$",
        "",
        cleaned,
        flags=re.I,
    ).strip(" ,.-")

    for city in CITY_LIST:
        idx = cleaned.lower().find(city.lower())
        if idx == -1:
            continue
        end = idx + len(city)
        tail = cleaned[end:]
        if re.match(r"\s*(on|with|for)\b", tail, flags=re.I):
            cleaned = cleaned[:end].strip(" ,.-")
        break

    if not cleaned:
        return None
    return cleaned


def _build_geocode_queries(location_name: Optional[str], address: Optional[str], normalized_address: Optional[str]) -> list[str]:
    queries = []
    for raw in (normalized_address, address, location_name):
        if not raw:
            continue
        base = str(raw).strip()
        if base:
            queries.append(base)
        cleaned = _clean_geocode_query(base)
        if cleaned:
            queries.append(cleaned)
    deduped = []
    seen = set()
    for q in queries:
        key = q.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(q)
    return deduped


def resolve_event_coordinates(event_id: str, location_name: Optional[str], address: Optional[str], normalized_address: Optional[str], lat_value, lon_value) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    lat = _to_float(lat_value)
    lon = _to_float(lon_value)
    if lat is not None and lon is not None:
        return lat, lon, normalized_address

    queries = _build_geocode_queries(location_name, address, normalized_address)
    if not queries:
        return None, None, normalized_address

    formatted = None
    geocoded_lat = None
    geocoded_lon = None
    for query in queries:
        formatted, geocoded_lat, geocoded_lon = geocode_with_opencage(query)
        if geocoded_lat is not None and geocoded_lon is not None:
            break
        formatted, geocoded_lat, geocoded_lon = geocode_with_nominatim(query)
        if geocoded_lat is not None and geocoded_lon is not None:
            break

    if geocoded_lat is None or geocoded_lon is None:
        return None, None, normalized_address

    cur = conn.cursor()
    new_normalized = normalized_address or formatted or queries[0]
    cur.execute(
        """
        UPDATE events
        SET lat = ?, lon = ?, normalized_address = ?
        WHERE id = ?
        """,
        (str(geocoded_lat), str(geocoded_lon), new_normalized, event_id),
    )
    conn.commit()
    return float(geocoded_lat), float(geocoded_lon), new_normalized

def classify(text: str):
    text = (text or "").lower()
    matched = set()
    for kw, folder in FOLDER_MAP.items():
        if kw in text:
            matched.add(folder)
    return list(matched) if matched else ["Other"]


def serpapi_search(query: str, num: int = 10):
    if not settings.SERPAPI_KEY:
        logger.warning("SERPAPI_KEY not set. Skipping SerpApi query.")
        return {}
    params = {"engine": "google", "q": query, "num": num, "api_key": settings.SERPAPI_KEY}
    r = session.get(SERPAPI_URL, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def collect_links_from_serp(serp_json) -> list:
    if not serp_json:
        return []
    links = []
    for bucket in ("organic_results", "top_stories", "news_results", "inline_results", "related_searches"):
        for it in (serp_json.get(bucket) or []):
            link = it.get("link") or it.get("url") or it.get("source")
            snippet = it.get("snippet") or it.get("description") or it.get("title")
            if link:
                links.append((link, snippet, bucket))
    seen = set()
    out = []
    for link, snippet, src in links:
        if link in seen:
            continue
        seen.add(link)
        out.append({"link": link, "snippet": snippet, "src": src})
    return out


def set_config(new_config: CollectorConfig):
    global CURRENT_CONFIG
    CURRENT_CONFIG = new_config


def collector_job():
    CollectorState.running = True
    CollectorState.last_error = None
    collected = 0
    try:
        cfg = CURRENT_CONFIG
        q = f"events in {cfg.city} {SEARCH_KEYWORDS}"
        logger.info("Running SerpApi search: %s", q)
        serp = serpapi_search(q, num=cfg.num_results) if settings.SERPAPI_KEY else {}
        links = collect_links_from_serp(serp) if serp else []
        for item in links:
            url = item["link"]
            if not url.lower().startswith("http"):
                continue
            if exists_url(conn, url):
                logger.debug("Already exists: %s", url)
                continue
            if url.lower().endswith((".pdf", ".jpg", ".jpeg", ".png", ".gif")):
                continue
            try:
                r = session.get(url, timeout=15)
                r.raise_for_status()
            except Exception as e:
                logger.warning("Failed to fetch %s: %s", url, e)
                continue
            html_text = r.text
            title = extract_title_from_html(html_text) or item.get("snippet") or ""
            uid = make_id(url)
            html_path = save_html(url, html_text)

            location_name, address, area = extract_event_location(html_text, url, snippet=item.get("snippet"))

            domain = urlparse(url).netloc.lower()
            if (not address or ("virtual" in (address or "").lower() and "virtual" not in (location_name or "").lower())):
                if "eventbrite.com" in domain and settings.EVENTBRITE_TOKEN:
                    vb_name, vb_address = fetch_eventbrite_venue(url)
                    if vb_address:
                        location_name = location_name or vb_name
                        address = vb_address
                        area = area or pick_city_from_text(vb_address)
                if (not address) and "meetup.com" in domain and settings.MEETUP_TOKEN:
                    mu_name, mu_address = fetch_meetup_venue(url)
                    if mu_address:
                        location_name = location_name or mu_name
                        address = mu_address
                        area = area or pick_city_from_text(mu_address)

            normalized_address = None
            lat = None
            lon = None
            if address and settings.OPENCAGE_KEY:
                try:
                    formatted, latv, lonv = geocode_with_opencage(address)
                    if formatted:
                        normalized_address = formatted
                        lat = latv
                        lon = lonv
                        area = area or pick_city_from_text(formatted)
                except Exception as e:
                    logger.debug("OpenCage error: %s", e)

            area = area or pick_city_from_text(" ".join(filter(None, [title, item.get("snippet") or "", address or ""])))

            ev = {
                "id": uid,
                "url": url,
                "domain": domain,
                "title": title,
                "description": item.get("snippet"),
                "start_time": None,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "serp_source": item.get("src"),
                "html_path": html_path,
                "notes": None,
                "location_name": location_name,
                "address": address,
                "area": area,
                "normalized_address": normalized_address,
                "lat": lat,
                "lon": lon
            }
            inserted = insert_event(ev)
            if not inserted:
                continue
            _send_interest_notifications_for_event(ev)
            collected += 1
            folders = classify(" ".join(filter(None, [title, ev['description']])))
            cur = conn.cursor()
            for fname in folders:
                cur.execute("INSERT OR IGNORE INTO folders(name) VALUES (?)", (fname,))
                conn.commit()
                cur.execute("SELECT id FROM folders WHERE name = ?", (fname,))
                fid_row = cur.fetchone()
                if fid_row:
                    fid = fid_row[0]
                    cur.execute("INSERT OR IGNORE INTO event_folders(event_id, folder_id) VALUES (?, ?)", (uid, fid))
                    conn.commit()
            logger.info("Saved event %s (%s) area=%s address=%s", uid, title, area, address)
        CollectorState.last_result_count = collected
    except Exception as e:
        CollectorState.last_error = str(e)
        logger.exception("Collector job error: %s", e)
    finally:
        CollectorState.running = False
        CollectorState.last_run = datetime.now(timezone.utc).isoformat()

