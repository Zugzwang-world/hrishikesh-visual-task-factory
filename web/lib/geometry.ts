// Frames. The scene file uses Blender's world: metres, right-handed, Z up. three.js is Y up, and a
// Blender glTF export maps Blender (x, y, z) to glTF (x, z, -y). We draw Blender-frame things
// inside a group rotated -90 degrees about X, which applies the same map, so the glTF (loaded
// as is) and the primitives line up. Cameras come as OpenCV world_to_camera (x right, y down,
// z forward) in Blender's world; three.js cameras look down -Z with +Y up.

import * as THREE from "three";
import type { CameraData, Mat4, Vec3 } from "./types";

/** Blender world -> three.js world: (x, y, z) -> (x, z, -y). */
export function blenderToThree(v: Vec3 | THREE.Vector3): THREE.Vector3 {
  const [x, y, z] = Array.isArray(v) ? v : [v.x, v.y, v.z];
  return new THREE.Vector3(x, z, -y);
}

export function blenderQuat(q: [number, number, number, number] | null): THREE.Quaternion {
  if (!q) return new THREE.Quaternion();
  const [w, x, y, z] = q;
  return new THREE.Quaternion(x, y, z, w);
}

function rows3(m: Mat4): { R: number[][]; t: number[] } {
  return {
    R: [m[0].slice(0, 3), m[1].slice(0, 3), m[2].slice(0, 3)],
    t: [m[0][3], m[1][3], m[2][3]],
  };
}

export interface CameraPose {
  position: THREE.Vector3; // three.js world
  quaternion: THREE.Quaternion; // three.js world
  fov: number; // vertical, degrees
  aspect: number;
  forward: THREE.Vector3; // three.js world, unit
  centerBlender: Vec3;
}

/** The three.js pose and lens of a camera from its K and world_to_camera. */
export function cameraPose(cam: CameraData): CameraPose | null {
  if (!cam.world_to_camera || !cam.K) return null;
  const { R, t } = rows3(cam.world_to_camera);
  // Centre C = -R^T t; the rows of R are the camera axes in world coordinates.
  const C: Vec3 = [0, 1, 2].map((j) => -(R[0][j] * t[0] + R[1][j] * t[1] + R[2][j] * t[2])) as Vec3;
  const xCv = new THREE.Vector3(...(R[0] as Vec3));
  const yCv = new THREE.Vector3(...(R[1] as Vec3));
  const zCv = new THREE.Vector3(...(R[2] as Vec3));
  // three camera axes in Blender world: X = x_cv, Y = -y_cv, Z = -z_cv. Then map to three world.
  const X = blenderToThree(xCv);
  const Y = blenderToThree(yCv.clone().negate());
  const Z = blenderToThree(zCv.clone().negate());
  const basis = new THREE.Matrix4().makeBasis(X, Y, Z);
  const quaternion = new THREE.Quaternion().setFromRotationMatrix(basis);
  const [w, h] = cam.resolution ?? [1024, 768];
  const fy = cam.K[1][1];
  const fov = (2 * Math.atan(h / 2 / fy) * 180) / Math.PI;
  return {
    position: blenderToThree(C),
    quaternion,
    fov,
    aspect: w / h,
    forward: Z.clone().negate().normalize(),
    centerBlender: C,
  };
}

/** Project a Blender-world point into the camera image (pixels of `resolution`), or null. */
export function projectPoint(cam: CameraData, p: Vec3): [number, number] | null {
  if (!cam.world_to_camera || !cam.K) return null;
  const m = cam.world_to_camera;
  const x = m[0][0] * p[0] + m[0][1] * p[1] + m[0][2] * p[2] + m[0][3];
  const y = m[1][0] * p[0] + m[1][1] * p[1] + m[1][2] * p[2] + m[1][3];
  const z = m[2][0] * p[0] + m[2][1] * p[1] + m[2][2] * p[2] + m[2][3];
  if (z <= 1e-6) return null;
  const K = cam.K;
  return [K[0][0] * (x / z) + K[0][2], K[1][1] * (y / z) + K[1][2]];
}

/** Camera frustum corners in Blender world at depth d (for drawing the rig). */
export function frustumCorners(cam: CameraData, d: number): Vec3[] | null {
  if (!cam.world_to_camera || !cam.K) return null;
  const { R, t } = rows3(cam.world_to_camera);
  const [w, h] = cam.resolution ?? [1024, 768];
  const K = cam.K;
  const pix: [number, number][] = [
    [0, 0],
    [w, 0],
    [w, h],
    [0, h],
  ];
  return pix.map(([u, v]) => {
    const xc = ((u - K[0][2]) / K[0][0]) * d;
    const yc = ((v - K[1][2]) / K[1][1]) * d;
    const pc = [xc - t[0], yc - t[1], d - t[2]];
    // world = R^T (pc)
    return [0, 1, 2].map((j) => R[0][j] * pc[0] + R[1][j] * pc[1] + R[2][j] * pc[2]) as Vec3;
  });
}
