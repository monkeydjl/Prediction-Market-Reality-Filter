"""Check World Cup events in the tracking system."""

from app.memory.event_store import list_all_events

events = list_all_events()
wc_events = [e for e in events if "2026" in e.get("event_id", "") or "world-cup" in e.get("event_id", "")]

print(f"Total tracked events: {len(events)}")
print(f"World Cup events tracked: {len(wc_events)}")

if wc_events:
    print("\nWorld Cup event IDs:")
    for e in wc_events[:10]:
        event_id = e.get("event_id")
        record = e.get("record", {})
        event_title = record.get("event_title", "")
        print(f"  - {event_id}")
        if event_title:
            print(f"    {event_title}")
else:
    print("\nNo World Cup events found in tracking system.")
    print("These events should be added through the event discovery process.")
