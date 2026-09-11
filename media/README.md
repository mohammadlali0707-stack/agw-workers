# media/manifest.jsonl

Append-only log written by `.github/workflows/media-request-bot.yml`. One JSON
object per line:

```json
{"issue": 12, "title": "lighthouse at sunset", "link": "https://...", "delivered_at": "2026-09-11T05:00:00Z"}
```

## Requesting media

Open an Issue in this repo whose body starts with `@media` followed by the
prompt. The bot acks automatically.

## Delivering media

Generate the image/video yourself with whatever AI tool, then comment the
resulting link (must start with `http://` or `https://`) on that same Issue.
The bot records it here, confirms, and closes the Issue.

No API keys and no auto-download involved -- the link itself is the record.
Anyone consuming an asset follows the link in the matching manifest row.
