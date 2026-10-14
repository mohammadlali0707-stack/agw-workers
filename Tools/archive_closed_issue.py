#!/usr/bin/env python3
"""Archive a CLOSED public agw-workers issue into the private CCP repo, then
remove it from public view (GraphQL deleteIssue; fallback = scrub + lock).

Env: GH_TOKEN (owner PAT), PUBLIC_REPO, CCP_DIR (checked-out CCP clone),
     ISSUES (comma list, optional), MODE (event|sweep|manual), CUTOFF (ISO),
     DELETE (true|false), DELAY_SECONDS.
Never touches issue 55 or issues labelled keep-public.
"""
import json, os, re, subprocess, sys, time, urllib.request, urllib.error

TOKEN = os.environ["GH_TOKEN"]
REPO = os.environ.get("PUBLIC_REPO", "mohammadlali0707-stack/agw-workers")
CCP = os.environ.get("CCP_DIR", "ccp")
PROTECTED = {55}
KEEP_LABEL = "keep-public"
OUT_DIR = "Reports/public_issues/agw-workers"


def api(method, url, data=None, ok=(200, 201, 204)):
    if not url.startswith("http"):
        url = "https://api.github.com" + url
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method, headers={
        "Authorization": "Bearer " + TOKEN,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "archive-closed-issue"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace"), e.headers


def paged(url):
    out = []
    while url:
        st, data, hd = api("GET", url)
        if st != 200:
            raise SystemExit("GET %s -> %s %s" % (url, st, str(data)[:300]))
        out += data
        m = re.search(r'<([^>]+)>;\s*rel="next"', hd.get("Link", "") or "")
        url = m.group(1) if m else None
    return out


def git(*a, check=True):
    return subprocess.run(["git", "-C", CCP, *a], check=check,
                          capture_output=True, text=True)


def push_with_retry():
    for i in range(5):
        if git("push", "origin", "HEAD:main", check=False).returncode == 0:
            return
        git("pull", "--rebase", "origin", "main", check=False)
        time.sleep(3 + i * 3)
    raise SystemExit("push to CCP failed after retries")


def summarize(issue, comments):
    text = "\n".join([issue.get("body") or ""] + [c.get("body") or "" for c in comments])
    shas = sorted(set(re.findall(r"\b[0-9a-f]{8,40}\b", text)))[:40]
    runs = sorted(set(re.findall(r"actions/runs/(\d+)", text)))[:20]
    last = comments[-1] if comments else None
    lines = [
        "## جمع‌بندی",
        "",
        "- وضعیت: closed (%s) در %s" % (issue.get("state_reason") or "completed", issue.get("closed_at")),
        "- سازنده: %s · ساخته‌شده: %s" % (issue["user"]["login"], issue.get("created_at")),
        "- برچسب‌ها: %s" % (", ".join(l["name"] for l in issue.get("labels", [])) or "—"),
        "- تعداد کامنت: %d" % len(comments),
        "- کامیت‌های اشاره‌شده: %s" % (", ".join(shas) or "—"),
        "- اجراهای Actions: %s" % (", ".join(runs) or "—"),
        "",
    ]
    if last:
        lines += ["### آخرین کامنت (%s، %s)" % (last["user"]["login"], last["created_at"]), "",
                  (last.get("body") or "").strip(), ""]
    return lines


def render(issue, comments):
    n = issue["number"]
    md = ["# agw-workers#%d — %s" % (n, issue["title"]), "",
          "> بایگانی خصوصی ایشوی عمومی بسته‌شده. ایشوی اصلی پس از بایگانی از ریپوی عمومی برداشته شد.", ""]
    md += summarize(issue, comments)
    md += ["---", "", "## متن ایشو", "", (issue.get("body") or "").strip(), "", "## گفتگو", ""]
    for c in comments:
        md += ["### %s · %s" % (c["user"]["login"], c["created_at"]), "", (c.get("body") or "").strip(), ""]
    return "\n".join(md).replace("\r\n", "\n") + "\n"


def remove_public(issue):
    q = {"query": "mutation($id:ID!){deleteIssue(input:{issueId:$id}){clientMutationId}}",
         "variables": {"id": issue["node_id"]}}
    st, data, _ = api("POST", "/graphql", q)
    if st == 200 and isinstance(data, dict) and not data.get("errors"):
        return "deleted"
    print("deleteIssue failed (%s): %s -- falling back to scrub+lock" % (st, str(data)[:300]))
    n = issue["number"]
    for c in paged("/repos/%s/issues/%d/comments?per_page=100" % (REPO, n)):
        api("DELETE", "/repos/%s/issues/comments/%d" % (REPO, c["id"]))
    api("PATCH", "/repos/%s/issues/%d" % (REPO, n),
        {"title": "archived #%d" % n, "body": "Archived to a private repository."})
    api("PUT", "/repos/%s/issues/%d/lock" % (REPO, n), {"lock_reason": "resolved"})
    return "scrubbed"


def candidates():
    explicit = [int(x) for x in re.findall(r"\d+", os.environ.get("ISSUES", ""))]
    if explicit:
        return explicit
    if os.environ.get("MODE") == "sweep":
        cutoff = os.environ.get("CUTOFF", "2026-10-14T00:00:00Z")
        items = paged("/repos/%s/issues?state=closed&per_page=100&since=%s" % (REPO, cutoff))
        return [i["number"] for i in items if not i.get("pull_request")
                and (i.get("closed_at") or "") >= cutoff]
    return []


def main():
    nums = candidates()
    print("candidates:", nums)
    done = []
    for n in nums:
        if n in PROTECTED:
            print("#%d protected, skip" % n); continue
        st, issue, _ = api("GET", "/repos/%s/issues/%d" % (REPO, n))
        if st != 200 or issue.get("pull_request"):
            print("#%d not found / PR, skip" % n); continue
        if issue["state"] != "closed":
            print("#%d still open, skip" % n); continue
        if any(l["name"] == KEEP_LABEL for l in issue.get("labels", [])):
            print("#%d keep-public, skip" % n); continue
        comments = paged("/repos/%s/issues/%d/comments?per_page=100" % (REPO, n))
        path = os.path.join(OUT_DIR, "issue-%d.md" % n)
        os.makedirs(os.path.join(CCP, OUT_DIR), exist_ok=True)
        open(os.path.join(CCP, path), "w", encoding="utf-8").write(render(issue, comments))
        idx = os.path.join(CCP, OUT_DIR, "INDEX.md")
        row = "- [#%d](issue-%d.md) · %s · closed %s\n" % (n, n, issue["title"].replace("\n", " "), issue.get("closed_at"))
        old = open(idx, encoding="utf-8").read() if os.path.exists(idx) else "# بایگانی ایشوهای عمومی agw-workers\n\n"
        if ("[#%d]" % n) not in old:
            open(idx, "w", encoding="utf-8").write(old + row)
        git("add", OUT_DIR)
        if git("diff", "--cached", "--quiet", check=False).returncode != 0:
            git("commit", "-m", "archive: agw-workers#%d closed-issue summary" % n)
            push_with_retry()
            sha = git("rev-parse", "HEAD").stdout.strip()
            cl = os.path.join(CCP, "Team/CHANGELOG.md")
            s = open(cl, encoding="utf-8").read()
            mark = "retroactively. Everything after it is.\n"
            line = "- `%s` -- `%s`: archive closed public issue agw-workers#%d (%s) before removing it from the public repo. No runtime change.\n" % (sha[:8], path, n, issue["title"].replace("`", "'"))
            if mark in s:
                open(cl, "w", encoding="utf-8").write(s.replace(mark, mark + line, 1))
                git("add", "Team/CHANGELOG.md")
                git("commit", "-m", "changelog: record %s" % sha[:8])
                push_with_retry()
        # confirm archive is on remote before removing anything public
        git("fetch", "origin", "main")
        if git("cat-file", "-e", "origin/main:" + path, check=False).returncode != 0:
            raise SystemExit("archive for #%d not on CCP main; NOT removing public issue" % n)
        done.append(n)
    if done and os.environ.get("DELETE", "true") == "true":
        delay = int(os.environ.get("DELAY_SECONDS", "0") or 0)
        if delay:
            print("waiting %ss so close-hooks (Notion Done) finish first" % delay); time.sleep(delay)
        for n in done:
            st, issue, _ = api("GET", "/repos/%s/issues/%d" % (REPO, n))
            if st == 200 and issue["state"] == "closed":
                print("#%d -> %s" % (n, remove_public(issue)))
    print("archived:", done)


if __name__ == "__main__":
    main()
