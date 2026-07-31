# 41 · Clock domains: choosing a ghost mode

Chapters 17 and 18 described prediction and interpolation as two techniques. They are not two
techniques. They are **two clock domains**, and every ghost on a client lives in exactly one
of them.

Once you see it that way, the `GhostAuthoringComponent` dropdown stops being a preference and
becomes what it actually is: a decision about which timeline an entity exists on, made per
prefab at bake time, and — for one of the three values — re-made per machine at runtime.

## The two domains

There is exactly one authoritative timeline, and it is on the server. A client is never on it.
A client runs two clocks, both offset from the server, in opposite directions.

```
                 interpolated                     server                predicted
                     tick                          tick                    tick
  ───────────────────●──────────────────────────────●──────────────────────●─────▶
                     │←── RTT/2 + interp buffer ───→│←──── RTT/2 ────→│
                  the past                        truth              the future
```

**Interpolated** — the entity lives in the server's *past*. Snapshots land in a ring buffer;
a read pointer trails the write pointer by a fixed number of ticks; the client lerps between
the two entries bracketing that read position. **The client never simulates this entity.** Not
once, not partially. It is a delay line with an interpolator on the output. Like every elastic
buffer between two clock domains, it can be late but it cannot be wrong. The package says so
in one line: interpolated ghosts "perform no simulation on the client"
(`Runtime/Snapshot/GhostPrefabCreation.cs:45`).

**Predicted** — the entity lives in the client's *future*. Every frame the client restores it
to the last tick the server confirmed and re-simulates every tick from there forward to the
present, re-feeding the recorded input for each replayed tick. Each arriving snapshot is a
checkpoint that either confirms the speculation or forces a different replay result. The
package's own summary is blunt: prediction is "both expensive and non-authoritative"
(`GhostPrefabCreation.cs:54`).

> **⚡ Hardware analogy** — this is a branch predictor with speculative execution and a
> pipeline flush sitting next to an elastic FIFO across a clock domain crossing. You already
> know both circuits. What is new is that you get to choose, per entity, which one an object
> is wired into.

**OwnerPredicted** — the selector. The owning client predicts; every other client
interpolates (`GhostPrefabCreation.cs:82–85`). This is the only setting in NetCode whose effect
*differs between machines*: the same prefab, spawned once on the server, is simultaneously in
different time domains on different clients. That is why staring at one Inspector never
explains the behaviour you are seeing.

## The contagion rule

**A predicted entity cannot correctly interact with an interpolated one.**

They are not simulating the same instant. One is roughly half a round trip ahead of the server;
the other is roughly `RTT/2 + interpolation buffer` behind it. At 60 ms round trip and a
two-tick buffer at 60 Hz that is a gap of about 95 ms. A collision between them is a collision
between two different moments in time, resolved as if they were the same moment. It will look
plausible and be wrong, and it will be wrong differently on every machine.

Therefore: **anything a predicted entity touches must itself be predicted.**

Prediction is contagious. You set out to predict one capsule; you discover you have signed up
to predict the crate it pushes, the platform it rides, the door it opens, and — the moment
physics is involved — every dynamic body sharing its physics world. This is the rule that
wrecks real projects, and it wrecks them late, because a single predicted capsule in an empty
test scene works perfectly.

> **💀 THE TRAP** — the contagion is transitive. Predicting A forces B; B touching C forces C.
> Draw the interaction graph *before* you set the dropdown, and treat the predicted set as a
> connected component of that graph, not as a list of individual choices.

The physics integration makes the same point structurally: a dynamic body that is not a ghost
cannot be rolled back, so NetCode's validation system finds those bodies and errors on them
(`Runtime/Physics/PredictedPhysicsSystemGroup.cs:240`, and chapter 33).

## The settings, and what they actually default to

All four live on `GhostAuthoringComponent`, which is a thin authoring wrapper over
`BaseGhostSettings`. These are the real defaults, from the field initialisers:

| Setting | Default | Traced to | What it decides |
|---|---|---|---|
| `DefaultGhostMode` | `Interpolated` | `Runtime/Authoring/BaseGhostSettings.cs:97` | The domain a ghost lands in when it spawns |
| `SupportedGhostModes` | `All` | `BaseGhostSettings.cs:102` | Which domains it may *ever* occupy |
| `OptimizationMode` | `Dynamic` | `BaseGhostSettings.cs:108` | Delta-compress every tick, or change-check first |
| `Importance` | `1` (min 1) | `BaseGhostSettings.cs:115` | Chunk priority when the packet runs out of room |

Two of those defaults cost you something quietly.

`DefaultGhostMode = Interpolated` means a freshly authored player prefab does **not** predict.
Your capsule will feel exactly one round trip late until you change it. This is the correct
default — most ghosts should be interpolated — but it is not the default you want on the thing
the player is holding.

`SupportedGhostModes = All` is the expensive one. Leaving it at `All` keeps runtime prediction
switching available, and in exchange it **disables the `GhostSendType` optimisation entirely**.
The reasoning is in the package: the server can infer which mode an `OwnerPredicted` ghost is
in on each client — owner predicts, everyone else interpolates — and it can infer the mode of a
ghost whose mask is narrowed at authoring time. It cannot infer the mode of a ghost that might
switch at runtime, so it must serialise identically for everyone
(`Runtime/Authoring/GhostModifiers.cs:41–50`). The internal flag that gates this is literally
`SupportedGhostModes != GhostModeMask.All || DefaultGhostMode == GhostMode.OwnerPredicted`
(`BaseGhostSettings.cs:169`).

So: narrowing the mask, or choosing `OwnerPredicted`, lets the server stop sending
predicted-only components (velocity, for instance) to clients that are only interpolating.
`All` pays full bandwidth for flexibility you probably never use.

The interpolation buffer that defines "the past" is two network ticks by default
(`ClientTickRate.InterpolationTimeNetTicks = 2`,
`Runtime/PredictionTicking/NetworkTimeSystem.cs:214`), with extrapolation clamped at 20
simulation ticks (`NetworkTimeSystem.cs:215`).

## The cost asymmetry

| | Interpolated | Predicted | OwnerPredicted |
|---|---|---|---|
| Client simulation | none | full, every frame | 1 entity full, N−1 none |
| Per-frame CPU | 2 buffer reads + a lerp | `ticks_behind × entities × systems` | `ticks_behind × 1 × systems` |
| Determinism required | no | yes | for the owned entity only |
| Bandwidth | can skip predicted-only fields | can skip interpolated-only fields | server infers per client |
| Correctness | always between real data | speculative, corrected | both, per machine |

The term that surprises people is `ticks_behind`. It is **the same for every predicted ghost**,
because there is one prediction loop for the whole set and it rewinds as far as the oldest
member requires. Doubling the predicted set doubles the cost of every replayed tick. A 200 ms
spike at 60 Hz is 12 replayed ticks; with 40 predicted entities that is 480 entity-tick
simulations crammed into one frame.

You do not control `ticks_behind` — that is round-trip time. You control the set size. That is
the only honest lever, and the contagion rule is exactly what makes it leak.

## The decision procedure

1. **Does the player's own input have to feel instant on this entity?**
   No → `Interpolated`. Stop. This covers most of your world.
2. **Yes, and exactly one client owns it?** → `OwnerPredicted`. This is the player capsule,
   the player's vehicle, the player's cursor.
3. **Yes, and every client needs it responsive?** → `Predicted`. A ball in a sports game; a
   shared physics prop; anything two players contend for.
4. **Now close the set.** List everything a predicted entity can touch. Every one of those
   must be `Predicted` too. Repeat until the list stops growing. If it does not stop growing,
   you have discovered that you are building a fully predicted game — decide that on purpose,
   not by accident.
5. **Then narrow the mask.** If a ghost will never switch modes at runtime, set
   `SupportedGhostModes` to just the one it uses and take the bandwidth back.

> **🔬 Probe** — in the Multiplayer Play Mode window, run two clients and watch the *same*
> ghost in both. Under `OwnerPredicted` one of them has `PredictedGhost` on that entity and the
> other does not. That single asymmetry is the whole chapter, visible in the Entity Inspector.

Two constraints worth knowing before they surprise you: an `OwnerPredicted` ghost cannot be
placed as a scene instance — the baker throws
(`Runtime/Authoring/Hybrid/GhostAuthoringComponentBaker.cs:78`) — and pre-spawned ghosts are
forced to `Predicted` with a `Static` optimization mode regardless of what you authored
(`Runtime/Snapshot/Prespawn/PrespawnHelper.cs:55–57`). Runtime prediction switching also
refuses to run on an `OwnerPredicted` ghost, because ownership already decides the mode
(`Runtime/GameObjectLayer/Facades/PredictionSwitching.cs:190`).

→ [24 · The debugging playbook](24-debug-playbook.md)
