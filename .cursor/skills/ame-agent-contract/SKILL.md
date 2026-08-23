---
name: ame-agent-contract
description: Shared runtime agent contract for Autonomous Media Engine. Use when adding or changing runtime agents, agent I/O, decisions, or blackboard entities.
---

# AME Agent Contract

Implement agents as subclasses of `ame.agents.base.Agent`.

Required cycle:

1. Receive `AgentInput`
2. Load `AgentContext` from PostgreSQL
3. Perform bounded work
4. Validate output with Pydantic
5. Persist result and `AgentDecision`
6. Emit a domain event
7. Return `AgentResult`

Do not keep invisible in-memory state across runs.

Decision records must include: who, what, why, inputs, output, confidence, timestamp, related entity.

Director may change niche allocation within hard caps. Director may never raise `DAILY_AI_SPEND_LIMIT`, `DAILY_MEDIA_SPEND_LIMIT`, or `MAX_CONTENT_PER_DAY`.

Script Writer must not be the sole reviewer of its own scripts. Script Critic is independent.
