# 01 · The first clock edge

Power rail comes up. The reset line releases. The core fetches its first instruction from
the reset vector. Some tens of millions of instructions later — firmware, bootloader,
kernel, init, display server — a process called `Unity` gets a PID and a main thread.

Everything in this book happens inside that one process. Let's zoom until it hurts.

## A frame is a scheduling quantum, not a unit of time

Here is the first thing that trips up people from embedded: **a frame is not a timer
interrupt.** Nothing fires it. It is a `while (true)` loop that runs as fast as the machine
and the presentation engine allow.

```
while (!quit) {
    pump OS events          // input, window, focus
    run PlayerLoop          // ← everything you write lives here
    render                  // submit to GPU
    present                 // block until vsync / swapchain slot
}
```

The only thing that makes it *periodic* is `present` blocking on the display. That is your
clock, and it is a lousy one: it jitters, it drops, it changes when the user drags the
window to a 60 Hz monitor from a 144 Hz one.

> **⚡ Hardware analogy** — the frame loop is a **free-running superloop**, not an ISR.
> Everything that needs real periodicity has to build its own timebase on top. That is
> exactly what NetCode's tick system does, and why Part III spends a whole chapter on it.

## The PlayerLoop is a mutable table

Unity's `PlayerLoop` is a tree of function pointers grouped into phases:
`Initialization`, `EarlyUpdate`, `FixedUpdate`, `PreUpdate`, `Update`, `PreLateUpdate`,
`PostLateUpdate`. You can read it, modify it, and write it back at runtime.

DOTS uses this. When a `World` is created, Entities inserts callbacks into that table so
its three root system groups get ticked:

| Root group | PlayerLoop phase |
|---|---|
| `InitializationSystemGroup` | `Initialization` |
| `SimulationSystemGroup` | `Update` |
| `PresentationSystemGroup` | `PreLateUpdate` |

> **⚡ Hardware analogy** — the PlayerLoop is a **vector table you can patch**. DOTS
> installs three entries. Everything DOTS ever does is downstream of those three function
> pointers being called once per frame per world.

That is the whole bootstrap secret. There is no daemon, no scheduler, no magic. Three
function pointers.

## Where our two capsules enter

Our project has, at peak, **six** live worlds in one process (Part I chapter 3 explains
why). Each one registers into that same table. So a single frame looks like:

```
frame N
├─ Initialization
│   ├─ ServiceWorld.Initialization
│   ├─ MenuWorld.Initialization
│   ├─ ClientWorld.Initialization
│   └─ ServerWorld.Initialization
├─ Update
│   ├─ ServiceWorld.Simulation      ← app state graph
│   ├─ ClientWorld.Simulation       ← input, prediction, our capsule
│   └─ ServerWorld.Simulation       ← authority, ghost send
└─ PreLateUpdate
    ├─ ClientWorld.Presentation     ← transforms → rendering
    └─ ServerWorld.Presentation     ← (mostly nothing, headless)
```

Single-threaded at this level. The worlds do not run concurrently with each other. They run
*in sequence, in one frame*, which is why "the client and the server are in the same
process" is a statement about determinism and not about parallelism.

> **💀 Trap** — because it is one process, a stall in the server world *is* a stall in the
> client world. When you see NetCode's "Server Tick Batching has occurred" warning in the
> editor, it usually means the editor itself hitched, not that your server logic is slow.

## The clock you actually get

Inside a system, `SystemAPI.Time.DeltaTime` gives you the world's delta. Which delta depends
on *where the system is standing*:

- In `SimulationSystemGroup` at the top level → frame delta (variable, jittery).
- Inside `FixedStepSimulationSystemGroup` → fixed delta, catch-up loop.
- Inside `PredictedSimulationSystemGroup` → **the network tick delta**, and the group may
  run the same system many times in one frame during a rollback.

That last one is why `PlayerMoveSystem` in our project reads `SystemAPI.Time.DeltaTime`
without further thought and is still correct. It is standing in the prediction group, so
the delta it gets is one network tick — every time, including during replay.

> **🔬 Probe** — in play mode, dump the live loop:
> ```csharp
> UnityEngine.LowLevel.PlayerLoop.GetCurrentPlayerLoop()
> ```
> You will see the DOTS entries sitting in the table next to Unity's built-ins.

Next: why any of this needs a new memory model at all.

→ [02 · Cache lines all the way down](02-cache-lines.md)
