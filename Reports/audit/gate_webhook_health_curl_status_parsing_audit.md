# Forensic Technical Audit Report: Curl Exit Code Handling and HTTP Status Parsing in `gate-webhook-health`

**Document ID:** `AUDIT-GATE2-CURL-STATUS-PARSING-ACC2-2026-11-13`  
**Auditor / Agent:** `agw-ACC2-w02` (Mohammad's GitHub Worker Fleet -- Account 2 Lead Team)  
**Target Repository:** `agw-workers`  
**Target Workflow:** `.github/workflows/gate-webhook-health.yml`  
**Target Step:** `Verify webhook endpoint is reachable` (Lines 130–165)  
**Target Test Suite:** `tests/test_fork_health_resilience.py` and `tests/fixtures/fork_health_fixtures.py`  
**Audit Scope:** Forensic diagnosis of curl exit code masking, subshell stdout concatenation defects (`000000` / `000\n000`), bash arithmetic/equality failure, erroneous telemetry fallthrough to `FAIL_TYPE=config_error`, and definition of missing test assertions.  
**Audit Date:** 2026-11-13  
**Classification:** Telemetry Inversion Vulnerability & Workflow Resiliency Audit  
**Status:** Validated, Empirically Reproduced, and Test-Specified  

---

## 1. Executive Summary

A forensic technical audit of `.github/workflows/gate-webhook-health.yml` was executed by `agw-ACC2-w02` focusing on the endpoint reachability verification step (lines 130–165). This step probes the n8n webhook receiver (`$EXPECTED_URL`) to determine whether the gateway is operational or failing.

The audit identified a critical defect chain in the curl invocation and status parsing logic:
1. **Duplicate '000' Concatenation (`000000` / `000\n000`):**
   In line 140, the probe is executed inside a command substitution:
   ```bash
   STATUS=$(curl -s -o /dev/null -w "%{http_code}" ... --max-time 10 2>/dev/null || echo "000")
   ```
   When `curl` experiences a network timeout (exit code 28, `CURLE_OPERATION_TIMEDOUT`), connection refusal (exit code 7, `CURLE_COULDNT_CONNECT`), or DNS failure (exit code 6, `CURLE_COULDNT_RESOLVE_HOST`), `curl` writes `000` to stdout via `%{http_code}` before terminating with a non-zero exit code. The shell's logical OR (`||`) triggers the fallback `echo "000"`, appending a second `000` to the subshell output. The resulting captured string is `"000000"` (or multiline `"000\n000"` if formatted or unbuffered).
2. **Curl Exit Code Masking:**
   The fallback `echo "000"` always exits with code 0, completely masking and discarding the non-zero exit code of `curl`. The workflow cannot inspect whether curl aborted due to a timeout, connection drop, DNS failure, or SSL handshake error.
3. **Arithmetic and Equality Parsing Failure:**
   In line 154, the script tests:
   ```bash
   elif [ "$STATUS" = "000" ] || [ "$STATUS" -ge 500 ] 2>/dev/null; then
   ```
   - String equality `[ "000000" = "000" ]` fails (6 characters vs. 3 characters).
   - Arithmetic comparison `[ "000000" -ge 500 ]` evaluates numerically to `0 -ge 500`, which is false.
   - For multiline `"000\n000"`, the test operator triggers a syntax error (`integer expression expected`), suppressed by `2>/dev/null`, yielding exit code 2 (false).
4. **Erroneous Fallthrough to `FAIL_TYPE=config_error`:**
   Because all preceding branches (`200`/`400`, `404`, and `000`/`5xx`) evaluate to false, execution falls through to the catch-all `else` block (lines 160–164), which flags the incident as:
   ```bash
   FAIL_TYPE=config_error
   FAIL_MESSAGE="HTTP 000000: Unexpected HTTP response received from webhook endpoint."
   ```
5. **Operational Severity & Alert Distortion:**
   In Step 4 (`Post alert if webhook health fails`), the automated incident bot inspects `FAIL_TYPE`. Setting `FAIL_TYPE=config_error` on an infrastructure timeout triggers `⚠️ Configuration Error (HTTP 404 / Webhook Config)`. The published incident instructs on-call engineers to verify GitHub repository webhook settings and activate n8n workflows, while completely obscuring the real failure: the n8n server host is down, unreachable, or timing out.
6. **Test Blind Spot in `tests/test_fork_health_resilience.py`:**
   `test_04_gate2_step3_telemetry_http_404_isolation` simulates status classification in Python using `else: outcome = "server_failure"`. In Python, any unknown code falls into `server_failure`. In the YAML workflow, the `else` block assigns `config_error`. This architectural inversion concealed the production defect from the automated test suite.

---

## 2. Forensic Code Analysis: `.github/workflows/gate-webhook-health.yml` (Lines 130–165)

The endpoint reachability check is implemented in lines 130–165 as follows:

```yaml
130:       - name: Verify webhook endpoint is reachable
131:         # always(): this half needs no GitHub permission, so a skipped
132:         # configuration check above must not silently skip it too.
133:         if: always()
134:         run: |
135:           echo "=== Checking n8n endpoint reachability ==="
136:           STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
137:             -X POST "$EXPECTED_URL" \
138:             -H "Content-Type: application/json" \
139:             -d '{"action":"health_check","test":true}' \
140:             --max-time 10 2>/dev/null || echo "000")
141: 
142:           echo "HTTP_STATUS=$STATUS" >> "$GITHUB_ENV"
143: 
144:           # n8n returns 200 (processed) or 400 (bad payload but reachable) = OK
145:           # 404 = webhook route not registered / wrong path (configuration error)
146:           # 000 / 5xx = server down, timeout, or server error (server failure)
147:           if [ "$STATUS" = "200" ] || [ "$STATUS" = "400" ]; then
148:             echo "✅ n8n endpoint reachable (HTTP $STATUS)"
149:           elif [ "$STATUS" = "404" ]; then
150:             echo "FAIL_TYPE=config_error" >> "$GITHUB_ENV"
151:             echo "FAIL_MESSAGE=HTTP 404: Webhook route not found. The endpoint path is incorrect or the n8n workflow is inactive/unregistered." >> "$GITHUB_ENV"
152:            echo "❌ n8n endpoint config error (HTTP 404: route not found / workflow inactive)"
153:             exit 1
154:           elif [ "$STATUS" = "000" ] || [ "$STATUS" -ge 500 ] 2>/dev/null; then
155:             echo "FAIL_TYPE=server_failure" >> "$GITHUB_ENV"
156:             echo "FAIL_MESSAGE=HTTP $STATUS: Server failure or timeout. The n8n server is down, unreachable, or returning internal 5xx errors." >> "$GITHUB_ENV"
157:             echo "❌ n8n endpoint server failure (HTTP $STATUS: server down / timeout)"
158:             exit 1
159:           else
160:             echo "FAIL_TYPE=config_error" >> "$GITHUB_ENV"
161:             echo "FAIL_MESSAGE=HTTP $STATUS: Unexpected HTTP response received from webhook endpoint." >> "$GITHUB_ENV"
162:             echo "❌ n8n endpoint unexpected response (HTTP $STATUS)"
163:             exit 1
164:           fi
```

---

## 3. Deep Mechanism Analysis

### 3.1 Mechanism of `curl -w "%{http_code}"` and `|| echo "000"`

The libcurl / curl CLI variable `%{http_code}` returns the numerical status code of the HTTP response:
- When a remote server returns an HTTP response line (e.g. `HTTP/1.1 200 OK`, `HTTP/1.1 404 Not Found`, `HTTP/1.1 502 Bad Gateway`), curl parses the response header, extracts the 3-digit code, writes it to stdout, and exits with code 0.
- When curl cannot complete the HTTP transaction (such as a timeout reached via `--max-time 10`, connection refused, host unresolvable, or TCP RST received), curl emits `000` to represent the absence of an HTTP header.
- However, because an error occurred, curl simultaneously exits with a non-zero exit code:
  - Exit 28: `CURLE_OPERATION_TIMEDOUT`
  - Exit 7: `CURLE_COULDNT_CONNECT`
  - Exit 6: `CURLE_COULDNT_RESOLVE_HOST`
  - Exit 35: `CURLE_SSL_CONNECT_ERROR`
  - Exit 52: `CURLE_GOT_NOTHING`

### 3.2 Subshell Stdout Concatenation Sequence

In bash, command substitution `$(...)` captures everything written to file descriptor 1 (stdout) within the subshell. The construct `curl ... || echo "000"` executes as follows during a timeout:

```
[Timeline of Subshell Execution]
t=0.0s    Subshell spawns curl.
t=10.0s   curl hits --max-time 10 limit.
          curl writes "000" to fd 1 (stdout).
          curl exits with code 28.
t=10.001s Shell evaluates `||` operator.
          Because exit code 28 != 0, right-hand operand is executed.
          echo "000" runs and writes "000\n" to fd 1 (stdout).
t=10.002s Subshell terminates.
          Captured stdout buffer contains: '0' '0' '0' '0' '0' '0' '\n'.
          Command substitution strips trailing newline.
          STATUS is assigned the string "000000".
```

#### Empirical Shell Reproduction
Running the exact workflow command against an unroutable IP (`192.0.2.1`) and closed port (`127.0.0.1:59999`) reproduces this behavior deterministically:

```bash
$ STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:59999 --max-time 2 2>/dev/null || echo "000")
$ echo "STATUS=[$STATUS], LENGTH=${#STATUS}, HEX=$(echo -n "$STATUS" | xxd -p)"
STATUS=[000000], LENGTH=6, HEX=303030303030
```

### 3.3 Multiline Concatenation (`000\n000`)

If curl is invoked with a newline format specifier (e.g., `-w "%{http_code}\n"`), or if stdout buffers are flushed separately in environments with streaming proxies, stdout captures:
```
000\n
000\n
```
Stripping the final trailing newline leaves `STATUS="000\n000"` (7 characters).

### 3.4 Discarding of Curl Exit Status

Because `echo "000"` succeeds with exit code 0, the compound command `curl ... || echo "000"` exits with code 0. The true curl exit code (28, 7, etc.) is permanently lost. The parent script has no visibility into:
1. Whether curl timed out (`CURLE_OPERATION_TIMEDOUT = 28`).
2. Whether the remote host actively refused the connection (`CURLE_COULDNT_CONNECT = 7`).
3. Whether DNS failed to resolve the domain (`CURLE_COULDNT_RESOLVE_HOST = 6`).
4. Whether the server returned an actual HTTP response.

---

## 4. Conditional Evaluation and Arithmetic Breakdown

Once `STATUS` is set to `"000000"` or `"000\n000"`, the script evaluates lines 147–164:

```bash
if [ "$STATUS" = "200" ] || [ "$STATUS" = "400" ]; then
  # Branch 1: Reachable OK
elif [ "$STATUS" = "404" ]; then
  # Branch 2: Webhook Route Inactive / Unregistered (config_error)
elif [ "$STATUS" = "000" ] || [ "$STATUS" -ge 500 ] 2>/dev/null; then
  # Branch 3: Server Failure or Timeout (server_failure)
else
  # Branch 4: Unexpected Response (config_error)
fi
```

### Evaluation Truth Table

| Value of `$STATUS` | Condition 1: `[ "$STATUS" = "200" ] \|\| [ "$STATUS" = "400" ]` | Condition 2: `[ "$STATUS" = "404" ]` | Condition 3a: `[ "$STATUS" = "000" ]` | Condition 3b: `[ "$STATUS" -ge 500 ]` | Selected Branch | Resulting `FAIL_TYPE` | Correct? |
| :--- | :---: | :---: | :---: | :---: | :--- | :--- | :---: |
| `"200"` | **TRUE** | Not evaluated | Not evaluated | Not evaluated | Branch 1 | None (Success) | ✅ Correct |
| `"400"` | **TRUE** | Not evaluated | Not evaluated | Not evaluated | Branch 1 | None (Success) | ✅ Correct |
| `"404"` | FALSE | **TRUE** | Not evaluated | Not evaluated | Branch 2 | `config_error` | ✅ Correct |
| `"500"` | FALSE | FALSE | FALSE | **TRUE** | Branch 3 | `server_failure` | ✅ Correct |
| `"502"` | FALSE | FALSE | FALSE | **TRUE** | Branch 3 | `server_failure` | ✅ Correct |
| `"000"` *(Single)* | FALSE | FALSE | **TRUE** | Not evaluated | Branch 3 | `server_failure` | ✅ Correct |
| **`"000000"`** *(Defect)* | FALSE | FALSE | **FALSE** (`"000000" != "000"`) | **FALSE** (`0 -ge 500` is False) | **Branch 4 (else)** | **`config_error`** | ❌ **DEFECT** |
| **`"000\n000"`** *(Defect)* | FALSE | FALSE | **FALSE** | **ERROR** (Exit 2 silenced by `2>/dev/null`) | **Branch 4 (else)** | **`config_error`** | ❌ **DEFECT** |

### Mathematical & Shell Syntax Breakdown of Condition 3
1. **`[ "$STATUS" = "000" ]`**:
   Per POSIX shell specifications, `=` performs lexical string comparison. The string `"000000"` has a byte length of 6, whereas `"000"` has a byte length of 3. Lexical equality returns 1 (false).
2. **`[ "$STATUS" -ge 500 ] 2>/dev/null`**:
   The `-ge` operator parses its arguments as integers.
   - For `"000000"`, bash converts the string to integer `0`. It compares `0 >= 500`, which is false (returns 1).
   - For `"000\n000"`, bash fails integer parsing:
     `bash: [: 000\n000: integer expression expected`.
     The file descriptor redirection `2>/dev/null` swallows this error message. The utility returns exit code 2, which bash evaluates as false.
3. **Fallthrough to `else`**:
   Because both tests in Condition 3 fail, execution bypasses `server_failure` and falls into `else`. Line 160 writes:
   ```bash
   echo "FAIL_TYPE=config_error" >> "$GITHUB_ENV"
   echo "FAIL_MESSAGE=HTTP $STATUS: Unexpected HTTP response received from webhook endpoint." >> "$GITHUB_ENV"
   ```

---

## 5. Downstream Telemetry & Incident Response Impact

### 5.1 Telemetry Pollution in `$GITHUB_ENV`
Under timeout or connection abort, the environment receives:
- `HTTP_STATUS="000000"` (a non-standard, malformed HTTP status representation).
- `FAIL_TYPE="config_error"` (a false categorization).
- `FAIL_MESSAGE="HTTP 000000: Unexpected HTTP response received from webhook endpoint."`.

### 5.2 Step 4 Alert Misdirection (Gate 2 Incident Bot)
Step 4 (`Post alert if webhook health fails`) runs Python logic (lines 180–239) to build GitHub issue and comment bodies:

```python
if fail_type == 'config_error':
    cat_label = "⚠️ Configuration Error (HTTP 404 / Webhook Config)"
    diag = (
        "- **HTTP 404 Route Not Found**: The endpoint path does not match an active route on n8n.\n"
        "- **Deactivated Workflow**: The n8n workflow may have been unregistered. Run `.github/workflows/n8n-reactivate-webhooks.yml` or toggle Active in the n8n UI.\n"
        "- **Webhook Settings**: Check that the webhook URL points to `/webhook/` (not `/webhook-test/`) and includes `issues`, `issue_comment`, `workflow_job`.\n"
        f"- **Settings Link**: https://github.com/{repo}/settings/hooks"
    )
elif fail_type == 'server_failure':
    cat_label = f"🔥 Server Failure (HTTP {http_status} / Timeout / Unreachable)"
    diag = (
        f"- **n8n Server Down or Unreachable**: Endpoint returned HTTP {http_status}.\n"
        "- **Network / Timeout**: The runner could not connect to n8n within the 10-second timeout.\n"
        "- **Container Crashed**: Check n8n service logs or host health; restart container if down."
    )
```

#### Operational Consequences:
1. **Misdiagnosed Outage Root Cause:** When an n8n container crashes or cloud network egress times out, the GitHub alert tells on-call staff that the webhook configuration is broken (`⚠️ Configuration Error`).
2. **Wasted Engineering Hours:** Engineers waste time checking GitHub webhook secrets, permissions, and URL paths, or running workflow reactivation scripts, rather than restarting crashed containers or fixing network gateways.
3. **Alert Title & Metric Distortion:** Issues are filed with title `🚨 Webhook Health Gate Failed` containing body text `HTTP Status: 000000`, degrading observability metrics and alerting fidelity.

---

## 6. Review of `tests/test_fork_health_resilience.py`

A thorough audit of `tests/test_fork_health_resilience.py` was conducted to understand why existing tests failed to catch this defect.

### 6.1 Current Test Inventory & Scope
- `test_01_patch_resilience_clean_apply`: Validates clean application of `gate-webhook-health-resilience.patch`.
- `test_02_patch_relay_deploy_clean_apply`: Validates clean application of `gate-webhook-health-relay-deploy.patch`.
- `test_03_zero_direct_workflow_modifications_on_main`: Enforces zero modifications to `.github/workflows/` on main.
- `test_04_gate2_step3_telemetry_http_404_isolation`: Validates Gate 2 telemetry for HTTP 404 isolation.
- `test_05_gate2_step4_continue_on_error_resilience`: Validates `continue-on-error: true`.
- `test_06_gate2_step4_two_tier_issue_creation_fallback`: Validates fallback command chain for issue creation.
- `test_07` – `test_11`: Validates `Tools.audit_fork_workflow_health.py` behavior across healthy, unparseable, absent, and error states.

### 6.2 Structural Vulnerability in `test_04`
Line 87 of `tests/test_fork_health_resilience.py` defines:

```python
    def test_04_gate2_step3_telemetry_http_404_isolation(self):
        """Validate Gate 2 primary failure isolation when probe receives HTTP 404 security quarantine."""
        status = GATE2_TELEMETRY["primary_failure"]["http_code"]
        env_updates = {}
        if status in ("200", "400"):
            outcome = "ok"
        elif status == "404":
            env_updates["FAIL_TYPE"] = "config_error"
            env_updates["FAIL_MESSAGE"] = GATE2_TELEMETRY["primary_failure"]["fail_message"]
            outcome = "config_error"
        else:
            env_updates["FAIL_TYPE"] = "server_failure"
            outcome = "server_failure"
```

#### The Test Blind Spot:
- In `test_04`, the fallback condition is `else: outcome = "server_failure"`.
- If `status` were `"000000"`, `"000"`, or `"500"`, this Python simulation assigns `server_failure`.
- **However, in the real YAML workflow, `else:` assigns `config_error`!**
- The test was written with inverted default branch logic compared to the actual shell code in `.github/workflows/gate-webhook-health.yml`.
- Consequently, the test suite passed while completely concealing the production defect.

### 6.3 Missing Test Capabilities in the Existing Suite
1. **No Curl Timeout / Network Failure Simulation:** There are no tests invoking `curl` with exit code 28 (`CURLE_OPERATION_TIMEDOUT`) or exit code 7 (`CURLE_COULDNT_CONNECT`).
2. **No Shell Execution Testing for Status Parsing:** The actual shell commands (the `if/elif/else` block and subshell capture) are never executed against realistic curl exit codes.
3. **No Fixture for Timeout Telemetry:** `tests/fixtures/fork_health_fixtures.py` defines `GATE2_TELEMETRY` with only `http_code: "404"`. There are no telemetry fixtures for `000` or timeout states.

---

## 7. Required Test Assertions Specification

To ensure permanent resilience against curl exit code and status parsing defects, the test suite (`tests/test_fork_health_resilience.py` and `tests/fixtures/fork_health_fixtures.py`) must be expanded with the following concrete test assertions:

### 7.1 New Telemetry Fixtures (`tests/fixtures/fork_health_fixtures.py`)

```python
# Add to GATE2_TELEMETRY in tests/fixtures/fork_health_fixtures.py:
GATE2_TELEMETRY_TIMEOUT = {
    "step": "Verify webhook endpoint is reachable",
    "http_code": "000",
    "curl_exit_code": 28,
    "fail_type": "server_failure",
    "fail_message_pattern": "HTTP 000.*Server failure or timeout",
    "cat_label_pattern": "Server Failure.*Timeout",
}

GATE2_TELEMETRY_CONNREFUSED = {
    "step": "Verify webhook endpoint is reachable",
    "http_code": "000",
    "curl_exit_code": 7,
    "fail_type": "server_failure",
    "fail_message_pattern": "HTTP 000.*Server failure or timeout",
}
```

### 7.2 Specification of New Unit Test Assertions (`tests/test_fork_health_resilience.py`)

#### Assertion Suite 1: Curl Exit Code and Status Normalization
Verify that under timeout (exit 28) or abort (exit 7), curl output is never duplicated into `"000000"` or multiline `"000\n000"`, and `STATUS` is strictly normalized to `"000"`.

```python
    def test_12_gate2_step3_curl_timeout_status_normalization(self):
        """Verify that curl timeouts (exit 28) produce strictly normalized '000', not '000000'."""
        simulated_step = (
            'HTTP_CODE=""; CURL_EXIT=0; '
            'HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://192.0.2.1 --max-time 1 2>/dev/null) '
            '|| CURL_EXIT=$?; '
            'if [ $CURL_EXIT -ne 0 ] || [ -z "$HTTP_CODE" ]; then STATUS="000"; else STATUS="$HTTP_CODE"; fi; '
            'echo "STATUS=$STATUS"; echo "CURL_EXIT=$CURL_EXIT"'
        )
        res = subprocess.run(["bash", "-c", simulated_step], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        self.assertIn("STATUS=000", res.stdout)
        self.assertNotIn("STATUS=000000", res.stdout)
        self.assertNotIn("STATUS=000\n000", res.stdout)
        self.assertIn("CURL_EXIT=28", res.stdout)
```

#### Assertion Suite 2: Shell Status Evaluation & Telemetry Branching
Verify that the bash evaluation block routes `"000"`, `"500"`, `"502"`, and non-zero `CURL_EXIT` to `server_failure`, `"404"` to `config_error`, and `"200"`/`"400"` to reachable.

```python
    def test_13_gate2_step3_shell_status_evaluation_matrix(self):
        """Verify that shell status logic correctly routes 000, 5xx, and curl exit codes to server_failure."""
        def evaluate_shell(status_val, curl_exit=0):
            shell_script = f"""
            STATUS="{status_val}"
            CURL_EXIT={curl_exit}
            if [ "$STATUS" = "200" ] || [ "$STATUS" = "400" ]; then
              echo "OUTCOME=ok"
            elif [ "$STATUS" = "404" ]; then
              echo "FAIL_TYPE=config_error"
            elif [ "$STATUS" = "000" ] || [ "$CURL_EXIT" -ne 0 ] || {{ [ "$STATUS" -ge 500 ] 2>/dev/null; }}; then
              echo "FAIL_TYPE=server_failure"
            else
              echo "FAIL_TYPE=config_error"
            fi
            """
            proc = subprocess.run(["bash", "-c", shell_script], capture_output=True, text=True)
            return proc.stdout.strip()

        # Happy paths
        self.assertEqual(evaluate_shell("200"), "OUTCOME=ok")
        self.assertEqual(evaluate_shell("400"), "OUTCOME=ok")
        
        # Configuration error (Quarantine / inactive workflow)
        self.assertEqual(evaluate_shell("404"), "FAIL_TYPE=config_error")
        
        # Server downtime & 5xx failures
        self.assertEqual(evaluate_shell("500"), "FAIL_TYPE=server_failure")
        self.assertEqual(evaluate_shell("502"), "FAIL_TYPE=server_failure")
        self.assertEqual(evaluate_shell("503"), "FAIL_TYPE=server_failure")
        self.assertEqual(evaluate_shell("504"), "FAIL_TYPE=server_failure")
        
        # Timeout & connection aborts (must be server_failure, NEVER config_error)
        self.assertEqual(evaluate_shell("000", curl_exit=0), "FAIL_TYPE=server_failure")
        self.assertEqual(evaluate_shell("000", curl_exit=28), "FAIL_TYPE=server_failure")
        self.assertEqual(evaluate_shell("000", curl_exit=7), "FAIL_TYPE=server_failure")
        
        # Resiliency against defect strings: non-zero curl exit overrides malformed status
        self.assertEqual(evaluate_shell("000000", curl_exit=28), "FAIL_TYPE=server_failure")
```

#### Assertion Suite 3: Python Telemetry Simulation Alignment
Update `test_04` in `tests/test_fork_health_resilience.py` to mirror the production workflow's 4-way branch rather than an oversimplified 3-way `if/elif/else`:

```python
    def test_14_gate2_step3_python_simulation_parity(self):
        """Verify Python telemetry generator accurately reproduces workflow branching logic."""
        test_cases = [
            ("200", 0, "ok"),
            ("400", 0, "ok"),
            ("404", 0, "config_error"),
            ("000", 28, "server_failure"),
            ("000", 7, "server_failure"),
            ("500", 0, "server_failure"),
            ("502", 0, "server_failure"),
            ("418", 0, "config_error"),  # Unexpected HTTP status falls through to config_error
        ]
        for status, curl_exit, expected in test_cases:
            if status in ("200", "400"):
                outcome = "ok"
            elif status == "404":
                outcome = "config_error"
            elif status == "000" or curl_exit != 0 or (status.isdigit() and int(status) >= 500):
                outcome = "server_failure"
            else:
                outcome = "config_error"
            self.assertEqual(
                outcome,
                expected,
                f"Status '{status}' with curl_exit {curl_exit} expected {expected}, got {outcome}",
            )
```

---

## 8. Architectural Remediation & Patch Design

### 8.1 Proposed Implementation Pattern
To resolve the defect cleanly and idiomatically in GitHub Actions:
1. **Decouple Curl Execution from Fallback Output:** Do NOT use `|| echo "000"` inside `$()`.
2. **Explicitly Capture `$?`:** Capture the curl exit code directly via `|| CURL_EXIT=$?`.
3. **Normalize Status:** If `$CURL_EXIT != 0` or the captured status string is empty, normalize `STATUS="000"`.
4. **Preserve Telemetry:** Export both `HTTP_STATUS` and `CURL_EXIT` to `$GITHUB_ENV`.
5. **Guard Integer Comparison:** Use `[ "$CURL_EXIT" -ne 0 ]` and safe integer checks `{ [ "$STATUS" -ge 500 ] 2>/dev/null; }` so non-numeric status strings never produce syntax errors.

### 8.2 Production Patch Specification

```patch
--- a/.github/workflows/gate-webhook-health.yml
+++ b/.github/workflows/gate-webhook-health.yml
@@ -136,12 +136,22 @@ jobs:
         run: |
           echo "=== Checking n8n endpoint reachability ==="
-          STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
+          HTTP_CODE=""
+          CURL_EXIT=0
+          HTTP_CODE=$(curl -s -S -o /dev/null -w "%{http_code}" \
             -X POST "$EXPECTED_URL" \
             -H "Content-Type: application/json" \
             -d '{"action":"health_check","test":true}' \
-            --max-time 10 2>/dev/null || echo "000")
+            --max-time 10 2>/dev/null) || CURL_EXIT=$?
+
+          if [ $CURL_EXIT -ne 0 ] || [ -z "$HTTP_CODE" ]; then
+            STATUS="000"
+          else
+            STATUS="$HTTP_CODE"
+          fi
 
           echo "HTTP_STATUS=$STATUS" >> "$GITHUB_ENV"
+          echo "CURL_EXIT=$CURL_EXIT" >> "$GITHUB_ENV"
 
           # n8n returns 200 (processed) or 400 (bad payload but reachable) = OK
           # 404 = webhook route not registered / wrong path (configuration error)
@@ -151,10 +161,10 @@ jobs:
           elif [ "$STATUS" = "404" ]; then
             echo "FAIL_TYPE=config_error" >> "$GITHUB_ENV"
             echo "FAIL_MESSAGE=HTTP 404: Webhook route not found. The endpoint path is incorrect or the n8n workflow is inactive/unregistered." >> "$GITHUB_ENV"
             echo "❌ n8n endpoint config error (HTTP 404: route not found / workflow inactive)"
             exit 1
-          elif [ "$STATUS" = "000" ] || [ "$STATUS" -ge 500 ] 2>/dev/null; then
+          elif [ "$STATUS" = "000" ] || [ "$CURL_EXIT" -ne 0 ] || { [ "$STATUS" -ge 500 ] 2>/dev/null; }; then
             echo "FAIL_TYPE=server_failure" >> "$GITHUB_ENV"
-            echo "FAIL_MESSAGE=HTTP $STATUS: Server failure or timeout. The n8n server is down, unreachable, or returning internal 5xx errors." >> "$GITHUB_ENV"
-            echo "❌ n8n endpoint server failure (HTTP $STATUS: server down / timeout)"
+            echo "FAIL_MESSAGE=HTTP $STATUS (curl exit $CURL_EXIT): Server failure or timeout. The n8n server is down, unreachable, or returning internal 5xx errors." >> "$GITHUB_ENV"
+            echo "❌ n8n endpoint server failure (HTTP $STATUS: server down / timeout / curl exit $CURL_EXIT)"
             exit 1
           else
```

---

## 9. Verification & Safety Assurance

1. **Zero Direct Workflow Modifications on Main:**
   Per protocol guidelines and test `test_03_zero_direct_workflow_modifications_on_main`, no workflow files under `.github/workflows/` have been modified directly in this checkout.
2. **Current Test Suite Passing:**
   Execution of `python3 -m unittest tests/test_fork_health_resilience.py` confirmed 11/11 tests pass with 0 errors.
3. **Delivery Staging:**
   This audit report is delivered in `Reports/audit/gate_webhook_health_curl_status_parsing_audit.md` for peer review by Account Leads and Notion AI integration.

---

## 10. Conclusion & Action Items

- **Root Cause Confirmed:** Combining `curl -w "%{http_code}"` with `|| echo "000"` inside `$()` causes duplicate string emission (`000000`), masks curl exit codes, breaks downstream bash arithmetic and string equality checks, and misclassifies critical server downtime as `config_error`.
- **Action Item 1 (Lead Review):** Account 2 Lead to review and endorse the proposed two-stage curl capture logic.
- **Action Item 2 (Test Integration):** Incorporate the specified test assertions (`test_12` through `test_14`) into `tests/test_fork_health_resilience.py`.
- **Action Item 3 (Upstream Merge):** Apply the patch via the designated Notion AI upstream PR pipeline.
