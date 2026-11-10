"""One-off maintenance: shrink photos that were uploaded before the API
started doing it, and rebuild their thumbnails.

    python scripts/shrink_images.py            # show what would change
    python scripts/shrink_images.py --apply    # actually change the photos

Safe to run more than once: photos that are already small are skipped.
Shrinking is lossy, so take a database backup before using --apply.
Uses the same configuration (environment variables / .env) as the API.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lager import images  # noqa: E402
from lager.config import load_settings  # noqa: E402
from lager.database import Database  # noqa: E402


def mb(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} MB"


def main() -> int:
    apply = "--apply" in sys.argv[1:]
    if not images.AVAILABLE:
        print("Pillow er ikke installert. Kjør 'pip install Pillow' (Run Pip Install i panelet) først.")
        return 1
    settings = load_settings()
    if not settings.db_user or not settings.db_name:
        print("Fant ikke databaseoppsettet (DB_USER / DB_NAME). Kjør skriptet i samme miljø som API-et.")
        return 1

    db = Database(settings)
    with db._cursor(dictionary=False) as cur:
        cur.execute("SELECT id FROM components WHERE image IS NOT NULL ORDER BY id")
        ids = [row[0] for row in cur.fetchall()]

    before_total = after_total = changed = 0
    for component_id in ids:
        found = db.get_image(component_id)  # one photo in memory at a time
        if found is None:
            continue
        data, mime = found
        smaller, new_mime = images.shrink_photo(data, mime)
        before_total += len(data)
        after_total += len(smaller)
        if smaller is data:
            continue
        changed += 1
        print(f"#{component_id}: {mb(len(data))} -> {mb(len(smaller))}")
        if apply:
            thumb = images.make_thumbnail(smaller)
            with db._cursor(dictionary=False) as cur:
                # updated_at = updated_at: maintenance, not an edit by the user.
                cur.execute(
                    "UPDATE components SET image = %s, image_content_type = %s, thumbnail = %s, "
                    "updated_at = updated_at WHERE id = %s",
                    (smaller, new_mime, thumb, component_id),
                )

    print()
    print(f"{len(ids)} bilder sjekket, {changed} for store.")
    print(f"Totalt: {mb(before_total)} -> {mb(after_total)}")
    if changed and not apply:
        print("Ingenting er endret ennå. Kjør med --apply for å krympe dem.")
    elif apply:
        print("Ferdig.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
