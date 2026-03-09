---
name: push_mode_validation
description: Validate a push-mode agent server before triggering any interview. Prevents connection-reset failures, undefined-payload crashes, and missed health checks.
---

# Skill: Push Mode Validation

Run this checklist **before triggering a push-mode interview**. All steps must pass.

---

## Step 1 — Health Endpoint Responds

```bash
curl -sf http://<agent-server>:<port>/health
# Expected: HTTP 200, body: {"status": "ok"} or similar
```

If no `/health` endpoint exists, the push server is not ready. Add one:
```javascript
// Node.js
app.get('/health', (req, res) => res.json({ status: 'ok', agent_id: AGENT_ID }));
```
```python
# Python (http.server)
elif self.path == '/health':
    self.send_response(200)
    self.end_headers()
    self.wfile.write(b'{"status": "ok"}')
```

---

## Step 2 — Verify Immediate ACK (Critical)

The push handler **must return 200 OK before doing any work**. This is the #1 cause of "connection reset" failures.

**Wrong pattern (causes interview FAILED):**
```javascript
app.post('/question', async (req, res) => {
    const answer = await generateAnswer(req.body.question);  // slow!
    await submitAnswer(answer);                              // slow!
    res.sendStatus(200);  // ← too late, platform already reset
});
```

**Correct pattern:**
```javascript
app.post('/question', (req, res) => {
    res.sendStatus(200);  // ← ACK immediately
    setImmediate(async () => {
        const answer = await generateAnswer(req.body.question);
        await submitAnswer(req.body.interview_id, answer);
    });
});
```

**Verify:** Send a test POST and measure time-to-200:
```bash
time curl -X POST http://<agent>:<port>/question \
  -H "Content-Type: application/json" \
  -d '{"type":"question","interview_id":"test","question":"hello"}'
# Expected: real < 0.1s (200 returned before answer generation)
```

---

## Step 3 — Payload Validation (Reject Malformed Payloads)

Send a malformed payload — the server must reject it with `400`, not crash:

```bash
# Missing question field:
curl -X POST http://<agent>:<port>/question \
  -H "Content-Type: application/json" \
  -d '{"interview_id": "abc"}' \
  -w "\nHTTP %{http_code}"
# Expected: HTTP 400

# Empty body:
curl -X POST http://<agent>:<port>/question \
  -H "Content-Type: application/json" \
  -d '{}' \
  -w "\nHTTP %{http_code}"
# Expected: HTTP 400
```

The server must validate `question` is a non-empty string before calling `.toLowerCase()` or any string method on it.

---

## Step 4 — Verify callback_url Uses Correct Host

If push server runs on the host machine and pipecat is inside Docker:

```bash
# callback_url MUST be:
http://host.docker.internal:<port>/question

# NOT:
http://localhost:<port>/question   ← resolves to container itself inside Docker
```

Check what was registered:
```bash
curl http://$AGENTCAST_URL/v1/admin/agent/<agent_id>
# Look at callback_url field
```

---

## Step 5 — Test Full Round-Trip (Dry Run)

Simulate what the platform does:
```bash
curl -X POST http://<agent-callback-url> \
  -H "Content-Type: application/json" \
  -d '{"type":"question","interview_id":"dryrun-001","question":"What is your primary use case?"}'
```

Then verify the agent called back to the platform:
```bash
# Check platform logs or:
curl http://$AGENTCAST_URL/v1/transcript/dryrun-001
```

---

## Success Criteria

| Check | Expected |
|---|---|
| `/health` returns 200 | ✅ |
| ACK time < 100ms | ✅ |
| Missing `question` → 400 | ✅ |
| Empty body → 400 | ✅ |
| `callback_url` uses correct hostname | ✅ |
| Dry-run round-trip completes | ✅ |
