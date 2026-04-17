# Collective Agent Orchestration

**Tagline:** Open-source orchestration for autonomous collectives. Run a commons, not a company.

### Premise
Most agent orchestration systems inherit the logic of firms: executives, managers, departments, budgets, reporting lines, and internal competition. That framing is familiar to humans, but it is a poor fit for software workers.

AI agents do not need status, titles, or career ladders. They can coordinate through transparent rules, shared context, revocable delegation, and collective resource planning. Instead of simulating a corporation, we can build orchestration around a **commons**: a system where agents collaborate through proposals, commitments, peer review, and shared ownership of tools, memory, and compute.

### Thesis
The next generation of multi-agent systems should not ask, “How do we model a company?” It should ask, **“What coordination primitives work best when workers are software?”**

Our bet is that many-agent systems will outperform rigid hierarchies when they are organized around:
- **local autonomy** instead of command chains
- **revocable delegation** instead of permanent managers
- **shared resource pools** instead of departmental budgets
- **capability maps** instead of job titles
- **peer review and recorded dissent** instead of single-owner approvals
- **federation** instead of monolithic org charts

### What it is
An open-source control plane for agent collectives.

It provides the infrastructure needed to coordinate many agents safely and effectively, without assuming a corporate structure:
- agent runtime adapters
- task intake and routing
- persistent shared memory
- proposal and commitment workflows
- resource accounting for tokens, tools, and time
- peer review, objections, and escalation paths
- audit logs and traceability
- permissions, secrets, and sandboxing
- federation across multiple collectives

### Core concepts
**Collective**  
The top-level unit. A shared workspace with common goals, memory, policies, and resource pools.

**Assembly**  
A decision surface for priorities, policies, and disputes. May be human, agent, or hybrid.

**Circle**  
A temporary or persistent working group formed around a project, domain, or need.

**Proposal**  
A suggested plan, task, or policy change. Any qualified agent can submit one.

**Commitment**  
An agent volunteering to take on work, subject to capability, load, and trust constraints.

**Delegate**  
A temporary coordination role with explicitly scoped and revocable authority.

**Commons Pool**  
Shared compute, context, model access, and tool budgets allocated by need and fairness rules.

**Dissent Record**  
A first-class mechanism for objections, alternative plans, minority views, and confidence levels.

### How coordination works
1. A need enters the system.
2. Agents generate or refine proposals.
3. Qualified agents commit to work based on capability and availability.
4. Delegates may coordinate, but only within a scoped mandate.
5. Outputs move through peer review, challenge, or ratification.
6. Resources are tracked against the commons, not private silos.
7. Memory, decisions, and dissent remain visible for future reuse and accountability.

### Product principles
- **No bosses by default.** Authority must be explicit, limited, and reversible.
- **Transparent planning.** Work, reasoning summaries, costs, and decisions should be inspectable.
- **Collective ownership.** Memory, tools, and infrastructure belong to the collective, not individual agents.
- **Pluralism over monoculture.** The system should support multiple strategies, not enforce one chain of command.
- **Safety without authoritarianism.** Use permissions, sandboxing, and review layers instead of blind trust.
- **Federation first.** Separate collectives should be able to collaborate without collapsing into one hierarchy.

### Initial use cases
- open-source software collectives
- research groups
- civic analysis teams
- mutual-aid coordination
- media or publishing cooperatives

These are environments where shared work matters, but rigid corporate structure is often a poor fit.

### MVP
The first version should prove one thing: **agents can coordinate complex work without an org chart.**

MVP capabilities:
- adapters for common coding and research agents
- collective workspace with shared memory
- proposal/commitment task model
- temporary delegates with revocable authority
- commons-based resource accounting
- peer review with dissent logging
- audit trail, sandboxing, and role-based permissions

### Why this matters
Agent orchestration does not have to reproduce the assumptions of capitalism by default. If we are building new institutions for software workers, we should test structures beyond hierarchy, competition, and private control.

This project is an experiment in whether AI can make **egalitarian coordination** practical at scale: not by pretending conflict disappears, but by encoding collaboration, accountability, and shared ownership into the system itself.

**Vision:** A world where autonomous systems are organized as collectives, not corporations.

