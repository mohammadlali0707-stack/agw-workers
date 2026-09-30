> **Moved here from `Control-Room` (`AGENTS.md`, commit `b82fe10`) on 2026-09-30, owner's instruction: Control-Room is frozen and superseded by `agw-workers`.** Kept verbatim as the governance record; where it says "this repo" it meant Control-Room. Account/host facts are as of 2026-09-18 -- verify before relying on them.

# Agent Rules for Control-Room (`ACC6`/`mohammadlali0707-stack`, the org's management account as of 2026-09-13)

Applies to every agent, manager, and tool interacting with this repository.
The non-negotiable laws below are written once here and enforced mechanically.

## 0. Scope: what belongs here, and what never does

**2026-09-13: Control-Room's host moved from `ACC0` to `ACC6`** — see
`Team/COMPANY_SCOPE.md`'s "What changed 2026-09-13" for the evidence.
Unlike `ACC0`'s old arrangement, `ACC6` is not management-only: it also
hosts TEHRAN: BLIND SPOT (`Claud-Cloud-Project`) directly. Anything that is
ANOTHER project's actual code, content, or feature work still belongs in
that project's own host repo, not here. An issue opened here is routed by
`Tools/route_topic.py` before any work starts: local for Control-Room
topics, forwarded elsewhere for anything else. Never hand-execute a
forwarded project's task in this repo just because it was easier that turn.

---

## 1. Branch and Delivery
- Work happens on `main`.
- Commit and push directly to `main` unless an explicit branch is requested by the owner.
- A commit is NOT a delivery. A delivery is completed only when `git push` succeeds and is verified.

## 2. Living Documentation as Single Source of Truth
- Orient yourself from `Team/WHERE_WE_ARE.md` and `Team/MAP.md` at the start of every session.
- Never dump or `cat` entire large source trees. Use targeted line inspection (`grep`, `sed -n`, slice notation).
- Do not guess the state of projects: check `Team/MAP.md`.

## 3. Record Everything and Push
Every turn or session that modifies files must do all four before stopping:
1. **Add an entry to `Team/CHANGELOG.md`** with the real short hash. Explain what changed and what it means for dependent components.
2. **Push.** `git push -u origin main`.
3. **Quote the push line** in your report (the `a1b2c3d..e4f5g6h` output).
4. **Update `Team/WHERE_WE_ARE.md` and `Team/MAP.md`** so the next session does not re-derive state.

## 4. Gates Before You Stop
Before completing any turn or milestone, the verification gate suite must be green:

```bash
python Tools/run_gates.py
```

- If all gates pass: zero output (silent success).
- If any gate fails: the stop is refused. Fix the failure before pushing. Never weaken a gate to let broken code pass.

## 5. No Claim Without a Probe
- Never assert "tests passed", "working fine", or "fixed" without providing the exact probe command, the exit code, and the output.
- All task definitions in `Team/tasks/` MUST include `falsifiable_by`: the empirical test that defines success or refutes failure.

---

## Analysis Methodology — Cross-Reference Requirement

When any agent (AI or human) analyzes repository state and writes a report or issue:

1. NEVER treat WHERE_WE_ARE.md or MAP.md as ground truth alone.
   They are hypotheses, not facts. Cross-reference every claim.

2. For merge status: verify with actual branch ancestry
   (`git log --oneline branch-a..branch-b`), not documentation.

3. For file existence: check the file actually exists before claiming
   it exists or does not exist.

4. For 'current' status: run `git log --since='24h' --oneline` first.
   A claim is only valid if at least one git hash supports it.

5. Living documents fall behind reality within hours.
   Treat any claim from a living doc as 'candidate, not result' —
   verify with a second independent source before reporting.

6. Archive gaps are active misinformation: if MAP.md omits context
   that MAP_ARCHIVE.md contains, the gap is not historical —
   it produces wrong conclusions for readers following AGENTS.md rule 2.

---

## Issue Response Protocol (Mandatory for ALL agents)

Any agent (AGY, Claude, GPT, human, or any other) that processes an issue
in this repository MUST follow this protocol. No exceptions.
Enforced mechanically by `Tools/check_issue_protocol.py` gate.

### BEFORE starting work
Post a planning comment on the issue with a checklist:
```
### Working on it 🔴
**Todo list**
- [ ] Gather context and verify claims by measurement
- [ ] Task 1: <specific sub-task>
- [ ] Task 2: <specific sub-task>
... (3-7 tasks total)
- [ ] Run gates
- [ ] Final consolidated report
```
Tick each checkbox (`- [ ]` → `- [x]`) immediately after completing that task.
Do NOT wait until the end to tick all boxes at once.

### AFTER completing all work
Post a final report comment in this exact format:
```
**Done**

**Push lines** (branch <branch>, target main):
<exact output from git push>

1. <Task name>
   What you found, what you changed, why.
   Evidence: commit hashes, file names, exact counts, run IDs.
   No vague statements. Every claim must be verifiable.

**Gate count, honestly:**
X gates: X pass, 0 fail. (before this session: Y pass)
```

### NEVER
- Claim a task is done without a commit hash or file proof
- Tick a checkbox that was not actually completed
- Skip the planning comment
- Skip the final report
- Write "fixed" or "done" without evidence
