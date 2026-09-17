# سیستم یکپارچه Notion + n8n + GitHub

مرجع عملیاتی خط لوله‌ای که یک تسک در Notion را به یک GitHub Issue در
`agw-workers` تبدیل می‌کند و بسته شدن آن Issue را دوباره به Notion برمی‌گرداند.

> **این فایل هیچ مقدار محرمانه‌ای ندارد و نباید داشته باشد.** فقط *نام* secretها
> اینجا نوشته شده، نه مقدارشان. به همین دلیل پیشوند واقعی توکن‌ها (GitHub PAT،
> Notion PAT، JWT) هم عمداً حرف‌به‌حرف نوشته نشده: `agw-workers` یک ریپوی عمومی
> است و WF4 (Secret Leak Scanner، پایین‌تر) روی هر push همین ریپو اجرا می‌شود و
> دقیقاً دنبال همان پیشوندها می‌گردد — نوشتنشان در این فایل باعث می‌شد اسکنر روی
> خودِ مستند یک issue با برچسب `security,critical` باز کند.

---

## معماری کلی

```
Notion (Task Board)
    |  native webhook (event-driven, zero delay)
    v
n8n (automation engine) -- https://n8n.airboxvip.top
    |                                  |
    v                                  v
GitHub Issues                   GitHub Webhooks
(agw-workers repos)             (push, issues events)
    |
    v
@agy runners (11 runner slot در هر حساب)
```

---

## اطلاعات سرور

| مورد | مقدار |
|---|---|
| n8n URL | `https://n8n.airboxvip.top` |
| Server | Arvan Cloud VPS (IP در secret با نام `SERVER_IP`) |
| Stack | Docker Compose — containerها: `n8n-n8n-1`، `n8n-postgres-1` |
| n8n Version | 2.39.6 |

---

## GitHub Secrets (فقط نام‌ها — مقادیر در GitHub Secrets)

روی هر دو ریپوی `Mohammadlali/agw-workers` و `mohammadlali0707-stack/agw-workers`
ذخیره شده‌اند:

| Secret Name | توضیح |
|---|---|
| `N8N_API_KEY` | توکن JWT برای n8n REST API |
| `N8N_URL` | آدرس n8n instance |
| `N8N_LOGIN_EMAIL` | ایمیل ورود به n8n |
| `N8N_LOGIN_PASSWORD` | رمز ورود به n8n |
| `NOTION_PAT` | Notion Personal Access Token |
| `NOTION_CONNECTION_TOKEN` | Notion Connection Token |
| `NOTION_TASK_DB_ID` | شناسه database مربوط به Task Board در Notion |
| `RUNNER_API_SECRET` | کلید احراز هویت `runner-api.py` |
| `SERVER_IP` | IP سرور Arvan |

---

## حساب‌های GitHub

هر حساب یک ریپوی `agw-workers` مخصوص خودش دارد و این ریپوها با هم جابه‌جا
**نمی‌شوند** — انتقال ۱۴۰۴/۰۶/۲۶ (2026-09-17) به ACC6 فقط مربوط به ریپوهای
پروژه بود، نه به `agw-workers`.

| index | حساب | نقش |
|---|---|---|
| ACC0 | `Mohammadlali` | خارج از pool هویت `@agy`؛ مالک status Gist (`ACC0_PAT`) |
| ACC1 | `momonakikugava-pixel` | worker |
| ACC2 | `lali94m-max` | worker |
| ACC3 | `ngocgminh5-debug` | worker |
| ACC4 | `hmmletssee7-design` | worker |
| ACC5 | `kidding602` | worker |
| ACC6 | `mohammadlali0707-stack` | worker + **relay host** + مالک همه ریپوهای پروژه |
| ACC7 | `mohammad97okk` | worker |
| ACC8 | `moradzahra85-png` | worker |
| ACC9 | `stranger77777777` | **بازنشسته** — در هیچ چرخه‌ای نیست |

> **نکته درباره شمارش slotها:** هشت حساب ACC1..ACC8 در pool هویت `@agy` هستند و
> هرکدام تا ۱۱ slot همزمان دارند، یعنی **۸۸ slot**، نه ۹۹. ACC0 ریپوی
> `agw-workers` خودش را دارد ولی `agw-worker.yml` آن را به‌عنوان یک هویت `@agy`
> استفاده نمی‌کند، و ACC9 کاملاً بازنشسته است. جزئیات در `README.md`.

### ریپوهای پروژه (همه روی ACC6)

| پروژه | tag در `@agy` | ریپو |
|---|---|---|
| Claud-Cloud-Project | `ccp` | `mohammadlali0707-stack/Claud-Cloud-Project` |
| Control-Room | `cr` | `mohammadlali0707-stack/Control-Room` |
| AirboxVIP Coffeenet | `coffeenet` | `mohammadlali0707-stack/AirboxVIP_Coffeenet` |
| status-dashboard | `status` | `mohammadlali0707-stack/status-dashboard` |

`status-dashboard` آخرین موردی بود که هنوز زیر `Mohammadlali` مانده بود و در
2026-09-17 به ACC6 منتقل شد. مسیریابی آن در `agy-issue-bot.yml` و کلون آن در
`deploy-status-dashboard-remote.yml`، `deploy-status-feed-remote.yml` و
`test-agy-chat-live.yml` هم‌زمان با همین انتقال به `ACC6_PAT` تغییر کرد.

**آنچه منتقل نشد:** status **Gist**. یک Gist همراه انتقال ریپو جابه‌جا نمی‌شود و
هنوز متعلق به ACC0 است — به همین دلیل `deploy-status-feed-remote.yml`،
`create-status-gist.yml` و `test-status-api-live.yml` همچنان `ACC0_PAT` مصرف
می‌کنند.

---

## n8n Credential IDs

| نوع | ID | نام |
|---|---|---|
| httpHeaderAuth (Notion) | `VnLKPQ4Si5OK6u5p` | Notion PAT |
| notionApi | `PfrA8Rmtrae2odET` | Notion API Key |
| httpHeaderAuth (GitHub ACC6) | `ec8l624V4zZ9FpJt` | GitHub ACC6 PAT |

---

## n8n Workflows (همه Active)

| # | نام | ID | Trigger |
|---|---|---|---|
| 1 | GitHub Ephemeral Runner Trigger v2 | `66e1beb3-bfd3-426d-b879-1266d7cea882` | `POST /webhook/github-runner-trigger` |
| 2 | Notion Event → agw-workers Issue | `7LhwzYMFAJyX7dGY` | `POST /webhook/notion-events` |
| 3 | GitHub Issue Close → Notion Done | `9AefIVy4aHRcna2W` | `POST /webhook/github-issue-events` |
| 4 | WF1 — Notion Webhook Health Monitor | `X60zb6NKrnoM2HMU` | schedule، هر ۵ دقیقه |
| 5 | WF2 — Duplicate Hook Detector | `ldr32ppsd7uxRf5y` | schedule، هر ساعت |
| 6 | WF3 — n8n Workflow JSON Validator | `C9lnypmJByvpXvnO` | `POST /webhook/validate-n8n-json` |
| 7 | WF4 — Secret Leak Scanner | `Qyj0MyoXFfgBZBfz` | `POST /webhook/secret-scan` |
| 8 | WF5 — Notion Subscription Monitor | `26KSkbigefNyB6Oj` | schedule، روزانه ۹ صبح |

### 1. GitHub Ephemeral Runner Trigger v2
هنگام باز شدن یک GitHub issue یک ephemeral runner می‌سازد. مسیریابی **همیشه** به
`agw-workers` است، نه `Control-Room`.

### 2. Notion Event → agw-workers Issue
تسک جدید در Notion → GitHub Issue در `agw-workers` → وضعیت Notion = `In Progress`.
فیلتر: فقط رویداد `page.created` از database مربوط به Task Board.
subscription در `Developer tools → Connections → Notion_Connection` فعال است.

### 3. GitHub Issue Close → Notion Done
بسته شدن issue → وضعیت Notion = `Done`. مکانیزم: body هر issue شامل
`<!-- notion-page-id: {pageId} -->` است.

### 4. WF1 — Notion Webhook Health Monitor
هر ۵ دقیقه به `/webhook/notion-events` ping می‌زند؛ اگر status ≠ 2xx بود یک GitHub
issue با برچسب `alert` باز می‌کند.

### 5. WF2 — Duplicate Hook Detector
هر ساعت هر ۹ ریپو را بررسی می‌کند و هر webhookای که URLاش `n8n.airboxvip.top`
نباشد را خودکار حذف می‌کند.

### 6. WF3 — n8n Workflow JSON Validator
روی رویداد `push` اجرا می‌شود و این چهار مورد را چک می‌کند:

- فیلد `active` نباید در body مربوط به ساخت workflow باشد (read-only است)
- `responseMode` نباید `"immediately"` باشد — باید `"onReceived"` باشد
- `connections.*.main` باید nested array باشد: `[[{node,type,index}]]`
- هر node باید `id` و `typeVersion` داشته باشد

در صورت یافتن خطا روی همان commit کامنت می‌گذارد.

### 7. WF4 — Secret Leak Scanner
روی رویداد `push` اجرا می‌شود و دنبال این الگوها می‌گردد: پیشوند PAT گیت‌هاب،
پیشوند توکن Notion، توکن‌های JWT، رشته‌های `secret_` با بیش از ۳۰ کاراکتر، و
پارامترهای query از نوع کلید API. در صورت یافتن، یک GitHub issue با برچسب
`security,critical,secret-leak` باز می‌کند.

### 8. WF5 — Notion Subscription Monitor
روزانه ساعت ۹ صبح وضعیت subscription مربوط به Notion webhook را بررسی می‌کند؛ اگر
inactive بود issue باز می‌کند.

---

## GitHub Webhooks (روی هر ۹ ریپوی `agw-workers`)

| Event | n8n Endpoint | Workflow |
|---|---|---|
| `issues` | `/webhook/github-issue-events` | Issue Close → Notion Done |
| `push` | `/webhook/validate-n8n-json` | WF3: JSON Validator |
| `push` | `/webhook/secret-scan` | WF4: Secret Scanner |

---

## Notion

- **Task Board Database ID:** در secret با نام `NOTION_TASK_DB_ID`
- **Webhook Subscription:** فعال در `Developer tools → Connections → Notion_Connection`
- **API Version:** `2022-06-28` برای عملیات روی page، و `2026-03-11` برای subscriptionها
- **Properties:**
  - `Name` (title)
  - `Status` (select): `Not Started` | `In Progress` | `Done`
  - `Github Issue URL` (select): مقدار `#N`
  - `Project` (select)

---

## قوانین مهم

1. **تسک‌های رندم → `agw-workers`**، نه `Control-Room`.
2. **`Control-Room` فقط برای قوانین کلی شرکت است.**
3. **همه webhookها از مسیر n8n** — هیچ webhook مستقیمی به سرور زده نشود.
4. **`responseMode` در webhook node باید `"onReceived"` باشد**، نه `"immediately"`.
5. **هنگام ساخت workflow از طریق API، فیلد `active` فرستاده نشود** (read-only است).
6. **فرمت `connections` باید nested array باشد:**
   ```json
   { "connections": { "NodeA": { "main": [[{ "node": "NodeB", "type": "main", "index": 0 }]] } } }
   ```

---

## مشکلات رایج و راه‌حل

| مشکل | علت | راه‌حل |
|---|---|---|
| Notion webhook خطای 500 می‌دهد | `responseMode: "immediately"` | تغییر به `"onReceived"` |
| داده‌ی execution از n8n API خالی برمی‌گردد | n8n نسخه ۲ داده را در جدول جدای `execution_data` نگه می‌دارد | مستقیم از PostgreSQL کوئری بگیر |
| خطای `id is read-only` روی PUT | فیلد `id` در body ارسال شده | فقط `name, nodes, connections, settings, staticData` بفرست |
| runnerها دوبار spawn می‌شوند | webhook مستقیم و webhook مربوط به n8n هر دو فعال بودند | فقط webhook مربوط به n8n نگه داشته شود |

---

## دستورات n8n API

```bash
# لیست همه workflowها
curl -H "X-N8N-API-KEY: $N8N_API_KEY" https://n8n.airboxvip.top/api/v1/workflows

# فعال‌سازی یک workflow
curl -X POST -H "X-N8N-API-KEY: $N8N_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{}' \
     https://n8n.airboxvip.top/api/v1/workflows/{id}/activate

# بررسی آخرین execution مستقیم از DB (روی سرور)
docker exec n8n-postgres-1 psql -U n8n -d n8n -t -A \
  -c "SELECT data FROM execution_data ORDER BY executionId DESC LIMIT 1"
```

---

## flow کامل سیستم

```
Notion: Task Board -> صفحه جدید ساخته می‌شود
        |  [Notion native webhook -- آنی]
        v
n8n: POST /webhook/notion-events
        |  [فیلتر: Task Board DB + page.created]
        v
n8n: GET /pages/{pageId}  ->  اطلاعات کامل تسک
        |
        v
GitHub: Issue در agw-workers
        body شامل: <!-- notion-page-id: {pageId} -->
        label: notion-task
        |
        v
Notion: Status = "In Progress"، Github Issue URL = "#N"
        |
        v
@agy runner کار را انجام می‌دهد...
        |
        v
Issue بسته می‌شود
        |  [GitHub webhook، رویداد issues -- آنی]
        v
n8n: POST /webhook/github-issue-events
        |  [استخراج notion-page-id از body مربوط به issue]
        v
Notion: Status = "Done"
```
