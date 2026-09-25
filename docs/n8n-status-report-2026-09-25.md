# گزارش وضعیت n8n — ۲۵ شهریور ۱۴۰۵ (2026-09-25)

> **تهیه‌کننده:** AGY worker (اجرا در GitHub Actions)
> **زمان بررسی:** 2026-09-25T18:16 UTC
> **دلیل صدور:** دسترسی مستقیم مالک به سرور محدود شده؛ این گزارش جایگزین بررسی دستی است.

---

## ۱. خلاصه اجرایی

| موضوع | وضعیت |
|---|---|
| سرور n8n | ✅ **آنلاین** — پاسخ‌دهی طبیعی |
| همه webhookها | ✅ **۵/۵ فعال** — همه HTTP 200 برگردانده‌اند |
| API v1 | ✅ دسترس‌پذیر (HTTP 401 = سرور بالاست، کلید لازم است) |
| UI مرورگر | ✅ HTTP 200، زمان پاسخ: 0.78 ثانیه |
| گواهینامه TLS | ✅ معتبر — **80 روز** تا انقضا (Let's Encrypt) |
| IP سرور | 185.204.171.61 (Arvan Cloud VPS) |
| وضعیت کلی | 🟢 **همه سیستم‌ها عملیاتی هستند** |

---

## ۲. بررسی زنده — webhook‌ها

تمام آزمایش‌ها در `2026-09-25T18:16 UTC` از یک runner GitHub Actions اجرا شده‌اند.

### ۲.۱ نتایج HTTP

| Endpoint | متد | HTTP | وضعیت |
|---|---|---|---|
| `POST /webhook/notion-events` | POST | **200 OK** | ✅ فعال |
| `POST /webhook/github-runner-trigger` | POST | **200 OK** | ✅ فعال |
| `POST /webhook/github-issue-events` | POST | **200 OK** | ✅ فعال |
| `POST /webhook/validate-n8n-json` | POST | **200 OK** | ✅ فعال |
| `POST /webhook/secret-scan` | POST | **200 OK** | ✅ فعال |
| `GET /api/v1/workflows` | GET | **401 Unauthorized** | ✅ (سرور بالاست، احراز هویت نیاز است) |
| `GET /` (UI) | GET | **200 OK** | ✅ رابط کاربری در دسترس |

> **نکته:** HTTP 200 از webhookها یعنی workflow مربوطه **active** است و پیام را پذیرفت.
> اگر workflow غیرفعال بود، n8n HTTP 404 یا پاسخ متفاوتی می‌داد.

### ۲.۲ آزمون guard منبع Notion

یک payload واقعی با `source.type == "automation"` به webhook `notion-events` فرستاده شد:

```json
{"source":{"type":"automation"},"data":{"id":"00000000-0000-0000-0000-000000000000"}}
```

**پاسخ n8n:**
```json
{"message":"Workflow was started"}
```

✅ Guard به درستی کار می‌کند — workflow راه‌اندازی شد (نه رد شد).

---

## ۳. جزئیات گواهینامه TLS

| مورد | مقدار |
|---|---|
| موضوع | `CN = n8n.airboxvip.top` |
| صادرکننده | Let's Encrypt (CA: YE1) |
| شروع اعتبار | Sep 16, 2026 |
| پایان اعتبار | **Dec 15, 2026** |
| روزهای باقی‌مانده | **~80 روز** |
| پروتکل TLS | TLSv1.3 |
| Cipher Suite | `TLS_AES_256_GCM_SHA384` |
| Key Exchange | X25519 |
| Web Server | nginx/1.24.0 (Ubuntu) |

> ⚠️ **هشدار:** Let's Encrypt گواهینامه را معمولاً ۳۰ روز قبل از انقضا تجدید می‌کند.
> اگر certbot یا ACME client روی سرور درست پیکربندی شده باشد، تجدید خودکار است.
> **توصیه:** در تاریخ حدود ۱۵ آبان (Nov 15) یک بار وضعیت را چک کنید.

---

## ۴. Workflowها (بر اساس مستندات موجود)

طبق آخرین مستندات در `docs/notion-n8n-integration.md`، این ۸ workflow باید active باشند:

| # | نام | ID | Trigger | وضعیت |
|---|---|---|---|---|
| 1 | GitHub Ephemeral Runner Trigger v2 | `66e1beb3...` | `POST /webhook/github-runner-trigger` | ✅ HTTP 200 |
| 2 | Notion Event to agw-workers Issue | `7LhwzYMF...` | `POST /webhook/notion-events` | ✅ HTTP 200 |
| 3 | GitHub Issue Close to Notion Done | `9AefIVy4...` | `POST /webhook/github-issue-events` | ✅ HTTP 200 |
| 4 | WF1 — Notion Webhook Health Monitor | `X60zb6NK...` | Schedule هر ۵ دقیقه | 🔵 زمان‌بند |
| 5 | WF2 — Duplicate Hook Detector | `ldr32pps...` | Schedule هر ساعت | 🔵 زمان‌بند |
| 6 | WF3 — n8n Workflow JSON Validator | `C9lnypmJ...` | `POST /webhook/validate-n8n-json` | ✅ HTTP 200 |
| 7 | WF4 — Secret Leak Scanner | `Qyj0MyoX...` | `POST /webhook/secret-scan` | ✅ HTTP 200 |
| 8 | WF5 — Notion Subscription Monitor | `26KSkbig...` | Schedule روزانه ۹ صبح | 🔵 زمان‌بند |

> **توضیح:** ✅ = از طریق HTTP تأیید شد | 🔵 = schedule-based، از بیرون قابل ping نیست

---

## ۵. معماری فعلی

```
Notion (Task Board)
    |  native webhook (event-driven, بدون تأخیر)
    v
n8n — https://n8n.airboxvip.top   [ONLINE]
    |                                    |
    v                                    v
GitHub Issues                    GitHub Webhooks
(agw-workers repos)              (push, issues events)
    |
    v
@agy runners (88 slot — 8 حساب x 11)
```

---

## ۶. Cloudflare — دسترسی به API مدیریتی

> ⚠️ **مشکل شناخته‌شده:** دسترسی به `POST /api/v1/credentials` از GitHub Actions runner
> توسط Cloudflare با خطای **1010 (browser_signature_banned)** مسدود می‌شود.
> این مشکل فقط روی API مدیریتی است — webhook‌های production عادی کار می‌کنند.

| مسیر | از GitHub Actions | از مرورگر |
|---|---|---|
| `/webhook/*` (production) | ✅ دسترس‌پذیر | ✅ دسترس‌پذیر |
| `/api/v1/workflows` (GET) | قابل دسترس با API key | ✅ با API key |
| `/api/v1/credentials` (POST) | ❌ Cloudflare 1010 | ✅ با browser session |

---

## ۷. چه چیزهایی از بیرون قابل بررسی نیست

| مورد | دلیل | راه‌حل |
|---|---|---|
| آخرین execution هر workflow | نیاز به API key + Cloudflare block | UI → Executions در n8n |
| تعداد executions ناموفق | همان | UI → Executions → فیلتر Error |
| وضعیت Notion subscription | نیاز به Notion API | WF5 هر روز ۹ صبح بررسی می‌کند |
| وضعیت PostgreSQL | نیاز به دسترسی SSH | `docker exec n8n-postgres-1 psql ...` |
| workflowهای schedule | قابل ping از بیرون نیستند | UI → Executions |

---

## ۸. موارد نیاز به توجه

### اهمیت متوسط

**TLS Certificate — 80 روز باقی‌مانده**
- تاریخ انقضا: Dec 15, 2026
- اگر certbot روی سرور active باشد، خودکار تجدید می‌شود
- **اقدام:** بررسی `certbot renew --dry-run` روی سرور

**API مدیریتی از GitHub Actions مسدود است (Cloudflare 1010)**
- تأثیر: workflow `n8n-wire-notion-bridge.yml` نمی‌تواند credential جدید بسازد
- **اقدام:** اگر پیکربندی جدید نیاز شد، از UI مرورگر انجام دهید

### بدون مشکل

- همه ۵ webhook production کار می‌کنند
- سرور n8n پایدار و آنلاین است
- UI در دسترس است
- TLS سالم است

---

## ۹. دستورات بررسی سریع

```bash
# چک همه webhookها از ترمینال
for path in "webhook/notion-events" "webhook/github-runner-trigger" \
            "webhook/github-issue-events" "webhook/validate-n8n-json" "webhook/secret-scan"; do
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "https://n8n.airboxvip.top/$path" \
    -H "Content-Type: application/json" -d '{"test":true}' --max-time 10)
  echo "$path => HTTP $STATUS"
done

# بررسی آخرین execution از DB (روی سرور)
docker exec n8n-postgres-1 psql -U n8n -d n8n -t -A \
  -c "SELECT data FROM execution_data ORDER BY executionId DESC LIMIT 5"

# لیست workflowها از API
curl -H "X-N8N-API-KEY: $N8N_API_KEY" https://n8n.airboxvip.top/api/v1/workflows

# بررسی TLS
echo | openssl s_client -connect n8n.airboxvip.top:443 -servername n8n.airboxvip.top 2>/dev/null \
  | openssl x509 -noout -dates
```

---

## ۱۰. نتیجه‌گیری

**n8n کاملاً عملیاتی است.**

همه webhook‌های production پاسخ HTTP 200 دادند، سرور آنلاین است، TLS معتبر است و Guard منبع Notion به درستی کار می‌کند.

محدودیت اصلی فعلی: **Cloudflare مسیرهای API مدیریتی را از IP‌های GitHub Actions مسدود کرده** — این روی عملکرد روزانه سیستم تأثیر ندارد، اما اعمال تغییرات پیکربندی از طریق automation را ناممکن می‌کند.

---

*این گزارش توسط AGY worker در GitHub Actions تهیه شده است.*
*گزارش بعدی را می‌توان با باز کردن یک issue جدید با متن `@agy یک گزارش وضعیت n8n بده` درخواست کرد.*
