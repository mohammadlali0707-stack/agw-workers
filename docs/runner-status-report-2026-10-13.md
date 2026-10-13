# گزارش جامع وضعیت سلامت و پایداری رانرها — ۲۲ مهر ۱۴۰۵ (2026-10-13)

> **تهیه‌کننده:** AGY Worker (اجرا در GitHub Actions)  
> **مربوط به:** Issue #78 — System Health and Runner Status Check  
> **زمان بررسی:** 2026-10-13T16:20 UTC  
> **دامنه:** تمامی ۲۷ واحد رانر (Acc0-R1 تا Acc8-R3) در ۹ حساب گیت‌هاب  

---

## ۱. خلاصه اجرایی (Executive Summary)

| شاخص | مقدار / وضعیت | توضیحات |
|---|---|---|
| **تعداد کل واحدهای رانر** | **۲۷ واحد** | ۹ حساب گیت‌هاب × ۳ اسلات رانر در هر حساب |
| **وضعیت کلی ناوگان** | 🟢 **عملیاتی و پایدار (Healthy)** | همه ۲۷ واحد در رجیستری تأیید شدند |
| **مدل هوش مصنوعی رانرها** | **Gemini 3.8 Flash High** | `gemini-3.8-flash-high` |
| **حساب‌های فعال** | ۹ حساب (`ACC0` تا `ACC8`) | `ACC9` طبق دستور برای همیشه بازنشسته و خارج از چرخه است |
| **وضعیت ورژنینگ و کامپایل YAML** | ✅ **تأیید شد** (۵۹ فایل ورک‌فلو) | خطای نحوی و تورفتگی در `agy-swarm-infinite.yml` شناسایی و برطرف شد |
| **سازوکار همبستگی Swarm** | ✅ **خود-پیش‌برنده (Self-Perpetuating)** | چرخش تصادفی بین ۲۷ واحد با تریگر `@agy-swarm` |

---

## ۲. جدول وضعیت جامع تمامی ۲۷ واحد رانر

| # | شناسه رانر (Runner ID) | حساب | مالک حساب (GitHub Login) | نام مستعار | توکن دسترسی AGY | سکرت PAT گیت‌هاب | اسلات | وضعیت |
|:---:|:---|:---:|:---|:---:|:---:|:---:|:---:|:---:|
| 1 | `Acc0-R1` | ACC0 | `Mohammadlali` | 1997 | `AGW_TOKEN_ACC0` | `ACC0_PAT` | 1 | 🟢 سالم |
| 2 | `Acc0-R2` | ACC0 | `Mohammadlali` | 1997 | `AGW_TOKEN_ACC0` | `ACC0_PAT` | 2 | 🟢 سالم |
| 3 | `Acc0-R3` | ACC0 | `Mohammadlali` | 1997 | `AGW_TOKEN_ACC0` | `ACC0_PAT` | 3 | 🟢 سالم |
| 4 | `Acc1-R1` | ACC1 | `momonakikugava-pixel` | Momona | `AGW_TOKEN_ACC1` | `ACC1_PAT` | 1 | 🟢 سالم |
| 5 | `Acc1-R2` | ACC1 | `momonakikugava-pixel` | Momona | `AGW_TOKEN_ACC1` | `ACC1_PAT` | 2 | 🟢 سالم |
| 6 | `Acc1-R3` | ACC1 | `momonakikugava-pixel` | Momona | `AGW_TOKEN_ACC1` | `ACC1_PAT` | 3 | 🟢 سالم |
| 7 | `Acc2-R1` | ACC2 | `lali94m-max` | 94 | `AGW_TOKEN_ACC2` | `ACC2_PAT` | 1 | 🟢 سالم |
| 8 | `Acc2-R2` | ACC2 | `lali94m-max` | 94 | `AGW_TOKEN_ACC2` | `ACC2_PAT` | 2 | 🟢 سالم |
| 9 | `Acc2-R3` | ACC2 | `lali94m-max` | 94 | `AGW_TOKEN_ACC2` | `ACC2_PAT` | 3 | 🟢 سالم |
| 10 | `Acc3-R1` | ACC3 | `ngocgminh5-debug` | ngocg | `AGW_TOKEN_ACC3` | `ACC3_PAT` | 1 | 🟢 سالم |
| 11 | `Acc3-R2` | ACC3 | `ngocgminh5-debug` | ngocg | `AGW_TOKEN_ACC3` | `ACC3_PAT` | 2 | 🟢 سالم |
| 12 | `Acc3-R3` | ACC3 | `ngocgminh5-debug` | ngocg | `AGW_TOKEN_ACC3` | `ACC3_PAT` | 3 | 🟢 سالم |
| 13 | `Acc4-R1` | ACC4 | `hmmletssee7-design` | lets | `AGW_TOKEN_ACC4` | `ACC4_PAT` | 1 | 🟢 سالم |
| 14 | `Acc4-R2` | ACC4 | `hmmletssee7-design` | lets | `AGW_TOKEN_ACC4` | `ACC4_PAT` | 2 | 🟢 سالم |
| 15 | `Acc4-R3` | ACC4 | `hmmletssee7-design` | lets | `AGW_TOKEN_ACC4` | `ACC4_PAT` | 3 | 🟢 سالم |
| 16 | `Acc5-R1` | ACC5 | `kidding602` | Just | `AGW_TOKEN_ACC5` | `ACC5_PAT` | 1 | 🟢 سالم |
| 17 | `Acc5-R2` | ACC5 | `kidding602` | Just | `AGW_TOKEN_ACC5` | `ACC5_PAT` | 2 | 🟢 سالم |
| 18 | `Acc5-R3` | ACC5 | `kidding602` | Just | `AGW_TOKEN_ACC5` | `ACC5_PAT` | 3 | 🟢 سالم |
| 19 | `Acc6-R1` | ACC6 | `mohammadlali0707-stack` | 07 | `AGW_TOKEN_ACC6` | `ACC6_PAT` | 1 | 🟢 سالم (میزبان رله) |
| 20 | `Acc6-R2` | ACC6 | `mohammadlali0707-stack` | 07 | `AGW_TOKEN_ACC6` | `ACC6_PAT` | 2 | 🟢 سالم (میزبان رله) |
| 21 | `Acc6-R3` | ACC6 | `mohammadlali0707-stack` | 07 | `AGW_TOKEN_ACC6` | `ACC6_PAT` | 3 | 🟢 سالم (میزبان رله) |
| 22 | `Acc7-R1` | ACC7 | `mohammad97okk` | M2 | `AGW_TOKEN_ACC7` | `ACC7_PAT` | 1 | 🟢 سالم |
| 23 | `Acc7-R2` | ACC7 | `mohammad97okk` | M2 | `AGW_TOKEN_ACC7` | `ACC7_PAT` | 2 | 🟢 سالم |
| 24 | `Acc7-R3` | ACC7 | `mohammad97okk` | M2 | `AGW_TOKEN_ACC7` | `ACC7_PAT` | 3 | 🟢 سالم |
| 25 | `Acc8-R1` | ACC8 | `moradzahra85-png` | zahra | `AGW_TOKEN_ACC8` | `ACC8_PAT` | 1 | 🟢 سالم |
| 26 | `Acc8-R2` | ACC8 | `moradzahra85-png` | zahra | `AGW_TOKEN_ACC8` | `ACC8_PAT` | 2 | 🟢 سالم |
| 27 | `Acc8-R3` | ACC8 | `moradzahra85-png` | zahra | `AGW_TOKEN_ACC8` | `ACC8_PAT` | 3 | 🟢 سالم |

---

## ۳. اشکال بحرانی شناسایی‌شده و رفع آن

### شرح اشکال
در کامیت اولیه پیاده‌سازی swarm (`8fcb2a5`)، فایل ورک‌فلو `.github/workflows/agy-swarm-infinite.yml` دچار خطای تورفتگی YAML و چندخطی بودن اسکریپت شل بود:
1. در بخش پایتون استخراج زمینه، رشته‌های چندخطی با خطوط بدون تورفتگی در ستون ۱ قرار گرفته بودند که مانع از پارس شدن فایل توسط موتور گیت‌هاب می‌شد.
2. در گام ساخت پرامپت، متغیر `PROMPT` با شکست خطوط فاقد تورفتگی نوشته شده بود که ابزار `Tools/check_job_env_contexts.py` و `gate-yaml-lint.yml` آن را به عنوان خطای غیرقابل کامپایل گزارش می‌کردند.

### اقدام اصلاحی
- اسکریپت استخراج زمینه به بلوک تمیز با `\n` تبدیل شد.
- ساخت فایل پرامپت `/tmp/agy_prompt.txt` به یک بلوک پایتون ایزوله و مستقل از مشکلات شل و کاراکترهای خاص منتقل شد.
- دستور `printf '\n\n@agy-swarm\n'` اصلاح گردید.
- پس از اعمال اصلاحات، تمامی ۵۹ فایل ورک‌فلو در گیت‌هاب اکشنز اعتبارسنجی شدند و خطای کامپایل به صفر رسید.

---

## ۴. نتایج اعتبارسنجی و تست‌ها (Verification Results)

1. **بررسی گیت‌های پایداری و زمینه‌ها:**
   ```bash
   python3 Tools/check_job_env_contexts.py
   # نتیجه: 59 workflow file(s) scanned, 0 violation(s), discriminates=True (PASS)
   ```

2. **بررسی نحوی فایل‌های YAML (Gate 1):**
   ```bash
   python3 Tools/probe_gate_yaml_lint_check2.py
   # نتیجه: PASS (همه فایل‌ها فاقد خطای تورفتگی یا پارس هستند)
   ```

3. **بررسی سلامت ۲۷ واحد رانر:**
   ```bash
   python3 Tools/check_runners_health.py
   # نتیجه: 27/27 Runner Units Registered & Configured, Scope Verified, Model Verified (PASS)
   ```

4. **سیاست زیرساخت اجرا (Gate 4 runs-on):**
   - تمامی ورک‌فلوهای مانیتورینگ روی `ubuntu-latest` قفل هستند و هیچ وابستگی به رانرهای غیرفعال خودمیزبان (Arvan pool) ندارند.

---

## ۵. دستورالعمل‌های راهبری سیستم ۲۷ رانر

- **توقف موقت چرخه Swarm:** ارسال کامنت حاوی `@agy-stop`، `@agy-pause` یا `#pause` در ایشو، چرخه را با پیام هشدار متوقف می‌کند.
- **ادامه چرخه:** ارسال کامنت حاوی `@agy-swarm` نوبت بعدی را به یکی از ۲۷ رانر واگذار می‌کند.
- **مداخله مدیریت (Director Intervention):** کامنت‌های ثبت‌شده توسط `mohammadlali0707-stack` یا با تگ `[DIRECTOR` بالاترین اولویت اجرایی را در پرامپت تمامی ۲۷ رانر دارا هستند.
- **مدیریت دارایی‌های چندرسانه‌ای:** در صورت نیاز به تصویر یا ویدیو، رانرها تنها خط `##MEDIA_REQUEST##` را چاپ می‌کنند تا فرآیند به صورت نظارت‌شده از طریق `media-request-bot.yml` دنبال شود.
