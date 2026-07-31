# 00 · How to read this

You already know how a machine works. You know that a cache line is 64 bytes, that a
branch mispredict costs you ~15 cycles, that DMA exists so the CPU can stop babysitting
the bus. That knowledge is not a handicap here. It is the *whole advantage*.

Because Unity DOTS is not a game engine feature. It is **a memory-layout argument that
won**, wearing a game engine as a coat.

And Unity NetCode is not "multiplayer". It is **a distributed system with a fixed clock,
a command ring buffer, and speculative execution with rollback** — which is to say, it is
a CPU pipeline. You have debugged this shape before. You just called the parts different
names.

## The promise

By the end you will be able to:

- Trace one keystroke from the USB interrupt to two capsules moving on two screens, naming
  every system it passes through and every buffer it lands in.
- Open any package in this stack, find the system responsible for a symptom, and say *why*
  it is responsible before you read its body.
- Author a Grove graph in code, know what bytes it becomes, and know which function pointer
  executes them.
- Diagnose a bug class none of us have hit yet, because you understand the mechanism rather
  than the symptom.

That last one is the actual goal. Symptoms are infinite. Mechanisms are about forty.

## The shape of the book

| Part | What it covers | Your existing intuition |
|---|---|---|
| **I · The Machine** | Clock, memory, worlds, Burst | Pipelines, cache, SIMD, cores |
| **II · The Stack** | The 21 packages, assemblies, Core, Grove, Canopy, Nerve, UI | Firmware layers, linkers, bytecode VMs |
| **III · The Wire** | Ghosts, ticks, commands, prediction | Distributed clocks, speculative execution |
| **IV · Our Game** | Keypress → pixel, ownership, the client graph | Full-system trace |
| **V · Mastery** | Debug playbook, the Linux saga, how to extend | Bring-up and board bring-down |

Chapters are short on purpose. Each one is a single idea you can hold in your head while
walking to the kitchen. Read them in order the first time — Part III leans on Part I hard.

## The rules I held myself to

1. **Every claim is checkable.** Where a chapter says "system X does Y", there is a file
   path. Go look. If I got it wrong, the file wins.
2. **No hand-waving at the interesting part.** When something is genuinely a hack, it is
   labelled a hack, with the reason it exists and the cost of removing it.
3. **Failure stories are first-class.** Every trap in Part V is one we actually hit and
   actually fixed. The war stories are where the mechanism becomes memorable.

## A note on the source

This book documents a stack that includes **commercial packages** (BovineLabs Core, Grove,
Canopy, Nerve, Anchor). It describes behaviour, cites file paths, and quotes small API
signatures — the way a book about an SDK should. It does not reproduce those packages.
If you want to read the implementations, buy them; they are worth it, and this book is
much better with the sources open beside it.

Prose and diagrams here: MIT. The packages remain their authors'.

## Conventions

> **⚡ Hardware analogy** — a bridge from something you already own.

> **💀 Trap** — something that fails *silently*. These are the expensive ones.

> **🔬 Probe** — a command you can run right now to see the thing with your own eyes.

Let's start where everything starts: a clock edge.

→ [01 · The first clock edge](01-clock-edge.md)
