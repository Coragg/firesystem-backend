---
name: plan-structure
description: Use
---
# Rules: Plan Format
When I ask for a plan, __ALWAYS__ respond with this structure, in this order:

## Goal
One sentence that describe the goal.

## Scope 
- **Includes:** ...
- **Excludes:** ...

## Assumptions
Short list. If anything is ambigous, ask me before continuing.

## Phases
Table with colums: ID | Task | Deliverable | Depends on | Effort (S/M/L)

## Dependency Flow
A Mermaid `flowchart TD` diagram placed right below the phase table.

**Diagram rules:**
- One node per phase, usin the same ID as the table (F1, F2...).
- Labels of 4 words max.
- Arrows must match the "Depends on" column exactly.
- 10 nodes max; if there are more, group them into subgraphs.
- No styling or `classDef`.
- No parantheses or special characters in labels; if needed, wrap them in quotes: A["Validate (JWT)"].

**Valid example:**
```mermaid
flowchart TD
    F1[User model] --> F2[Register / Login]
    F2 --> F3[JWT]
    F3 --> F4[Project routes]
    F4 --> F6 [Test]
    F5 --> F6
```

## Risk
3 max, each with its mitigations

## Acceptance Criteria
Verifiable checklist. 

## Next Action
One concrete action to start today.

## Style
- Every task starts with an imperative verv.
- No long paraghraphs or filler
- Don't repeat in text what the diagram already shos.
- Don't invent data I dindn't give you. 
