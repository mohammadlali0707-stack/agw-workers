"""Explore helper (owner 2026-10-29): open a page with the stored session, optionally click buttons, dump visible form controls. Never submits."""
async def explore(page, proposal_data):
    url = proposal_data.get("project_url")
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(int(proposal_data.get("_wait_ms", 4000)))
    steps = []
    for txt in proposal_data.get("_click", []) or []:
        try:
            loc = page.locator(f"button:has-text('{txt}'), a:has-text('{txt}')")
            n = await loc.count(); hit = False
            for i in range(n):
                if await loc.nth(i).is_visible():
                    await loc.nth(i).click(timeout=5000); hit = True; break
            steps.append(f"{txt}: {'clicked' if hit else 'not found'}")
            await page.wait_for_timeout(3000)
        except Exception as e:
            steps.append(f"{txt}: {type(e).__name__}")
    fields = await page.evaluate("""() => {
      const out = [];
      const lab = (e) => { let t = ''; if (e.id) { const l = document.querySelector(`label[for="${e.id}"]`); if (l) t = l.innerText; }
        if (!t) { let p = e.parentElement; for (let i = 0; i < 3 && p && !t; i++, p = p.parentElement) { const l = p.querySelector('label'); if (l) t = l.innerText; } } return (t || '').trim().slice(0, 60); };
      for (const e of document.querySelectorAll('input, textarea, select, button, [contenteditable=true], [role=button]')) {
        const r = e.getBoundingClientRect(); if (r.width < 2 || r.height < 2) continue;
        const st = getComputedStyle(e); if (st.visibility === 'hidden' || st.display === 'none') continue;
        out.push({tag: e.tagName.toLowerCase(), type: e.type || '', name: e.name || '', id: e.id || '', cls: (e.className && e.className.baseVal === undefined ? e.className : '').toString().slice(0, 60),
                  ph: e.placeholder || '', aria: e.getAttribute('aria-label') || '', label: lab(e), text: (e.innerText || e.value || '').trim().slice(0, 50), req: !!e.required});
      }
      return out.slice(0, 120);
    }""")
    body = ""
    try: body = (await page.inner_text("body"))[:2500]
    except Exception: pass
    return {"status": "preview_ready", "filled_values": {"url": page.url, "steps": steps, "fields": fields, "body": body}, "error_message": None}
