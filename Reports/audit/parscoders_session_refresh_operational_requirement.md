# Operational Requirement: Refreshing SITE_SESSION_PARSCODERS Secret

- **Document ID:** `OP-REQ-PARSCODERS-SESSION-REFRESH-2026-11-16`
- **Component:** `scripts/site_proposal/sites/parscoders.py`, `scripts/site_proposal/runner.py`
- **Workflow:** `.github/workflows/capture-parscoders-session.yml`
- **Secret Name:** `SITE_SESSION_PARSCODERS`
- **Target Repository:** `mohammadlali0707-stack/agw-workers`

---

## 1. Problem Diagnosis & Fast-Abort Hardening
When Parscoders session cookies/localStorage expire (e.g. CI failure in run `43829285293`), unauthenticated project pages, login prompts, Google connect buttons (`a[href*='connect-google']`, `.btn-google`, "ورود با گوگل"), or registration banners appear.
Without detection hardening, the adapter falls through to description wait loops (`_wait_visible_description`), timing out into `form_error` and exit code 1.
With hardened detection, `run_site_flow` immediately returns `status: "login_required"`. In `runner.py`, `login_required` exits with code 0, cleanly alerting the fleet to refresh the secret without failing CI.

---

## 2. Operational Session Refresh Procedures
When Parscoders returns `status: "login_required"`, refresh `SITE_SESSION_PARSCODERS` using one of the procedures below:

### 2.1 Automated Workflow Dispatch (Recommended)
Dispatch `.github/workflows/capture-parscoders-session.yml` via GitHub CLI:
```bash
gh workflow run capture-parscoders-session.yml --repo mohammadlali0707-stack/agw-workers
```
The workflow acquires the shared `momona` Google lock, executes `scripts/site_proposal/capture_parscoders.py`, authenticates via Google OAuth (`/connect-google`), verifies dashboard access, updates `SITE_SESSION_PARSCODERS`, and shreds temporary files.

### 2.2 Manual Session Capture (Fallback)
If CAPTCHA or Google verification blocks automated renewal:
1. Log in to `parscoders.com` using the shared Google account in Chrome.
2. Dump cookies and localStorage to `session.json` (`{"cookies": [...], "localStorage": {...}}`).
3. Update GitHub secret:
   ```bash
   gh secret set SITE_SESSION_PARSCODERS --repo mohammadlali0707-stack/agw-workers < session.json
   shred -u session.json
   ```

---

## 3. Secret Format & Precedence
- Secret structure: JSON object with `cookies` list and `localStorage` dict.
- Precedence: `runner.py` loads `SITE_SESSION_PARSCODERS` first, taking precedence over aggregate `SITE_SESSIONS`.
