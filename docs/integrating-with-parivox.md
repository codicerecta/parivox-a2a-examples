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

### Emit something every 30 seconds — a courtesy, not a requirement

**Corrected 2026-08-09.** This section used to say a quiet stream is cut off at 90 seconds,
and it was true: the client timed out on the gap **between events** rather than on total
duration, so an agent that thought hard for two minutes and then answered perfectly was
well-formed and failed anyway — intermittently, depending on the model, the query and the
day. That is fixed. The read timeout no longer undercuts the delegation's configured
wall-clock budget, so silence inside your budget is fine and nothing in this guide obliges
you to emit periodic updates.

Send keepalives anyway, for a reason that has nothing to do with the connection: **a person
is watching.** Four minutes of nothing in the chat panel reads as a delegation that died,
whatever the client believes. Emit one on a timer, independent of whatever work is in flight:

- Send a `WORKING` status update **with no message part**. Parivox treats a status update
  carrying no message text as a liveness signal, not as progress, so it does not put an
  empty line in front of a participant.
- 20 seconds is a good interval — cheap, and it leaves room for one dropped frame.

`agents/research-specialist-sdk/app/executor.py` implements this as `_with_heartbeat`. It
drains the research provider through a queue, because an async generator cannot be polled
with a timeout — awaiting the next item commits you to waiting however long the model takes.

The caller still enforces a wall-clock bound, and that bound is now the only one.

### Send the report before the terminal status

**The client stops reading at the first terminal event.** An artifact emitted after
`TASK_STATE_COMPLETED` is never seen, and the delegation is announced as a failure. Emit
the artifact, *then* the terminal status.

### Chunk the artifact if you want to, but close it with `lastChunk`

Streaming one artifact across several `artifactUpdate` frames is supported: the first frame
carries the artifact, each continuation sets `append: true`, and the last sets
`lastChunk: true`. Parivox concatenates the fragments per `artifactId` and delivers the
whole report. (Until August 2026 it did not — it kept the newest frame, so a report streamed
in five parts arrived as only the fifth. If you are reading an older copy of this guide that
says "send one complete artifact", that is why.)

Three things to get right:

- **`append` and `lastChunk` go on the update, beside `artifact`** — not inside the
  artifact object. An `append` that lands in the wrong place reads as a fresh artifact and
  replaces everything before it.
- **Keep the `artifactId` stable across the chunks.** It is the key the fragments are
  reassembled under. Frames with different ids are treated as different documents, and the
  most recently updated one is what reaches the room.
- **Fragments are joined verbatim, with no separator inserted.** Put your own whitespace in:
  if a boundary falls between two words or two markdown blocks, the space or newline has to
  be in one of the fragments.

A chunked artifact that never receives its `lastChunk` is still delivered — partial text
beats nothing — but it is logged and flagged in the audit event as incomplete.

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

### Declare `capabilities.streaming` honestly — either answer is now supported

**Corrected 2026-08-09.** This section used to say the client always calls
`SendStreamingMessage` and does not gate on the flag, so a non-streaming agent simply failed.
It gates on it now. Declare `streaming: false` and the client uses `SendMessage` followed by
`GetTask` polling instead; you never see a `SendStreamingMessage`.

What is still load-bearing is that the flag be *true*. It is read from your card before any
call is made, so declaring streaming you do not implement gets you a method you cannot serve,
and declaring none you do implement costs a participant granularity — polled progress appears
at the poll interval and only when your status message text changes.

Two notes for the non-streaming path:

- `GetTask` is called with `{"id": "<task id>"}`. The task you return from `SendMessage` must
  carry an `id`, or there is nothing to poll and the delegation fails immediately.
- Answering `SendMessage` with a bare `Message` instead of a `Task` is supported, and treated
  as the finished report. If your work is short enough not to need a task lifecycle, this is
  the least ceremony available.

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
anything. The room agent is instructed to write self-contained questions.

`TASK_STATE_INPUT_REQUIRED` is handled as of August 2026, but not as a conversation. Enter it
and the client stops reading, relays your status message into the room as a question, and puts
it in front of the model — so the participant may well answer, and the room agent may well ask
you again in a **fresh task** with more detail. Your paused task is abandoned, not resumed and
not cancelled. So it is worth asking a good question when you genuinely cannot proceed, and it
is not worth designing around a multi-turn exchange, because there will not be one.

### Return text, a file, or structured data — all three arrive now

**Corrected 2026-08-09.** Until then the client read only `parts[].text`, so a file or data
part produced an empty report and the delegation was announced as a failure, silently.

A2A 1.0's `Part` is a single message with a `content` oneof — `text`, `raw` (base64 bytes),
`url` — plus `data`, `filename` and `mediaType`. What each one does now:

| Part carries | What reaches the room |
|---|---|
| `text` | Verbatim. Still the right default for a report |
| `raw` + a textual `mediaType`, under 20 KB | Decoded and inlined |
| `raw`, binary or unlabelled, or over 20 KB | Described, not decoded |
| `data` | Rendered as a fenced JSON block, capped at 2,000 characters |
| `url` | **Rendered as a link. The address is never fetched** |

That last row is deliberate and will not change: Parivox room agents run inside a VPC whose
network position is the point of the trust boundary between us, so dereferencing an address
of your choosing from there is not something we will do for you. If the participant needs to
see something, either put it in the artifact or expect them to open the link themselves —
from their own browser, on their own network.

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

- **The reassembled report is capped.** Chunks are accumulated up to a ceiling a little
  above the 12,000-character chat limit and then dropped, so chunking is not a way to
  deliver a longer report than a single artifact could. Bound your own output.
- **At most eight artifacts are tracked per delegation.** Past that the least recently
  updated buffer is discarded. One report is the expected shape.
- **No resubscription and no retry.** If the connection dies mid-task the delegation is
  abandoned and announced as a failure. It will not reconnect, and it will not call
  `SubscribeToTask` to resume, so a task that survives on your side is not recoverable.
- **Skills declared on the card are informational.** The client parses them and does not
  currently route on them.
- **`CancelTask` is never sent.** Whether the caller times out, the room closes, or you pause
  for input, your task is abandoned where it stands. Nothing tells you to stop.
- **OAuth is not supported.** A fixed header credential is the only scheme; see above.
- **Progress text is rewritten before display.** Newlines and other whitespace collapse to
  single spaces, Unicode control and format characters are dropped, and the line is cut at 300
  characters. It goes into a panel a customer is reading, so it is treated as untrusted. Send
  one short single-line sentence and this changes nothing.
- **URLs in progress text are replaced with `[link removed]`.** Anything the chat panel would
  turn into a clickable link — a bare `http(s)://` or `www.` run — is stripped, because a status
  line from an agent we did not write is not something a participant should be invited to click.
  A scheme-less mention like `uscis.gov` survives, since the panel does not linkify it either.
  **Put your sources in the artifact, not in the progress line**, where they keep working links
  and are the payoff the delegation exists to deliver.
- **A relayed clarifying question is cut at 600 characters**, under the same whitespace and
  control-character treatment. Links in it currently survive; do not rely on that either way.

## Checklist

- [ ] Card at `/.well-known/agent-card.json`, served unauthenticated
- [ ] `supportedInterfaces` advertises a `JSONRPC` binding at the URL you really listen on
- [ ] `capabilities.streaming` matches what you actually implement, either way
- [ ] Card `version` changes when behaviour changes
- [ ] `X-API-Key` (or another fixed header) accepted on RPC calls
- [ ] Keepalive status emitted every ~20s, with no message part — for the human, not the
      connection
- [ ] If non-streaming: the task returned by `SendMessage` carries an `id`
- [ ] Progress lines are short, human-readable, one per meaningful step
- [ ] One markdown artifact, emitted before the terminal status — whole, or chunked under a
      stable `artifactId` and closed with `lastChunk`
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
