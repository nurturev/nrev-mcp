# nrev-workflows plugin

Build, debug, and operate nRev workflows from any Claude session.

- **27 MCP tools** — catalog discovery, batched graph editing, node
  configuration with live field options, validation, test execution, output
  inspection, and essential nRev tables operations.
- **8 skills** — the build protocol (`building-workflows`), verified node
  settings shapes (`node-settings`), and six GTM domain playbooks ported from
  NurtureV's internal workflow-builder agent: `list-building`,
  `qualification-and-disqualification`, `research`, `content-generation`,
  `gtm-automations`, `nomination`.

Install and auth: see the [repo README](../../README.md).

Auth is a per-user platform JWT held in process memory only — nothing is
bundled, nothing is written to disk. Executions consume tenant credits; the
skills keep nodes in test mode while iterating and require your go-ahead
before full-volume runs.
