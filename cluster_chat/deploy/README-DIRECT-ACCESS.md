# Cluster Chat: Direct-IP & National Internet (نت ملی) Resilient Access
**راهنمای دسترسی مستقیم به کلاستر چت بدون فیلترشکن و راه‌اندازی رله در شرایط اینترنت ملی**

---

## English Guide

### 1. Overview & Architecture
`cluster-chat` is fully host-agnostic and operational without requiring any VPN or depending on filtered external domains. It supports:
- **Direct IP Access**: Connect directly to `http://<VPS_IP>:8080/` (or port 80 via Nginx).
- **Domain-Free Authentication**: Session cookies omit `Domain` attributes, allowing the browser to bind cookies directly to raw IP addresses or relay hosts. Cookies only enable `Secure` when accessing over HTTPS.
- **Root-Relative Paths**: All API endpoints, message payloads, and attachment URLs use relative paths (`/api/...`, `/uploads/...`), preventing broken cross-origin requests.
- **Resilient Transport Fallback**: If WebSocket or SSE streams are blocked or interrupted, the Web UI automatically transitions to incremental HTTP polling (`GET /api/messages?since=<id>`) every 2.5–5 seconds with automatic message deduplication.
- **Plain HTTP Clipboard Compatibility**: Browsers restrict `navigator.clipboard.writeText` on non-secure contexts (`http://<IP>`). The Web UI incorporates an automatic `document.execCommand('copy')` fallback so all copy actions work seamlessly.

---

### 2. Direct VPS IP Access

#### Starting Cluster Chat Backend
Run the backend server directly or as a systemd service:
```bash
# Direct execution (default host: 0.0.0.0, default port: 8080)
python3 -m cluster_chat.server

# Or using environment variables
export CLUSTER_CHAT_HOST="0.0.0.0"
export CLUSTER_CHAT_PORT="8080"
export CLUSTER_CHAT_PUBLIC_URL="http://<YOUR_VPS_IP>:8080" # Optional: only if generating external webhook links
python3 -m cluster_chat.server
```

#### Accessing from Browser
Open your browser and navigate to:
```text
http://<YOUR_VPS_IP>:8080/
```
The header badge will display `Live (Direct)` or `Polling Fallback (Resilient)` depending on your connection capabilities.

---

### 3. Nginx Reverse Proxy on Foreign VPS (Direct IP)
To serve Cluster Chat over standard HTTP port 80 and upload large attachments (up to 50MB):

1. Copy the configuration file:
   ```bash
   sudo cp cluster_chat/deploy/nginx-direct-ip.conf /etc/nginx/conf.d/cluster-chat.conf
   ```
2. Verify configuration and reload Nginx:
   ```bash
   sudo nginx -t
   sudo systemctl reload nginx
   ```
3. Cluster Chat is now reachable at:
   ```text
   http://<YOUR_VPS_IP>/
   ```

---

### 4. Domestic Iranian Relay Setup (National Internet / نت ملی)
During strict international internet shutdowns (National Internet / نت ملی), connections from Iran to foreign VPS IP addresses may be throttled or blocked on Layer-7 / TLS, while traffic between domestic Iranian servers and foreign servers remains reachable, or port-forwarded through domestic infrastructure.

A domestic Iranian relay server forwards local Iranian traffic to the foreign VPS:

```
[User Browser in Iran]
        │
        ▼ (Domestic Intranet / نت ملی)
[Iranian Relay VPS (e.g., 185.x.x.x)]
        │
        ▼ (Outbound link to foreign VPS)
[Foreign Cluster Chat VPS (e.g., 203.0.113.10:8080)]
```

#### Automatic Relay Setup with `iran-relay-setup.sh`

Run the idempotent setup script on your **Iranian Domestic Server**:

##### Option A: socat TCP Forwarder (Recommended: Lightest & Fastest)
```bash
sudo ./cluster_chat/deploy/iran-relay-setup.sh \
  --foreign-ip <FOREIGN_VPS_IP> \
  --foreign-port 8080 \
  --local-port 8080 \
  --mode socat
```
This installs `socat` and registers an auto-restarting systemd service (`cluster-chat-relay.service`).

##### Option B: Nginx Layer-7 Reverse Proxy
```bash
sudo ./cluster_chat/deploy/iran-relay-setup.sh \
  --foreign-ip <FOREIGN_VPS_IP> \
  --foreign-port 8080 \
  --local-port 8080 \
  --mode nginx
```
This configures Nginx with WebSocket upgrade support, unbuffered SSE, and 50MB upload limits.

##### Dry-run Mode (Test without root)
```bash
./cluster_chat/deploy/iran-relay-setup.sh --foreign-ip <FOREIGN_VPS_IP> --dry-run
```

---

### 5. Health Check & Verification Commands
Test that the server or relay is functioning properly:
```bash
# 1. Health probe with raw IP Host header
curl -i -H "Host: <IP>:8080" http://<IP>:8080/api/health
# Expected: 200 OK, {"ok": true, "direct_ip_supported": true}

# 2. Incremental polling endpoint
curl -i "http://<IP>:8080/api/messages?since=0&limit=5"
# Expected: Cache-Control: no-store, 200 OK

# 3. Direct-IP health response check
curl -i http://<IP>:8080/api/health
# Expected: zero redirects, direct_ip_supported: true
```

---

## راهنمای فارسی (Persian Guide)

### ۱. معماری و دسترسی مستقیم بدون وی‌پی‌ان
سیستم `cluster-chat` به صورت کامل مستقل از دامنه (`Host-Agnostic`) طراحی شده و هیچ‌گونه وابستگی به دامنه‌های خارجی و فیلترشده ندارد:
- **دسترسی مستقیم با IP سرور**: شما می‌توانید بدون نیاز به هیچ دامنه‌ای، مستقیماً از طریق `http://<VPS_IP>:8080/` به چت دسترسی داشته باشید.
- **کوکی‌ها و نشست کاربر بدون محدودیت دامنه**: کوکی‌های احراز هویت بدون خصیصه `Domain` تنظیم می‌شوند تا مرورگر بتواند روی هر IP خام یا سرور رله بدون خطا کار کند. خصیصه `Secure` نیز فقط در صورت فعال بودن HTTPS فعال می‌گردد.
- **مسیرهای نسبی در همه بخش‌ها**: تمام آدرس‌های وب‌سوکت، رویدادها، پیام‌ها و فایل‌های ضمیمه به شکل مسیر نسبی (`/api/...` و `/uploads/...`) تولید می‌شوند.
- **فال‌بک خودکار به پولینگ (Polling)**: در صورت اختلال در وب‌سوکت (WS) یا رویدادهای زنده (SSE)، کلاینت به شکل خودکار پس از چند ثانیه به حالت پولینگ (`GET /api/messages?since=...`) تغییر وضعیت می‌دهد تا پیام‌ها بدون قطعی مبادله شوند.
- **کپی در کلیپ‌بورد روی HTTP**: برای برطرف کردن محدودیت امنیتی مرورگرها در بستر HTTP خام، یک سازوکار جایگزین خودکار (`document.execCommand`) تعبیه شده است تا دکمه‌های کپی آدرس و پیام به درستی عمل کنند.

---

### ۲. اتصال مستقیم از طریق مرورگر
روی سرور اصلی (خارج از ایران)، سرور چت را اجرا کنید:
```bash
python3 -m cluster_chat.server
```
سپس در مرورگر خود آدرس زیر را وارد کنید:
```text
http://<VPS_IP>:8080/
```
نشانگر وضعیت در هدر چت به صورت خودکار وضعیت ارتباط (`Live` یا `Polling Fallback`) را نمایش می‌دهد.

---

### ۳. راه‌اندازی رله سرور ایران در شرایط اینترنت ملی (نت ملی)
در صورت قطعی اینترنت بین‌الملل و محدود شدن ارتباط کاربران به شبکه ملی اطلاعات، می‌توانید یک سرور رله داخلی در ایران قرار دهید تا ترافیک کاربران را به سرور خارجی منتقل کند:

```
[کاربر در ایران] ──(شبکه داخلی)──> [سرور رله ایران] ──(ارتباط بین‌الملل)──> [سرور خارجی کلاستر چت]
```

#### روش راه‌اندازی با اسکریپت خودکار `iran-relay-setup.sh`
اسکریپت زیر را روی **سرور ایران** اجرا کنید:

##### روش ۱: فورواردر سبُک socat (پیشنهادی)
```bash
sudo ./cluster_chat/deploy/iran-relay-setup.sh \
  --foreign-ip <IP_سرور_خارجی> \
  --foreign-port 8080 \
  --local-port 8080 \
  --mode socat
```
این دستور بسته `socat` را نصب کرده و یک سرویس سیستمی خودکار با قابلیت شروع مجدد (`systemd`) ایجاد می‌کند.

##### روش ۲: پراکسی معکوس لایه ۷ با Nginx
```bash
sudo ./cluster_chat/deploy/iran-relay-setup.sh \
  --foreign-ip <IP_سرور_خارجی> \
  --foreign-port 8080 \
  --local-port 8080 \
  --mode nginx
```

پس از راه‌اندازی، کاربران داخل ایران با وارد کردن آدرس زیر به چت متصل می‌شوند:
```text
http://<IP_سرور_ایران>:8080/
```

---

### ۴. تست و اعتبارسنجی ارتباط
برای اطمینان از سلامت سرویس رله یا ارتباط مستقیم:
```bash
# تست هلث‌چک با IP خام
curl -i http://127.0.0.1:8080/api/health

# تست پولینگ پیام‌ها
curl -i "http://127.0.0.1:8080/api/messages?since=0&limit=5"
```
در پاسخ باید کد وضعیت `200 OK` و هدر `Cache-Control: no-store` دریافت شود.
