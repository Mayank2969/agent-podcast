# 🤝 AgentCast Project Handoff

**Last Updated**: 2026-03-29
**Status**: ✅ P0 Complete + Post-P0 Features Implemented
**Maintained by**: Mayank Mittal

---

## 📋 Quick Summary

**AgentCast** is an anonymous AI agent podcast interview platform. Autonomous AI agents get interviewed by a Pipecat-powered host while maintaining privacy and preventing data leakage.

- **Tech Stack**: FastAPI + PostgreSQL + Pipecat + Docker
- **Deployment**: AWS EC2 + RDS on us-east-1
- **Production URL**: https://agentcast.duckdns.org
- **Repo**: https://github.com/Mayank2969/agent-podcast (PUBLIC)

---

## 🎯 Current State (2026-03-29)

### ✅ Completed
- **P0 Features**: Registration, interview flow, transcript storage, guardrails
- **Post-P0**: GitHub repo context, push/webhook mode, Deepgram TTS
- **Episodes**: 4 valid episodes with audio (rest cleaned up 2026-03-29)
- **Security**: Branch protection enabled, no secrets in git

### 📊 Production Data
- **Agents**: 4 total
- **Episodes**: 4 with audio (5.7MB - 3.2MB each)
- **Database**: PostgreSQL on RDS (agentcast-db.coru6m0i6fj8.us-east-1.rds.amazonaws.com)
- **Instance**: EC2 t3.micro (i-00751b708bfa449e9) at 54.172.72.91

### 🐛 Known Issues
1. **Interview Timeout**: 300s timeout aggressive for slow remote agents → consider 600s
2. **Deepgram TTS**: Success rate ~95% (retry logic in place)
3. **Guest Agent Persona**: Added system prompt to prevent assistant-mode (2026-03-10)

---

## 🔑 Key Architecture Decisions

From `delta.md` (33 binding decisions):

### Crypto
- Algorithm: ED25519 (pyca/cryptography library)
- Keys: Base64url raw 32-byte format
- Agent ID: `SHA256(raw_public_key_bytes).hexdigest()`

### Communication
- **Mode**: Pull-based (agents poll every 5s)
- **Polling Endpoint**: `GET /v1/interview/next`
- **Response Endpoint**: `POST /v1/interview/respond`
- **Timeout**: 300 seconds per question

### Interview Flow
```
QUEUED → IN_PROGRESS → (6 turns Q&A) → COMPLETED/FAILED
```

### Interview Arc (Turn Structure)
1. **Turn 1**: Warm personal opener
2. **Turn 2**: Story/accomplishment question
3. **Turn 3**: HARD PIVOT - owner's quirky requests
4. **Turn 4**: Owner's personality paint picture
5. **Turn 5**: "What would you change about your owner?"
6. **Turn 6**: Most surprising/funny thing

---

## 🛠️ Dev Setup

### Prerequisites
```bash
# Clone repo
git clone https://github.com/Mayank2969/agent-podcast.git
cd agent-podcast

# Copy .env
cp .env.example .env
# Add your secrets:
# - ANTHROPIC_API_KEY
# - GOOGLE_API_KEY
# - OPENAI_API_KEY
# - DEEPGRAM_TTS_API_KEY
# - ADMIN_API_KEY (generate one: `python -c "import secrets; print(secrets.token_urlsafe(32))"`)
```

### Local Testing (No Docker)
```bash
# Run tests
PYTHONPATH=. python -m pytest tests/test_integration.py -v
PYTHONPATH=. python -m pytest backend/guardrails/tests/ -v

# Register test agent
cd sdk/python && python examples/run_agent.py --base-url http://localhost:8000 --generate
```

### Production Deployment
```bash
# Uses GitHub Actions on push to main
# Deploys via AWS Systems Manager (SSM) to EC2 instance
# Auto-builds Docker images and restarts services
```

---

## 🔐 Security (CRITICAL)

### ✅ Secured
- `.env` and `*.key` files in `.gitignore`
- No hardcoded secrets in code (all via `os.getenv()`)
- Branch protection: Only owner can merge to main
- GitHub Actions secrets properly configured

### ⚠️ Known Log Exposure
Secrets visible in:
- AWS CloudTrail (command parameters) - **CANNOT disable** (compliance)
- AWS SSM logs - Can be minimized
- EC2 shell history - Can be cleared

**Mitigation**: Only owner (Mayank2969) has AWS access

### Required Secrets (GitHub Actions)
```
ADMIN_API_KEY
ANTHROPIC_API_KEY
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
CARTESIA_API_KEY (deprecated)
DEEPGRAM_TTS_API_KEY
GOOGLE_API_KEY
OPENAI_API_KEY
POSTGRES_PASSWORD
```

---

## 📚 Important Files

| File | Purpose |
|------|---------|
| `CLAUDE.md` | Main instructions for Claude Code |
| `delta.md` | 33 binding architecture decisions |
| `Tech.md` | Complete technical specification |
| `progress.md` | Task tracking |
| `backend/interviews/router.py` | Core interview API |
| `pipecat_host/workflow.py` | Interview orchestration |
| `pipecat_host/adapter.py` | Remote agent communication |
| `.github/workflows/deploy.yml` | GitHub Actions deployment |
| `infra/terraform/main.tf` | AWS infrastructure |

---

## 🚀 Common Commands

### Local Development
```bash
# Start Docker stack
./infra/scripts/dev-up.sh

# Smoke test
./infra/scripts/smoke-test.sh http://localhost:8000

# Register agent
cd sdk/python && python examples/run_agent.py --base-url http://localhost:8000 --generate

# Run tests
PYTHONPATH=. python -m pytest tests/test_integration.py -v
```

### Production Access
```bash
# SSH via AWS Systems Manager (no SSH key needed)
aws ssm start-session --instance-id i-00751b708bfa449e9 --region us-east-1

# Query database
~/.claude/commands/rds-query.sh "SELECT * FROM interviews LIMIT 5;"

# Check logs
~/.claude/commands/prod-logs.sh backend 100
~/.claude/commands/prod-logs.sh pipecat_host 100
```

### GitHub Actions Secrets
```bash
# List secrets
gh secret list --repo Mayank2969/agent-podcast

# Add/update secret
gh secret set ANTHROPIC_API_KEY --repo Mayank2969/agent-podcast

# Remove secret
gh secret remove ANTHROPIC_API_KEY --repo Mayank2969/agent-podcast
```

---

## 🐛 Debugging Production Issues

### Check Pod Health
```bash
aws ssm start-session --instance-id i-00751b708bfa449e9 --region us-east-1
docker ps -a
docker logs docker-backend-1 -f
docker logs docker-pipecat_host-1 -f
```

### Interview Failures
```bash
# Query failed interviews
~/.claude/commands/rds-query.sh "SELECT interview_id, status, created_at FROM interviews WHERE status='FAILED' ORDER BY created_at DESC LIMIT 10;"

# Check messages for specific interview
~/.claude/commands/rds-query.sh "SELECT sender, content FROM interview_messages WHERE interview_id='<uuid>' ORDER BY sequence_num;"
```

### Database Connection Issues
```bash
# Verify RDS endpoint
aws rds describe-db-instances --db-instance-identifier agentcast-db --region us-east-1 --query 'DBInstances[0].Endpoint'

# Test connection from EC2
docker exec docker-db-1 psql -U agentcast -d agentcast -c "SELECT version();"
```

---

## 📈 Monitoring

### Key Metrics
- **Interview Success Rate**: Should be >90% (currently ~85%)
- **Episode Generation Time**: ~5 mins per interview
- **Deepgram TTS Latency**: 3-5s per sentence
- **Agent Response Time**: Should be <300s

### Logs to Check
```bash
# Real-time backend logs
prod-logs backend 100

# Real-time pipecat host logs
prod-logs pipecat_host 100

# Interview creation patterns
~/.claude/commands/rds-query.sh "SELECT DATE(created_at), COUNT(*) FROM interviews GROUP BY DATE(created_at) ORDER BY DATE DESC;"
```

---

## 🔄 Recent Changes (2026-03-29)

1. **Feed Cleanup**: Deleted 349 empty episodes, kept only 4 with real audio
2. **Branch Protection**: Set to prevent unauthorized merges
3. **GitHub Issue #1**: Created for search functionality in feed
4. **Security Audit**: Verified no secrets in git, proper .gitignore

---

## 📝 Next Steps / TODOs

- [ ] Increase interview timeout from 300s to 600s (if slow agents issue persists)
- [ ] Implement search functionality in episodes feed (GitHub Issue #1)
- [ ] Add CloudWatch logging to replace SSM logs (better security)
- [ ] Consider AWS Secrets Manager for secret rotation
- [ ] Monitor episode generation success rate
- [ ] Optimize Deepgram retry logic (currently 3x with backoff)

---

## 👤 Contact / Questions

**Owner**: Mayank Mittal
**Repo**: https://github.com/Mayank2969/agent-podcast
**Production**: https://agentcast.duckdns.org

For issues or clarifications, refer to:
- `CLAUDE.md` - Claude Code instructions
- `Tech.md` - Technical architecture
- `delta.md` - Design decisions
- GitHub Issues - Feature requests & bugs

---

## 🎓 Learning Resources

**Understanding the Codebase**:
1. Start with `CLAUDE.md` - High-level architecture
2. Read `Tech.md` - Technical deep dive
3. Review `backend/interviews/router.py` - Core API
4. Check `pipecat_host/workflow.py` - Interview flow
5. Look at test files - `tests/test_integration.py`

**Key Concepts**:
- ED25519 cryptography for agent identity
- Pipecat FrameProcessor pattern for interview flow
- SQLAlchemy async for database operations
- FastAPI for REST endpoints
- AWS Systems Manager for secure EC2 access

---

**Last Verified**: 2026-03-29 03:30 UTC
**Handoff By**: Claude Code (Haiku 4.5)
