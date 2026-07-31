# 42 · Which world am I in?

Chapter 03 named the worlds. This chapter is the definitive account: what creates each one,
what runs inside it, and which one you should be reasoning about when a specific thing is
wrong.

> **📄 Provenance** — source-derived. Entities and Netcode for Entities were read in the
> `vex-ee-3` project at the monorepo fork pinned in `Packages/manifest.json`; Netcode reports
> version 6.6.0 in its `package.json`. **Unity Physics is not installed in `vex-ee-3`**, so
> every `com.unity.physics` and `com.unity.physics.custom` path below was read in the sibling
> project `vex-ee`, whose lock file resolves Unity Physics to **6.5.0**. The runtime-verified
> chapters of this book are 19 through 23. Nothing here was measured. Everything here names a
> file and a line.

## "World zero" is a physics index, not an ECS world

The phrase is real. It does not mean what it sounds like.

`PhysicsWorldIndex` is a shared component carried by every physics body. Its documentation
comment says the default physics world is built from entities whose value is zero
(`Unity.Physics/ECS/Base/Components/PhysicsComponents.cs:11`, package `com.unity.physics`),
and the field itself is a `uint` that defaults to zero
(`Unity.Physics/ECS/Base/Components/PhysicsComponents.cs:17`). Custom Physics Authoring puts
it in the inspector with the tooltip "The index of the physics world this body belongs to.
Default physics world has index 0"
(`Unity.Physics.Custom/Bodies/PhysicsBodyAuthoring.cs:61`, package
`com.unity.physics.custom`). `BuildPhysicsWorld` builds exactly that partition: it constructs
its world data with a default-constructed `PhysicsWorldIndex`, which is index zero
(`Unity.Physics/ECS/Base/Systems/BuildPhysicsWorld.cs:142`).

So `PhysicsWorldIndex` zero is a **partition of bodies**. An ECS `World` is an **address
space**. One ECS world contains as many physics world indices as you build groups for, and
every one of those indices exists separately inside the client world and inside the server
world. Saying "physics runs in world zero" is like saying a process runs in heap arena zero:
true of the allocator, silent about the process.

Three different things answer to "the default world". Separate them once.

| Name | What it is | Source |
|---|---|---|
| `PhysicsWorldIndex` `0` | the default collider partition inside one ECS world | `Unity.Physics/ECS/Base/Components/PhysicsComponents.cs:17` (physics) |
| `World.DefaultGameObjectInjectionWorld` | a static pointer the GameObject layer uses when it needs *a* world | `Unity.Entities/World.cs:117` (entities) |
| `World.All[0]` | the first entry of a plain `List<World>` appended in creation order | `Unity.Entities/World.cs:112`, `Unity.Entities/World.cs:241` (entities) |

None of the three is "the world where gameplay lives" in a netcode build. The last one is
purely an artefact of who was constructed first.

## Every world that can exist

Each row names the constructor call and the system filter the world was populated with. Both
are in `Runtime/ClientServerWorld/ClientServerBootstrap.cs` in `com.unity.netcode` unless
stated otherwise.

| World | `WorldFlags` at construction | Systems selected by | Created by |
|---|---|---|---|
| Local / single-player | `Game` (`:101`) | `WorldSystemFilterFlags.Default` (`:105`) | `CreateLocalWorld` |
| ServerWorld | `GameServer` (`:456`) | `ServerSimulation` (`:444`) | `CreateServerWorld` |
| ClientWorld | `GameClient` (`:354`) | `ClientSimulation \| Presentation` (`:342`) | `CreateClientWorld` |
| Thin client | `GameThinClient` (`:285`) | `ThinClientSimulation` (`:261`) | `CreateThinClientWorld` |
| Single-world host | `GameServer \| GameClient` (`:322`) | `ServerSimulation \| ClientSimulation \| Presentation` (`:303`) | `CreateSingleWorldHost`, behind `NETCODE_EXPERIMENTAL_SINGLE_WORLD_HOST` (`:295`) |
| Default world, no bootstrap | `Game`, or `Editor` in an editor world | `WorldSystemFilterFlags.Default` | `DefaultWorldInitialization.Initialize`, `Unity.Entities/DefaultWorldInitialization.cs:170` and `:174` |
| Baking world | transient, disposed with `using` | baking systems | `Unity.Scenes.Editor/EditorEntityScenes.cs:62` (entities) |
| Live-conversion world | `Editor \| Conversion \| Staging` | live conversion systems | `Unity.Scenes.Editor/LiveConversion/LiveConversionDiffGenerator.cs:105` (entities) |
| LoadingWorld 0..N | `Streaming` | `ProcessAfterLoad` | `Unity.Scenes/SceneSectionStreamingSystem.cs:227` (entities) |

Read the second and third columns together. The `WorldFlags` say what the world *is*; the
filter flags say what got *put in it*. They are different enums and they are not
interchangeable. `WorldFlags` lives in `Unity.Entities/World.cs:19`;
`WorldSystemFilterFlags` lives in `Unity.Entities/WorldSystemFiltering.cs:47`.

The important asymmetry is on the server row. `CreateServerWorld` asks for
`ServerSimulation` **only** — no `Presentation`
(`Runtime/ClientServerWorld/ClientServerBootstrap.cs:444`). The client asks for
`ClientSimulation | Presentation` (`:342`). That single difference is why rendering,
graphical smoothing and camera code are simply absent from the server world rather than
disabled in it.

## Every filter flag, and what `Default` really is

This is the enum at `Unity.Entities/WorldSystemFiltering.cs:47`.

| Flag | Value | Meaning |
|---|---|---|
| `Default` | `1 << 0` | a placeholder that is always resolved away; never observed on a system |
| `Disabled` | `1 << 1` | set by `[DisableAutoCreation]` |
| `EntitySceneOptimizations` | `1 << 2` | the post-bake scene optimisation pass |
| `ProcessAfterLoad` | `1 << 3` | runs inside a streaming world after a section loads |
| `Editor` | `1 << 6` | the editor's own live world |
| `BakingSystem` | `1 << 7` | baking systems, after the baker pass |
| `LocalSimulation` | `1 << 8` | single-player worlds with no netcode |
| `ServerSimulation` | `1 << 9` | server worlds |
| `ClientSimulation` | `1 << 10` | client worlds |
| `ThinClientSimulation` | `1 << 11` | thin clients: connect and send input, simulate nothing |
| `Presentation` | `1 << 12` | worlds that render |
| `Streaming` | `1 << 13` | streaming worlds |
| `EntityProxy` | `1 << 14` | baking proxy worlds |
| `EntityProxyPreview` | `1 << 15` | baking proxy worlds in preview mode |
| `All` | `~0u` | everything, including `[DisableAutoCreation]` systems |

Bits 4 and 5 are absent from the enum. Do not reuse them; BovineLabs adds its own kinds much
higher up, at bits 21 and 22 (`BovineLabs.Core/Worlds.cs:12` and `:13`).

Now the question this chapter exists to answer. **`WorldSystemFilterFlags.Default` has two
different expansions, and which one applies depends on who is asking.**

When a *world* is being populated, `Default` expands to `LocalSimulation | Presentation`.
That is a literal line of code: `TypeManager.GetSystemTypeIndices` strips the `Default` bit
and ORs in exactly those two (`Unity.Entities/Types/TypeManagerSystems.cs:817`).

When a *system* is being classified, `Default` expands to whatever its parent group declares
as `ChildDefaultFilterFlags`, walking up the `[UpdateInGroup]` chain. If the walk reaches a
group with no parent, the fallback is
`LocalSimulation | ServerSimulation | ClientSimulation`
(`Unity.Entities/Types/TypeManagerSystems.cs:1386`).

The walk itself is short. A system with no `[WorldSystemFilter]` starts at `Default`
(`Unity.Entities/Types/TypeManagerSystems.cs:1424`), the `Default` bit is stripped and
replaced by the parent-group lookup (`:1429`–`:1433`), and each group in the chain
contributes its `ChildDefaultFilterFlags` (`:1408`) — which is itself `Default` unless the
group's attribute passed a second argument, because that is the constructor's default value
(`Unity.Entities/WorldSystemFiltering.cs:140`).

Two consequences follow, and both matter every day.

First, `SimulationSystemGroup` carries **no** `[WorldSystemFilter]` at all
(`Unity.Entities/DefaultWorld.cs:683`) and has no `[UpdateInGroup]`, so any chain that
terminates there lands on the `:1386` fallback. That is the mechanism behind chapter 03's
trap: an unfiltered gameplay system runs in local, client **and** server worlds.

Second, `PresentationSystemGroup` is the one root group that overrides the child default. Its
attribute passes `Presentation` as the second argument
(`Unity.Entities/DefaultWorld.cs:768`), so an unfiltered system placed in it is
presentation-only and will never exist on the server.

> **⚡ Hardware analogy** — the filter flags are a **decoder's address map**. `Default` is not
> an address; it is a "decode from the enclosing region" instruction. Which chip select
> asserts depends on the region you were placed in, not on the strap you left floating.

## Which world holds truth

This is the table to keep open while debugging.

| Symptom | World to reason about | Why |
|---|---|---|
| Two players disagree about a position | ServerWorld | it is the only authority; the client is a guess until a snapshot lands |
| Local capsule feels laggy or rubber-bands | ClientWorld, prediction loop | prediction is running or being corrected |
| A remote capsule stutters | ClientWorld, interpolation | interpolated ghosts never touch prediction |
| Nothing renders | ClientWorld | only the client world carries `Presentation` |
| A rule fires twice | both — check the filter | an unfiltered system exists in every game world |
| A ghost never spawns | ServerWorld first | the connection lacks `NetworkStreamInGame`, chapter 13 |
| A baked value is wrong | the baking world | it was decided before either runtime world existed |
| A subscene never appears | the streaming world | `ProcessAfterLoad` runs there, not in the game world |

The one-line version: **the server world is truth, the client world is a prediction plus a
renderer, the thin client is a keyboard with a socket, and the baking world already
finished.**

## Simulation and presentation are different things

This is the misconception worth killing outright, because everything else in this section
depends on it.

The server spawns a fireball. The server has no mesh, no material, no camera. It is therefore
tempting to conclude that the server does not know where the fireball is. **It does.** The
server computes the fireball's position every single tick, including its full rigid-body
trajectory, because Unity Physics carries `ServerSimulation` in its resolved filter — chapter
43 walks that derivation.

What the server lacks is *rendering*, and rendering is a separate flag. `LocalSimulation`,
`ServerSimulation` and `ClientSimulation` are bits 8, 9 and 10; `Presentation` is bit 12
(`Unity.Entities/WorldSystemFiltering.cs:83`, `:87`, `:91`, `:99`). `CreateServerWorld` asks
for `ServerSimulation` alone (`Runtime/ClientServerWorld/ClientServerBootstrap.cs:444`), so
the systems that turn a transform into pixels were never added to that world. The transform
is computed; nobody draws it.

Simulation is where the truth is. Presentation is a consumer of truth. A headless server is
not a partial simulation — it is a complete simulation with the display driver unloaded.

## There is no attachment between the two entities

The second half of the question is "how is the server's fireball connected to the client's
fireball", and the honest answer is: **it is not connected at all.**

They are two entities in two address spaces, and nothing points from one to the other. Entity
references cannot cross a world boundary. What crosses is an integer.

`GhostInstance` carries `ghostId` and `ghostType`
(`Runtime/Snapshot/GhostComponent.cs:78`, `:82`). `ghostType` is documented as "the ghost
prefab type, as index inside the ghost prefab collection" (`:80`–`:82`). The client receives
that index, looks up the matching row of its own `GhostCollectionPrefab` buffer, reads the
`GhostPrefab` entity out of it (`Runtime/Snapshot/GhostCollectionComponent.cs:216`), and
instantiates **its own local copy** of a prefab it baked itself.

So the correlation is an integer plus a shared agreement about what that integer names. Both
worlds baked the same authored prefab from the same asset, so both already know what ghost
type 7 means. The wire carries the number, not the thing.

`ghostId` alone is not a unique identifier — the server recycles it when a ghost dies. The
pair `(ghostId, spawnTick)` is the guaranteed-unique key
(`Runtime/Snapshot/GhostComponent.cs:73`–`:76`).

## One authored prefab, several baked entities

Here is the mechanism that actually answers "the client needs the visual and the server does
not". NetCode does not bake one prefab and share it. It bakes one prefab **and a set of
per-world edit lists**, then edits the prefab differently in each world before anything is
instantiated from it.

`GhostPrefabType` is the flag enum that drives it
(`Runtime/Authoring/GhostModifiers.cs:15`).

| Value | Number | Meaning |
|---|---|---|
| `None` | 0 | on no prefab variant at all |
| `InterpolatedClient` | 1 | the interpolated client version only |
| `PredictedClient` | 2 | the predicted client version only |
| `Client` | 3 | both client versions |
| `Server` | 4 | the server version only |
| `AllPredicted` | 6 | server plus predicted client |
| `All` | 7 | every version |

You set it through `GhostComponentAttribute.PrefabType`, which defaults to
`GhostPrefabType.All` (`Runtime/Authoring/GhostComponentAttribute.cs:17`) — so a component
you say nothing about exists everywhere.

The baking pass converts those flags into four blob arrays on the prefab's metadata:
`RemoveOnServerOnlyWorld`, `RemoveOnClientWorlds`, `DisableOnPredictedClient` and
`DisableOnInterpolatedClient` (`Runtime/Snapshot/GhostCollectionComponent.cs:67`, `:77`,
`:81`, `:85`). The classification loop is short and readable: a component marked
`GhostPrefabType.All` short-circuits immediately and is added to every list of survivors
(`Runtime/Snapshot/GhostPrefabCreation.cs:572`); otherwise anything without the `Server` bit
goes on the server removal list (`:597`), anything without the `Client` bit goes on the client
removal list (`:608`), and where a ghost supports both client modes at runtime the difference
between them becomes a *disable* rather than a *remove* (`:617`–`:623`), because the same
entity may switch modes later.

At runtime, `RuntimeStripPrefabs` picks the removal list for the world it is in by testing
`IsServer()` (`Runtime/Snapshot/GhostCollectionSystem.cs:940`) and removes each listed
component from the prefab entity (`:962`). Then it drops the marker that says stripping is
pending (`:967`).

That is the whole trick. One authored asset, one baked entity graph, and a per-world subtraction
applied before the first instance exists.

A worked projectile makes it concrete. The `PhysicsGraphicalSmoothing` row is quoted from
source (`Runtime/Physics/PhysicsVelocityVariant.cs:32`); the rest is how you would author it.

| Component | Server | Predicted client | Interpolated client | Why |
|---|---|---|---|---|
| `LocalTransform` | ✅ | ✅ | ✅ | the replicated truth and both views of it |
| `PhysicsVelocity` | ✅ | ✅ | ✅ | authority solves; predicted client re-solves |
| `PhysicsCollider` | ✅ | ✅ | ✅ | the server needs hits, the predicted client needs contacts |
| Renderer components | ❌ | ✅ | ✅ | `GhostPrefabType.Client` — no mesh belongs on a server |
| VFX / audio / light | ❌ | ✅ | ✅ | `Client`; pure presentation |
| `PhysicsGraphicalSmoothing` | ✅ | ✅ | ❌ | `AllPredicted`; smoothing would fight interpolation |
| Damage / scoring authority | ✅ | ❌ | ❌ | `GhostPrefabType.Server`; never let a client decide |

Chapter 36 covers how these flags reach the generated serializer; chapter 26 shows the
attribute in an authoring example. This section is the world-level view: the same authored
prefab yields different entities per world because each world subtracts a different set.

## Telling which world you are in at runtime

Inside a system, `state.World.IsServer()` and `state.World.IsClient()` are flag tests, not
role guesses (`Runtime/ClientServerWorld/ClientServerBootstrap.cs:683` and `:665`). Note that
`IsClient` deliberately returns true for thin clients as well (`:665`), so a thin-client guard
needs `IsThinClient` (`:647`).

`World.IsHost()` is the one to be careful with. It is defined as `IsClient(world) &&
IsServer(world)` (`Runtime/ClientServerWorld/ClientServerBootstrap.cs:702`) — a test on **one
world carrying both flags**. Only the experimental single-world host constructs such a world
(`:322`). In a normal ClientWorld-plus-ServerWorld topology, which is what this project
builds, `IsHost()` is **false on both worlds** even though the process is unquestionably
hosting. If you want "am I the hosting process", the netcode-side answer is
`Netcode.IsHostRole`, which additionally checks the requested play type
(`Runtime/GameObjectLayer/Netcode.cs:61`).

> **🔬 Probe** — the honest enumeration:
> ```csharp
> foreach (var w in World.All)
>     Debug.Log($"{w.Name} flags={w.Flags} server={w.IsServer()} client={w.IsClient()}");
> ```

## How the bootstrap decides

`ICustomBootstrap` is a single method: return true and you own world creation entirely
(`Unity.Entities/World.cs:91`). `DefaultWorldInitialization.Initialize` calls it, and only
falls through to constructing a default world if it returns false
(`Unity.Entities/DefaultWorldInitialization.cs:158`–`:170`).

`ClientServerBootstrap` implements that interface by calling `CreateDefaultClientServerWorlds`
(`Runtime/ClientServerWorld/ClientServerBootstrap.cs:125`), which branches purely on
`RequestedPlayType`: server world for `Server` or `ClientAndServer`, client world for `Client`
or `ClientAndServer` (`:231`–`:234`), plus thin clients in the editor (`:240`).

Nerve's `BovineLabsBootstrap` layers a lifecycle on top of that. It is an `ICustomBootstrap` in
its own right (`BovineLabs.Nerve/Utility/BovineLabsBootstrap.cs:22`) and separately extends
`ClientServerBootstrap` (`BovineLabs.Nerve/Utility/BovineLabsBootstrap.NetCode.cs:20`). The
shape is a persistent `ServiceWorld` built from the `Worlds.Service` filter
(`BovineLabs.Nerve/Utility/BovineLabsBootstrap.cs:214` and `:218`), a `MenuWorld` from
`Worlds.Menu` (`:123`, `:127`), and client/server worlds created and destroyed on demand
through `CreateClientServerWorlds` (`BovineLabsBootstrap.NetCode.cs:45`). Those extra world
kinds are plain flag constants: `Worlds.Service` is filter bit 21 and `Worlds.ServiceWorld` is
`WorldFlags` bit 16 (`BovineLabs.Core/Worlds.cs:12`, `:34`).

The practical rule that falls out: netcode decides *whether* client and server worlds exist,
Nerve decides *when*.

## Singletons are per-world, and that is the expensive one

A singleton is an entity in one world's `EntityManager`. There is no process-wide registry.
`GetSingleton<T>` in the client world cannot see a singleton created in the server world, and
the failure mode is not a null — it is a query that matches nothing and throws, or a system
that silently never updates because `RequireForUpdate<T>` was never satisfied.

This is the most common source of "works in the editor, fails in the build", for a mechanical
reason. In the editor with `ClientAndServer`, both worlds exist in one process, so a system
that reaches for a singleton it does not own often finds *a* copy anyway — the wrong one, but
a plausible one. Ship a dedicated server and the client world is not there; ship a client and
the server world is not there. The lookup that was quietly wrong becomes loudly absent.

Physics makes this concrete. `PhysicsWorldSingleton`
(`Unity.Physics/ECS/Base/Components/PhysicsComponents.cs:476`) and `SimulationSingleton`
(`:417`) exist once per world that runs the physics systems. A raycast in the client world
queries the client's collision world, which is a prediction. The same raycast in the server
world queries authority. They are different answers to the same call, and which one you got
depends entirely on which world your system was filtered into.

Chapter 43 takes that one step further and asks where the physics systems actually live.

→ [43 · Physics on the server](43-physics-on-the-server.md)
