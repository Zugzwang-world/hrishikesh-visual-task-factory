# G8 on the digital twins

Gate G8 registers a photo of a physical twin onto its layout and checks that every object is where the layout sheet says. Before any real photo exists, it is proved here on the digital twins: each of the 12 layouts rendered in Blender with the printed mat on the table, from the three photo spots of the guide, and each render put through G8 as if it were a phone photo (`uv run vtf twin check out/twins_digital`). Ground truth for the pose is the render's own camera.

## Result

- Correctly placed layouts: **36 of 36 views pass**.
- Negative control (scene_0001, the brown onion moved 4 cm): **3 of 3 views rejected**; measured displacement 41, 40, 39 mm.
- Camera pose from the markers: position error median 0.62 mm (max 3.23), rotation median 0.055 deg (max 0.302); marker reprojection at most 1.395 px; at least 3 of 4 markers in every view.
- Placement error of correctly placed objects: median 1.0 mm, max 10.4 mm over 72 object views; 45 object views not checked (white on the white mat, or hidden behind or inside another object).

## Tolerances

- At least 3 of the 4 ArUco markers; marker-corner reprojection at most 2.0 px (at 1024 px width).
- Each object within 2 cm of its layout position (searched +-6 cm on the table plane), and found: at least 50% of its outline differs from the printed mat at the best position.
- Not checked automatically: white objects (the tape roll and the spray bottle look like the white paper with a shadow), and objects with under 30% of their outline clear of other objects in that view. Their outlines are drawn on the overlay for a glance.

## Per view

| Layout | Scene | View | Pose error (mm, deg) | Reprojection (px) | Placement error per object (mm) | G8 |
| --- | --- | --- | --- | --- | --- | --- |
| L01 | scene_0001 | front | 1.17, 0.06 | 0.6666 | brown onion 2, white tape n/c, white bottle n/c | pass |
| L01 | scene_0001 | overhead | 0.65, 0.013 | 0.6424 | brown onion 1, white tape n/c, white bottle n/c | pass |
| L01 | scene_0001 | side | 0.25, 0.064 | 0.5584 | brown onion 1, white tape n/c, white bottle n/c | pass |
| L02 | scene_0006 | front | 0.7, 0.043 | 0.7544 | white tape n/c, brown onion 1, brown kiwi 1 | pass |
| L02 | scene_0006 | overhead | 0.55, 0.004 | 0.6776 | white tape n/c, brown onion 0, brown kiwi 1 | pass |
| L02 | scene_0006 | side | 0.54, 0.06 | 0.7535 | white tape n/c, brown onion 1, brown kiwi 0 | pass |
| L03 | scene_0008 | front | 1.2, 0.07 | 0.6448 | white tape n/c, green lime 2, white bottle n/c, green lime 2 | pass |
| L03 | scene_0008 | overhead | 0.41, 0.022 | 0.6114 | white tape n/c, green lime 1, white bottle n/c, green lime 1 | pass |
| L03 | scene_0008 | side | 0.1, 0.05 | 0.9858 | white tape n/c, green lime 0, white bottle n/c, green lime 1 | pass |
| L04 | scene_0012 | front | 0.84, 0.043 | 0.6758 | white tape n/c, brown bowl 0, brown onion 0 | pass |
| L04 | scene_0012 | overhead | 0.61, 0.01 | 0.6439 | white tape n/c, brown bowl 0, brown onion 1 | pass |
| L04 | scene_0012 | side | 0.41, 0.051 | 0.7446 | white tape n/c, brown bowl 0, brown onion 3 | pass |
| L05 | scene_0015 | front | 2.15, 0.199 | 0.7982 | brown bowl 1, green lime n/c, white bottle n/c | pass |
| L05 | scene_0015 | overhead | 1.22, 0.073 | 0.6652 | brown bowl 1, green lime n/c, white bottle n/c | pass |
| L05 | scene_0015 | side | 0.54, 0.06 | 0.7184 | brown bowl 2, green lime n/c, white bottle n/c | pass |
| L06 | scene_0019 | front | 0.95, 0.054 | 0.757 | green lime 1, green lime 1, green lime 1 | pass |
| L06 | scene_0019 | overhead | 0.55, 0.007 | 0.6677 | green lime 1, green lime 1, green lime 1 | pass |
| L06 | scene_0019 | side | 0.62, 0.063 | 0.7136 | green lime 1, green lime 1, green lime 1 | pass |
| L07 | scene_0021 | front | 1.86, 0.172 | 0.8106 | white tape n/c, brown onion 1, white bottle n/c | pass |
| L07 | scene_0021 | overhead | 0.65, 0.012 | 0.6906 | white tape n/c, brown onion 1, white bottle n/c | pass |
| L07 | scene_0021 | side | 0.39, 0.083 | 0.6305 | white tape n/c, brown onion 5, white bottle n/c | pass |
| L08 | scene_0027 | front | 1.03, 0.056 | 0.8329 | brown bowl 1, brown kiwi 2, white tape n/c | pass |
| L08 | scene_0027 | overhead | 0.64, 0.008 | 0.6788 | brown bowl 1, brown kiwi 1, white tape n/c | pass |
| L08 | scene_0027 | side | 0.34, 0.081 | 0.6241 | brown bowl 1, brown kiwi 1, white tape n/c | pass |
| L09 | scene_0028 | front | 0.75, 0.04 | 0.7213 | brown onion 10, brown kiwi 2, green lime 3 | pass |
| L09 | scene_0028 | overhead | 0.54, 0.008 | 0.6689 | brown onion 2, brown kiwi 2, green lime 4 | pass |
| L09 | scene_0028 | side | 0.21, 0.066 | 0.6055 | brown onion 2, brown kiwi 2, green lime 3 | pass |
| L10 | scene_0030 | front | 0.7, 0.039 | 0.7306 | white tape n/c, brown onion 6, brown kiwi 2 | pass |
| L10 | scene_0030 | overhead | 0.57, 0.016 | 0.6662 | white tape n/c, brown onion 1, brown kiwi 1 | pass |
| L10 | scene_0030 | side | 0.24, 0.071 | 0.6073 | white tape n/c, brown onion 2, brown kiwi 1 | pass |
| L11 | scene_0034 | front | 0.76, 0.042 | 0.7869 | green lime 2, green lime 1, brown bowl 0, green lime 1 | pass |
| L11 | scene_0034 | overhead | 0.56, 0.006 | 0.6762 | green lime 2, green lime 1, brown bowl 0, green lime 1 | pass |
| L11 | scene_0034 | side | 0.25, 0.07 | 0.6212 | green lime 1, green lime 1, brown bowl 0, green lime 0 | pass |
| L12 | scene_0035 | front | 1.11, 0.055 | 0.6652 | white tape n/c, white bottle n/c, brown bowl 1, green lime n/c | pass |
| L12 | scene_0035 | overhead | 3.23, 0.302 | 1.3954 | white tape n/c, white bottle n/c, brown bowl 1, green lime n/c | pass |
| L12 | scene_0035 | side | 0.23, 0.07 | 0.5842 | white tape n/c, white bottle n/c, brown bowl 1, green lime n/c | pass |
| L01 | scene_0001 | front (misplaced) | 1.17, 0.061 | 0.6659 | brown onion 41, white tape n/c, white bottle n/c | reject |
| L01 | scene_0001 | overhead (misplaced) | 0.62, 0.013 | 0.6418 | brown onion 40, white tape n/c, white bottle n/c | reject |
| L01 | scene_0001 | side (misplaced) | 0.23, 0.063 | 0.5582 | brown onion 39, white tape n/c, white bottle n/c | reject |

n/c = not checked. Overlays: out/twins_digital/g8_overlays/ (green placed, red rejected with the measured position, yellow not checked). Data: out/twins_digital/g8_check.json.
