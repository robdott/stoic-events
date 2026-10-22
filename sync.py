#!/usr/bin/env python3
"""Pull public Meetup iCal feeds for every group in groups.json into events.json.

Standard library only (Python 3.9+). Run it on a schedule (see
.github/workflows/sync.yml) and serve events.json next to index.html.

Usage:
  python sync.py                      # fetch live feeds
  python sync.py --from-dir fixtures  # read <slug>.ics files instead (testing)
"""
import argparse
import html
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DEFAULT_TZ = "America/New_York"
SUMMARY_CHARS = 240
DESCRIPTION_CHARS = 1800


# ---------- fetching ----------

def fetch(url):
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (compatible; meetup-events-sync/1.0)"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", "replace")


def slug_of(group_url):
    return urlparse(group_url).path.strip("/").split("/")[0]


# ---------- iCal parsing ----------

def unfold(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n[ \t]", "", text)  # folded lines start with a space/tab


def unescape(value):
    out, i = [], 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append("\n" if nxt in "nN" else nxt)
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def parse_vevents(text):
    events, cur = [], None
    for line in unfold(text).split("\n"):
        if line == "BEGIN:VEVENT":
            cur = {}
        elif line == "END:VEVENT":
            if cur is not None:
                events.append(cur)
            cur = None
        elif cur is not None and ":" in line:
            head, _, value = line.partition(":")
            parts = head.split(";")
            params = {}
            for p in parts[1:]:
                if "=" in p:
                    k, v = p.split("=", 1)
                    params[k.upper()] = v
            cur[parts[0].upper()] = (params, value)
    return events


def zone(name):
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo(DEFAULT_TZ)


def to_dt(params, value):
    """Return (aware datetime, tz name, is_all_day)."""
    v = value.strip()
    if re.fullmatch(r"\d{8}", v):
        return datetime.strptime(v, "%Y%m%d").replace(tzinfo=zone(DEFAULT_TZ)), DEFAULT_TZ, True
    is_utc = v.endswith("Z")
    dt = datetime.strptime(v.rstrip("Z"), "%Y%m%dT%H%M%S")
    if is_utc:
        return dt.replace(tzinfo=timezone.utc), DEFAULT_TZ, False
    tzname = params.get("TZID", DEFAULT_TZ)
    return dt.replace(tzinfo=zone(tzname)), tzname, False


# ---------- text cleanup (Meetup descriptions are markdown-ish) ----------

def plain(md):
    s = md
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", s)
    s = re.sub(
        r"\[([^\]]*)\]\((https?://[^)]*)\)",
        lambda m: m.group(2) if (not m.group(1) or m.group(1).startswith("http")) else m.group(1),
        s,
    )
    s = re.sub(r"^\s*[*\-]\s+", "• ", s, flags=re.M)
    s = re.sub(r"(\*\*|__)(.+?)\1", r"\2", s, flags=re.S)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", s, flags=re.S)
    s = re.sub(r"\\([\\`*_{}\[\]()#+\-.!~|<>,;])", r"\1", s)
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def shorten(text, limit):
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:-")
    return cut + "…"


def clean_description(raw, group_name):
    lines = raw.split("\n")
    if lines and lines[0].strip() == group_name.strip():
        lines = lines[1:]  # Meetup repeats the group name as the first line
    lines = [ln for ln in lines if not ln.strip().lower().lstrip("*_ ").startswith("cosponsored with")]
    return plain("\n".join(lines))


# ---------- building events ----------

def build_events(ics_text, group):
    out = []
    for ev in parse_vevents(ics_text):
        if "DTSTART" not in ev or "SUMMARY" not in ev:
            continue
        if ev.get("STATUS", ({}, ""))[1].strip().upper() == "CANCELLED":
            continue
        start, tzname, all_day = to_dt(*ev["DTSTART"])
        if "DTEND" in ev:
            end, _, _ = to_dt(*ev["DTEND"])
        else:
            end = start + (timedelta(days=1) if all_day else timedelta(hours=1))
        raw_desc = unescape(ev.get("DESCRIPTION", ({}, ""))[1])
        # The listing that says "Cosponsored with <other group>" is the secondary copy of a shared event.
        is_cosponsor_copy = bool(re.search(r"cosponsored with", raw_desc, re.I))
        desc = clean_description(raw_desc, group["name"])
        first_para = desc.split("\n\n")[0] if desc else ""
        out.append(
            {
                "id": unescape(ev.get("UID", ({}, ""))[1]) or f"{group['slug']}-{start.isoformat()}",
                "title": unescape(ev["SUMMARY"][1]).strip(),
                "start": start.isoformat(),
                "end": end.isoformat(),
                "all_day": all_day,
                "tz": tzname,
                "summary": shorten(first_para or desc, SUMMARY_CHARS),
                "description": desc[:DESCRIPTION_CHARS].rstrip() + ("…" if len(desc) > DESCRIPTION_CHARS else ""),
                "location": unescape(ev.get("LOCATION", ({}, ""))[1]).strip(),
                "sources": [{"group": group["slug"], "url": ev.get("URL", ({}, ""))[1].strip(),
                             "cosponsor": is_cosponsor_copy}],
            }
        )
    return out


def dedupe_key(ev):
    title = re.sub(r"\s+", " ", ev["title"]).strip().lower()
    start = datetime.fromisoformat(ev["start"]).astimezone(timezone.utc).isoformat()
    return (title, start)


def merge(events):
    """Co-hosted events appear in several groups; show them once with every RSVP link."""
    merged = {}
    for ev in events:
        key = dedupe_key(ev)
        if key not in merged:
            merged[key] = ev
            continue
        base = merged[key]
        for src in ev["sources"]:
            if all(s["group"] != src["group"] for s in base["sources"]):
                base["sources"].append(src)
        if len(ev["description"]) > len(base["description"]):
            base["description"], base["summary"] = ev["description"], ev["summary"]
    return list(merged.values())


def load_rules():
    path = ROOT / "rules.json"
    if not path.exists():
        return []
    return json.loads(path.read_text()).get("primary_group", [])


def apply_primary(ev, rules):
    """Shared events: put the owning group first (it sets the tag, color and RSVP link).

    1. A matching rule in rules.json wins (manual override).
    2. Otherwise the copy that does NOT say "Cosponsored with ..." is the owner.
    3. If that doesn't settle it, the group listed first in groups.json is used.
    """
    title = ev["title"].lower()
    for rule in rules:
        if rule["title_contains"].lower() in title:
            ev["sources"].sort(key=lambda s: s["group"] != rule["group"])  # stable: preferred first
            return
    ev["sources"].sort(key=lambda s: bool(s.get("cosponsor")))  # stable: non-cosponsor copy first


def og_image(page):
    for m in re.finditer(r"<meta\b[^>]*>", page, re.I):
        tag = m.group(0)
        if re.search(r'(?:property|name)\s*=\s*["\'](?:og:image|twitter:image)["\']', tag, re.I):
            c = re.search(r'content\s*=\s*["\']([^"\']+)["\']', tag, re.I)
            if c and c.group(1).startswith("https://"):
                return html.unescape(c.group(1))
    return ""


def enrich(events, previous, from_dir):
    """Add each event's photo (and 'Online event') from its own Meetup page. Cached by event id."""
    prev_by_id = {e["id"]: e for e in previous}
    for ev in events:
        old = prev_by_id.get(ev["id"], {})
        # Reuse a saved photo only if it came from the group that currently owns the event.
        primary_url = ev["sources"][0]["url"]
        ev["image"] = old.get("image", "") if old.get("image_from") == primary_url else ""
        ev["image_from"] = primary_url if ev["image"] else ""
        if not ev["location"] and old.get("location"):
            ev["location"] = old["location"]
        if ev["image"]:
            continue  # already have it; don't re-fetch
        for src in ev["sources"]:
            try:
                if from_dir:
                    num = re.search(r"/events/(\d+)", src["url"]).group(1)
                    page = (Path(from_dir) / "pages" / f"{num}.html").read_text()
                else:
                    page = fetch(src["url"])
                    time.sleep(0.5)
            except Exception as exc:
                print(f"WARNING page {src['url']}: {exc}", file=sys.stderr)
                continue
            if not ev["location"] and re.search(r'Online event|"eventType"\s*:\s*"ONLINE"', page):
                ev["location"] = "Online event"
            img = og_image(page)
            if img:
                ev["image"] = img
                ev["image_from"] = src["url"]
                break


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-dir", help="read <slug>.ics files from this folder instead of fetching")
    ap.add_argument("--out", default=str(ROOT / "events.json"))
    args = ap.parse_args()

    raw_groups = json.loads((ROOT / "groups.json").read_text())
    groups = [
        {"slug": slug_of(g["url"]), "name": g["name"], "url": g["url"], "color": g.get("color", "#555555")}
        for g in raw_groups
    ]

    out_path = Path(args.out)
    previous = []
    if out_path.exists():
        try:
            previous = json.loads(out_path.read_text()).get("events", [])
        except Exception:
            previous = []

    collected, failed = [], set()
    for g in groups:
        try:
            if args.from_dir:
                text = (Path(args.from_dir) / f"{g['slug']}.ics").read_text()
            else:
                text = fetch(g["url"].rstrip("/") + "/events/ical/")
            if "BEGIN:VCALENDAR" not in text:
                raise ValueError("response was not an iCal feed")
            found = build_events(text, g)
            print(f"{g['slug']}: {len(found)} events")
            collected.extend(found)
        except Exception as exc:  # keep going; one bad group shouldn't blank the page
            print(f"WARNING {g['slug']}: {exc}", file=sys.stderr)
            failed.add(g["slug"])

    if len(failed) == len(groups):
        print("All feeds failed; leaving events.json untouched.", file=sys.stderr)
        sys.exit(1)

    events = merge(collected)

    # For a group whose feed failed, keep what we knew last time.
    seen = {dedupe_key(e) for e in events}
    for old in previous:
        if any(s["group"] in failed for s in old.get("sources", [])) and dedupe_key(old) not in seen:
            events.append(old)

    now = datetime.now(timezone.utc)
    events = [e for e in events if datetime.fromisoformat(e["end"]) >= now]
    events.sort(key=lambda e: (datetime.fromisoformat(e["start"]), e["title"]))

    rules = load_rules()
    for ev in events:
        apply_primary(ev, rules)
        # Co-hosted copies exist only to get extra eyes on Meetup; show each event once, under its own group.
        ev["sources"] = ev["sources"][:1]
        ev["sources"][0].pop("cosponsor", None)
    enrich(events, previous, args.from_dir)

    payload = {"groups": groups, "events": events}
    if out_path.exists():
        try:
            old = json.loads(out_path.read_text())
            if old.get("groups") == groups and old.get("events") == events:
                print("No changes.")
                return
        except Exception:
            pass
    payload["generated"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {len(events)} events to {out_path}")


if __name__ == "__main__":
    main()
