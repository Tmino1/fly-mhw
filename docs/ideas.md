# Ideas

A running reference of design ideas discussed in chat, including ones
that haven't been adopted. `docs/architecture.md` stays the record of what
is actually built and decided; an idea moves there once it's adopted and
implemented.

Each idea carries a status:

- **Adopted**: decided and recorded in `architecture.md`; kept here as a
  short pointer so this log stays complete.
- **Decided**: the user's call, made in chat, but not yet reflected in code
  or `architecture.md`.
- **Direction**: the user wants to head this way; the details are open.
- **Suggestion**: raised by Claude and discussed, not adopted.
- **Open**: a question that has to be answered before building.

## Snapshot (2026-09-24)

| Idea | Status |
|---|---|
| [Drop the connectome brain](#drop-the-connectome-brain) | Adopted |
| [Principles: softer in IL, strict in RL](#principles-softer-in-il-strict-in-rl) | Adopted |
| [Compositional MoE: weapon experts × monster experts](#compositional-moe-weapon-experts--monster-experts) | Direction |
| [Tactics vs. mechanics hierarchy](#tactics-vs-mechanics-hierarchy) | Direction |
| [Shared tells and monster families](#shared-tells-and-monster-families) | Direction |
| [GNN over the moveset graph](#gnn-over-the-moveset-graph) | Direction |
| [First experiment: the 2×2 held-out cell](#first-experiment-the-22-held-out-cell) | Suggestion |
| [Gauge-gated moves](#gauge-gated-moves) | Open |

---

## Drop the connectome brain

**Adopted (2026-09-24)** — `architecture.md`, "Policy model + privileged
IL", and Design Principle 1 (retired). The policy is an ordinary learned
model: vision encoder → temporal model → readout, all trainable.

Knock-on effects noted there: expect to need more demos, or a
pretrained vision backbone to offset them; the clutch claw/slinger
warm-start plan was rewritten; the environment layer carries over
unchanged.

## Principles: softer in IL, strict in RL

**Adopted (2026-09-24)** — Design Principle 4 in `architecture.md`, as
rewritten. In short:

- **Inputs, every phase:** pixels + the efference copy (the agent's own
  combo-graph state). Identical in IL and RL.
- **IL:** privileged game state may be an auxiliary prediction target,
  feed a privileged teacher distilled into the pixel student, and check
  labels. Never an input.
- **RL:** privileged state feeds only the reward.

**Still open:** whether auxiliary targets carry on during RL fine-tuning.
Lua is already running for the reward, so it's feasible, and it may help
guard against forgetting, but it's a softening inside the strict phase.

## Compositional MoE: weapon experts × monster experts

**Direction (2026-09-24).** One base model. Each weapon has an expert and
each monster has an expert. A matchup activates its weapon expert plus its
monster expert (hard routing by matchup, no learned router). Goal: a new
matchup is learned quickly because both halves already exist.

Proposed pieces (Claude suggestions discussed alongside):

- **Shared, always-on base.** Skills that belong to neither axis: camera
  control, reading the HUD, generic dodging, stamina. Without it they get
  duplicated, each copy undertrained.
- **Tiny matchup adapter** for knowledge that only exists for the pair
  (which Great Sword punish fits Rathalos's openings). For a new matchup:
  freeze the base and both experts, train only this adapter on a few
  hunts. That's the "learn quickly" mechanism, and it keeps a new
  matchup from damaging experts that other matchups depend on.

**Main risk: entanglement.** A Great Sword expert trained only alongside
the Jagras expert will absorb Jagras-specific habits, because nothing
forces the split. The fix is in data collection, not architecture: every
weapon must be seen with ≥2 monsters and every monster with ≥2 weapons. A
crossed recording grid, not a list of pairs.

**Why not learned sparse routing (Switch/Mixtral-style):** sparse MoE
buys capacity at fixed compute. This project's bottleneck is data from a
single real-time game instance, and learned routers collapse or
under-train experts on thin data. Adapters/FiLM are the other data-light
option if hard-routed experts struggle.

**Open:**
- How experts combine (summed adapters are the simplest; gating is more
  expressive but blurs "plug in two pieces").
- New matchup: zero-shot + RL, or a few demo hunts first? Probably the
  latter, given real-time-only data.
- Demo metadata must record the (weapon, monster) cell cleanly. Episodes
  are already per-quest; this just needs checking.

## Tactics vs. mechanics hierarchy

**Direction (2026-09-24).** Experts sit at different levels, not all at
the same layer. The split mirrors how good players think:

- **Tactics (mostly monster-driven):** read the monster's state, spot an
  opening, choose punish / reposition / evade / wait, and judge how much
  commitment the opening affords.
- **Mechanics (mostly weapon-driven):** turn the intent into a tool call:
  which graph node, direction, charge level, when to release.

```
pixels → shared perception
       → tell reader (shared, with a thin monster/family adapter)
       → tactical policy    ← monster expert, plus a weapon summary
       → mechanics policy   ← weapon expert + GNN head
       → tool executor      (already scripted)
```

**Information flows both ways.** Tactics needs the weapon's commitment
profile: a 1.5 s opening is a True Charged Slash for Great Sword but a
full combo for Dual Blades. The GNN's pooled graph embedding (including
move durations) is a compact way to hand that up.

**Open:**
- **Interface:** a learned latent intent (end-to-end, opaque), or a small
  explicit intent vocabulary (punish / reposition / evade / wait + an
  opening-size estimate). Explicit is easier to audit but needs labels or
  a structural assumption.
- **Timing:** both levels every tool call (the hierarchy is only in layer
  depth; simplest start), or tactics decides less often, e.g. once per
  opening (options framework / feudal-style).

## Shared tells and monster families

**Direction (2026-09-24).** Monsters share many tells, and a good human
adapts to a new monster quickly. So most monster knowledge lives in a
**shared tell reader** (wind-ups, charges, tail spins, roars, enrage,
flinch/topple states). The per-monster expert is thin: it maps this
monster's quirks onto the shared vocabulary.

**Family tier (suggestion).** MHW monsters in the same class share
skeletons and many animations, so the monster side can be layered:
shared → family → monster. For example:

- Fanged Wyverns: Great Jagras, Tobi-Kadachi, Odogaron
- Brute Wyverns: Anjanath, Barroth, Radobaan
- Flying Wyverns: Rathalos, Rathian

A new Fanged Wyvern would start from the Fanged Wyvern expert. Weapons
could get a family tier too (blademaster vs. gunner at minimum).

**Making the tell reader actually learn generic tells.** Calling a module
"shared" doesn't make it learn shared tells. The strongest push is to
predict **the monsters' own animation IDs as an auxiliary training
target** during IL. This is allowed under Design Principle 4 (a privileged training target
during IL, never an input). `state_reader.lua` already reads the player's
`lmtID`; reading the monster's the same way is **unverified**.

## GNN over the moveset graph

**Direction.** How the idea evolved:

- **Under the connectome (first discussion):** marginal. The Great Sword
  graph is ~29 nodes and ~40 edges and fixed, and the tracker already
  resolves it exactly. A GNN adds inductive bias, not information, and on
  one fixed graph it becomes a per-node embedding table. The suggested
  start then was a graph-relative readout with hand-built node features
  (depth, charge level, input kind, motion value, node kind).
- **Under the MoE:** central, because the action space now varies by
  weapon. Roles:
  1. **Shared action head.** The mechanics policy outputs a query vector
     and scores the valid nodes of whichever graph is loaded. There are no
     per-weapon output heads, so "weapon is data" holds at the
     architecture level. Growing a moveset (clutch claw, slinger) becomes
     "add nodes to the YAML," not "grow the readout."
  2. **Weapon context.** The pooled graph embedding conditions (or
     generates) the weapon expert, so a never-seen weapon gets a
     reasonable starting expert instead of a random one. It also gives
     tactics the weapon summary it needs.
  3. **Shared anchors.** Universal tools (dodge, move, camera, sheathe)
     appear in every graph and tie the weapon embedding spaces together.
- **Edge structure worth using:** edge types (own / `continues_as` /
  global / re-root), input features (`y_tap`, `y_hold`, `rt_y`, ...),
  charge levels. Unconfirmed `candidates:` edges could enter as weighted
  edges instead of all-or-nothing. That points to a relational GNN
  (R-GCN or GAT with edge features). Graphs are tiny, so hand-rolled
  message passing is enough; no graph-library dependency needed.
- Monsters have no graph in our data, so a new monster starts cold, apart
  from the family tier.

## First experiment: the 2×2 held-out cell

**Suggestion.** The cheapest test of the whole compositional idea:

|             | Monster A (e.g. Great Jagras) | Monster B |
|---|---|---|
| Great Sword | train | train |
| Weapon B    | train | **held out** |

Train on three cells; measure the fourth zero-shot, then after a few
hunts of adapter fine-tuning; compare against training from scratch on
those same few hunts. If composition doesn't win at 2×2, it won't at
14×40.

Choosing the cells:

- Weapon B should be structurally unlike Great Sword (Long Sword or Charge
  Blade) so the test measures real transfer. That forces
  [gauge-gated moves](#gauge-gated-moves) to be solved early.
- For monsters, testing both a same-family and a different-family pairing
  (Jagras → Tobi-Kadachi vs. Jagras → Anjanath) measures how much the
  shared tell reader and the family tier are doing.

Sequencing (suggestion): one Great Sword + Jagras policy end to end on the
new architecture → add a second monster (tests monster conditioning) → add
a second weapon (tests the GNN head) → the 2×2 → the wider grid.

## Gauge-gated moves

**Open.** Great Sword's validity mask depends only on the agent's own
calls, which keeps it clean. Other weapons don't work that way:

- Charge Blade's big finisher needs phials.
- Long Sword's spirit gauge gates certain moves.
- Bowguns depend on ammo.

The gauge is visible on the HUD (pixels) but can't be computed from the
agent's own calls, so the tracker can't mask these moves by itself.

Options:

- Read the gauge from Lua for masking. This breaks the RL-strict rule,
  since the mask would come from privileged state.
- **Learned validity from pixels**, trained during IL with the Lua gauge
  reading as an auxiliary target. This fits the IL-soft / RL-strict split
  cleanly.
- Don't mask gauge-gated moves: an invalid call becomes a logged no-op,
  like masked calls today. The policy learns not to make it.

Decide this before the moveset schema grows past Great Sword.

---

## Older ideas already recorded in `architecture.md`

Pointers only; the detail lives there under "Not yet decided":

- Clutch claw / slinger expansion as a staged v2, not a dynamic unlock.
  Its warm-start reasoning is affected by
  [dropping the connectome](#drop-the-connectome-brain); the GNN head
  makes growing the moveset cheaper.
- IL→RL transition: off-policy RL plus a BC-regularization term against
  policy collapse.
- IL dataset size: record in batches and stop when held-out accuracy
  plateaus.
