import os
import uuid
from datetime import datetime, timezone

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "eventsite.settings")

import django

django.setup()

from collector.collector_logic import classify, conn, insert_event, make_id, _send_interest_notifications_for_event


TEST_TITLE = "Unstop - Competitions, Quizzes, Hackathons, Scholarships and Internships for Students and Corporates"


def main():
    unique_suffix = uuid.uuid4().hex[:10]
    url = f"https://example.com/test-unstop-event-{unique_suffix}"
    event_id = make_id(url)
    now = datetime.now(timezone.utc).isoformat()

    event = {
        "id": event_id,
        "url": url,
        "domain": "example.com",
        "title": TEST_TITLE,
        "description": "Test inserest-notification email flow.",
        "start_time": None,
        "fetched_at": now,
        "serp_source": "manual_test_seed",
        "html_path": None,
        "notes": "manual test event",
        "location_name": "Test Venue",
        "address": "Test Address, Pune, India",
        "area": "Pune",
        "normalized_address": "Test Address, Pune, India",
        "lat": None,
        "lon": None,
    }

    inserted = insert_event(event)
    if not inserted:
        print("Event was not inserted (possibly duplicate URL).")
        return

    cur = conn.cursor()
    folders = classify(f"{event['title']} {event['description']}")
    for folder_name in folders:
        cur.execute("INSERT OR IGNORE INTO folders(name) VALUES (?)", (folder_name,))
        cur.execute("SELECT id FROM folders WHERE name = ?", (folder_name,))
        row = cur.fetchone()
        if row:
            cur.execute(
                "INSERT OR IGNORE INTO event_folders(event_id, folder_id) VALUES (?, ?)",
                (event_id, row[0]),
            )
    conn.commit()

    _send_interest_notifications_for_event(event)

    print("Inserted test event and triggered notification flow.")
    print(f"event_id={event_id}")
    print(f"title={TEST_TITLE}")


if __name__ == "__main__":
    main()
