# 43 · Physics on the server

Chapter 33 explained how a solver survives being put inside a rollback loop. This chapter is
the other half: where those systems physically live, what the server is actually responsible
for, and what changes when *everything* in your game is a rigid body rather than one crate.

> **📄 Provenance** — source-derived. Netcode for Entities and Entities were read in the
> `vex-ee-3` project at the monorepo fork pinned in `Packages/manifest.json`; Netcode reports
> version 6.6.0. **Unity Physics is not installed in `vex-ee-3`**, so every
> `com.unity.physics` and `com.unity.physics.custom` path below was read in the sibling
> project `vex-ee`, whose lock file resolves Unity Physics to **6.5.0** even though its
> manifest requests 6.6.0. The runtime-verified chapters of this book are 19 through 23.
> Nothing here was measured.

## Where the physics systems actually live

`PhysicsSystemGroup` carries exactly one attribute:
`[UpdateInGroup(typeof(FixedStepSimulationSystemGroup))]`
(`Unity.Physics/ECS/Base/Systems/PhysicsSystemGroups.cs:13`). There is **no**
`[WorldSystemFilter]` on it, and there is none on `BuildPhysicsWorld`
(`Unity.Physics/ECS/Base/Systems/BuildPhysicsWorld.cs:136`) or on `ExportPhysicsWorld`
(`Unity.Physics/ECS/Base/Systems/ExportPhysicsWorld.cs:17`) either. Grep the whole runtime
assembly and the only `WorldSystemFilter` attributes in the package are on baking systems and
debug-display systems.

Chapter 42 traced what that means. An unfiltered system resolves to
`WorldSystemFilterFlags.Default`, `Default` is replaced by the parent group's child default,
and a chain that terminates at `SimulationSystemGroup` — which declares no filter attribute at
all (`Unity.Entities/DefaultWorld.cs:683`) — falls through to the hard-coded fallback
`LocalSimulation | ServerSimulation | ClientSimulation`
(`Unity.Entities/Types/TypeManagerSystems.cs:1386`).

So the answer to the reader's question, from code: **Unity Physics carries
`LocalSimulation | ServerSimulation | ClientSimulation`.** It runs in the single-player world,
the client world and the server world. It does **not** run in a thin client, because
`ThinClientSimulation` is not in that set and `CreateThinClientWorld` asks for that flag
alone (`Runtime/ClientServerWorld/ClientServerBootstrap.cs:261`). Physics is not "world zero"
anything; it is present wherever the world was populated with one of those three flags.

The pipeline inside the group is three stages in a fixed order: `PhysicsInitializeGroup`
builds the world (`PhysicsSystemGroups.cs:157`), `PhysicsSimulationGroup` solves it
(`:169`), and `ExportPhysicsWorld` writes the results back onto entities
(`ExportPhysicsWorld.cs:17`).

> **A name that is not in this package** — `StepPhysicsWorld` does not exist in Unity Physics
> 6.5.0. It was a system in the 0.x and 1.0 APIs and is now `PhysicsSimulationGroup` with its
> four sub-groups. If a tutorial tells you to order against `StepPhysicsWorld`, it predates
> this package by several major versions.

## NetCode re-clocks the whole group

`PredictedPhysicsConfigSystem` runs in `InitializationSystemGroup` and is filtered to
`ClientSimulation | ServerSimulation`
(`Runtime/Physics/PredictedPhysicsSystemGroup.cs:177`–`:178`, package `com.unity.netcode`).
Both sides. This is not a client-only rearrangement.

On its first and only update it does three things
(`Runtime/Physics/PredictedPhysicsSystemGroup.cs:181`–`:187`): move the physics systems,
install `NetcodePhysicsRateManager` on the physics group, and remove itself from the update
list.

The move is a transitive closure, not a list. It seeds a set with `PhysicsSystemGroup`
(`:203`) and then repeatedly scans every system in `FixedStepSimulationSystemGroup`, adding
any system whose `[UpdateBefore]` or `[UpdateAfter]` names something already in the set
(`:146`–`:170`), looping until a full pass adds nothing (`:135`–`:143`). Everything in the set
is removed from `FixedStepSimulationSystemGroup` and added to
`PredictedFixedStepSimulationSystemGroup` (`:205`–`:213`).

> **⚡ Hardware analogy** — this is a **clock domain crossing**. The combinational logic is
> untouched; only the clock feeding it is swapped. It used to be driven by a wall-clock
> accumulator at roughly one edge per frame. It is now driven by the prediction tick, which
> can issue many edges in a single frame.

Two consequences you inherit whether you wanted them or not.

Anything you ordered against physics travels with it, automatically, if you used
`[UpdateBefore]` or `[UpdateAfter]` against a physics system. Anything you ordered against
`FixedStepSimulationSystemGroup` in general does **not**, and now runs in a different group
from the thing it was ordering against. `PhysicsWorldHistory` is a package example of the
former: it declares `[UpdateInGroup(typeof(PhysicsSystemGroup), OrderLast = true)]`
(`Runtime/Physics/PhysicsWorldHistory.cs:479`) and therefore moves with the group.

And a replayed tick **re-steps the solver**. Not a transform integration — a broadphase, a
narrowphase, and a constraint solve over every body in the predicted physics world, from the
restored state, once for each tick being replayed. That is the fact the rest of this chapter
is about.

## The contagion rule, and the arithmetic

A predicted body and an interpolated body are in different time domains. The predicted body
is at the client's predicted tick, ahead of the server. The interpolated body is at the
interpolation tick, behind the server. They are never simultaneously correct, so a contact
between them is a contact between two different moments and it resolves differently on every
machine.

In a game where everything is physical, this rule propagates. If the player can push a crate,
the crate is predicted. If the crate can knock a barrel, the barrel is predicted. The
predicted set is the transitive closure of "can be touched by something predicted", and in a
fully physics-based game that is close to everything dynamic.

That is a budget, so do the arithmetic before the design.

The server steps physics once per simulation tick, and the default tick rate is 60
(`Runtime/ClientServerWorld/ClientServerTickRate.cs:301`), with
`PredictedFixedStepSimulationTickRatio` defaulting to 1 (`:304`). One solve per tick, sixty
solves per second, over the whole authoritative body set.

The client steps physics once per tick it is predicting. The number of ticks replayed in a
frame is roughly the gap between the newest snapshot tick and the client's predicted tick,
which is round-trip time plus command slack, converted to ticks. At 60 Hz, a 100 ms
round trip is about six ticks, and command slack and jitter push it higher. So a client on an
ordinary connection runs on the order of **eight full solves per rendered frame** where the
server runs one.

Combine the two and the shape is clear. The server's physics cost is proportional to body
count. The client's is body count times replay depth. A body set the server handles in one
millisecond costs the client eight, and the client also has to render. This is why the honest
advice is not "optimise the solver" but "keep the predicted set small", and why interpolated
physics ghosts — where the client never solves at all — are the cheapest thing on the menu.

## What is actually on the wire

Netcode ships two default serialization variants for physics, and they are the entire
contract.

| Component | Variant | Fields | Options | Source |
|---|---|---|---|---|
| `PhysicsVelocity` | `PhysicsVelocityDefaultVariant` | `Linear`, `Angular` | `PrefabType = All`, `SendTypeOptimization = OnlyPredictedClients`, `Quantization = 1000` | `Runtime/Physics/PhysicsVelocityVariant.cs:13`, `:19`, `:23` |
| `PhysicsGraphicalSmoothing` | `PhysicsGraphicalSmoothingDefaultVariant` | none | `PrefabType = AllPredicted` | `Runtime/Physics/PhysicsVelocityVariant.cs:31`–`:32` |

`OnlyPredictedClients` means the server does not spend bytes sending velocity to a client that
is interpolating the ghost (`Runtime/Authoring/GhostModifiers.cs:65`). `AllPredicted` means
server and predicted-client prefab versions only
(`Runtime/Authoring/GhostModifiers.cs:28`) — a deletion, so that an interpolated client does
not have physics smoothing fighting snapshot interpolation.

Both are registered by `PhysicsDefaultVariantSystem`, which uses `TrySetDefaultVariant` and
therefore never overrides a rule you set yourself
(`Runtime/Physics/PhysicsVelocityVariant.cs:56`–`:57`).

That table is complete, and the completeness is the point. `PhysicsMass`,
`PhysicsGravityFactor`, `PhysicsDamping` and `PhysicsCollider` appear nowhere in the netcode
runtime — grep returns nothing. They are not replicated, and by the mechanism in chapter 33
they are therefore never rolled back. Position and rotation reach the wire through the
transform variants, not through anything physics-specific.

## Static bodies do not need to be ghosts

`BuildPhysicsWorld` splits the world with two queries. Dynamic bodies are entities with
`PhysicsVelocity`, `LocalTransform` and `PhysicsWorldIndex`
(`Unity.Physics/ECS/Base/Systems/PhysicsWorldData.cs:144`). Static bodies are entities with
`PhysicsCollider` and `PhysicsWorldIndex` and **no** `PhysicsVelocity`
(`Unity.Physics/ECS/Base/Systems/PhysicsWorldData.cs:150`–`:153`).

`ExportPhysicsWorld` writes back exactly two component types: `LocalTransform` and
`PhysicsVelocity` (`Unity.Physics/ECS/Base/Systems/PhysicsWorldExporter.cs:132`, `:134`). A
static body has neither written to it, so nothing about it changes at runtime.

And the validation system that complains about non-ghost dynamic bodies queries
`PhysicsVelocity` and `PhysicsWorldIndex` with `WithNone<GhostInstance>`, filtered to physics
world index zero (`Runtime/Physics/PredictedPhysicsSystemGroup.cs:255`–`:259`). A static
collider never matches it.

The rule that falls out is the one that wins the bandwidth in a physics game: **a body baked
into a SubScene that never moves needs no ghost, no snapshot and no prediction.** Both worlds
bake the same subscene from the same data, so both already agree about it. Reserve replication
for things that can change, and let level geometry be free.

## Determinism, stated honestly

Unity Physics does not promise cross-platform bitwise determinism, and the package does not
claim it. Netcode does not need it, because it does not run lockstep — it corrects the client
against server snapshots every time one arrives and replays forward from there.

What the design requires is weaker: that per-tick divergence stays small enough that the
correction, when it lands, is below the threshold a player notices. Within one machine, replay
is repeatable. Across machines, treat agreement as a tuning target.

Divergence in practice comes from four places, all of them yours: mutating non-replicated
state inside the prediction loop, floating-point ordering differences from a different
broadphase body order, quantization error accumulating in velocity, and any input the server
has that the client does not. The first is by far the most common.

## Configuration

`NetCodePhysicsConfig` is a `MonoBehaviour` baked into a SubScene
(`Runtime/Physics/Hybrid/NetCodePhysicsConfig.cs:16`). Its baker always adds
`PhysicsGroupConfig`, and adds `LagCompensationConfig` only when lag compensation is enabled
and `PredictedPhysicsNonGhostWorld` only when the index is non-zero (`:64`–`:79`).

| Field | Default | Line | Effect |
|---|---|---|---|
| `PhysicGroupRunMode` | `LagCompensationEnabledOrKinematicGhosts` (enum value 0) | `:29` | the gate on the physics group |
| `EnableLagCompensation` | `false` | `:35` | bakes `LagCompensationConfig`; turns on the history ring |
| `ServerHistorySize` | `0` | `:38` | zero means the default, which the tooltip states is 16 |
| `ClientHistorySize` | `1` | `:41` | 0 disables client history entirely |
| `ClientNonGhostWorldIndex` | `0` | `:48` | 0 means do not relocate non-ghost dynamic bodies |
| `DeepCopyDynamicColliders` | `true` | `:52` | deep-copies dynamic collider blobs into history |
| `DeepCopyStaticColliders` | `false` | `:56` | deep-copies static collider blobs; expensive |

**When the config is absent**, the rate manager reads its singleton with `TryGetSingleton` and
falls through to a default-initialised struct
(`Runtime/Physics/PredictedPhysicsSystemGroup.cs:86`). Default-initialised means enum value
zero, which is `LagCompensationEnabledOrKinematicGhosts`
(`Runtime/Physics/PhysicGroupConfig.cs:30`) — the **narrowest** of the three modes. No config
is not "everything on"; it is the most restrictive gate, and it is why physics can appear to
be missing on a client that has no predicted ghosts yet.

`PhysicsStep` is the solver's own settings component, and Unity Physics uses these values when
no such component exists in the scene
(`Unity.Physics/ECS/Base/Components/PhysicsComponents.cs:328`).

| Field | Default | Source | When to change |
|---|---|---|---|
| `SimulationType` | `UnityPhysics` | `PhysicsComponents.cs:394` | rarely |
| `Gravity` | `(0, -9.81, 0)` | `:395` | game feel |
| `EnableGyroscopicTorque` | `false` | `:396` | realism for fast-spinning bodies |
| `SubstepCount` | `1` | `:397` | fast bodies tunnelling or jitter at contacts |
| `SolverIterationCount` | `4` | `:398` | stacks and joint chains that sag |
| `MultiThreaded` | `1` | `:401` | leave on |
| `CollisionTolerance` | `0.01` | `:402`, via `CollisionWorld.cs:70` | raise if tunnelling persists |
| `SynchronizeCollisionWorld` | `0` | `:403` | only if a later system must query this tick's result |
| `IncrementalDynamicBroadphase` | `false` | `:404` | many dynamic bodies that mostly do not move |
| `IncrementalStaticBroadphase` | `false` | `:405` | large static worlds |
| `MaxDynamicDepenetrationVelocity` | `3.0` | `:406`, via `ISimulation.cs:56` | lower if overlaps launch bodies |
| `MaxStaticDepenetrationVelocity` | `+∞` | `:407`, via `ISimulation.cs:61` | lower to stop wall-clip ejections |

Raising `SubstepCount` or `SolverIterationCount` multiplies against replay depth on the
client. Both are the wrong first knob under prediction.

## Authoring: custom versus hybrid

Two authoring stacks bake to the same runtime components.

The **hybrid path** ships inside `com.unity.physics` and bakes Unity's own built-in
components: `Rigidbody` (`Unity.Physics.Hybrid/EntitiesBaking/BakingSystems/RigidbodyBakingSystem.cs:43`)
and the standard colliders — box, sphere, capsule and mesh
(`Unity.Physics.Hybrid/EntitiesBaking/BakingSystems/ColliderBakingSystem.cs:289`, `:329`,
`:348`, `:387`). You author with the components every Unity tutorial already uses.

The **custom path** is `com.unity.physics.custom`, which in this project is a Vex Studio fork
of Unity's CustomPhysicsAuthoring sample and is explicitly not an official Unity package (its
`README.md` says so). It adds MonoBehaviours under `Unity.Physics.Authoring`:
`PhysicsBodyAuthoring` (`Unity.Physics.Custom/Bodies/PhysicsBodyAuthoring.cs:9`),
`PhysicsShapeAuthoring`, eight joint types, four motors
(`Unity.Physics.Custom/Motors/`), material templates, and category and material tag assets.

`PhysicsBodyAuthoring` exposes what the hybrid path hides: motion type, smoothing mode, mass
(default 1), linear damping (default 0.01), angular damping (default 0.05), gravity factor
(default 1), an explicit inertia tensor, and the physics world index
(`Unity.Physics.Custom/Bodies/PhysicsBodyAuthoring.cs:18`–`:64`).

Choose the custom path for a netcode physics game, for three reasons. The world index field is
on the body inspector, and you need it the moment you want client-only debris in a second
physics world. Joints and motors have no hybrid equivalent. And smoothing mode is explicit per
body (`BodySmoothing` — `None`, `Interpolation`, `Extrapolation`,
`Unity.Physics.Hybrid/Components/RigidbodyAuthoring.cs:30`), which matters because netcode
restricts `PhysicsGraphicalSmoothing` to predicted prefabs.

> **💀 Trap** — the package README states it plainly: **do not mix custom and hybrid body or
> shape authoring on the same GameObject or in a competing compound.** Mixing double-bakes,
> drops colliders, or produces conflicting motion types. One stack per body hierarchy.

## When to use what

- **Static, baked, non-ghost.** Every collider that never moves. No replication, no
  prediction, no cost beyond the broadphase. Start here and justify every promotion out of it.
- **Interpolated physics ghost.** Anything dynamic the local player cannot touch. The server
  solves, the client interpolates, the client's solver is not involved.
- **Predicted physics ghost.** Only what must answer input in zero frames, plus its
  transitive contact closure. Each one costs a full solve per replayed tick.
- **A second physics world.** Set `ClientNonGhostWorldIndex` and give the prefabs a matching
  `PhysicsWorldIndex`. This is where ragdolls, debris and cosmetic impacts belong in a
  physics-heavy game, and it is the only way to have them at all without failing validation.
- **`PhysicGroupRunMode.AlwaysRun` with `PredictionLoopUpdateMode.AlwaysRun`.** When you must
  raycast static geometry before any predicted ghost exists. Both gates must open.
- **Substeps and solver iterations last.** They multiply against replay depth. Fix the
  predicted body count first.

→ [24 · The debugging playbook](24-debug-playbook.md)
