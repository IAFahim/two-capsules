# The Atlas

Forty-odd mechanism maps. Each one answers *how does this machine work* — never *what happened one time*.

A map earns its place only if you cannot hold the mechanism in your head without it. If a paragraph does the job, the paragraph wins.

**Status:** `built` = delivered at 9/9 showcase, 0 errors, 0 warnings · `writing` = an agent has it · `planned` = specced here, not built.

**Front door:** `00-world` (architecture, built) — the six regions as terrain, with the standout maps named inside each. This is the landing page.

---

## I · The machine underneath

You already know cache lines and clock edges. These maps say which hardware idea each ECS mechanism actually is — and where the analogy stops being true.

| # | Map | Type | The mechanism it shows | Status |
|---|---|---|---|---|
| 01 | `01-machine` | architecture | The whole stack, one screen: CPU → chunks → worlds → wire | built |
| 05 | `05-chunk-layout` | architecture | Inside one 16 KiB chunk: header, entity array, per-component arrays, why the stride is the whole point | built |
| 06 | `06-archetype-graph` | architecture | An archetype is a *set*. Adding a component is an edge walk to a different chunk, and that is why it costs a structural change | planned |
| 07 | `07-entity-identity` | architecture | `Entity` is index plus version. The version field is what makes a stale reference detectable rather than catastrophic | planned |
| 08 | `08-query-matching` | dataflow | `EntityQuery` → matched archetypes → chunk iteration → enableable bitmask. Where the filtering actually happens, and what it costs | planned |
| 09 | `09-job-graph` | workflow | The dependency graph the safety system builds for you, and how a read/write conflict becomes a `JobHandle` edge | planned |
| 10 | `10-burst-pipeline` | dataflow | C# → IL → Burst → native, and the exact things that silently kick you back to Mono | planned |
| 11 | `11-sync-points` | lifecycle | The stall: structural change → complete all tracked jobs → resume. Every sync point is a barrier you paid for | planned |
| 12 | `12-ecb-playback` | workflow | Record now, play back at a known point. Ordering, determinism, and the parallel writer | planned |

## II · World, systems, and the update loop

**Highest-value gap.** Nothing here is built yet, and for a Unity newcomer this region is where "why is my entity missing" actually lives. `13-bootstrap` and `16-baking` outrank several region IV maps.

| # | Map | Type | The mechanism it shows | Status |
|---|---|---|---|---|
| 13 | `13-bootstrap` | workflow | `ICustomBootstrap` → world creation → system discovery → placement → the player-loop splice. Where your world actually comes from; the tree itself belongs to 14 | planned |
| 14 | `14-system-groups` | architecture | The full update tree — and the two fixed-step groups nobody separates: prediction declares `UpdateBefore` the ordinary fixed step, and netcode nests a second one inside itself | planned |
| 15 | `15-system-lifecycle` | lifecycle | `OnCreate` → gate → `OnUpdate` → `OnDestroy`. The gate is a **level** (`Enabled && ShouldRunSystem()`, one `if`); `OnStartRunning`/`OnStopRunning` are **edges** off that one-bit latch | planned |
| 16 | `16-baking` | dataflow | GameObject → `Baker` → entity → SubScene file → runtime load. The compile step nobody tells you is a compile step | planned |
| 17 | `17-subscene-streaming` | lifecycle | Section load and unload, `RequestSceneLoaded`, and the bit nobody mentions: a section is deserialized in a **separate world your systems cannot see** | planned |
| 18 | `18-blob-assets` | dataflow | `BlobBuilder` → `BlobAssetReference`. Relative pointers, position independence, why a blob can be memory-mapped | planned |

## III · The wire

| # | Map | Type | The mechanism it shows | Status |
|---|---|---|---|---|
| 02 | `02-keypress` | dataflow | One `float2` of intent and every local consequence it causes | built |
| 03 | `03-connect` | sequence | Handshake → approval → network id → in-game | built |
| 19 | `19-netcode-worlds` | architecture | Client world, server world, thin client — and why `IsHost` means one world with both flags | planned |
| 20 | `20-tick-timeline` | dataflow | Input target, client predicted, server authoritative, interpolated. **Four** timelines, collapsing to three only when *effective* input latency is zero — which a high-ping player never gets | planned |
| 21 | `21-snapshot-pipeline` | dataflow | Importance sort → baseline delta → packet fill → receive → apply. A snapshot is not the world; it is as much of the world as fits | planned |
| 22 | `22-prediction-loop` | workflow | Restore, replay, carry on — and why there is no *compare* in the hot path | built |
| 23 | `23-interpolation` | dataflow | Buffer mechanics only — waypoints, the 20-tick extrapolation clamp, jitter scaling, `InterpolationDelayCorrectionFraction`. The domain choice belongs to 41 | planned |
| 41 | `41-clock-domains` | architecture | Two time domains on one client, ownership as the selector, and why prediction is contagious | built |
| 24 | `24-rpc` | sequence | `IRpcCommand` → serialize → deliver → invoke. The reliable channel next to the unreliable one | planned |
| 36 | `36-ghost-codegen` | dataflow | What the source generator reads and what it emits | writing |
| 39 | `39-connection-topologies` | architecture | Listen server, dedicated, relay, NAT punchthrough — and the driver stack under all of them | writing |

## IV · Feel, scale, and the adversary

| # | Map | Type | The mechanism it shows | Status |
|---|---|---|---|---|
| 33 | `33-predicted-physics` | workflow | Physics inside the rollback loop, and what determinism costs | writing |
| 34 | `34-predicted-spawning` | workflow | Spawn on the client, classify against the server's ghost, reconcile | writing |
| 35 | `35-lag-compensation` | sequence | Rewinding the collision world to the tick the shooter actually saw | writing |
| 37 | `37-interest-management` | dataflow | Importance scaling as bus arbitration: everyone wants the packet, priority decides | writing |
| 38 | `38-profiling` | workflow | Which counter to read, and what each of the four usual bottlenecks looks like in it | writing |
| 40 | `40-cheat-surface` | architecture | The trust boundary drawn once, with every hole in it labelled | writing |

## V · The BovineLabs stack

| # | Map | Type | The mechanism it shows | Status |
|---|---|---|---|---|
| 25 | `25-grove-vm` | dataflow | Nodes → authoring objects → blob image → generated switch dispatch. Grove is a bytecode VM and this is its pipeline | planned |
| 26 | `26-grove-state` | architecture | `GroveState` as writable RAM: a hash map living on a `DynamicBuffer` | planned |
| 27 | `27-canopy-hsm` | lifecycle | The active *path* from root to leaf, and what a named jump does to it | planned |
| 28 | `28-nerve-session` | architecture | Bootstrap, SubSceneSet routing, controller and controlled pawn | planned |
| 29 | `29-anchor-ui` | dataflow | UI Toolkit bound to entity data without a managed round trip per frame | planned |
| 30 | `30-core-containers` | architecture | The container and utility layer everything above stands on | planned |

## VI · Our two capsules

| # | Map | Type | The mechanism it shows | Status |
|---|---|---|---|---|
| 04 | `04-clientgraph` | lifecycle | The client state graph: connecting → gate → gameplay → disconnected | built |
| 31 | `31-assembly-graph` | architecture | Three asmdefs, their constraints, and the silent death an all-or-nothing `defineConstraints` causes | planned |
| 32 | `32-mppm` | architecture | Virtual players, shared `EditorPrefs`, and per-player roles | planned |

---

## Building one

```bash
cd ~/.claude/skills/archify
node bin/archify.mjs validate <type> ~/GitHub/two-capsules/diagrams/src/NN-slug.json --quality showcase --json
node bin/archify.mjs deliver  <type> ~/GitHub/two-capsules/diagrams/src/NN-slug.json \
                              ~/GitHub/two-capsules/diagrams/NN-slug.html --quality showcase --json
```

Then a narration script at `narrate/scripts/NN-slug.json` — `nodes[].short` for hover, `nodes[].deep` for click — and `python3 tools/gen_narration.py`.

**The lesson from the first four:** when the validator throws a wall of geometry errors, the graph is too dense. Delete nodes and edges. Do not reach for `via`, `channelX`, or `labelDy` until exactly one error remains and you know which one it is. Fighting the layout loses every time; restructuring wins on the first try.
