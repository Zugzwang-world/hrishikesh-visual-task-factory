# NOW

**Updated:** 23 Nov 2026, 19:15 IST
**Stage:** **Stage 3, Core factory, in progress (brief 05, overnight and unattended).** Contracts v0.2 are on main; eight builder lanes run in parallel worktrees.
**Deadline:** submissions close 6 Dec; our real deadline is the night of 5 Dec.

## Running now

- Contracts v0.2 on main: asset manifest contract, 12 question families, shortcut_conflict, twins, three camera rigs with a quality dial, key light, compressed depth. The family rules are in spec/families.md.
- Lanes: render assets and look (P10, P12); render cameras and pipeline (P13, glTF, batching); sampler v1 and mesh ray caster (P11); all 12 question families (P14); gates G2, G4, G6 and the scale driver (P15, P18 tooling); G5, audit page and dataset report (P15, P16, P17); Task Explorer on Vercel (P25); physical-twin mat, layouts and shopping list (P19).

## Merged so far

- Asset manifest: 40 Poly Haven objects and 4 exact-size references (P10 part 1).
- Task Explorer v0, live and public: https://vtf-task-explorer.vercel.app (stage-2 sample for now).
- Physical twins: placement mat (A3 and tiled A4), ArUco registration stub, shopping list, layout-sheet generator (placeholder layouts until sampler v1 lands).
- G5 balance and blind baseline, eval sampling with splits by family, rig and shortcut_conflict, the blind audit page (stage-2 nearest-to-gripper set ready at out/audit_s2/index.html), the dataset report.

## Next tonight

1. Merge each lane when it runs; a 20-scene end-to-end check on all three cameras.
2. Re-run nearest-to-gripper with the key light and the low gripper; accuracy split by shortcut_conflict.
3. Scale run: 500 scenes x 3 cameras into out/v1, batches of 25, detached; Sonnet on up to 1,500 tasks; dataset report.

## Running cost of model calls

- Stage 2: $0.25. Stage 3 so far: $0.

## Asks

- **Free disk space (blocks the scale run).** Your rule: keep at least 20 GB free during the scale run. At 19:10 the Mac had 9.9 GB free, because swap has grown to 21 GB over 11 days without a restart. The scale run checks free space before every batch and stops at the last finished batch below the floor. To unblock it: restart the Mac (clears the swap), or clear app caches outside the project (Chrome about 5.7 GB, Playwright browsers about 4.4 GB, VS Code update files 1.6 GB, pnpm 1.5 GB, Homebrew 0.8 GB). The run resumes with one command, re-rendering nothing.
- Vercel asks for two-factor authentication on the account: https://vercel.com/account/settings/authentication?setup=authenticator
