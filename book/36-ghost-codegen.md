# 36 · Ghost serializer code generation

Chapter 14 said the prefab is the schema and that baking generates serializer code per ghost
type. This chapter opens that box: what the generator reads, what it writes, where the written
code goes, and how the runtime dispatches into it.

> **📄 Provenance** — derived from reading the Netcode for Entities package source at version
> 6.6.0, including the generator project under `Runtime/SourceGenerators/Source~`. **Not
> measured on the two-capsule project**; the runtime-verified chapters of this book are 19
> through 23. Every default value names the file and line it came from.

## It is a Roslyn generator, not a baking step

`NetCodeSourceGenerator` implements `ISourceGenerator` and runs during C# compilation, once
per assembly (`Runtime/SourceGenerators/Source~/NetCodeSourceGenerator/Generators/NetCodeSourceGenerator.cs:85`).
It registers a syntax receiver that sweeps every syntax tree and sorts candidates into seven
buckets: components, RPCs, commands, inputs, variants, ghost behaviours, and remotes
(`Runtime/SourceGenerators/Source~/NetCodeSourceGenerator/Generators/NetCodeSourceGenerator.cs:88`).

This is why replication has no runtime reflection cost. By the time the world exists, the
serialization code is already compiled machine code with a Burst function pointer attached to
it.

It also explains a category of confusing failure. If your component lives in an assembly that
does not reference `Unity.NetCode`, the generator skips that assembly entirely
(`Runtime/SourceGenerators/Source~/NetCodeSourceGenerator/Generators/NetCodeSourceGenerator.cs:112`),
and your `[GhostField]` attributes do nothing at all — with no error, because nothing ran.

## What it reads: three attributes and a pile of templates

The attributes are the schema declaration.

`[GhostField]` goes on fields and properties, and every option has a default worth knowing
(`Runtime/Authoring/GhostFieldAttribute.cs:14`):

| Option | Default | Effect |
|---|---|---|
| `Quantization` | `-1` | negative means unset; floats then require an explicit choice or an unquantized template. `0` sends full precision (line 28) |
| `Composite` | `false` | one change bit per inner field; set true for a single bit covering the whole nested struct (line 40) |
| `Smoothing` | `SmoothingAction.Clamp` | interpolation behaviour on the receiving client (line 46) |
| `SubType` | `GhostFieldSubType.None` | selects a custom template for this field's type (line 50) |
| `SendData` | `true` | false keeps the field local and out of the snapshot (line 56) |
| `MaxSmoothingDistance` | `0` | above this delta, snap instead of interpolate; `0` disables the check (line 67) |

`[GhostComponent]` goes on the type and controls the component as a whole
(`Runtime/Authoring/GhostComponentAttribute.cs:12`):

| Option | Default | Effect |
|---|---|---|
| `PrefabType` | `GhostPrefabType.All` | which baked prefab variants keep the component at all (line 17) |
| `SendTypeOptimization` | `GhostSendType.AllClients` | send to interpolated clients, predicted clients, or both (line 22) |
| `OwnerSendType` | `SendToOwnerType.All` | send to the owner, to everyone else, or both (line 27) |
| `SendDataForChildEntity` | `false` | children are not replicated unless you opt in, because finding them costs a chunk lookup (line 35) |

`[GhostEnabledBit]` goes on the type and takes no arguments
(`Runtime/Authoring/GhostFieldAttribute.cs:77`). It is only legal on an `IEnableableComponent`,
and the generator reports a diagnostic if you put it anywhere else
(`Runtime/SourceGenerators/Source~/NetCodeSourceGenerator/Generators/TypeInformationBuilder.cs:79`).
Without it, an enableable component still replicates its **fields** while disabled; the enabled
flag itself is simply not on the wire.

The templates are the code shapes. They live as `.NetCodeSourceGenerator.additionalfile` files
under `Runtime/SourceGenerators/Templates`, one per supported field type, with separate
quantized and unquantized variants — `GhostSnapshotValueFloat` and
`GhostSnapshotValueFloatUnquantized`, `GhostSnapshotValueQuaternion` and its unquantized twin,
and so on down to every fixed string size.

## What it emits

For each replicated component the generator instantiates the component serializer template and
produces a struct containing a private `Snapshot` layout plus these methods:

| Emitted method | Runs where | Job |
|---|---|---|
| `CopyToSnapshot` | server | component values into the snapshot struct, applying quantization |
| `CalculateChangeMask` | server | compare against the baseline snapshot, set one bit per changed field |
| `PredictDelta` | both | derive a predicted baseline from the three most recent baselines |
| `Serialize` | server | write only the fields whose change bit is set, delta-coded |
| `Deserialize` | client | read the same fields; unset bits copy from the baseline |
| `CopyFromSnapshot` | client | snapshot back into components, dequantizing and interpolating |
| `RestoreFromBackup` | client | restore only replicated fields from the prediction history |
| `ReportPredictionErrors` | editor and debug builds | per-field error magnitude for the prediction debugger |

Alongside them it emits one `GhostComponentSerializerRegistrationSystem` per assembly, ordered
into `GhostComponentSerializerCollectionSystemGroup` and created before
`DefaultVariantSystemGroup`
(`Runtime/SourceGenerators/Templates/GhostComponentSerializerRegistrationSystem.NetCodeSourceGenerator.additionalfile:24`).
Its whole job is to call `AddSerializer` once per generated serializer
(`Runtime/Snapshot/GhostComponentSerializerCollectionSystemGroup.cs:320`).

## The registry

`GhostComponentSerializer.State` is the descriptor the registration system hands over
(`Runtime/Snapshot/GhostComponentSerializer.cs:222`). It ends up as a dynamic buffer on the
`GhostCollection` singleton entity.

| Field | What it carries |
|---|---|
| `SerializerHash`, `GhostFieldsHash`, `VariantHash` | identity and protocol version inputs (lines 227, 232, 237) |
| `ComponentType` | the type this serializer acts on (line 241) |
| `ComponentSize`, `SnapshotSize` | in-chunk size versus on-the-wire size (lines 249, 253) |
| `ChangeMaskBits` | how many change bits this component contributes (line 261) |
| `SerializesEnabledBit` | one if `[GhostEnabledBit]` was present (line 264) |
| `PrefabType`, `SendMask`, `SendToOwner` | the routing rules from `[GhostComponent]` (lines 270, 275, 280) |
| `CopyToSnapshot`, `CopyFromSnapshot`, `RestoreFromBackup`, `PredictDelta`, `Serialize`, `SerializeBuffer`, `PostSerialize`, `Deserialize` | Burst function pointers into the generated code (lines 284 through 324) |

> **⚡ Hardware analogy** — this buffer is a **descriptor table**, and each entry is a device
> descriptor with a small vtable of entry points. Dispatch is an index into a buffer followed
> by an indirect call, exactly like a driver jump table. Nothing is looked up by name, and
> nothing is reflected over, at any point after startup.

`GhostCollectionSystem` then composes these per-component descriptors into per-prefab
descriptors — `GhostCollectionPrefabSerializer` — which is where the totals live:
`SnapshotSize`, `ChangeMaskBits`, `EnableableBits`, and the number of buffers. Those totals are
what the spawn and send paths read when they size a snapshot slot.

## Change masks

A snapshot slot begins with a tick, then the change mask words, then the enabled-bit mask
words, then the component data — all aligned. `PredictedGhostSpawnSystem` computes exactly that
layout when it initialises a client-spawned ghost, which makes it the clearest place in the
package to read the arithmetic
(`Runtime/Snapshot/PredictedGhostSpawnSystem.cs:141`).

Each replicated field contributes one bit by default. `Composite = true` collapses a nested
struct to a single bit for the whole struct. Enabled bits are counted separately from change
bits and get their own mask array.

The mask is what makes a snapshot cheap. `Serialize` writes a field only when its bit is set;
`Deserialize` copies from the baseline when it is not. An unchanged component costs its change
mask bits and nothing more.

## Quantization, and why it is a compression trick

The quantized float template does exactly two arithmetic operations. On the way out it rounds
`value * scale` to an int; on the way in it multiplies the int by `1 / scale`.

The important part is the ordering. Quantization happens in `CopyToSnapshot`, which runs
**before** `CalculateChangeMask`. The comparison is therefore between integers, not floats. A
position that jitters in the eighth decimal place produces the same integer two ticks running,
its change bit stays clear, and it costs nothing.

That is the real reason quantization saves bandwidth. The narrower field width is secondary;
the primary win is that quantized values compare equal far more often.

> **💀 Trap** — the same ordering is why over-coarse quantization causes continuous
> micro-rollbacks. The client compares its prediction against the **dequantized** server value.
> If your quantization step is larger than the distance your simulation moves in one tick, the
> two can never agree, and you rollback every single tick forever.

## The delta-compression encoder

Serialization does not delta against the previous value. It deltas against a **prediction** of
the current value derived from three baselines.

`GhostDeltaPredictor` takes the current tick and three baseline ticks and computes two
fractions in sixteenths (`Runtime/Snapshot/GhostDeltaPredictor.cs:28`). `PredictInt` extends
the trend from baseline two to baseline one, checks whether that extrapolation is actually
better than simply using baseline zero, and returns the better of the two
(`Runtime/Snapshot/GhostDeltaPredictor.cs:41`). A body moving at constant velocity therefore
produces a predicted baseline that is nearly exact, and the delta written to the wire is close
to zero.

The zero itself is then encoded with a `StreamCompressionModel`, a Huffman model over small
magnitudes, so near-zero deltas cost a handful of bits. Baselines older than `MaxBaselineAge`
are refused (`Runtime/Snapshot/GhostSendSystem.cs:89`), and the snapshot ring the baselines
come from is `GhostSystemConstants.SnapshotHistorySize`, which is 32 by default and can be
compiled down to 16 or 6 with a define (`Runtime/Snapshot/GhostSendSystem.cs:65`).

> **⚡ Hardware analogy** — a **linear predictor feeding an entropy coder**. Same structure as
> a lossless image or audio codec: predict from history, encode the residual, and let the
> entropy stage reward you for predicting well.

## Reading the generated code yourself

The generator writes its output to disk when you enable it, and the switch is a Roslyn global
config file rather than a project setting.

| Option | Default | Effect |
|---|---|---|
| `unity.netcode.sourcegenerator.write_files_to_disk` | off unless configured | writes the generated C# to the output folder |
| `unity.netcode.sourcegenerator.write_logs_to_disk` | off unless configured | writes `SourceGenerator.log` |
| `unity.netcode.sourcegenerator.logging_level` | `info` in the template | generator log verbosity |
| `unity.netcode.sourcegenerator.emit_timing` | `0` | per-phase generator timings |
| `unity.netcode.sourcegenerator.outputfolder` | `Temp/NetCodeGenerated` | where the above land (`Runtime/SourceGenerators/Source~/NetCodeSourceGenerator/Helpers/SourceGeneratorHelpers.cs:98`) |
| `unity.netcode.sourcegenerator.attach_debugger` | commented out | blocks the generator until a debugger attaches |

The menu item **Assets → Create → Multiplayer → SourceGenerator AnalyzerConfig** writes a
`Default.globalconfig` at the root of `Assets` with all of these pre-filled
(`Editor/SourceGeneratorSettings.cs:14`). Two more menu items exist:
**Assets → Multiplayer → Open Source Generated Folder** and
**Assets → Multiplayer → Force Code Generation**, the latter forcing a reimport of the netcode
runtime folder because templates are not tracked as compilation dependencies
(`Editor/CodeGenMenu.cs:8`).

Read a generated serializer for a component you already understand. `Serialize` will show you
one `if` per field guarding one packed-delta write; that is the entire wire format for that
component, in plain C#.

## When to use what

- **`[GhostField]` with no options.** Integers, booleans, enums, and anything already discrete.
  Quantization does not apply and the defaults are correct.
- **`Quantization = 1000` with `Smoothing = InterpolateAndExtrapolate`.** Positions and
  rotations on a human-scale character. Half a millimetre of error, smooth on interpolated
  clients.
- **`Quantization = 0`.** Full precision, thirty-two bits per component. Use for values where
  any rounding is a correctness bug, and expect to pay for it every tick the field changes.
- **`Composite = true`.** A tightly coupled struct whose fields always change together — a
  colour, a two-component aim vector. One bit instead of four, and no loss, because they never
  change independently anyway.
- **`SendData = false`.** A field inside an otherwise replicated struct that is derived,
  cached, or local. Keeps the struct's shape without paying for the member.
- **`OwnerSendType = SendToOwner`.** Ammo counts, cooldowns, private state. A bandwidth saving
  and an anti-cheat measure in one attribute: data never sent cannot be extracted.
- **`SubType` and a custom template.** The last resort, for a domain-specific encoding that the
  built-in templates cannot express — a compressed direction, a bit-packed state machine. You
  are writing a codec; make sure the bandwidth saving justifies owning it.
- **A `[GhostComponentVariation]` instead.** When the type belongs to someone else and you
  cannot attach attributes to it. That is exactly how the package replicates
  `PhysicsVelocity`, as chapter 33 showed.

→ [37 · Interest management: scale and relevancy](37-interest-management.md)
