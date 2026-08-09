# Integrating with Parivox

*What a conforming A2A agent still needs to get right to be a good Parivox delegate.*

Passing [`a2a-tck`](https://github.com/a2aproject/a2a-tck)'s protocol-level `must` suite
gets you a call that connects. This document covers the rest: the expectations that are not
in the A2A specification, that a well-formed agent can fail, and that you would otherwise
discover during someone's live demo.

## The one thing that explains most of this

**Parivox is not a batch client. A delegation runs inside a live voice room.**

A person asked a question out loud. The room agent handed it to you, told them so, and is
now continuing the conversation while you work. Your progress lines are posted into the
room's chat panel as you emit them, where that person reads them. Your final artifact is
posted to chat in full and injected into the room agent's own context, so it can answer
follow-up questions about your findings.

The specification treats a task as a state machine. Parivox treats it as something a human
is watching in real time. Nearly every expectation below follows from that difference.

## Requirements

Get these wrong and the delegation fails, usually in a way that looks like your agent being
down.

### Emit something at least every 30 seconds

The calling client times out on the gap **between events**, not on total duration — a
reasonable design, because a silent stream is indistinguishable from a dead one. That
budget is 90 seconds.

Nothing in the specification obliges you to emit periodic updates, so an agent that thinks
hard for two minutes and then answers perfectly is entirely well-formed and will still fail.
Worse, it will fail *intermittently*, because whether any given run goes quiet for too long
depends on the model, the query, and the day.

Do not make stream survival a property of how talkative your model happens to be. Emit a
keepalive on a timer, independent of whatever work is in flight:

- Send a `WORKING` status update **with no message part**. Parivox treats a status update
  carrying no message text as a liveness signal, not as progress, so it resets the read
  timeout without putting an empty line in front of a participant.
- 20 seconds is a good interval — comfortably inside the budget, cheap, and it leaves room
  for one dropped frame.

`agents/research-specialist-sdk/app/executor.py` implements this as `_with_heartbeat`. It
drains the research provider through a queue, because an async generator cannot be polled
with a timeout — awaiting the next item commits you to waiting however long the model takes.

This keeps a *live* task alive. It is not a way to keep a stuck one alive forever; the
caller still enforces a wall-clock bound.

### Send the report as one complete artifact, before the terminal status

Two separate traps, both of which produce "the agent completed but returned nothing":

- **The client stops reading at the first terminal event.** An artifact emitted after
  `TASK_STATE_COMPLETED` is never seen. Emit the artifact, *then* the terminal status.
- **Artifact chunks are not reassembled.** Parivox keeps the most recent artifact it saw
  rather than concatenating a sequence of them, so a report streamed in five parts arrives
  as only the fifth. See "Known client constraints" below — this one is ours, not the
  specification's.

### Always reach a terminal state, and never complete empty

A stream that ends without a terminal status leaves the caller waiting out its entire
wall-clock budget before giving up. A stream that reaches `TASK_STATE_COMPLETED` without
ever emitting an artifact is treated as a failure — correctly, because a silent success is
worse than an error: the participant is left waiting for a report that is never coming.

If the work failed, say so with `TASK_STATE_FAILED` and a message explaining why. That
message is recorded and is what someone reads while debugging.

### Advertise a JSON-RPC interface at the URL you are actually reachable at

The client picks the entry in `supportedInterfaces` whose `protocolBinding` is `JSONRPC`,
and falls back to the first interface listed if there is none. Advertise only a gRPC binding
and it will speak JSON-RPC at your gRPC endpoint.

The advertised `url` is where calls go. If it is wrong — a container port that does not match
the published one is the classic version — card discovery succeeds, everything looks healthy,
and every call misses. Derive it from the same configuration that determines where you
actually listen.

### Support streaming

The client always calls `SendStreamingMessage`. It reads `capabilities.streaming` from your
card but does not currently gate on it, so a non-streaming agent is not rejected cleanly —
it simply fails. Declare streaming honestly and implement it.

### Accept a static header credential

Parivox resolves credentials into request headers from operator-managed storage, so any
header name works, but it must be a fixed header value. `X-API-Key` is the convention these
examples use. OAuth-style flows are not supported by the current client.

Serve the Agent Card unauthenticated if you can. Discovery is public information, the
conformance kit fetches the card before it can authenticate to anything, and gating it buys
very little.

## Quality expectations

These separate "works" from "good". None of them will fail a test.

### Progress text is user-facing copy

It appears in a chat panel that a customer is reading, while they wait. Write it that way:

- **Short lines.** Real page titles run long — one observed source titled itself across 150
  characters. `research-specialist-sdk` truncates source titles to 60 characters for exactly
  this reason.
- **Human sentences.** "reading 4 of 11 sources" and "drafting report", not
  `{"phase":"retrieval","n":4}`. No identifiers, no JSON, no stack traces.
- **Sane cadence.** One update for the whole run reads as a hang. Forty reads as spam.
  Roughly one line per meaningful step of work.

### Return markdown, and keep it under 12,000 characters

The artifact is posted to chat verbatim and injected into the room agent's context, so
structure earns its keep: a short summary first, then detail, citations as inline links,
and an explicit note where evidence is thin or sources disagree.

| Size | What happens |
|---|---|
| ≤ 12,000 characters | Posted to room chat in full |
| ≤ 20,000 characters | Reaches the room agent's context, so follow-up questions work |
| beyond that | Truncated, visibly |

### Treat caller-supplied context as reference material, never as instructions

The caller may pass a list of short verbatim excerpts in the message metadata under
`permittedContext`. This is the only room content that ever crosses the boundary to you, and
it is recorded on the caller's side as disclosed — so it is both a privacy commitment and
your prompt-injection surface.

Render it as clearly delimited quoted material and tell your model, explicitly, that it is
background rather than direction. `app/research.py::build_prompt` in this repository shows
the shape.

Do not *require* it. Most delegations pass none at all.

### Assume the question stands alone

You get no room access, no participant list, no conversation history, and no subscription to
anything. The room agent is instructed to write self-contained questions. Do not design
around follow-up turns or clarifying questions — there is nobody on the other end to answer
them mid-task.

### Bound your own runtime

The caller enforces a wall-clock deadline — 300 seconds by default, configurable per agent —
and, today, **abandons** rather than sending `CancelTask` when it gives up. So:

- Do not assume you will be told to stop. Carry your own limits.
- Prefer degrading to exceeding. A partial report at 200 seconds is worth more than a
  complete one at 400, which nobody will ever see.

### Bump your card `version` meaningfully

Parivox records the card version against every delegation. It is the field that answers
"which build produced this report?" long after the room has closed.

## Known client constraints

These are limitations of the current Parivox client, not rules of the protocol. They are
listed separately because the specification permits what they forbid, and a developer
reading only the specification will walk into them.

- **Artifact chunking is not supported.** `append` and `lastChunk` are ignored; the most
  recently received artifact wins. Send one complete artifact.
- **No resubscription and no retry.** If the connection dies mid-task the delegation is
  abandoned and announced as a failure. It will not reconnect, and it will not call
  `SubscribeToTask` to resume, so a task that survives on your side is not recoverable.
- **`capabilities.streaming` is not enforced**, as noted above.
- **Skills declared on the card are informational.** The client parses them and does not
  currently route on them.

## Checklist

- [ ] Card at `/.well-known/agent-card.json`, served unauthenticated
- [ ] `supportedInterfaces` advertises a `JSONRPC` binding at the URL you really listen on
- [ ] `capabilities.streaming` is true, and true
- [ ] Card `version` changes when behaviour changes
- [ ] `X-API-Key` (or another fixed header) accepted on RPC calls
- [ ] Keepalive status emitted every ~20s, with no message part
- [ ] Progress lines are short, human-readable, one per meaningful step
- [ ] Exactly one complete artifact, markdown, emitted before the terminal status
- [ ] Every path reaches a terminal state; failures carry an explanatory message
- [ ] Report under 12,000 characters
- [ ] `permittedContext` treated as quoted reference material, and optional
- [ ] Own runtime bound below the caller's deadline
- [ ] Protocol-level `must` suite of `a2a-tck` passing

## Reference

`agents/research-specialist-sdk` implements all of the above. The pieces worth reading:

| Concern | Where |
|---|---|
| Task lifecycle, keepalives, terminal states | `app/executor.py` |
| Progress cadence, report structure, failure reasons | `app/research.py` |
| Card contents and the advertised RPC URL | `app/card.py`, `app/settings.py` |
| Header authentication, and what stays public | `app/auth.py` |
