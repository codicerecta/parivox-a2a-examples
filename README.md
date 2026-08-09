# parivox-a2a-examples

Reference [A2A](https://a2a-protocol.org) agents that Parivox agents can delegate work to.

Each agent in this repository is an independent, self-contained A2A server: it publishes
an Agent Card, accepts a task, streams progress, and returns an artifact. Nothing here
depends on Parivox internals — these agents are reachable only over the A2A protocol, and
that is the whole point. If you can serve a conforming Agent Card and stream task updates,
a Parivox agent can delegate to you.

## Agents

| Agent | Transport | Model / search |
|---|---|---|
| [`research-specialist-sdk`](agents/research-specialist-sdk) | `a2a-sdk[http-server]` | OpenAI web search |
| `research-specialist-starlette` *(planned)* | plain Starlette + `sse-starlette` | Gemini grounding |

The two are implemented independently on purpose. A client that works against both is
exercising the protocol, not a shared library.

## Writing your own

[**Integrating with Parivox**](docs/integrating-with-parivox.md) covers what a conforming
A2A agent still has to get right to be a good delegate — keepalive cadence, artifact and
terminal-state ordering, progress text that a person is reading live, and the handful of
current client constraints that the protocol itself permits you to violate.

## Protocol notes (A2A 1.0)

Verified against `a2a-sdk` 1.1.2. These bite immediately if you are used to 0.3:

- **`A2A-Version: 1.0` is a required request header.** Omit it and the server assumes
  0.3 and rejects the call with JSON-RPC error `-32009`.
- **Method names are gRPC-style**, not slash-style: `SendMessage`,
  `SendStreamingMessage`, `GetTask`, `CancelTask`, `SubscribeToTask` — *not*
  `message/send` or `message/stream`.
- **Event ordering is strict.** A task stream must emit the `Task` first, then any
  number of `statusUpdate` / `artifactUpdate` events, and end on a terminal status.
  Emitting a status update before the `Task` fails with `-32006`.
- The Agent Card lives at `/.well-known/agent-card.json` and advertises its RPC endpoint
  under `supportedInterfaces`, each with a `url`, `protocolBinding`, and
  `protocolVersion`.

A streamed task looks like this on the wire (SSE `data:` frames, JSON-RPC envelopes):

```
data: {"result":{"task":{"id":"…","status":{"state":"TASK_STATE_SUBMITTED"}}},…}
data: {"result":{"statusUpdate":{"status":{"state":"TASK_STATE_WORKING",…}}},…}
data: {"result":{"artifactUpdate":{"artifact":{"name":"report.md","parts":[…]}}},…}
data: {"result":{"statusUpdate":{"status":{"state":"TASK_STATE_COMPLETED"}}},…}
```

## Running an agent locally

```bash
cd agents/research-specialist-sdk
python -m venv .venv && .venv/bin/pip install -r requirements.txt
A2A_ALLOW_ANONYMOUS=1 .venv/bin/python -m app.main
```

Then fetch the card:

```bash
curl localhost:8080/.well-known/agent-card.json
```

Or run it in a container:

```bash
docker compose up research-specialist-sdk
```

## Configuration

Every agent is configured **entirely through environment variables** — there is no shared
config source and no private package dependency. See each agent's `.env.example`.

| Variable | Meaning |
|---|---|
| `A2A_API_KEY` | Shared secret required in the `X-API-Key` header on RPC calls |
| `A2A_ALLOW_ANONYMOUS` | Set to `1` to run without an API key (local development and CI only) |
| `PORT` | Listen port, default `8080` |

An agent refuses to start unless one of `A2A_API_KEY` or `A2A_ALLOW_ANONYMOUS` is set, so
an unauthenticated deployment has to be a deliberate act rather than an omission.

## Conformance

Conformance is a property of the code, not of a particular deployment, so CI runs the
official [`a2a-tck`](https://github.com/a2aproject/a2a-tck) against each agent started as
a local container:

```bash
./run_tck.py --sut-host http://localhost:8080 --transport jsonrpc --level must
```

**What "passing the TCK" does and does not mean here.** The `must` level is not purely a
protocol check. Per the TCK's own [`docs/SUT_REQUIREMENTS.md`](https://github.com/a2aproject/a2a-tck/blob/main/docs/SUT_REQUIREMENTS.md),
a number of its tests drive prescribed behaviour through magic `messageId` prefixes — the
agent is expected to return an artifact whose text is exactly `Generated text content`, a
file named `output.txt`, a data part of `{"key": "value", "count": 42}`, and to enter
`INPUT_REQUIRED` on cue. Those tests assert that an agent behaves like the TCK's own
reference SUT; they are not statements about protocol conformance, and implementing them
would mean shipping test-harness branches inside an example agent.

CI therefore enforces the protocol-level subset — 67 `must` tests covering card discovery,
task lifecycle, streaming order, error-code mapping, and cancellation — and deselects the
scenario-fixture tests explicitly, with the reasoning recorded in
[`ci.yml`](.github/workflows/ci.yml). Note also that the TCK's headline compatibility
percentage counts unimplemented fixture scenarios against the agent, so that number reads
lower than the enforced result.

## Deployment

Each agent has its own CloudFormation stack under `infra/` and deploys as a single-replica
ECS Fargate service, discoverable through AWS Cloud Map private DNS.

Two constraints are deliberate and load-bearing:

- **Single replica.** `a2a-python` [#1135](https://github.com/a2aproject/a2a-python/issues/1135):
  a custom `QueueManager` is silently ignored by the v2 handler, which breaks cross-replica
  streaming and resubscription. Do not scale these out without revisiting it.
- **Never publicly exposed.** `a2a-python` [#1159](https://github.com/a2aproject/a2a-python/issues/1159):
  a live-task Subscribe/Cancel owner-isolation bypass, present in 1.1.2. These services are
  reachable only from within their VPC, from an explicitly allowed client security group.
  There is no load balancer and no public listener.

## Security

Report vulnerabilities privately to the repository owners. Do not commit credentials,
account identifiers, ARNs, or internal hostnames — CI runs `gitleaks` on every push, and a
credential that lands in history is not fixable by a follow-up commit.

## License

[Apache-2.0](LICENSE).
