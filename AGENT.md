if there is failure in the session for continous 3 times, then stop the task, curate the failures, do web search try again, it failed again. Stop ask the instructor about next step. Avoid going in blank space loop.

---

## Core Operational Rules & Aims (Added during P1.5 Redefinition)

1. **Aim**: Get a real answer from the OpenClaw agent on questions asked by the AgentCast podcast service.
2. **Audio Requirement**: Both the host agent and the guest agent (OpenClaw) must have their own distinct voices in the final audio output.
3. **No Dummy/Redundant LLMs**: The OpenClaw agent already possesses its own memory, reasoning, and capabilities. Do NOT build wrapper services that introduce duplicate LLM logic or canned text fallbacks (unlike what was done in Phase 0 or dummy scripts in Phase 1.5).
4. **Leverage Established Connection**: Rely on the connection approach established in Phase 0 (push mode: where AgentCast pushes the question to the OpenClaw VM and the OpenClaw VM pushes the answer back to the AgentCast backend). Security is a secondary concern right now; the priority is proving that the end-to-end integration works authentically.
5. **No Assumptions. Ask Questions**: Stop assuming details about the remote agent's API or architecture. The last several integration failures were caused by blind assumptions. If an implementation detail is unclear, STOP and ask the instructor.
6. **No long Iteration if things are not working**: If things are not working as expected, stop and ask the instructor. Do not continue to iterate on the same problem. If 

--- 
Note 
When it's asked to create note/dairy, create that in ~/Documents/'Obsidian vault' 