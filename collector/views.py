from functools import wraps
import math
import os
import requests
import re
from datetime import datetime, timezone

from django.conf import settings
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.contrib.auth.models import User
from django.contrib import messages
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme

from .collector_logic import (
    CollectorConfig,
    CollectorState,
    conn,
    CURRENT_CONFIG,
    resolve_event_coordinates,
    pick_city_from_text,
)
from .scheduler import start_or_update_scheduler, is_scheduler_running, stop_scheduler

SESSION_KEY = "eventbot_admin_authenticated"

def admin_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.session.get(SESSION_KEY):
            return redirect("admin_login")
        return view_func(request, *args, **kwargs)
    return wrapper


def user_or_admin_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if request.user.is_authenticated or request.session.get(SESSION_KEY):
            return view_func(request, *args, **kwargs)
        return redirect("user_login")
    return wrapper


def _ensure_preference_table():
    cur = conn.cursor()
    cur.execute("""
      CREATE TABLE IF NOT EXISTS user_preferences (
        user_id INTEGER NOT NULL,
        preference_type TEXT NOT NULL,
        preference_value TEXT NOT NULL,
        score INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL,
        PRIMARY KEY(user_id, preference_type, preference_value)
      )
    """)
    conn.commit()


def _record_preference(user_id: int, pref_type: str, pref_value: str, weight: int = 1):
    if not pref_value:
        return
    value = str(pref_value).strip()
    if not value:
        return
    cur = conn.cursor()
    cur.execute("""
      INSERT INTO user_preferences(user_id, preference_type, preference_value, score, updated_at)
      VALUES (?, ?, ?, ?, ?)
      ON CONFLICT(user_id, preference_type, preference_value)
      DO UPDATE SET
        score = user_preferences.score + excluded.score,
        updated_at = excluded.updated_at
    """, (user_id, pref_type, value[:120], max(1, weight), datetime.now(timezone.utc).isoformat()))
    conn.commit()


def _top_preferences(user_id: int, pref_type: str, limit: int = 5):
    cur = conn.cursor()
    cur.execute("""
      SELECT preference_value, score
      FROM user_preferences
      WHERE user_id = ? AND preference_type = ?
      ORDER BY score DESC, updated_at DESC
      LIMIT ?
    """, (user_id, pref_type, limit))
    return cur.fetchall()


def _normalize_title(text: str) -> str:
    value = re.sub(r"\s+", " ", (text or "").strip().lower())
    return value[:240]


def _ensure_interest_tables():
    cur = conn.cursor()
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
    conn.commit()


def _subscribe_user_interest(user: User, event_title: str):
    cleaned_title = (event_title or "").strip()
    normalized = _normalize_title(cleaned_title)
    if not cleaned_title or not normalized:
        return False
    cur = conn.cursor()
    cur.execute("""
      INSERT INTO user_event_interests(user_id, user_email, event_title, event_title_norm, created_at)
      VALUES (?, ?, ?, ?, ?)
      ON CONFLICT(user_id, event_title_norm)
      DO UPDATE SET
        user_email = excluded.user_email,
        event_title = excluded.event_title,
        created_at = excluded.created_at
    """, (
        user.id,
        (user.email or "").strip(),
        cleaned_title[:240],
        normalized,
        datetime.now(timezone.utc).isoformat(),
    ))
    conn.commit()
    return True

#model import 

reco_model = "recommendation_model//event_recommender.pkl"


def _recommend_events_for_user(user_id: int, limit: int = 8):
    reco_model = None
    folder_prefs = _top_preferences(user_id, "folder", limit=3)
    area_prefs = _top_preferences(user_id, "area", limit=3)
    keyword_prefs = _top_preferences(user_id, "keyword", limit=3)
    if not folder_prefs and not area_prefs and not keyword_prefs:
        return []

    folder_weights = {int(v): s for v, s in folder_prefs if str(v).isdigit()}
    area_weights = {str(v): s for v, s in area_prefs}
    keyword_values = [str(v) for v, _ in keyword_prefs]

    cur = conn.cursor()
    cur.execute("""
      SELECT
        e.id, e.title, e.url, e.description, e.fetched_at, e.html_path, e.location_name, e.address, e.area,
        GROUP_CONCAT(ef.folder_id) as folder_ids
      FROM events e
      LEFT JOIN event_folders ef ON e.id = ef.event_id
      GROUP BY e.id
      ORDER BY e.fetched_at DESC
      LIMIT 300
    """)

    ranked = []
    for row in cur.fetchall():
        event_id, title, url, description, fetched_at, html_path, location_name, address, area, folder_ids = row
        score = 0
        folder_id_list = []
        if folder_ids:
            folder_id_list = [int(x) for x in str(folder_ids).split(",") if x.isdigit()]
        for fid in folder_id_list:
            if fid in folder_weights:
                score += folder_weights[fid] * 3
        if area and area in area_weights:
            score += area_weights[area] * 2
        blob = f"{title or ''} {description or ''}".lower()
        for kw in keyword_values:
            if kw.lower() in blob:
                score += 2
        if score > 0:
            ranked.append({
                "id": event_id,
                "title": title,
                "url": url,
                "description": description,
                "fetched_at": fetched_at,
                "html_file": os.path.basename(html_path) if html_path else None,
                "location_name": location_name,
                "address": address,
                "area": area,
                "score": score,
            })
    ranked.sort(key=lambda x: (x["score"], x["fetched_at"] or ""), reverse=True)
    return ranked[:limit]


def _extract_keywords(text: str, max_words: int = 4):
    words = re.findall(r"[A-Za-z]{4,}", (text or "").lower())
    stop_words = {
        "this", "that", "with", "from", "your", "about", "event",
        "events", "meetup", "conference", "workshop", "online", "city"
    }
    filtered = []
    for w in words:
        if w in stop_words:
            continue
        if w not in filtered:
            filtered.append(w)
        if len(filtered) >= max_words:
            break
    return filtered
#  above it recommendation logic . 

def admin_login_view(request):
    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        password = (request.POST.get("password") or "").strip()
        if username == settings.ADMIN_USERNAME and password == settings.ADMIN_PASSWORD:
            request.session[SESSION_KEY] = True
            next_url = request.POST.get("next") or request.GET.get("next") or "dashboard"
            if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
                next_url = "dashboard"
            return redirect(next_url)
        messages.error(request, "Invalid username or password.")
    return render(request, "admin_login.html")


@admin_required
def admin_logout_view(request):
    request.session.pop(SESSION_KEY, None)
    return redirect("admin_login")


def public_home(request):
    return render(request, "home.html")


def user_register(request):
    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        email = (request.POST.get("email") or "").strip()
        password = (request.POST.get("password") or "").strip()
        if not username or not password:
            messages.error(request, "Username and password are required.")
            return render(request, "register.html")
        if User.objects.filter(username=username).exists():
            messages.error(request, "Username already exists.")
            return render(request, "register.html")
        user = User.objects.create_user(username=username, email=email, password=password)
        auth_login(request, user)
        return redirect("folders")
    return render(request, "register.html")


def user_login(request):
    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        password = (request.POST.get("password") or "").strip()
        user = authenticate(request, username=username, password=password)
        if user is not None:
            auth_login(request, user)
            next_url = request.POST.get("next") or request.GET.get("next") or "folders"
            if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
                next_url = "folders"
            return redirect(next_url)
        messages.error(request, "Invalid username or password.")
    return render(request, "user_login.html")


def user_logout(request):
    auth_logout(request)
    return redirect("home")


def _parse_int(value, default, min_value=1, max_value=10000):
    try:
        parsed = int(value)
        if parsed < min_value:
            return default
        if parsed > max_value:
            return max_value
        return parsed
    except Exception:
        return default


def _parse_float(value, default):
    try:
        return float(value)
    except Exception:
        return default

# formula for calculating distance between two lat/lon points on Earth using Haversine formula
def _haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(a))
def _reverse_geocode_city(lat: float, lon: float):
    try:
        if settings.OPENCAGE_KEY:
            params = {
                "q": f"{lat},{lon}",
                "key": settings.OPENCAGE_KEY,
                "no_annotations": 1,
                "limit": 1,
            }
            resp = requests.get("https://api.opencagedata.com/geocode/v1/json", params=params, timeout=8)
            resp.raise_for_status()
            js = resp.json()
            results = js.get("results") or []
            if not results:
                return None
            top = results[0]
            comps = top.get("components") or {}
            city_candidate = comps.get("city") or comps.get("town") or comps.get("village") or comps.get("state")
            if city_candidate:
                picked = pick_city_from_text(str(city_candidate))
                if picked:
                    return picked
            return pick_city_from_text(top.get("formatted") or "")

        resp = requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={
                "lat": lat,
                "lon": lon,
                "format": "jsonv2",
                "addressdetails": 1,
                "zoom": 10,
            },
            headers={"User-Agent": "EventAtlas/1.0"},
            timeout=8,
        )
        resp.raise_for_status()
        js = resp.json()
        addr = js.get("address") or {}
        city_candidate = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("state")
        if city_candidate:
            picked = pick_city_from_text(str(city_candidate))
            if picked:
                return picked
        return pick_city_from_text(js.get("display_name") or "")
    except Exception:
        return None


def _event_matches_city(user_city: str, location_name: str, address: str, area: str, normalized_address: str):
    if not user_city:
        return False
    blob = " ".join(filter(None, [location_name or "", address or "", area or "", normalized_address or ""]))
    if not blob:
        return False
    if user_city.lower() in blob.lower():
        return True
    detected = pick_city_from_text(blob)
    return bool(detected and detected.lower() == user_city.lower())
@admin_required
def dashboard(request):
    city = CURRENT_CONFIG.city
    num_results = CURRENT_CONFIG.num_results
    interval_minutes = CURRENT_CONFIG.interval_minutes

    if request.method == "POST":
        action = (request.POST.get("action") or "start").strip().lower()
        if action == "stop":
            stop_scheduler()
            messages.info(request, "Collector stopped.")
        else:
            city = (request.POST.get("city") or settings.DEFAULT_CITY).strip()
            num_results = _parse_int(request.POST.get("num_results"), settings.DEFAULT_NUM_RESULTS, min_value=1, max_value=100)
            interval_minutes = _parse_int(request.POST.get("interval_minutes"), settings.DEFAULT_INTERVAL_MINUTES, min_value=1, max_value=24 * 60)
            config = CollectorConfig(city=city, num_results=num_results, interval_minutes=interval_minutes)
            start_or_update_scheduler(config)
            messages.success(request, "Collector started with the provided settings.")

    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM events")
    total_events = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM folders")
    total_folders = cur.fetchone()[0]

    context = {
        "city": city,
        "num_results": num_results,
        "interval_minutes": interval_minutes,
        "scheduler_running": is_scheduler_running(),
        "last_run": CollectorState.last_run,
        "last_error": CollectorState.last_error,
        "last_result_count": CollectorState.last_result_count,
        "total_events": total_events,
        "total_folders": total_folders,
    }
    return render(request, "index.html", context)


@user_or_admin_required
def list_folders_page(request):
    _ensure_preference_table()
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM folders ORDER BY name")
    rows = cur.fetchall()
    folders = [{"id": r[0], "name": r[1]} for r in rows]
    recommendations = []
    has_preferences = False
    if request.user.is_authenticated:
        recommendations = _recommend_events_for_user(request.user.id, limit=8)
        has_preferences = (
            len(_top_preferences(request.user.id, "folder", 1)) > 0
            or len(_top_preferences(request.user.id, "area", 1)) > 0
            or len(_top_preferences(request.user.id, "keyword", 1)) > 0
        )
    return render(request, "folders.html", {
        "folders": folders,
        "recommendations": recommendations,
        "has_preferences": has_preferences,
    })


@user_or_admin_required
def search_events_page(request):
    _ensure_preference_table()
    q = (request.GET.get("q") or "").strip()
    events = []
    if q:
        if request.user.is_authenticated:
            _record_preference(request.user.id, "keyword", q[:50], weight=2)
        cur = conn.cursor()
        pattern = f"%{q}%"
        cur.execute("""
          SELECT id, title, url, description, fetched_at, html_path, location_name, address, area
          FROM events
          WHERE title LIKE ? OR description LIKE ? OR area LIKE ? OR address LIKE ?
          ORDER BY fetched_at DESC
          LIMIT 120
        """, (pattern, pattern, pattern, pattern))
        for r in cur.fetchall():
            events.append({
                "id": r[0],
                "title": r[1],
                "url": r[2],
                "description": r[3],
                "fetched_at": r[4],
                "html_file": os.path.basename(r[5]) if r[5] else None,
                "location_name": r[6],
                "address": r[7],
                "area": r[8],
            })
    return render(request, "search.html", {"query": q, "events": events})


@user_or_admin_required
def folder_events_page(request, fid: int):
    _ensure_preference_table()
    if request.user.is_authenticated:
        _record_preference(request.user.id, "folder", str(fid), weight=2)
    area = request.GET.get("area")
    cur = conn.cursor()
    cur.execute("SELECT name FROM folders WHERE id = ?", (fid,))
    row = cur.fetchone()
    if not row:
        raise Http404("Folder not found")
    folder_name = row[0]
    if area:
        cur.execute("""
          SELECT e.id, e.title, e.url, e.description, e.fetched_at, e.html_path, e.location_name, e.address, e.area, e.normalized_address, e.lat, e.lon
          FROM events e
          JOIN event_folders ef ON e.id = ef.event_id
          WHERE ef.folder_id = ? AND (e.area = ? OR e.area LIKE ?)
          ORDER BY e.fetched_at DESC
        """, (fid, area, f"%{area}%"))
    else:
        cur.execute("""
          SELECT e.id, e.title, e.url, e.description, e.fetched_at, e.html_path, e.location_name, e.address, e.area, e.normalized_address, e.lat, e.lon
          FROM events e
          JOIN event_folders ef ON e.id = ef.event_id
          WHERE ef.folder_id = ?
          ORDER BY e.fetched_at DESC
        """, (fid,))
    events = []
    for r in cur.fetchall():
        events.append({
            "id": r[0],
            "title": r[1],
            "url": r[2],
            "description": r[3],
            "fetched_at": r[4],
            "html_path": r[5],
            "location_name": r[6],
            "address": r[7],
            "area": r[8],
            "normalized_address": r[9],
            "lat": r[10],
            "lon": r[11],
            "html_file": os.path.basename(r[5]) if r[5] else None
        })
    cur.execute("SELECT DISTINCT area FROM events WHERE area IS NOT NULL ORDER BY area")
    areas = [row[0] for row in cur.fetchall() if row[0]]
    return render(request, "events.html", {
        "events": events,
        "folder_name": folder_name,
        "folder_id": fid,
        "areas": areas,
        "selected_area": area,
    })


@user_or_admin_required
def event_page(request, eid: str):
    _ensure_preference_table()
    cur = conn.cursor()
    cur.execute("SELECT id, title, url, description, fetched_at, html_path, location_name, address, area, normalized_address, lat, lon FROM events WHERE id = ?", (eid,))
    r = cur.fetchone()
    if not r:
        raise Http404("Event not found")
    event = {
        "id": r[0],
        "title": r[1],
        "url": r[2],
        "description": r[3],
        "fetched_at": r[4],
        "html_path": r[5],
        "location_name": r[6],
        "address": r[7],
        "area": r[8],
        "normalized_address": r[9],
        "lat": r[10],
        "lon": r[11],
        "html_file": os.path.basename(r[5]) if r[5] else None
    }
    folder_id = request.GET.get("fid")
    if request.user.is_authenticated and folder_id and str(folder_id).isdigit():
        _record_preference(request.user.id, "folder", str(folder_id), weight=3)
    if request.user.is_authenticated and event.get("area"):
        _record_preference(request.user.id, "area", event["area"], weight=2)
    if request.user.is_authenticated:
        for kw in _extract_keywords(f"{event.get('title') or ''} {event.get('description') or ''}", max_words=4):
            _record_preference(request.user.id, "keyword", kw, weight=1)
    return render(request, "event.html", {"event": event})


@user_or_admin_required
def api_mark_event_interest(request, eid: str):
    if request.method != "POST":
        return JsonResponse({"ok": False, "message": "POST method required."}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({"ok": False, "message": "Please login as a user first."}, status=401)
    email = (request.user.email or "").strip()
    if not email:
        return JsonResponse({"ok": False, "message": "Please add email in your profile to receive notifications."}, status=400)

    _ensure_interest_tables()
    cur = conn.cursor()
    cur.execute("SELECT title FROM events WHERE id = ?", (eid,))
    row = cur.fetchone()
    if not row:
        return JsonResponse({"ok": False, "message": "Event not found."}, status=404)

    event_title = row[0] or ""
    saved = _subscribe_user_interest(request.user, event_title)
    if not saved:
        return JsonResponse({"ok": False, "message": "Unable to save interest for this event."}, status=400)

    return JsonResponse({
        "ok": True,
        "message": "You will get notification related to this event.",
        "event_title": event_title,
    })


@user_or_admin_required
def serve_html_page(request, filename: str):
    file_path = os.path.join(settings.HTML_DIR, filename)
    if not os.path.exists(file_path):
        raise Http404("HTML file not found")
    return FileResponse(open(file_path, "rb"), content_type="text/html")


def api_health(request):
    return JsonResponse({
        "status": "ok",
        "service": "eventcart",
        "version": "v1",
    })


@user_or_admin_required
def api_nearby_selected_events(request):
    event_ids = request.GET.getlist("event_ids")
    if not event_ids:
        return JsonResponse({"results": [], "count": 0})

    lat = _parse_float(request.GET.get("lat"), None)
    lon = _parse_float(request.GET.get("lon"), None)
    radius_raw = (request.GET.get("radius_km") or "").strip().lower()
    radius_km = None if radius_raw == "all" else _parse_float(radius_raw or request.GET.get("radius_km"), 10.0)
    if lat is None or lon is None:
        return JsonResponse({"error": "lat and lon are required"}, status=400)
    if radius_km is not None and radius_km <= 0:
        return JsonResponse({"error": "radius_km must be positive"}, status=400)

    placeholders = ",".join("?" for _ in event_ids)
    cur = conn.cursor()
    cur.execute(
        f"""
          SELECT id, title, location_name, address, area, normalized_address, lat, lon
          FROM events
          WHERE id IN ({placeholders})
        """,
        tuple(event_ids),
    )

    results = []
    for row in cur.fetchall():
        event_id, title, location_name, address, area, normalized_address, lat_value, lon_value = row
        event_lat, event_lon, normalized = resolve_event_coordinates(
            event_id,
            location_name,
            address,
            normalized_address,
            lat_value,
            lon_value,
        )
        if event_lat is None or event_lon is None:
            continue

        distance = _haversine_km(lat, lon, event_lat, event_lon)
        if radius_km is None or distance <= radius_km:
            results.append({
                "id": event_id,
                "title": title,
                "distance_km": round(distance, 2),
                "location_name": location_name,
                "address": address,
                "area": area,
                "normalized_address": normalized,
                "lat": event_lat,
                "lon": event_lon,
            })

    results.sort(key=lambda item: item["distance_km"])
    return JsonResponse({"results": results, "count": len(results)})

@user_or_admin_required
def api_areas(request):
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT area FROM events WHERE area IS NOT NULL ORDER BY area")
    rows = cur.fetchall()
    return JsonResponse([r[0] for r in rows if r[0]], safe=False)


@user_or_admin_required
def api_list_folders(request):
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM folders ORDER BY name")
    rows = cur.fetchall()
    return JsonResponse([{"id": r[0], "name": r[1]} for r in rows], safe=False)


@user_or_admin_required
def api_folder_events(request, fid: int):
    area = request.GET.get("area")
    cur = conn.cursor()
    if area:
        cur.execute("""
          SELECT e.id, e.title, e.url, e.description, e.fetched_at, e.html_path, e.location_name, e.address, e.area, e.normalized_address, e.lat, e.lon
          FROM events e
          JOIN event_folders ef ON e.id = ef.event_id
          WHERE ef.folder_id = ? AND (e.area = ? OR e.area LIKE ?)
          ORDER BY e.fetched_at DESC
        """, (fid, area, f"%{area}%"))
    else:
        cur.execute("""
          SELECT e.id, e.title, e.url, e.description, e.fetched_at, e.html_path, e.location_name, e.address, e.area, e.normalized_address, e.lat, e.lon
          FROM events e
          JOIN event_folders ef ON e.id = ef.event_id
          WHERE ef.folder_id = ?
          ORDER BY e.fetched_at DESC
        """, (fid,))
    events = []
    for r in cur.fetchall():
        events.append({
            "id": r[0], "title": r[1], "url": r[2], "description": r[3], "fetched_at": r[4],
            "html_path": r[5], "location_name": r[6], "address": r[7], "area": r[8],
            "normalized_address": r[9], "lat": r[10], "lon": r[11]
        })
    return JsonResponse(events, safe=False)














@user_or_admin_required
def api_folder_nearby_events(request, fid: int):
    lat = _parse_float(request.GET.get("lat"), None)
    lon = _parse_float(request.GET.get("lon"), None)
    radius_raw = (request.GET.get("radius_km") or "").strip().lower()
    radius_km = None if radius_raw == "all" else _parse_float(radius_raw or request.GET.get("radius_km"), 10.0)
    area = (request.GET.get("area") or "").strip()

    if lat is None or lon is None:
        return JsonResponse({"error": "lat and lon are required"}, status=400)
    if radius_km is not None and radius_km <= 0:
        return JsonResponse({"error": "radius_km must be positive"}, status=400)

    user_city = _reverse_geocode_city(lat, lon)

    cur = conn.cursor()
    if area:
        cur.execute(
            """
              SELECT e.id, e.title, e.url, e.description, e.fetched_at, e.html_path,
                     e.location_name, e.address, e.area, e.normalized_address, e.lat, e.lon
              FROM events e
              JOIN event_folders ef ON e.id = ef.event_id
              WHERE ef.folder_id = ? AND (e.area = ? OR e.area LIKE ?)
              ORDER BY e.fetched_at DESC
            """,
            (fid, area, f"%{area}%"),
        )
    else:
        cur.execute(
            """
              SELECT e.id, e.title, e.url, e.description, e.fetched_at, e.html_path,
                     e.location_name, e.address, e.area, e.normalized_address, e.lat, e.lon
              FROM events e
              JOIN event_folders ef ON e.id = ef.event_id
              WHERE ef.folder_id = ?
              ORDER BY e.fetched_at DESC
            """,
            (fid,),
        )

    results = []
    for row in cur.fetchall():
        event_id, title, url, description, fetched_at, html_path, location_name, address, area_value, normalized_address, lat_value, lon_value = row

        event_lat, event_lon, normalized = resolve_event_coordinates(
            event_id,
            location_name,
            address,
            normalized_address,
            lat_value,
            lon_value,
        )

        distance_km = None
        is_near = False
        if event_lat is not None and event_lon is not None:
            distance_km = round(_haversine_km(lat, lon, event_lat, event_lon), 2)
            is_near = True if radius_km is None else distance_km <= radius_km

        city_match = _event_matches_city(user_city, location_name, address, area_value, normalized)

        include_event = (is_near or city_match) if radius_km is not None else True

        if include_event:
            if radius_km is None:
                match_type = "all"
            else:
                match_type = "distance" if is_near else "city"
            results.append({
                "id": event_id,
                "title": title,
                "url": url,
                "description": description,
                "fetched_at": fetched_at,
                "location_name": location_name,
                "address": address,
                "area": area_value,
                "normalized_address": normalized,
                "lat": event_lat,
                "lon": event_lon,
                "distance_km": distance_km,
                "match_type": match_type,
            })

    results.sort(key=lambda item: (item["distance_km"] is None, item["distance_km"] if item["distance_km"] is not None else 99999, item["title"] or ""))

    return JsonResponse({
        "results": results,
        "count": len(results),
        "user_city": user_city,
        "radius_km": radius_km,
    })

