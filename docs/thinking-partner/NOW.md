# NOW

**Updated:** 23 Nov 2026, 18:45 IST
**Stage:** **Stage 3, Core factory, in progress (brief 05, overnight and unattended).** Contracts v0.2 are on main; eight builder lanes run in parallel worktrees.
**Deadline:** submissions close 6 Dec; our real deadline is the night of 5 Dec.

## Running now

- Contracts v0.2 on main: asset manifest contract, 12 question families, shortcut_conflict, twins, three camera rigs with a quality dial, key light, compressed depth. The family rules are in spec/families.md.
- Lanes: render assets and look (P10, P12); render cameras and pipeline (P13, glTF, batching); sampler v1 and mesh ray caster (P11); all 12 question families (P14); gates G2, G4, G6 and the scale driver (P15, P18 tooling); G5, audit page and dataset report (P15, P16, P17); Task Explorer on Vercel (P25); physical-twin mat, layouts and shopping list (P19).

## Next tonight

1. Merge each lane when it runs; a 20-scene end-to-end check on all three cameras.
2. Re-run nearest-to-gripper with the key light and the low gripper; accuracy split by shortcut_conflict.
3. Scale run: 500 scenes x 3 cameras into out/v1, batches of 25, detached; Sonnet on up to 1,500 tasks; dataset report.

## Running cost of model calls

- Stage 2: $0.25. Stage 3 so far: $0.

## Asks

- Disk: about 17 GB free on the Mac. Enough for tonight; free some space before stage 4.
