# Jules delegation contract

The supervising agent owns work selection, diagnosis, scope, and acceptance. Jules owns one bounded repository-local implementation attempt.

## Use Jules when

- the task is repository-local and bounded;
- the required behavior is explicit;
- success is independently testable;
- there is no unresolved architecture ruling;
- no conflicting PR or managed dispatch owns the same work;
- remote parallel execution is useful.

## Do not use Jules for

- broad repository improvement prompts;
- unresolved design decisions;
- release, production, or destructive actions;
- tightly coupled central refactors without an integration owner;
- work whose critical context is unavailable in the packet;
- duplicate work already represented by a PR, issue claim, or dispatch key.

## Required packet

```markdown
# Objective

# Source object

# Diagnosis

# Required behavior

# Scope exclusions

# Relevant seams

# Verification

# Stop conditions

# Delivery
```

## Sequence

1. Read the source object and repository instructions.
2. Inspect the current implementation and related tests.
3. Check for conflicting PRs and dispatches.
4. Produce the packet and a stable caller-owned `dispatch_key`.
5. Submit through the keyless queue.
6. Retain the candidate and dispatch receipts.
7. Reconcile later rather than waiting synchronously.
8. Answer Jules only from evidence.
9. Treat `COMPLETED` as Jules finishing an execution epoch, not acceptance.
10. Review the current GitHub PR head and rerun acceptance after every new commit.

## Example dispatch key

```text
github:EffortlessMetrics/perl-lsp:issue:1234:implementation
```

Recurring work includes one occurrence in the key so tomorrow's intended run is not deduplicated against today's completed run.
