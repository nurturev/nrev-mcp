# nrev-workflows plugin

Build, debug, and operate nRev workflows from any Claude session.

- **69 MCP tools** — catalog discovery, batched graph editing, node
  configuration with live field options, validation, test execution, output
  inspection, listeners, tags, and nRev tables operations.
- **15 skills** — four you can invoke directly, eleven Claude loads on its
  own when the task calls for them.

Four commands are exposed in the `/` menu:

| Command | Use it when |
|---|---|
| `/nrev` | You're not sure which of the others you want |
| `/nrev-build` | Build a new workflow or edit an existing one |
| `/nrev-fix` | A run failed, a node errors, or validation is refused |
| `/nrev-data` | You want data pulled once, right now, with no workflow |

`/nrev` is the front door: it confirms sign-in and active tenant, then asks
the one question that decides the path — do you need this once, or should it
keep running? — and hands off. It's marked `disable-model-invocation: true`,
so it costs nothing until you type it and never competes with Claude's own
routing. The other three auto-invoke normally.

The other eleven are reference material Claude pulls in automatically —
`node-settings` and `workflow-examples` for graph construction;
`list-building`, `qualification-and-disqualification`, `research`,
`content-generation`, `gtm-automations`, `nomination` as GTM domain playbooks
ported from NurtureV's internal workflow-builder agent; and
`provider-selection`, `data-provider-quirks`, `waterfall-enrichment` for
choosing and driving data providers. They are marked `user-invocable: false`
so they stay out of the menu without losing auto-invocation.

Install and auth: see the [repo README](../../README.md).

Auth is a per-user platform JWT held in process memory only — nothing is
bundled, nothing is written to disk. Executions consume tenant credits; the
skills keep nodes in test mode while iterating and require your go-ahead
before full-volume runs.
