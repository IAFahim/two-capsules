# 02 · Cache lines all the way down

You know this graph in your bones: DRAM latency has been roughly flat for twenty years
while core throughput went up two orders of magnitude. A cache miss costs you a few hundred
cycles. A modern core can retire a dozen instructions in that time, times however many
lanes your SIMD unit has.

So the fastest code is not the code with the fewest instructions. It is the code that
**streams**.

Classic Unity `GameObject`s do the opposite of streaming. Every `MonoBehaviour` is a
managed heap object with a vtable and a pointer back to a native `Transform`, which lives
in a different allocation, which points at a parent, which is somewhere else entirely.
Iterating a thousand of them is a thousand pointer chases into cold memory. It is a linked
list wearing an OOP costume.

ECS is the fix, and the fix is *just* struct-of-arrays with a scheduler bolted on.

## The three nouns

| Noun | What it really is |
|---|---|
| **Entity** | A 64-bit handle: `{ int Index; int Version; }`. Not an object. An index. |
| **Component** | A plain struct. No methods that matter, no inheritance, no vtable. |
| **Chunk** | A **16 KiB block** holding many entities that share the same component set. |

The chunk is the whole idea. Inside one chunk, memory is laid out as parallel arrays:

```
chunk (16384 bytes)
┌──────────────────────────────────────────────────────────┐
│ header: archetype ptr, entity count, change versions ... │
├──────────────────────────────────────────────────────────┤
│ Entity[]           e0 e1 e2 e3 ... eN                    │
│ LocalTransform[]   t0 t1 t2 t3 ... tN                    │
│ PlayerMoveInput[]  i0 i1 i2 i3 ... iN                    │
│ GhostOwner[]       o0 o1 o2 o3 ... oN                    │
└──────────────────────────────────────────────────────────┘
```

Walking `LocalTransform` for every entity in that chunk is a **linear scan of contiguous
memory**. The prefetcher sees it coming from the second element onward. There is no
indirection, no vtable, no `null` check.

> **⚡ Hardware analogy** — a chunk is a **DMA-friendly buffer**. You picked 16 KiB for the
> same reason an embedded engineer picks a buffer size: it fits comfortably in L1/L2, and
> it amortises the per-block bookkeeping over hundreds of elements.

## Archetype: the type of a chunk

An **archetype** is the *set* of component types. `{LocalTransform, PlayerMoveInput,
GhostOwner, Simulate}` is an archetype. Every chunk belongs to exactly one archetype, and
every entity in a chunk has exactly that component set — no more, no less.

This is what makes queries fast. `SystemAPI.Query<RefRW<LocalTransform>, RefRO<PlayerMoveInput>>()`
does **not** scan entities. It:

1. Matches the query mask against the (small) list of archetypes — a handful of bitwise ops.
2. For each matching archetype, walks its chunk list.
3. For each chunk, takes a raw pointer to each component array.
4. Loops `0..count`.

The per-entity cost is a struct read and a struct write. The per-*query* cost is the
archetype matching, done once.

> **💀 Trap** — adding or removing a component **moves the entity to a different chunk**.
> That is a memcpy of every component plus bookkeeping, and it invalidates any raw pointer
> you were holding. This is why DOTS code obsessively defers structural changes into an
> `EntityCommandBuffer` and plays it back at a known safe point. It is the same discipline
> as "don't `free()` inside the interrupt handler".

## Enableable components: the escape hatch

Moving chunks for a boolean is absurd, so Entities added **enableable** components. The
chunk header carries a 128-bit mask per enableable type. Disabling one flips a bit; the
entity stays put. Queries respect the mask via `ChunkEntityEnumerator`.

Remember this word: **`Simulate`**. It is an enableable tag component, and in Part III it
becomes the single most important bit in the whole prediction system. NetCode flips it to
say "this entity should tick on this tick". Forget to respect it and your capsule
integrates its velocity five times per frame instead of once.

## Versions: cheap change detection

Every chunk stores a `uint` version per component type, bumped whenever a system takes
write access. `WithChangeFilter<T>()` compares versions and skips whole chunks.

> **⚡ Hardware analogy** — a **dirty bit per cache line**, except the granularity is a
> chunk and the write-back is a system deciding whether to bother.

## What this costs you

Nothing is free. The bill for all that throughput:

- **No references.** A component cannot hold a `class`. It holds an `Entity` handle and you
  look it up — which is a random access, i.e. the exact thing you were avoiding.
- **Structural changes are expensive.** Design so they are rare and batched.
- **Debugging is different.** There is no object to inspect. You inspect *chunks*. Get
  comfortable with the Entities Hierarchy and `EntityQuery` in the debugger.

> **🔬 Probe** — count what is actually resident:
> ```csharp
> var q = em.CreateEntityQuery(ComponentType.ReadOnly<Unity.NetCode.GhostInstance>());
> q.CalculateEntityCount();          // entities
> q.CalculateChunkCount();           // chunks — the number that predicts your cost
> ```

Next: how many independent machines are we actually running?

→ [03 · Worlds: six machines in one process](03-worlds.md)
