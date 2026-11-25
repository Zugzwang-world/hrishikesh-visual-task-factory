# Scale stated: distance and free space

The 272 distance and free-space tasks among the 1,567 eval tasks and the 150 audit tasks, asked again to every Claude setting with one sentence in front of the question. 128 sit on the printed grid and get "The table has a printed grid of 1 cm squares with bold lines every 10 cm."; the other 144 have no grid and use a procedural reference object for scale, so they get that object's exact size instead ("The cube is exactly 5 cm on each side."), because the grid sentence would be false there. Everything else is unchanged. Paired on the same tasks; 95% intervals from 2,000 scene resamples. Frozen v1 results are untouched; these answers are in out/v1/sweep_scale/.

| Setting | Not stated | Stated | Change, points (95% interval) | Distance | Free space | Grid scenes | Reference scenes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Haiku 5.5 (high) | 45% | 50% | +5 (-1 to +11) | +12 (+3 to +22) | -1 (-7 to +6) | +9 (+1 to +16) | +2 (-6 to +10) |
| Sonnet 5.5 (high) | 51% | 60% | +10 (+4 to +15) | +20 (+9 to +30) | +1 (-5 to +7) | +13 (+5 to +22) | +6 (-2 to +14) |
| Opus 5.5 (high) | 62% | 63% | +1 (-5 to +6) | -1 (-12 to +10) | +2 (-3 to +7) | +2 (-6 to +10) | -1 (-9 to +7) |
| Opus 5.5 (xhigh) | 67% | 67% | +1 (-5 to +6) | +3 (-7 to +13) | -1 (-7 to +4) | +1 (-6 to +8) | +1 (-8 to +9) |

Tasks that flipped (wrong to right / right to wrong) when the scale was stated:

- Haiku 5.5 (high): 36 / 22 of 272
- Sonnet 5.5 (high): 51 / 25 of 272
- Opus 5.5 (high): 29 / 27 of 272
- Opus 5.5 (xhigh): 31 / 29 of 272
