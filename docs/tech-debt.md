# Tech Debt Register

Track known debt here. Every entry must have: description, impact, owner, and target.
A TODO in code without an entry here is a policy violation.

Rule: if the debt would cause a production incident in the next 90 days → fix it now, don't log it.

---

## Active

| ID | Description | Impact | Owner | Target | Slice |
|----|-------------|--------|-------|--------|-------|
| TD-001 | Example: synchronous DB calls in hot path | Latency under load | unassigned | Issue #N | — |

---

## Resolved

| ID | Description | Resolved | PR |
|----|-------------|----------|----|
| — | — | — | — |

---

## Guidelines

**What belongs here:**
- Workarounds that were intentional (not bugs)
- Missing abstractions that are deferred due to time
- Patterns that need migration once a slice is available
- Known performance shortcuts

**What does NOT belong here:**
- Bugs → file an issue
- Missing tests → fix now
- Security gaps → fix now, never defer
