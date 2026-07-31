# 09 · Canopy: hierarchical states

Canopy is one idea: **a state is a node that can contain other states.** Everything else
follows.

## The tree

```mermaid
stateDiagram-v2
    [*] --> root
    state root {
        [*] --> connecting
        connecting --> gameplay : gate completed
        gameplay --> disconnected : tracker sees terminal event
        connecting --> disconnected : tracker sees terminal event
    }
```

Rules the validator enforces (`CanopyGraphValidation`):

| Rule | Error if violated |
|---|---|
| Exactly one state has no parent | `Graph must define exactly one root state.` |
| Every composite state has exactly one default child | `Composite state 'X' must define exactly one default child.` |
| State ids are unique **after FixedString64 hashing** | `State id 'a' collides with state id 'b' after FixedString64 hash.` |

That third one is delightful and worth pausing on. `CanopyStateId` is a hash of a
`FixedString64Bytes`, so two different names can collide. The validator brute-forces the
check at author time rather than letting you ship a 1-in-2^n heisenbug.

> **⚡ Hardware analogy** — a **CAM with collision detection at programming time**. Same
> reason you validate a hash table's key set offline when lookups must be O(1) and correct.

## What "active" means

At any moment the machine has an **active path** from root to a leaf, not a single state.
In our client graph the active path is `root → gameplay`. Both are active. `root`'s blocks
run every tick — which is exactly why the connection tracker lives there and keeps working
no matter which child is current.

```
active path:   root ──▶ gameplay
                │
                └─ blocks on root run EVERY tick, in EVERY child state
```

> **💀 Trap** — put the connection tracker on `connecting` instead of `root` and you stop
> detecting disconnects the moment you enter gameplay. The bug appears only after a
> successful connection, which is the worst possible time to find it.

## Blocks: what a state actually *does*

A state is a container. The work is in **blocks** attached to it.

| Concept | Grove type | Runs |
|---|---|---|
| State | `GroveContextNode<CanopyStateAuth, CanopyStateData, CanopyUpdateState>` | while on the active path |
| Block | `CanopyBlockAuth<TData>` | each tick its owner state is active |

Blocks receive a `CanopyUpdateState`, which is an enum-ish signal:

```
Enter  →  Update  →  Update  →  …  →  Exit
```

Every block must check for `Exit` and bail. Look at any Nerve node and the first three lines
are the same:

```csharp
if (updateState == CanopyUpdateState.Exit) { return; }
```

> **💀 Trap** — forget that check and your block does its work *while the state is being torn
> down*, typically after the data it depends on is gone.

## Transitions: `CanopyGoTo`

There are no transition edges in the graph. A node just calls:

```csharp
CanopyGoTo.Execute(in groveContext, ref context, data.OnDisconnection);
```

where `OnDisconnection` is a `CanopyStateId` authored as a **string**. The machine resolves
it, exits the old path bottom-up, enters the new path top-down.

This is deliberate. Edges in a visual graph get unreadable fast; a named jump is a `goto` and
is honest about it. The validator still checks that every `CanopyGoTo` target exists.

## History

`RememberHistory` on a composite state makes re-entry resume the child you left, instead of
the default child. Classic UML shallow history. Free, and occasionally exactly what a pause
menu wants.

## The executor

```csharp
public struct NerveStateMachineExecutor<T>
{
    private CanopyStateMachineExecutor<T> executor;
    private ComponentTypeHandle<NerveStateMachine> stateMachineTypeHandle;
}
```

Three calls, and that is the whole driver system:

```csharp
this.executor.OnCreate(ref state);                     // build query: GroveState + NerveStateMachine
var execution = this.executor.GetExecution(ref state); // update context, wrap
this.executor.Run(ref state, ref execution);           // chunks → entities → ExecuteGraph
```

Plus `Exit` on destroy, which walks the active path calling every block with
`CanopyUpdateState.Exit` so nothing leaks. Our `ClientAppStateSystem.OnDestroy` does this —
skip it and the last state never cleans up.

## Why Nerve is written in Canopy

Because "app flow" *is* a hierarchical state machine, and once it is data:

- a designer can see it
- the validator can prove it well-formed
- the tests can build one in six lines (`ClientConnectionTrackerExecutionTests` does)
- and you can ship a different one per world without recompiling

That last point is the one we exploited today: the sample shipped a service graph and a menu
graph, and we added a **client** graph without touching a single package file.

→ [10 · Nerve: the game framework](10-nerve.md)
