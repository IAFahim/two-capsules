# Two Capsules

**From the first clock edge to two capsules moving on two screens.**

A complete, mechanism-first handbook for building server-authoritative multiplayer with
Unity DOTS, Unity NetCode for Entities, and the BovineLabs stack (Core · Grove · Canopy ·
Nerve · Anchor).

Written for someone who knows hardware and is new to this. Every chapter bridges from
something you already own — cache lines, DMA buffers, branch predictors, PLLs, dynamic
linkers — to the thing it explains.

The goal is not "can you use this". The goal is **could you write it**.

---

## The interactive maps

Standalone HTML — dark/light, pan/zoom, search, focus, guided views, PNG/SVG export.
Open them directly, or serve the folder and browse from [`index.html`](index.html).

| Map | Type | What it shows |
|---|---|---|
| [`01-machine`](diagrams/01-machine.html) | architecture | ECS worlds in one process, who owns what, what may cross |
| [`02-keypress`](diagrams/02-keypress.html) | dataflow | one float2 of intent, from key to pixel |
| [`03-connect`](diagrams/03-connect.html) | sequence | socket → NetworkId → approval → in game → spawn |
| [`04-clientgraph`](diagrams/04-clientgraph.html) | lifecycle | the Canopy state machine that arms replication |

Plus eight deeper **Grove** maps in [`diagrams/grove/`](diagrams/grove/) — the graph VM's
assemblies, import pipeline, per-frame execution, blob layout, source-gen contract,
selectors, custom-node kit, and `GroveState` memory model.

---

## The book

### Part I · The Machine
*What the silicon is actually doing.*

| # | Chapter | The idea |
|---|---|---|
| 00 | [How to read this](book/00-how-to-read.md) | the promise, the conventions |
| 01 | [The first clock edge](book/01-clock-edge.md) | the frame loop is a free-running superloop, not an ISR |
| 02 | [Cache lines all the way down](book/02-cache-lines.md) | a chunk is a DMA buffer; an archetype is its type |
| 03 | [Worlds: six machines in one process](book/03-worlds.md) | separate address spaces, same frame |
| 04 | [Burst and jobs](book/04-burst-and-jobs.md) | a cross-compiler and a scoreboard |

### Part II · The Stack
*The 21 packages, and why four of them exist only to satisfy a compiler.*

| # | Chapter | The idea |
|---|---|---|
| 05 | [The package map](book/05-package-map.md) | the layer cake, and the two forks |
| 06 | [Assemblies and the silent-death trap](book/06-assemblies.md) | `defineConstraints` is all-or-nothing, and silent |
| 07 | [Core](book/07-core.md) | settings routing, a Burst-safe UART, a handle registry |
| 08 | [Grove](book/08-grove.md) | **a bytecode VM with an AOT linker** |
| 09 | [Canopy](book/09-canopy.md) | hierarchical states, validated at author time |
| 10 | [Nerve](book/10-nerve.md) | worlds, subscenes, sessions, ownership, approval |
| 11 | [Anchor and the UI layer](book/11-anchor-ui.md) | the doorbell register, and the import-order trap |
| 12 | [From editor node to runtime bytes](book/12-authoring-to-blob.md) | the 7-stage toolchain and where it dies |

### Part III · The Wire
*A distributed system with a fixed clock and speculative execution.*

| # | Chapter | The idea |
|---|---|---|
| 13 | [Network topology](book/13-topology.md) | the connection entity **is** the state machine |
| 14 | [Ghosts](book/14-ghosts.md) | schema, delta baselines, importance, quantization |
| 15 | [The tick](book/15-the-tick.md) | three clocks, wrapping counters, partial ticks |
| 16 | [Commands](book/16-commands.md) | **send intent, never results** |
| 17 | [Prediction and rollback](book/17-prediction.md) | a branch predictor with a pipeline flush |
| 18 | [Interpolation](book/18-interpolation.md) | an elastic buffer between clock domains |

### Part IV · Our Game
*The full trace, on real code.*

| # | Chapter | The idea |
|---|---|---|
| 19 | [Connection lifecycle](book/19-connection-lifecycle.md) | eight steps, and the two silent gaps |
| 20 | [Keypress to pixel](book/20-keypress-to-pixel.md) | 19 hops, both worlds, three different "nows" |
| 21 | [The client graph](book/21-client-graph.md) | Grove + Canopy + Nerve doing one real job |
| 22 | [Ownership and spawning](book/22-ownership.md) | controller vs pawn, and the four-layer wiring |
| 23 | [Two players on one machine](book/23-mppm.md) | MPPM, shared EditorPrefs, multiplayer roles |

### Part V · Internals, Options, and Judgement
*The datasheet, every knob, and how to choose.*

| # | Chapter | The idea |
|---|---|---|
| 28 | [Grove internals, in full](book/28-grove-internals.md) | node taxonomy, blob bytes, source-gen contract, variants |
| 29 | [Every knob in NetCode](book/29-netcode-settings.md) | tick rates, ghost fields, quantization, importance, relevancy |
| 30 | [How it is made to feel nice](book/30-feel.md) | the ten perception mechanisms, ranked |
| 31 | [When to use what](book/31-when-to-use-what.md) | ten decision tables |
| 32 | [Tying it together](book/32-tying-it-together.md) | one feature, end to end, touching every layer |

### Part VI · Mastery

| # | Chapter | The idea |
|---|---|---|
| 24 | [The debugging playbook](book/24-debug-playbook.md) | the silent-failure index and the universal ladder |
| 25 | [The Linux library saga](book/25-linux-libs.md) | sonames, and why `RUNPATH` is not transitive |
| 26 | [Extending the stack](book/26-extend.md) | five recipes, four declarations each |
| 27 | [Build your own](book/27-build-your-own.md) | five layers, what each really costs |

---

## The five sentences

If you keep nothing else:

1. **A chunk is a DMA buffer and an archetype is its type.**
2. **A blob is position-independent code; `GroveState` is its RAM.**
3. **`Simulate` is a clock-enable line.**
4. **Send intent, never results.**
5. **Nobody on screen is seeing "now", and your job is to make that invisible.**

---

## Reading it locally

```bash
python3 -m http.server 8765
# → http://127.0.0.1:8765/
```

Markdown renders on GitHub with all Mermaid diagrams inline. The `.html` maps are
self-contained — no CDN, no network, no build step.

## Regenerating the maps

```bash
npx skills add tt-a1i/archify -g
ARCH=~/.claude/skills/archify/bin/archify.mjs

node $ARCH deliver architecture diagrams/src/01-machine.architecture.json   diagrams/01-machine.html    --quality showcase
node $ARCH deliver dataflow     diagrams/src/02-keypress.dataflow.json      diagrams/02-keypress.html   --quality showcase
node $ARCH deliver sequence     diagrams/src/03-connect.sequence.json       diagrams/03-connect.html    --quality showcase
node $ARCH deliver lifecycle    diagrams/src/04-clientgraph.lifecycle.json  diagrams/04-clientgraph.html --quality showcase
```

All four pass showcase validation: 9/9 artifact checks, 0 errors, 0 warnings.

## Source of truth

Behaviour described here is traced from the packages in `Library/PackageCache/` of the
companion project, and verified at runtime through the editor CLI. Where a chapter states
that a system does something, the file path is given — **the file wins.**

## Licence

Prose, diagrams, and diagram sources: **MIT**.

The packages described remain their authors'. BovineLabs Core, Grove, Canopy, Nerve, and
Anchor are commercial products; this book documents behaviour and cites paths, it does not
reproduce them. Buy them — the book is much better with the sources open beside it.
