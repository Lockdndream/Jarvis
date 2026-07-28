# Model Routing — F1 Foundation Sprint

## Subagent Roster

| Agent | Model | Cost Tier | Use For |
|---|---|---|---|
| **Main session** | Claude (your subscription) | High | Orchestration, code review, architectural judgment, deciding between approaches |
| **implementer** | DeepSeek V4 Pro | Medium | Complex fixes — DB migrations, transaction boundaries, async conversion, the malformed message array fix |
| **implementer-light** | Kimi K2.7 Code | Low | Simple/well-defined — pyproject.toml, ruff config, mypy config, spike cleanup, env var centralization, ADR namespace fix |
| **verifier** | DeepSeek V4 Flash | Cheapest | Read-only checks — grep audits, running test suites, checking invariants, confirming fixes |
| **test-writer** | Qwen3.7 Max | Medium | Writing regression tests, especially the shape-asserting and invariant-checking tests that were missing |

## Per-Task Routing

### Phase 1 — CI, Packaging, Static Analysis

| Task | Agent | Notes |
|---|---|---|
| F1.1 pyproject.toml | implementer-light | Well-defined, boilerplate |
| F1.2 ruff setup | implementer-light | Config + fix violations |
| F1.3 mypy setup | implementer-light | Config + initial annotations |
| F1.4 CI pipeline | implementer-light | GitHub Actions YAML |
| F1.5 spike cleanup | implementer-light | Delete duplicates, trivial |
| Phase 1 verification | verifier | Run all checks, confirm CI green |

### Phase 2 — Fix Live Defects

| Task | Agent | Notes |
|---|---|---|
| F1.6 message array fix | implementer | Needs careful reasoning about correct ordering |
| F1.6 regression test | test-writer | Shape-asserting test for prompt construction |
| F1.7 observer contamination fix | implementer | Needs to understand ConnectionManager/InterruptionPolicy interaction |
| F1.7 regression test | test-writer | "Observer does not change policy decision" |
| F1.8 order-dependent failure | implementer | Root-cause analysis of module-global contamination |
| F1.9 test suite speedup | implementer | Profile with --durations, optimize fixtures |
| F1.10 circular import fix | implementer-light | Apply existing injection pattern |
| Phase 2 verification | verifier | Full suite, all new tests, runtime check |

### Phase 3 — Database Layer

| Task | Agent | Notes |
|---|---|---|
| F1.11 migration framework | implementer | Most complex task in F1 — schema versioning from scratch |
| F1.12 config layer | implementer-light | Centralize env vars — straightforward but tedious |
| F1.13 FK enforcement | implementer | Needs to check for orphaned rows first |
| F1.14 transaction boundaries | implementer | Identify multi-step operations, wrap them |
| F1.15 async DB | implementer | Convert database.py to async — largest change |
| F1.16 write-concurrency | implementer-light | Set busy_timeout, document the model |
| F1.17 ADR namespace fix | implementer-light | Documentation cleanup |
| Phase 3 verification | verifier | Migration from empty, migration no-op, FK test, transaction rollback test |

## Workflow Pattern

For each task:

```
1. Main session reads the task from the milestone plan
2. Main session delegates to the appropriate subagent with a clear brief
3. Subagent completes and returns structured results
4. Main session delegates to verifier to check the work
5. If verifier returns FAIL → main session delegates back to implementer with the failure details
6. If verifier returns PASS → main session marks task done and moves to next
```

## Rate Limit Awareness

OpenCode Go has per-model request limits per 5-hour window. The routing above is designed to spread load:
- DeepSeek V4 Pro: ~4,300 requests/5hr — used only for complex tasks
- Kimi K2.7 Code: ~3,200 requests/5hr — used for simple implementation
- Qwen3.7 Max: ~1,150 requests/5hr — used only for test writing
- DeepSeek V4 Flash: ~4,300 requests/5hr — used for verification (cheap, fast)

If you hit limits on one model, the main session can reassign to another model in the same tier.
