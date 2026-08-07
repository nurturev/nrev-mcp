# Quickstart

Install nRev, sign in once, and run your first pull. About five minutes.

| | Step | |
|---|---|---|
| 1 | **Add the marketplace** | Point your assistant at this repo |
| 2 | **Install the plugin** | Pick `nrev-workflows` from it |
| 3 | **Connect** | One browser sign-in |
| 4 | **Ask** | Type `/nrev` and say what you want |

Nothing to download or configure — nRev runs in the cloud.

---

## Install

Adding a marketplace and installing a plugin are two separate steps. The
marketplace is the catalog; the plugin is the thing you install from it.

### Claude Cowork

1. Open **Customize** in the sidebar, then **Plugins**.
2. Select **Add marketplace** and enter:
   ```
   https://github.com/nurturev/nrev-mcp
   ```
3. Select **Browse plugins**, find **nrev-workflows**, and click **Install**.
4. You'll be prompted to sign in — see [Connect](#connect) below.

### Claude Code

```
/plugin marketplace add nurturev/nrev-mcp
/plugin install nrev-workflows@nrev
```

If the install summary says `Run /reload-plugins to activate.`, run that.

### Codex

```bash
codex plugin marketplace add https://github.com/nurturev/nrev-mcp
codex plugin add nrev-workflows@nrev
```

### Gemini

Gemini installs from a local folder, so clone the repo first:

```bash
git clone https://github.com/nurturev/nrev-mcp
gemini extensions install ./nrev-mcp/packages/gemini
```

---

## Connect

nRev asks you to sign in the first time. Approve the prompt, sign in with
Google in the browser tab that opens, and you're done — it stays signed in.

If you don't see a prompt, type `/mcp` and connect `nrev-workflows` from there.

**On a team with more than one nRev workspace?** Ask *"which workspace am I
in?"* before you start. You can switch it in the nRev web app, and it's worth
confirming once rather than wondering why your results look empty.

---

## Ask

```
/nrev
```

That's the front door. It checks you're signed in, asks whether you need this
**once** or **on repeat**, and takes it from there. You can skip ahead by
saying what you want up front:

```
/nrev get me the comments on this LinkedIn post
```

Once you know your way around, go straight to the one you need:

| | |
|---|---|
| `/nrev-data` | Get something once, right now |
| `/nrev-build` | Set up something that runs on a schedule or a trigger |
| `/nrev-fix` | Something broke |

### Try one of these

```
/nrev-data pull the comments on https://linkedin.com/posts/... and tell me
which commenters work at companies with 200+ employees
```

```
/nrev-data what's the latest news and hiring activity at stripe.com,
ramp.com, and mercury.com
```

```
/nrev-build every Monday, check my target accounts for new job postings
mentioning RevOps, and post the matches to #gtm-signals in Slack
```

> **What you can pull instantly today:** LinkedIn posts, comments, and
> reactions, plus company news, job postings, tech stack, customers, and
> partners. Finding new people, contact details, and web research are coming
> soon — for now those go through `/nrev-build`.

---

## Scale it up

Pull it once, look at what came back, then automate the version that was
actually useful:

```
/nrev-build turn that into a weekly run and save the results to a table
```

---

## What it costs

Runs use credits. Two things keep that predictable:

- **You see the price first.** Anything that spends comes back with an
  estimate and waits for your go-ahead.
- **Building is cheap.** While you're setting a workflow up, it runs on a
  small sample. Only the final run is full size.

Ask *"what will this cost?"* or *"how many credits do I have left?"* any time.

---

## If something breaks

Type `/nrev-fix` and describe what happened. It knows where to look.

Still stuck? [Open an issue](https://github.com/nurturev/nrev-mcp/issues).
