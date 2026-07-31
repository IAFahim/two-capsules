# 44 · Timeline under prediction

Chapter 43 put rigid bodies on the server. This chapter puts the thing that *drives* them
there: an authored timeline, where every jump, slide and combat action is a clip, and every
force is emitted by a clip. Nothing about that design is unusual on a single machine. Under
prediction it changes shape completely, and the change is one line of arithmetic.

> **📄 Provenance** — source-derived, not measured. BovineLabs Timeline, Core and Nerve were
> read in the `tertle-monorepo` working tree at commit `d55ffc7d`, where Timeline reports
> version 2.0.0. Netcode for Entities 6.6.0 and Entities were read in `vex-ee-3`. The older,
> locally modified copy of the Timeline package in the sibling project `vex-ee` reports
> version 1.0.3 and is cited only where the two differ. BovineLabs Timeline is a commercial
> package; every path below is a citation, and no package source is reproduced. The
> runtime-verified chapters of this book are 19 through 23. Nothing here was measured.

## A timeline is two numbers on the wire

A timeline is a pure function. The authored asset is a blob — clip start and end times,
curves, track bindings — identical on every machine because it was baked from the same source
and shipped in the same build. Given that blob, the entire visible state of a playing timeline
is determined by two numbers: which timeline, and how far into it.

That is six bytes. A `ushort` identifying the authored timeline, and a `NetworkTick` marking
the tick it started, which is a single `uint` (`Runtime/PredictionTicking/NetworkTick.cs:234`
in Netcode). Both are written once, when the action begins, and never change for the rest of
playback. A field that does not change contributes only its change-mask bit to a snapshot,
because `Deserialize` copies unchanged fields from the baseline — chapter 36 walks that
machinery in detail. So a two-second combat action costs six bytes on the tick it starts and
effectively nothing for the hundred and twenty ticks that follow.

Compare that against streaming the result. Replicating the transform of a moving character
costs bytes on every tick it changes, which is every tick. Replicating the cause costs bytes
once. That is the whole argument for timeline-driven gameplay in a networked game.

But it only holds if both machines derive the same "how far in" from the same tick counter.
That is where the shipped package and the netcode loop disagree.

## Accumulate versus derive

> **📐 A sharper statement of the rule, learned by building it.**
> "Never accumulate" is the slogan, and it is *almost* right. A predicted movement system
> accumulates into `PhysicsVelocity` every single tick and that is perfectly correct — because
> `PhysicsVelocity` is a ghost field, so the prediction history backs it up and rollback puts
> it back. The real rule is:
>
> **Only accumulate into state that rollback restores.**
>
> A clip's local time is the one thing that never qualifies, because `Timer.Time` is not a
> ghost field and nothing in NetCode has ever heard of it. That is why the timeline playhead
> specifically must be derived while the physics it drives may happily accumulate.


Here is the entire chapter in two lines of pseudocode.

```csharp
// Sequential. Needs a saved register, and something to restore it.
elapsed += deltaTime;

// Combinational. Needs nothing.
elapsed = currentTick - startTick;
```

The first is a register with feedback. Its value at tick N depends on its value at tick
N−1, all the way back to the start. Rewind the machine and the register is wrong unless
someone explicitly saved and restored it. In a rollback loop every such register is a
liability, and there is one per accumulator you write.

The second is combinational logic hanging off a counter. It reads the tick, subtracts a
constant, produces an answer. It has no memory, so there is nothing to restore. Replay tick
104 nine times and it produces the same number nine times, because the only input is the tick
— and NetCode already restores the tick, every frame, for free.

This is not a metaphor stretched to fit. It is the same distinction a hardware engineer draws
between a counter that needs a reset line and a decoder that does not. The state you do not
keep is the state you cannot corrupt.

**The shipped package accumulates.** `TimerUpdateSystem` assigns the per-clock delta at
`BovineLabs.Timeline/Schedular/TimerUpdateSystem.cs:474` and accumulates it into `Timer.Time`
at `:475`. That delta comes from `ClockData`, which `ClockUpdateSystem` fills from
`SystemAPI.Time.DeltaTime` for `ClockUpdateMode.GameTime`
(`BovineLabs.Timeline/Schedular/ClockUpdateSystem.cs:32`), from
`UnityEngine.Time.unscaledDeltaTime` for `UnscaledGameTime` (`:33`), or from an authored
constant (`:61`). Downstream, `ClipLocalTimeSystem` converts the timer position into per-clip
local time (`BovineLabs.Timeline/Timeline/ClipLocalTimeSystem.cs:78`) and sets the
`ClipActive` enable bit from it (`:95`); `ClipWeightSystem` evaluates the authored weight curve
at that local time (`BovineLabs.Timeline/Timeline/ClipWeightSystem.cs:38`).

Everything from `LocalTime` downward is already pure. The only sequential element in the chain
is that one addition. Replace its input and the rest of the package becomes rollback-safe
untouched.

## Where the shipped package actually runs

`TimelineSystemGroup` declares `Worlds.Simulation | Presentation | Editor | Menu` for both its
own flags and its children's (`BovineLabs.Timeline/TimelineSystemGroup.cs:20`–`:21`), and
`Worlds.Simulation` is `ServerSimulation | LocalSimulation | ClientSimulation`
(`BovineLabs.Core/Worlds.cs:21`–`:22`). The monorepo package **is** present in the server
world. Settled.

The group chain needs a correction. `TimelineSystemGroup` is placed in
`BeforeTransformSystemGroup` (`TimelineSystemGroup.cs:23`, under `BL_NERVE`, which is always
defined because Nerve is a hard dependency of the Timeline package). `BeforeTransformSystemGroup`
*inherits* `BLSimulationSystemGroup` (`BovineLabs.Nerve/Groups/BeforeTransformSystemGroup.cs:17`),
but `BLSimulationSystemGroup` is an abstract base class carrying the `IDisableWhilePaused`
marker (`BovineLabs.Nerve/Groups/BLSimulationSystemGroup.cs:10`) — it is not a node in the
update tree. `BeforeTransformSystemGroup` carries no `[UpdateInGroup]`, only
`[UpdateBefore(typeof(TransformSystemGroup))]` (`:16`), so it lands directly in
`SimulationSystemGroup`. The real chain is:

`SimulationSystemGroup` → `BeforeTransformSystemGroup` → `TimelineSystemGroup` →
`ScheduleSystemGroup`, then `TimelineUpdateSystemGroup`.

`PredictedSimulationSystemGroup` is a *sibling* of `BeforeTransformSystemGroup` inside
`SimulationSystemGroup`, ordered first
(`Runtime/PredictionTicking/GhostPredictionSystemGroup.cs:183`). The timeline runs strictly
after the entire prediction loop has finished. Outside prediction, confirmed.

One more correction, for anyone reading the older package. The `vex-ee` copy does use
`WorldSystemFilterFlags.Default` (`BovineLabs.Timeline/TimelineSystemGroup.cs:20`), but
`Default` on a *system's* filter never means `LocalSimulation | Presentation`. That expansion
is on the world-request side, in `GetSystemTypeIndices`
(`Unity.Entities/Types/TypeManagerSystems.cs:817`). On the system side the bit is stripped
(`:1431`) and replaced by a walk up the `[UpdateInGroup]` chain (`:1388`–`:1416`), falling
back to `LocalSimulation | ServerSimulation | ClientSimulation` at a system with no parent
group (`:1386`). Chapter 42 traces that split in full. `vex-ee` has no Nerve package, so
`BL_NERVE` is undefined, so `TimelineSystemGroup` there carries no `[UpdateInGroup]` at all
and lands on that fallback — **including the server**.

What removes the client in that copy is a local hand-patch narrowing `ClockUpdateSystem` to
`Worlds.ServerLocal | Worlds.Menu`, with a comment explaining the intent
(`BovineLabs.Timeline/Schedular/ClockUpdateSystem.cs:20`–`:22`, `vex-ee` copy). No clock
update, no timer advance. I cannot ground the original "client only" observation against the
code as it stands; the filter that would have caused it is not there any more.

## Two rate managers, two different clocks

Now the central question: does the client's timeline genuinely free-run relative to the
server's? Yes — and the mechanism is worse than drift.

On a dedicated server, `NetcodeServerRateManager` re-enters the whole `SimulationSystemGroup`
once per pending simulation tick, running while `RemainingTicksToRun > 0`
(`Runtime/PredictionTicking/UpdateRateManagement/NetcodeServerRateManager.cs:48`–`:51`) and
pushing a fresh `TimeData` on each entry (`:65`, `:81`) whose delta is
`SimulationFixedTimeStep` times the batch length
(`Runtime/PredictionTicking/UpdateRateManagement/NetcodeTimeTracker.cs:201`–`:204`, pushed at
`:141`). The server's timeline is evaluated once per tick, at integer tick boundaries, with a
tick-quantised delta. Genuinely tick-locked.

On the client, `NetcodeClientRateManager.ShouldGroupUpdate` returns `true` once and `false` on
the immediately following call, popping the time it pushed
(`Runtime/PredictionTicking/UpdateRateManagement/NetcodeClientRateManager.cs:54`–`:63`,
`:142`–`:144`). The client's `SimulationSystemGroup` runs **exactly once per rendered frame**.
Its delta is not wall-clock either — it is
`(deltaTicks + serverTickFraction − previousFraction) × SimulationFixedTimeStep` (`:108`), how
far the predicted target tick moved this frame, fractional part included.

So the two sides advance the same accumulator with differently-shaped deltas. The server steps
in whole ticks; the client steps once per frame and lands on a *fraction* of a tick. Over a
long run the sums track each other, because both are ultimately counting server ticks. At any
instant they disagree by up to one tick of phase, and nothing brings them back together.

That last part is the real finding: there is no correction channel at all. The Timeline
package contains no reference to `Unity.NetCode`, `GhostField`, `GhostComponent` or
`NetworkTick` anywhere — grep the whole package, in either copy, and it returns nothing.
`Timer.Time` is not a ghost field, so the server never sends it, the client never receives
it, and no amount of divergence is ever noticed.

Nor is it restored. Rollback restores a predicted ghost's state from
`GhostPredictionHistorySystem`, whose backup is a memcopy of ghost components into memory
attached to the chunk, and which on restore copies back **only the fields serialised as part
of the snapshot** (`Runtime/Snapshot/GhostPredictionHistorySystem.cs:184`–`:187`);
`GhostUpdateSystem` performs that restore before the next prediction loop
(`Runtime/Snapshot/GhostUpdateSystem.cs:26`). A non-`GhostField` value is deliberately
preserved across the restore — exactly right for local scratch state, exactly wrong for a
clock.

Put the two together. The timeline runs outside `PredictedSimulationSystemGroup`, so it does
not re-run during replay; its state is neither replicated nor restored, so it is not rewound
either. When the client rolls back ten ticks and re-predicts them, the timeline does nothing
at all — then advances once, by one frame's worth, as if nothing had happened.

A sharper hazard rides alongside. `ClockUpdateMode.UnscaledGameTime` reads
`UnityEngine.Time.unscaledDeltaTime` (`ClockUpdateSystem.cs:33`), a per-*frame* engine global.
On a server catching up, `SimulationSystemGroup` re-enters N times in one frame, so a clock in
that mode consumes the same frame delta N times and runs N times as fast as the client's.
`GameTime` clocks escape this because `SystemAPI.Time.DeltaTime` is the pushed netcode time.
`UnscaledGameTime` is not netcode time at all.

## The rollback boundary, stated hard

**Rollback restores component values. It does not restore entity existence.**

The backup is a memcopy of component data into a side buffer keyed by chunk
(`GhostPredictionHistorySystem.cs:184`). There is no create and no destroy in it. An entity
your clip spawned on tick 104 is still there when tick 104 is replayed, and your clip will
happily spawn a second one.

NetCode's answer is not to undo the spawn but to make the duplicate reconcilable. A client
predicting a spawn adds `PredictedGhostSpawnRequest` (`Runtime/Snapshot/GhostComponent.cs:364`),
disabled on add and consumed by `PredictedGhostSpawnSystem`; when the authoritative ghost
arrives, a classification system matches it against the predicted one instead of spawning a
second (`Runtime/Snapshot/GhostSpawnClassificationSystem.cs:178`), and unmatched predicted
spawns are destroyed once they age out
(`Runtime/Snapshot/PredictedGhostSpawnSystem.cs:411`–`:414`).

The same asymmetry covers cosmetics: a particle burst is not a component value, and replay
cannot un-emit it. The tool there is `NetworkTime.IsFirstTimeFullyPredictingTick`
(`Runtime/PredictionTicking/NetworkTime.cs:159`), set only when a tick is fully predicted for
the first time
(`Runtime/PredictionTicking/UpdateRateManagement/NetcodeClientPredictionRateManager.cs:230`–`:233`)
and cleared on partial ticks (`:256`).

## The four clip kinds

Once the boundary is stated, clips sort into exactly four kinds, and each has a different
correct home.

| kind | where it runs | what the framework must guarantee |
|---|---|---|
| value / continuous | predicted, both worlds | sampled from `tick − start`, pure, idempotent under replay |
| cosmetic event | client only | auto-gated on `IsFirstTimeFullyPredictingTick` |
| spawn event | predicted, both worlds | routed through `PredictedGhostSpawnRequest` + classification, never duplicates |
| authority event | server only | stripped from the client prefab via `GhostPrefabType.Server` |

The instinct is to write this table into a wiki page and ask designers to respect it. That
fails in the worst possible way: silently, on one machine, under load, months later.

Make it a type-system concern instead. A clip's kind should be carried by the interface it
implements, and the interface should decide which system group evaluates it. A cosmetic clip
that physically cannot run outside the gated client system is not a clip a designer can
misuse. A spawn clip whose only spawn API emits a `PredictedGhostSpawnRequest` cannot produce
an unclassified duplicate. Make the wrong thing unrepresentable, not discouraged.

Then close the remaining gap at bake time. A clip marked cosmetic that writes a replicated
component is a static fact about the authored asset, checkable before the game runs and
reportable as a bake error. This is not a new idiom for the stack — Canopy already does this
shape of check: `CanopyGraphValidation.Validate`
(`BovineLabs.Canopy.Authoring/CanopyGraphValidation.cs:14`) returns structural errors for a
graph, and `CanopyStateMachineAuthoring.ShouldBake` logs them and aborts baking for that
authoring component (`BovineLabs.Canopy.Authoring/CanopyStateMachineAuthoring.cs:27`–`:30`).

The runtime gate itself is four lines, and it is the only netcode-aware code most clip authors
will ever see:

```csharp
// Cosmetic clips: run once per tick, not once per replay of that tick.
var time = SystemAPI.GetSingleton<NetworkTime>();
if (!time.IsFirstTimeFullyPredictingTick)
    return;
```

## Kinematic versus dynamic, the bandwidth lever

Chapter 43 established the contagion rule: touch a predicted body and you join the predicted
set. Timelines give you a lever on that rule.

A root-motion move driven entirely by a timeline is genuinely `f(id, elapsed)`. The character
goes where the clip says regardless of what it brushes past, so both machines compute the same
path from the same six bytes, and the transform need not replicate during the move at all.
That is the cheap case, and it is cheap precisely because it is kinematic.

The moment a clip emits force into a dynamic body, that stops being true. The outcome now
depends on contact — mass, restitution, and which other bodies were where at that instant —
and none of that is derivable from the authored asset. The result has to replicate. Chapter 43
has the table of what goes on the wire for a physics body and the arithmetic on what a
predicted body costs per frame.

So the rule is blunt. Kinematic root motion is a compression scheme. Force emission is a
bandwidth commitment. Choose per clip, deliberately.

## What adopting the monorepo package buys you

It **does** get you present-on-the-server. `TimelineSystemGroup.cs:20` includes
`Worlds.Simulation`; the timer, clip local time and clip weight systems all run in the server
world; server-side gameplay logic can read `ClipActive` and `LocalTime`. That is a real
upgrade over a client-only build.

It **does not** get you rollback-safe. The advance at `TimerUpdateSystem.cs:475` is still an
accumulator, the group is still outside `PredictedSimulationSystemGroup`, and nothing in the
package is a ghost field. Present on the server and correct under prediction are two different
properties, and the package has the first one.

One structural note: `TimelineCrossingSystem` and the `TimerEvaluation` / `TimelineCrossing`
types do not exist in the `vex-ee` copy at all. It emits every cue and clip boundary crossed by
forward timer movement, refuses to emit when time did not move forward
(`BovineLabs.Timeline/Timeline/TimelineCrossingSystem.cs:70`–`:73`), and keeps a cursor into
the authored definition list across ticks (`:75`–`:80`). The right primitive for event clips —
but that cursor is itself sequential state, so it inherits every problem in this chapter.

## A migration path

In order, and none of these steps forks the authoring pipeline.

1. **Adopt the monorepo package unchanged.** You get the server-side systems and
   `TimelineCrossingSystem` without editing anything. Verify by querying `ClipActive` in a
   server-world system.
2. **Replicate the start, not the state.** Add a small ghost component holding
   `(ushort timelineId, NetworkTick startTick)` to whatever entity owns the action. Six bytes,
   written on activation, static thereafter.
3. **Add a second runtime consumer of the same authored blob.** Do not fork
   `TimelineSystemGroup`. Write a predicted evaluator in `PredictedSimulationSystemGroup`,
   filtered `ClientSimulation | ServerSimulation`, that reads the same baked clip data and
   computes local time from `NetworkTime.ServerTick − startTick`. Authoring pipeline, blob
   format and designer workflow are all untouched.
4. **Move gameplay clips across, kind by kind.** Value clips first — pure, mechanical. Then
   spawn clips, through `PredictedGhostSpawnRequest`. Then authority clips, stripped from the
   client prefab with `GhostPrefabType.Server` (`Runtime/Authoring/GhostModifiers.cs:26`).
5. **Leave presentation where it is.** The shipped `TimelineSystemGroup` keeps running on the
   client for VFX, audio, animation blending and UI, once per frame, as designed.
6. **Make the presentation clock a follower.** Instead of accumulating its own delta, have the
   presentation timer read its position from the predicted gameplay clock and interpolate
   between ticks for smoothness.
7. **Add the bake-time validator last**, once the kinds exist.

Step 6 is the one people skip, and it is the one that makes the two clocks agree. A
presentation clock that accumulates is a second source of truth. One that samples is a
display.

## When to use what

**Use the shipped `TimelineSystemGroup` as-is** for anything cosmetic, for single-player, and
for menu and UI timelines. Frame-rate evaluation is the correct clock for a thing whose only
consumer is a human eye; moving it into prediction would re-run particle spawns on every
replay and cap effects at the tick rate.

**Use a predicted evaluator** for anything that decides an outcome: hitboxes, i-frames, root
motion, force emission, stat changes, spawns. If a server-side check would ever disagree with
a client-side one, it belongs on the tick clock.

> **⚡ Two things that only show up once you build this.**
>
> **The fourth kind is not a clip kind.** An authority event is not a way of writing a clip —
> it is a statement that the effect does not belong on the client's timeline at all. In
> practice one authored ability decomposes into a value clip, a cosmetic clip, and a
> server-side event firing at the same tick offset: three systems hanging off one metronome,
> from one thing the designer dragged onto a track.
>
> **The expensive decision is not in this model at all.** It is whether your clip-driven
> forces live in `PredictedSimulationSystemGroup` or `PredictedFixedStepSimulationSystemGroup`.
> For a game where every force comes from a clip, that placement decides whether forces are
> double-applied on partial ticks — and it is invisible until you profile packet by packet.

**Derive, never accumulate, inside prediction.** `elapsed = tick − start` costs a subtraction
and buys correctness under replay. `elapsed += dt` costs nothing today and a save/restore path
forever after.

**Keep the asset singular and the consumers plural.** One baked blob, one predicted gameplay
clock, one presentation clock that follows it. Forking the authoring pipeline is the expensive
way to buy what a second system gives you free.
