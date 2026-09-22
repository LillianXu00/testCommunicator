# Agent Task Experience

Experience Memos have `kind: agent-task-experience` and two independent bodies.

## Execution Process

`executionProcess.problems` records issues observed in the completed run, their
impact, and any resolution. `effectiveActions` records actions that demonstrably
helped. `unresolvedIssues` records remaining execution concerns.

Use this section to improve a Skill only when the issue belongs to the reusable
task workflow. Route runtime, authentication, queue, network, Adapter, and
provider failures to platform maintenance instead.

## Task Improvement

`taskImprovement.strengths` identifies behavior worth preserving.
`improvements` contains a task-specific quality area, the current limitation, a
concrete recommendation, and the expected benefit. `nextTimePlan` describes the
recommended sequence for a future execution.

For report-writing tasks, useful task-specific areas include source coverage,
fact and interpretation separation, analytical depth, report structure,
conclusion support, audience relevance, and actionable recommendations.

Treat empty arrays as an explicit absence of evidence. Do not infer failures or
quality problems that the Memo does not contain.
