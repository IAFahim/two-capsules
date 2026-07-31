# 29 · Every knob in NetCode

A reference chapter. Each setting: what it does, what it costs, and how to choose.

## 1 · Tick and send rate — `ClientServerTickRate`

| Field | Default | Meaning |
|---|---|---|
| `SimulationTickRate` | 60 | simulation ticks per second |
| `NetworkTickRate` | = sim rate | **snapshot** sends per second |
| `MaxSimulationStepsPerFrame` | 4 | catch-up limit before batching |
| `MaxSimulationStepBatchSize` | 4 | how many ticks may be merged into one step |
| `TargetFrameRateMode` | Auto | whether NetCode caps the server frame rate |

The one people miss: **`NetworkTickRate` is separate from `SimulationTickRate`.** Simulating
at 60 and sending at 20 is the standard shape — three sims per snapshot, one third of the
bandwidth, and interpolation hides the difference.

```
SimulationTickRate 60 ─┬─ tick ─ tick ─ tick ─┬─ tick ─ tick ─ tick ─┬─
NetworkTickRate    20 ─┴──── snapshot ────────┴──── snapshot ────────┴─
```

| Pick | When |
|---|---|
| sim 30 / send 15 | strategy, MMO, large worlds, mobile |
| **sim 60 / send 20** | most action games — good default |
| sim 60 / send 60 | competitive, small player counts, LAN |
| sim 120 / send 60 | fighting games, twitch shooters, expensive |

**Batching** kicks in when the server falls behind: it merges several ticks into one step with
a larger delta. It protects against a death spiral and it degrades input fidelity. If you see
the batching warning outside of editor hitches, your server tick is genuinely too expensive.

## 2 · Ghost authoring — per prefab

| Setting | Options | Choose |
|---|---|---|
| `DefaultGhostMode` | Interpolated · Predicted · **OwnerPredicted** | OwnerPredicted for player pawns; Interpolated for scenery and NPCs; Predicted only if everyone must simulate it |
| `SupportedGhostModes` | All · Interpolated · Predicted | narrow it to skip generating unused serializers |
| `OptimizationMode` | Dynamic · **Static** | Static for things that rarely move — the server skips resending unchanged ghosts entirely |
| `HasOwner` | bool | required for input, ownership filtering, and owner-predicted |
| `SupportAutoCommandTarget` | bool | lets input route automatically to ghosts you own |
| `TrackInterpolationDelay` | bool | needed for server-side lag compensation |
| `GhostGroup` | bool | keep several ghosts atomically in one snapshot |
| `Importance` | int | base priority in the send scheduler |

> **⚡ `OptimizationMode.Static` is the cheapest win in the whole system.** A static ghost that
> has not changed is not serialised, not delta-compared, and not sent. For level props and
> parked vehicles it removes them from the bandwidth budget entirely.

## 3 · Per-field replication — `[GhostField]`

```csharp
[GhostField(Quantization = 1000, Smoothing = SmoothingAction.InterpolateAndExtrapolate,
            SendData = true, Composite = false, SubType = 0, MaxSmoothingDistance = 5f)]
public float3 Position;
```

| Parameter | Effect | Guidance |
|---|---|---|
| `Quantization` | float → scaled int | position 1000 · rotation 1000 · health 1 · **0 = send full float** |
| `Smoothing` | Clamp / Interpolate / InterpolateAndExtrapolate | Interpolate for transforms; Clamp for discrete values |
| `MaxSmoothingDistance` | snap threshold | above this, teleport instead of sliding — set it for anything that can legitimately jump |
| `SendData` | include in snapshot | `false` keeps the field predicted-only |
| `Composite` | treat a struct as one changed/unchanged unit | cheaper change masks for tightly-coupled fields |
| `SubType` | custom serializer id | when you need a domain-specific encoding |

`[GhostComponent(...)]` controls the component as a whole:

| Parameter | Use |
|---|---|
| `PrefabType` | which variants carry it (`AllPredicted`, `Client`, `Server`, `InterpolatedClient`…) |
| `SendTypeOptimization` | send to owner only, non-owner only, or all |
| `OwnerSendType` | e.g. send ammo count only to the owner |
| `SendDataForChildEntity` | opt children in or out |

> **Design rule** — `OwnerSendType = SendToOwner` on anything only the owner needs (ammo,
> cooldowns, private state) is both a bandwidth saving and an anti-cheat measure: information
> you never send cannot be extracted.

## 4 · Quantization, chosen properly

Quantization error is `0.5 / Quantization` in world units.

| Quantization | Error | Fits |
|---|---|---|
| 100 | 5 mm | large vehicles, ships |
| **1000** | 0.5 mm | characters — the usual answer |
| 10000 | 0.05 mm | precision tools, tiny scales |
| 0 | none | 32 bits per component, use sparingly |

Too coarse and the client's prediction disagrees with the dequantized server value **every
tick**, producing constant micro-rollbacks that read as jitter. Too fine and you pay bits for
precision no one can see. Match it to your world scale, not to a habit.

## 5 · Bandwidth scheduling

| Knob | Where | Effect |
|---|---|---|
| `GhostSendSystemData.MaxSendChunks` | server | cap on chunks actually serialised into a snapshot |
| `MaxIterateChunks` | server | cap on chunks the scheduler will even look at |
| `MinSendImportance` | server | floor below which a **chunk** waits, applied *before* importance scaling |
| `FirstSendImportanceMultiplier` | server | boosts newly-spawned ghosts so they appear fast |
| `IrrelevantImportanceDownScale` | server | how hard to deprioritise the barely-relevant |
| `BaseGhostSettings.MaxSendRate` | per prefab | ceiling on how often this ghost type may be sent at all |
| `UseSingleBaseline` | per prefab | skip multi-baseline delta search — cheaper CPU, worse compression |
| `GhostImportance` | per ghost | your own scoring function (distance, team, threat) |
| `GhostRelevancy` | per connection | binary in/out — the ghost does not exist for them |

> **💀 The scheduler thinks in chunks, not entities.** `MaxSendEntities` used to be the
> obvious knob and no longer works — in netcode 6.6 it carries
> `[Obsolete("No longer functional!…")]` and `[ReadOnly]` at
> `Runtime/Snapshot/GhostSendSystem.cs:242`. Reach for `MaxSendChunks` and `MaxIterateChunks`
> instead, and note that `MinSendImportance` gates a whole chunk, not one ghost. If your
> mental model is "cap the entity count," you will set a field that silently does nothing.
> Chapter 37 covers the chunk scheduler properly.

**Importance vs relevancy** is the distinction to internalise:

```
importance  →  "you will get this, later"      (rate control)
relevancy   →  "you will never get this"       (interest management)
```

Use relevancy for zones, rooms, and fog of war. Use importance for everything that is visible
but not equally urgent. Relevancy is also a security boundary: a client cannot wall-hack data
it was never sent.

## 6 · Prediction controls

| Knob | Effect |
|---|---|
| `ClientTickRate.MaxPredictionStepBatchSizeRepeatedTick` | batch replayed ticks after the first |
| `ClientTickRate.TargetCommandSlack` | how far ahead the client runs — bigger absorbs jitter, adds input latency |
| `ClientTickRate.InterpolationTimeNetTicks` | interpolation delay in ticks |
| `ClientTickRate.InterpolationTimeMS` | same, in milliseconds |
| `GhostPredictionSwitchingQueues` | move a ghost between predicted and interpolated at runtime |
| `PredictionSwitchingSmoothing` | blend the visual pop when it switches |
| `[GhostEnabledBit]` / `PredictedGhost` | inspect what was corrected and when |

**Prediction switching** is the advanced move: predict only the ghosts near the local player,
interpolate the rest, and reassign as they approach. It keeps rollback cost bounded in a
crowded world. Budget it by *predicted entity count*, because rollback cost is
`predicted entities × replayed ticks`.

## 7 · Smoothing the correction

```csharp
GhostPredictionSmoothing.RegisterSmoothingAction<LocalTransform>(world, DefaultTranslateSmoothingAction.Action);
```

Runs after a rollback and blends the **rendered** value toward the corrected one.

> **💀 Trap** — smoothing must never feed back into simulation. Presentation reads simulation;
> the reverse is a feedback loop that diverges.

## 8 · Transport and emulation

| Knob | Where | Use |
|---|---|---|
| `NetworkSimulatorSettings` | editor | inject latency, jitter, packet loss |
| `SimulatorUtility.Parameters` | code | same, programmatically |
| `NetworkConfigParameter.disconnectTimeoutMS` | driver | how long before a silent peer is dropped |
| `heartbeatTimeoutMS` | driver | keepalive interval |
| `maxFrameTimeMS` | driver | debugger-friendly timeout suppression |
| `ReliableSequencedPipelineStage` window | driver | in-flight reliable packets |

**Always develop with emulation on.** 80 ms RTT and 2% loss as your daily setting will surface
every prediction bug while it is still cheap. A LAN-perfect connection hides exactly the class
of bug that ships.

## 9 · Relevancy modes

```csharp
var relevancy = SystemAPI.GetSingletonRW<GhostRelevancy>();
relevancy.ValueRW.GhostRelevancyMode = GhostRelevancyMode.SetIsIrrelevant;   // allowlist vs denylist
relevancy.ValueRW.GhostRelevancySet.Add(new RelevantGhostForConnection(networkId, ghostId), 1);
```

| Mode | Semantics | Use |
|---|---|---|
| `Disabled` | everything is relevant | small worlds |
| `SetIsRelevant` | **only** listed ghosts are sent | large worlds, strict interest |
| `SetIsIrrelevant` | listed ghosts are excluded | exceptions to a mostly-visible world |

Nerve wraps this in a `RelevancySettings` asset, which our project registers on
`ServerSettings.prefab`.

## 10 · Thin clients

| Knob | Effect |
|---|---|
| `RequestedNumThinClients` | how many headless load-generators to spawn |
| `ThinClientSimulation` filter | systems that exist only in them |

Thin clients are the cheapest scale test you will ever write: no rendering, no presentation,
real connections, real commands. Give them a fake-input system and you can put 50 players on
your laptop.

→ [30 · How it is made to feel nice](30-feel.md)
