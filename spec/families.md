# Question families v0.2 (contract)

The 12 families in `vtf.core.program.FAMILIES`, with the exact rule each program computes on the settled scene file (G1), the independent pixel route G2 recomputes from the rendered index and depth passes, the G4 margins, the 2D shortcut heuristic behind `shortcut_conflict`, and the twin rule G6 applies. The questions lane (`vtf.programs`) and the validation lane (`vtf.validate`) both build against this file. If a rule must change, change it here first (Coordinator).

## Shared rules

- **Frames.** Every relation names its frame: "from the camera's view" (front and overhead rigs) or "in the gripper camera's view" (gripper rig). Camera-frame left and right are image left and right.
- **Naming rule.** Any object a question names, and any object answer, must be uniquely nameable in the scene. The name is the first of these that is unique among all scene objects (comparing category, colour and size word): `category`, `colour category`, `size category`, `size colour category`. An object with no unique name is never named and never an answer. Duplicates are allowed only as the counted objects of a count question about that category or colour. Support answers may also be `table`.
- **Names in answers.** `answer` for an object is its unique name; for a set, the sorted list of unique names (every member must be nameable, else reject); `answer_text` joins them with commas, or says `none`.
- **Scoring (scorer).** A free-text object answer is correct when the colour, size and category words it uses match exactly one object visible in the task's image (`task.scene_objects` lists the objects with at least 20 visible pixels in that camera, v0.2.2) and that object is the truth. So "the red mug" is right when the truth is "mug" and only one mug exists. Sets: every item resolves, and the resolved set equals the truth. Numbers: |answer - truth| <= tolerance.
- **Resting.** "Resting on X" means the object's lowest point is within 1 cm of X's top surface below it, and its centre lies over X's footprint. X is the table or another object.
- **Inside a container.** Using the container's `ContainerInfo` in its local frame: the object's centre is inside the interior footprint (circle radius or rect, shrunk by 5 mm) and its lowest point is between floor_z - 1 cm and rim_z above the container's lowest point.
- **Footprint.** The object mesh projected onto the table plane (convex hull of the xy of its world vertices). Centre = bounding-box centre.
- **Image point.** The projection of an object's centre into the task's camera. Image footprint = the projected mesh vertices.
- **Visibility (G3).** Evidence objects: visible fraction >= 0.4 and >= 300 px, except counted objects in count families: visible fraction >= 0.15 and >= 120 px (rule A: nothing counted is fully hidden).
- **Scale in frame (rule A).** distance and free_space are asked only when the table shows a printed grid (`table.grid_cell_m` set) in the task's camera, or a procedural reference object (`proc_*` asset) is visible there (G3 thresholds).
- **2D shortcut heuristic.** Computed from the scene file by projection, never from pixels. `shortcut_conflict` = heuristic answer != truth, recorded on every spatial task (true or false), None for the others.

## Families

| Family | Programs (params) | Answer | Spatial |
| --- | --- | --- | --- |
| count | count_all {}; count_category {category} | int | no |
| count_attribute | count_color {color}; count_color_in {color, container} | int | no |
| nearest_to_gripper | nearest_to_gripper {} | object | yes |
| distance | distance_cm {a, b} | number (cm) | no |
| front_to_back | front_to_back {a, b} | object | yes |
| relation | relation {anchor, direction: left or right} | object | yes |
| support | support {a} | object or table | no |
| containment | contents {container} | set | no |
| occlusion | occluded_by {occluder} | object | yes |
| reachability | in_zone {zone} | set | yes |
| free_space | room_between {item, a, b} | bool | yes |
| gripper_view | gripper_center {} (gripper rig only) | object | no |

### count

- **Truth.** count_all: every object in the scene (stacked and contained objects included). count_category: every object of that category. Phrasings say "in the scene" or "on the table, including stacked objects and objects inside containers".
- **G2.** Number of distinct object pass indices in the index image that match (category from the scene file by pass index), each with >= 120 px.
- **G4.** None (exact integers).
- **Twin.** Remove one counted object (TwinEdit.remove). The count drops by one.

### count_attribute

- **Truth.** count_color: objects with that colour word. count_color_in: objects with that colour inside the container.
- **G2.** count_color as count. count_color_in: back-project each matching object's visible pixels with depth and K; the object counts when the median of its points satisfies the inside rule for the container.
- **Twin.** count_color: remove one counted object. count_color_in: move one counted object out of the container onto free table space.

### nearest_to_gripper

- **Truth.** The object whose mesh surface is nearest (3D) to the gripper tip point. Phrasing: "closest to the tip of the gripper".
- **G2.** Back-project every visible pixel of each object (depth and K); the object with the nearest point to the tip wins. Must equal the truth.
- **G4.** Runner-up at least 3 cm farther (overhead and gripper rigs) or 5 cm (front rig). Pixel: the projected nearest points of winner and runner-up at least 10 px apart.
- **2D heuristic.** The object whose image footprint comes closest (in pixels) to the projected tip point.
- **Twin.** Move the winner to free table space at least 15 cm farther from the tip than the runner-up.

### distance

- **Truth.** Horizontal distance between the two objects' centres on the table plane, in cm, rounded to 1 decimal. Tolerance max(2 cm, 10% of truth). Phrasing names the centres and "in centimetres".
- **G2.** Back-project each object's visible pixels; distance between the xy medians of the two point sets, within the tolerance of the truth.
- **G4.** Both objects pass G3; truth at least 8 cm.
- **Twin.** Move b so the distance changes by more than three tolerances.

### front_to_back

- **Truth.** The object with the smaller planar camera depth of its centre; rejected unless straight-line distance to the camera agrees.
- **G2.** Median visible depth of each object; same order.
- **G4.** Depth gap at least 4 cm (overhead and gripper) or 6 cm (front). Pixel: image points at least 10 px apart.
- **2D heuristic.** The object whose image footprint reaches lower in the image (larger max y) is "closer".
- **Twin.** Move the farther object to free space in front of the nearer one (so the order flips).

### relation

- **Truth.** In the task camera's frame, candidates are objects whose centre direction from the anchor, projected on the table plane and expressed in camera-frame left/right, lies within 30 degrees of the asked direction. The answer is the candidate nearest the anchor (centre to centre, table plane).
- **G2.** Same rule using the xy medians of back-projected visible pixels for each object.
- **G4.** Runner-up candidate at least 3 cm farther; no object within 5 degrees of the cone edge closer than the answer; pixel: answer and runner-up image points at least 10 px apart.
- **2D heuristic.** Among objects whose image point is on the asked side of the anchor's image point, the nearest in pixels.
- **Twin.** Move the answer object to the opposite side of the anchor.

### support

- **Truth.** What the object rests on: the object directly beneath it (resting rule) or the table. Asked mostly for stacked and contained objects.
- **G2.** Back-project the object's visible pixels; take the pixels just below its lowest visible points in the image column (or the depth neighbours under it in 3D); the most common pass index among support pixels within 2 cm below, or the table.
- **G4.** None beyond G3; the support object must also be visible.
- **Twin.** Move the object onto the table (if stacked or contained) or onto a stackable object (if on the table).

### containment

- **Truth.** Sorted names of the objects inside the container (may be empty: answer `none`).
- **G2.** Inside rule on the median back-projected point of each visible object near the container.
- **G4.** No object whose centre is within 1 cm of the interior boundary.
- **Twin.** Move one contained object out to free table space (or, if empty, move one small object in).

### occlusion

- **Truth.** By exact ray casting of the scene file in the task camera: the object X with the largest share of its amodal silhouette covered by the occluder (first hit is the occluder). X must be at least 10% covered by the occluder and at least 30% visible overall. Rejected when X is the object the occluder rests on or sits inside (v0.2.1: a lemon in a bowl "hides" the bowl only in a degenerate sense).
- **G2.** From the rendered amodal masks and index: the share of X's amodal pixels where the index shows the occluder. Same winner.
- **G4.** Runner-up share below 3% (absolute) or at most half the winner's share.
- **2D heuristic.** The object whose image point is nearest the occluder's image point.
- **Twin.** Move X to free space away from the occluder.

### reachability

- **Truth.** Sorted names of objects whose centre (x, y) lies inside the zone (circle or rotated rect).
- **G2.** Median xy of each object's back-projected visible pixels inside the zone.
- **G4.** No object centre within 2 cm of the zone boundary; pixel: no image point within 6 px of the projected boundary.
- **2D heuristic.** Objects whose image point lies inside the projected zone outline.
- **Twin.** Move one member out of the zone (or one non-member in).
- **In frame (v0.2.2).** Asked only when the whole zone outline projects inside the image (4 px margin); otherwise "inside the zone" cannot be judged.

### free_space

- **Truth.** gap = minimum distance between the footprints of a and b on the table plane; width = the item's largest horizontal extent. Yes when gap >= width + 2 cm and no other object's footprint enters the corridor between a and b; no when gap <= width - 1 cm or another object blocks the corridor. Otherwise reject (near-tie).
- **G2.** Gap from back-projected visible pixels of a and b (minimum xy distance between point sets), same decision.
- **G4.** |gap - width| at least 1.5 cm on the decided side; pixel: gap at least 6 px different from the item's projected width.
- **2D heuristic.** Pixel gap between a and b's image footprints compared with the item's image-footprint width.
- **Twin.** Move b to make the gap clearly larger or smaller so the answer flips.

### gripper_view

- **Truth.** In the gripper camera, the object hit by the ray through the image centre (exact ray cast); a disc of 15 px radius around the centre must be covered by that object alone.
- **G2.** Index pixels in the same 15 px disc: one pass index only, equal to the truth.
- **G4.** The disc rule above.
- **Twin.** Move the object out from under the gripper to free space.

## G4 summary

| Family | Metric margin | Pixel margin |
| --- | --- | --- |
| nearest_to_gripper | 3 cm; front 5 cm | 10 px |
| front_to_back | 4 cm; front 6 cm | 10 px |
| relation | 3 cm, 5 degree cone edge | 10 px |
| distance | truth >= 8 cm | G3 only |
| reachability | 2 cm from the boundary | 6 px |
| free_space | 1.5 cm | 6 px |
| occlusion | runner-up < 3% or <= half | n/a |
| gripper_view | 15 px disc | 15 px |
