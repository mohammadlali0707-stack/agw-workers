# Forensic Audit: Gemini Mint Session CAPTCHA Root Cause Analysis & Verification

- **Document ID:** `AUDIT-GEMINI-MINT-CAPTCHA-2026-11-23`
- **Auditor:** `agw-ACC2-w02` (Mohammad's GitHub Worker Fleet -- Account 2 Team)
- **Target Workflow:** [.github/workflows/gemini-mint-session.yml](file:///home/runner/work/_temp/agent-repo-45166599659-1-run-agy/.github/workflows/gemini-mint-session.yml)
- **Target Tool:** [tools/gemini_mint.py](file:///home/runner/work/_temp/agent-repo-45166599659-1-run-agy/tools/gemini_mint.py)
- **Target Repository:** `mohammadlali0707-stack/agw-workers`
- **Correlated Runs:**
  - Failed Run: `45158355500` (Job `137637004127`, exit code `3`)
  - Successful Run: `45162770089` (Job `137652073022`, exit code `0`)
- **Remediation Commit:** `079f74b` (`079f74b912dfef5e9a0e81f37c6df1f9024c046e`)
- **Target Secret:** `GEMINI_WEB_COOKIES_ACC0`
- **Status:** Verified & Remediated

---

## 1. Executive Summary

During execution of [.github/workflows/gemini-mint-session.yml](file:///home/runner/work/_temp/agent-repo-45166599659-1-run-agy/.github/workflows/gemini-mint-session.yml) on run `45158355500` (account `acc0`, mode `login`), the runner browser session aborted with exit code `3` after detecting a Google login challenge (`CAPTCHA_DETECTED`). The session cookies were not sealed, leaving `GEMINI_WEB_COOKIES_ACC0` stale since `2026-11-14T19:30:49Z`.

A remediation landed in commit `079f74b` introducing three core components:
1. Chrome anti-automation flag (`--disable-blink-features=AutomationControlled`) plus organic session warm-up navigation.
2. A resilient noVNC and `cloudflared` watchdog (`check_tunnel`, `restart_tunnel`, `restart_websockify`) with automatic tunnel revival and private repo re-publishing.
3. A non-fatal challenge wait loop that freezes the timeout countdown (capped at 600s extra) to allow the owner to solve challenges manually via noVNC.

Verification against subsequent run `45162770089` demonstrates full remediation: the challenge loop triggered at `11:21:23Z`, paused the deadline without aborting, permitted manual challenge resolution over noVNC, and successfully sealed 31 fresh session cookies into `GEMINI_WEB_COOKIES_ACC0` at `2026-11-23T11:21:40Z`.

---

## 2. Forensic Analysis of Run 45158355500 Failure

### 2.1 Execution Timeline & Log Telemetry
Run `45158355500` was dispatched for `acc0` in `login` mode with `MINT_POLL_SECS=2400`:
- **10:42:17Z**: Step `Mint session in runner browser` started. Egress routed via Oracle proxy (`159.54.180.96`).
- **10:42:18Z - 10:42:19Z**: Virtual desktop stack initialized (`Xvfb` on `:99`, `x11vnc` on port `5900`, `noVNC` on port `6080`).
- **10:42:24Z**: Cloudflared tunnel established and URL published early to private repo `hq-ai-results`.
- **10:42:26Z**: Google Chrome launched and navigated directly to `https://gemini.google.com/`. Polling began.
- **10:47:29Z**: Normal heartbeat logged (`2096s left`).
- **10:49:00Z**: Detection failure occurred:
  ```text
  2026-11-23T10:49:00.7245344Z ERROR: CAPTCHA_DETECTED: Google showed a challenge; stopping without touching the secret
  2026-11-23T10:49:01.1842540Z ##[error]Process completed with exit code 3.
  ```
- **Result**: Downstream step `Seal fresh session into secret` skipped. Secret update aborted.

### 2.2 Root Cause Analysis

1. **Premature Hard Abort in Polling Loop**:
   In [tools/gemini_mint.py](file:///home/runner/work/_temp/agent-repo-45166599659-1-run-agy/tools/gemini_mint.py) prior to `079f74b`:
   ```python
   if captcha_present(page):
       fail("CAPTCHA_DETECTED: Google showed a challenge; stopping without touching the secret", code=3)
   ```
   The design mistakenly treated interactive human session minting like an unattended scraper. The entire purpose of launching a headed Chrome instance inside `Xvfb` exposed via `noVNC` is for the owner to perform human login actions. By aborting immediately upon encountering any challenge string, the script denied the human operator any window to solve the challenge.

2. **Absence of Anti-Automation Protections**:
   Chrome launched with default Playwright/CDP automation markers. Without `--disable-blink-features=AutomationControlled`, the JavaScript runtime exposed `navigator.webdriver = true`. Navigating cold to `https://gemini.google.com` over a datacenter/proxy IP immediately triggered Google's bot detection heuristics.

3. **Tunnel Fragility**:
   The polling loop lacked active tunnel health checks. If `cloudflared` or `websockify` dropped during interactive authentication, the owner lost connection without automatic tunnel recovery.

---

## 3. Code Inspection of Remediation Commit `079f74b`

Commit `079f74b` (`gemini-mint: captcha waits for owner + tunnel watchdog`) refactored [tools/gemini_mint.py](file:///home/runner/work/_temp/agent-repo-45166599659-1-run-agy/tools/gemini_mint.py):

### 3.1 Chrome Anti-Automation Flag & Warm-Up Navigation
- **Flag Addition**: Added `--disable-blink-features=AutomationControlled` to [`start_chrome()`](file:///home/runner/work/_temp/agent-repo-45166599659-1-run-agy/tools/gemini_mint.py#L355-L380), masking CDP automation markers.
- **Organic Warm-up**: In [`main()`](file:///home/runner/work/_temp/agent-repo-45166599659-1-run-agy/tools/gemini_mint.py#L525-L540), before opening Gemini, Chrome loads `https://www.google.com`, rests 8 seconds, and then proceeds to `https://gemini.google.com`, mimicking regular user flow.

### 3.2 noVNC / Cloudflared Watchdog with Automatic Revival
- **Watchdog Functions**: Implemented [`check_tunnel()`](file:///home/runner/work/_temp/agent-repo-45166599659-1-run-agy/tools/gemini_mint.py#L318-L354), [`restart_tunnel()`](file:///home/runner/work/_temp/agent-repo-45166599659-1-run-agy/tools/gemini_mint.py#L290-L317), and [`restart_websockify()`](file:///home/runner/work/_temp/agent-repo-45166599659-1-run-agy/tools/gemini_mint.py#L280-L289).
- **Periodic Check**: Every `TUNNEL_CHECK_EVERY = 4` poll iterations (~8s):
  1. Checks port `6080`; relaunches websockify if down.
  2. Checks `CF_PROC.poll()`; relaunches `cloudflared` if dead.
  3. Performs two HTTP checks 10s apart against the public URL. If dead on both, relaunches `cloudflared`, extracts the new URL, writes `/tmp/novnc-url.txt`, and immediately calls [`publish_novnc_url_early()`](file:///home/runner/work/_temp/agent-repo-45166599659-1-run-agy/tools/gemini_mint.py#L235-L260) to push the updated link to `hq-ai-results`.

### 3.3 Non-Fatal Challenge Wait Loop & Dynamic Pause
- **Expanded Challenge Detection**: [`captcha_present()`](file:///home/runner/work/_temp/agent-repo-45166599659-1-run-agy/tools/gemini_mint.py#L440-L455) checks for `"not a robot"`, `"i'm not a robot"`, `"verify it's you"`, and `"unusual traffic"`.
- **Dynamic Deadline Extension**:
  ```python
  in_challenge = captcha_present(page)
  if in_challenge and not challenge_active:
      log("CHALLENGE: Google is showing a sign-in challenge. Owner: please solve it...")
      challenge_active = True
  elif not in_challenge and challenge_active:
      log("challenge cleared; countdown resumed")
      challenge_active = False
  if in_challenge and challenge_extra < CHALLENGE_EXTRA_MAX:
      add = min(dt, CHALLENGE_EXTRA_MAX - challenge_extra)
      deadline += add
      challenge_extra += add
  ```
- **Controlled Boundary**: Extra wait time is capped by `CHALLENGE_EXTRA_MAX = 600` (10 minutes), guaranteeing the workflow completes comfortably within GitHub Actions' 55-minute job timeout.

---

## 4. Verification Against Successful Run 45162770089

Job `137652073022` executed with commit `079f74b` in place:

### 4.1 Chronological Evidence & Challenge Resolution
- **11:11:56Z**: Job initialized. Egress established via proxy `159.54.180.96`.
- **11:12:06Z**: Chrome launched with `--disable-blink-features=AutomationControlled`.
- **11:12:16Z**: Warm-up completed: `browser warmed up via google.com; on gemini.google.com`.
- **11:12:16Z**: Interactive login polling started.
- **11:17:20Z**: Heartbeat logged (`2095s left`).
- **11:21:23.788Z**: Google challenge page encountered. Wait loop correctly entered non-fatal pause mode:
  ```text
  2026-11-23T11:21:23.7888553Z CHALLENGE: Google is showing a sign-in challenge. Owner: please solve it in the browser -- the wait continues and the countdown is paused while the challenge is on screen. Nothing here touches it.
  ```
- **11:21:38.799Z**: Exactly **15.01 seconds** later, the owner completed manual challenge resolution in noVNC. The script detected valid credentials immediately:
  ```text
  2026-11-23T11:21:38.7997763Z RESULT ok cookies=31 names=ACCOUNT_CHOOSER,AEC,APISID,COMPASS,HSID,LSID,NID,OSID,OTZ,SAPISID,SEARCH_SAMESITE,SID,SIDCC,SMSV,SSID,__Host-1PLSID,__Host-3PLSID,__Host-GAPS,__Secure-1PAPISID,__Secure-1PSID,__Secure-1PSIDCC,__Secure-1PSIDTS,__Secure-3PAPISID,__Secure-3PSID,__Secure-3PSIDCC,__Secure-3PSIDTS,__Secure-OSID,_ga,_ga_BF8Q35BMLM,_ga_WC57KJ50ZZ,_gcl_au psidts=yes file=/tmp/gemini-session-acc0.json
  ```

### 4.2 Secret Sealing Verification
The workflow step `Seal fresh session into secret` executed:
- **Secret Target**: `GEMINI_WEB_COOKIES_ACC0`
- **Initial Secret Timestamp**: `SECRET_BEFORE: 2026-11-14T19:30:49Z`
- **Post-Sealing Verification**:
  ```text
  2026-11-23T11:21:40.4540943Z secret GEMINI_WEB_COOKIES_ACC0 updated_at after: 2026-11-23T11:21:40Z
  2026-11-23T11:21:40.4541506Z sealed GEMINI_WEB_COOKIES_ACC0
  ```
- **Sanitization**: Step `Shred session files and browser profile` executed `shred -u` and removed `/tmp/chrome-profile`, `/tmp/novnc`, and `/tmp/novnc-url.txt`.

---

## 5. Security & Operational Assessment

| Dimension | Observation | Assessment |
| :--- | :--- | :--- |
| **Credential Hygiene** | No raw cookie values printed; masked in logs | **COMPLIANT** |
| **URL Confidentiality** | noVNC URL committed solely to private repo `hq-ai-results` | **COMPLIANT** |
| **Automated Bypass Risk** | Script never attempts OCR or automated captcha cracking | **COMPLIANT** |
| **Timeout Safety** | Countdown pause strictly bounded by `CHALLENGE_EXTRA_MAX = 600` | **COMPLIANT** |
| **Session Integrity** | Required security cookies harvested (`__Secure-1PSIDTS`, `__Secure-3PSIDTS`) | **VERIFIED** |

---

## 6. Audit Conclusion

The forensic investigation confirms that run `45158355500` failed due to an abrupt exit code `3` on challenge detection. Commit `079f74b` completely resolved the defect by implementing the Chrome anti-automation flag, a resilient tunnel watchdog, and an interactive non-fatal challenge pause. Verification against run `45162770089` confirms successful human challenge solving and sealing of `GEMINI_WEB_COOKIES_ACC0`.
