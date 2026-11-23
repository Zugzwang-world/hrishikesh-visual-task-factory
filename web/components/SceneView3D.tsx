"use client";

import { Line, OrbitControls, useGLTF } from "@react-three/drei";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { Component, Suspense, useEffect, useMemo, useRef, type ReactNode } from "react";
import * as THREE from "three";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";
import { blenderQuat, blenderToThree, cameraPose, frustumCorners } from "@/lib/geometry";
import type { CameraData, ObjectData, SceneData, Vec3 } from "@/lib/types";

export type ViewMode = "camera" | "orbit";
export type LabelMode = "evidence" | "all" | "none";

const ACCENT = "#e8590c";
const PICK = "#d9342b";
const BLENDER_FRAME: [number, number, number] = [-Math.PI / 2, 0, 0];

interface Props {
  scene: SceneData;
  dataBase: string;
  camera: CameraData | null;
  evidence: Set<string>;
  picks: Set<string>; // objects a model wrongly answered
  mode: ViewMode;
  snapKey: number;
  labels: LabelMode;
  useGltf: boolean;
  onOrbit: () => void;
  onGltfError: () => void;
}

/** Linear-light rgb from the scene file; three's working colour space is linear sRGB. */
function linearColor(rgb: Vec3 | null): THREE.Color {
  const c = new THREE.Color();
  if (rgb) c.setRGB(rgb[0], rgb[1], rgb[2], THREE.LinearSRGBColorSpace);
  else c.set("#9a9a9a");
  return c;
}

function Solid({ shape, dims, color, opacity = 1, cast = true }: {
  shape: string | null; dims: Vec3; color: THREE.Color | string; opacity?: number; cast?: boolean;
}) {
  const [dx, dy, dz] = dims;
  const mat = (
    <meshStandardMaterial
      color={color}
      roughness={0.55}
      metalness={0.05}
      transparent={opacity < 1}
      opacity={opacity}
    />
  );
  if (shape === "sphere")
    return (
      <mesh scale={[dx, dy, dz]} castShadow={cast} receiveShadow>
        <sphereGeometry args={[0.5, 40, 20]} />
        {mat}
      </mesh>
    );
  if (shape === "cylinder")
    return (
      <mesh rotation={[Math.PI / 2, 0, 0]} scale={[dx, dz, dy]} castShadow={cast} receiveShadow>
        <cylinderGeometry args={[0.5, 0.5, 1, 48]} />
        {mat}
      </mesh>
    );
  if (shape === "cone")
    return (
      <mesh rotation={[Math.PI / 2, 0, 0]} scale={[dx, dz, dy]} castShadow={cast} receiveShadow>
        <coneGeometry args={[0.5, 1, 48]} />
        {mat}
      </mesh>
    );
  return (
    <mesh scale={[dx, dy, dz]} castShadow={cast} receiveShadow>
      <boxGeometry />
      {mat}
    </mesh>
  );
}

const unitEdges = new THREE.EdgesGeometry(new THREE.BoxGeometry(1, 1, 1));

function HighlightBox({ dims, color }: { dims: Vec3; color: string }) {
  const pad = 0.012;
  return (
    <lineSegments
      geometry={unitEdges}
      scale={[dims[0] + pad, dims[1] + pad, dims[2] + pad]}
      renderOrder={2}
    >
      <lineBasicMaterial color={color} depthTest={false} transparent opacity={0.95} />
    </lineSegments>
  );
}

function SceneObject({ obj, evidence, pick, drawSolid }: {
  obj: ObjectData; evidence: boolean; pick: boolean; drawSolid: boolean;
}) {
  if (!obj.position || !obj.dimensions) return null;
  const q = blenderQuat(obj.rotation);
  const color = linearColor(obj.rgb);
  const primitive = obj.source !== "asset";
  return (
    <group position={obj.position} quaternion={q}>
      {drawSolid && (
        <Solid
          shape={primitive ? obj.shape : "box"}
          dims={obj.dimensions}
          color={color}
          opacity={primitive ? 1 : 0.6}
        />
      )}
      {(evidence || pick) && <HighlightBox dims={obj.dimensions} color={pick ? PICK : ACCENT} />}
    </group>
  );
}

interface LabelItem {
  id: string;
  name: string;
  pos: THREE.Vector3; // three.js world, above the object
}

/** Moves the DOM labels (outside the Canvas) to their projected positions every frame. Plain DOM
 * writes, no nested React roots, so labels never unmount inside the three.js reconciler. */
function LabelProjector({ items, refs }: {
  items: LabelItem[]; refs: React.RefObject<Map<string, HTMLSpanElement>>;
}) {
  const v = useMemo(() => new THREE.Vector3(), []);
  useFrame(({ camera, size }) => {
    for (const it of items) {
      const el = refs.current.get(it.id);
      if (!el) continue;
      v.copy(it.pos).project(camera);
      if (v.z > 1 || v.z < -1) {
        el.style.visibility = "hidden";
        continue;
      }
      el.style.visibility = "visible";
      const x = ((v.x + 1) / 2) * size.width;
      const y = ((1 - v.y) / 2) * size.height;
      el.style.transform = `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px) translate(-50%, -100%)`;
    }
  });
  return null;
}

function Table({ scene }: { scene: SceneData }) {
  const t = scene.table;
  if (!t) return null;
  const [sx, sy] = t.size_xy;
  const legH = t.height - t.thickness;
  const legs: [number, number][] = [
    [sx / 2 - 0.04, sy / 2 - 0.04],
    [-sx / 2 + 0.04, sy / 2 - 0.04],
    [sx / 2 - 0.04, -sy / 2 + 0.04],
    [-sx / 2 + 0.04, -sy / 2 + 0.04],
  ];
  return (
    <group>
      <mesh position={[0, 0, t.height - t.thickness / 2]} scale={[sx, sy, t.thickness]} receiveShadow>
        <boxGeometry />
        <meshStandardMaterial color="#c9b291" roughness={0.8} />
      </mesh>
      {legs.map(([x, y], i) => (
        <mesh key={i} position={[x, y, legH / 2]} scale={[0.04, 0.04, legH]}>
          <boxGeometry />
          <meshStandardMaterial color="#a38b6a" roughness={0.8} />
        </mesh>
      ))}
    </group>
  );
}

function Gripper({ scene, drawSolid }: { scene: SceneData; drawSolid: boolean }) {
  const g = scene.gripper;
  return (
    <group>
      {drawSolid &&
        g.parts.map((p) => (
          <group key={p.name} position={p.pose.position} quaternion={blenderQuat(p.pose.rotation)}>
            <Solid shape={p.shape} dims={p.dimensions} color="#8d939b" />
          </group>
        ))}
      {g.tip && (
        <mesh position={g.tip.position}>
          <sphereGeometry args={[0.008, 16, 8]} />
          <meshBasicMaterial color={ACCENT} />
        </mesh>
      )}
    </group>
  );
}

function Zones({ scene }: { scene: SceneData }) {
  const h = (scene.table?.height ?? 0.75) + 0.002;
  return (
    <>
      {scene.zones.map((z) => {
        const color = z.rgb ? linearColor(z.rgb) : new THREE.Color("#2f7dd1");
        let pts: Vec3[] = [];
        if (z.shape === "circle" && z.radius) {
          pts = Array.from({ length: 65 }, (_, i) => {
            const a = (i / 64) * Math.PI * 2;
            return [z.center_xy[0] + z.radius! * Math.cos(a), z.center_xy[1] + z.radius! * Math.sin(a), h];
          });
        } else if (z.size_xy) {
          const [w, d] = z.size_xy;
          const yaw = (z.yaw_deg * Math.PI) / 180;
          const corners: [number, number][] = [[-w / 2, -d / 2], [w / 2, -d / 2], [w / 2, d / 2], [-w / 2, d / 2], [-w / 2, -d / 2]];
          pts = corners.map(([x, y]) => [
            z.center_xy[0] + x * Math.cos(yaw) - y * Math.sin(yaw),
            z.center_xy[1] + x * Math.sin(yaw) + y * Math.cos(yaw),
            h,
          ]);
        }
        return pts.length ? <Line key={z.id} points={pts} color={color} lineWidth={2.5} /> : null;
      })}
    </>
  );
}

function Frusta({ scene, active, hideActive }: {
  scene: SceneData; active: string | null; hideActive: boolean;
}) {
  return (
    <>
      {scene.cameras.map((cam) => {
        if (hideActive && cam.camera_id === active) return null;
        const pose = cameraPose(cam);
        const corners = frustumCorners(cam, 0.12);
        if (!pose || !corners) return null;
        const c = pose.centerBlender;
        const pts: Vec3[] = [
          c, corners[0], corners[1], c, corners[1], corners[2], c, corners[2], corners[3], c,
          corners[3], corners[0],
        ];
        const on = cam.camera_id === active;
        return (
          <Line
            key={cam.camera_id}
            points={pts}
            color={on ? ACCENT : "#5d6470"}
            lineWidth={on ? 2 : 1.2}
            transparent
            opacity={on ? 1 : 0.7}
          />
        );
      })}
    </>
  );
}

function GltfModel({ url }: { url: string }) {
  const { scene } = useGLTF(url);
  useEffect(() => {
    scene.traverse((o) => {
      const m = o as THREE.Mesh;
      if (m.isMesh) {
        m.castShadow = true;
        m.receiveShadow = true;
      }
    });
  }, [scene]);
  return <primitive object={scene} />;
}

class GltfBoundary extends Component<{ onError: () => void; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch() {
    this.props.onError();
  }
  render() {
    return this.state.failed ? null : this.props.children;
  }
}

/** Puts the default camera exactly at the scene camera when snapKey changes (mode camera). */
function CameraRig({ camera, scene, mode, snapKey }: {
  camera: CameraData | null; scene: SceneData; mode: ViewMode; snapKey: number;
}) {
  const cam = useThree((s) => s.camera) as THREE.PerspectiveCamera;
  const controls = useThree((s) => s.controls) as unknown as OrbitControlsImpl | null;
  useEffect(() => {
    if (mode !== "camera") return;
    const pose = camera ? cameraPose(camera) : null;
    const tableY = scene.table?.height ?? 0.75;
    if (pose) {
      cam.position.copy(pose.position);
      cam.quaternion.copy(pose.quaternion);
      cam.fov = pose.fov;
      const f = pose.forward;
      let t = f.y < -1e-3 ? (tableY - pose.position.y) / f.y : 1;
      t = Math.min(Math.max(t, 0.2), 5);
      controls?.target.copy(pose.position.clone().addScaledVector(f, t));
    } else {
      cam.position.set(0.9, tableY + 0.85, 1.25);
      cam.fov = 40;
      cam.lookAt(0, tableY, 0);
      controls?.target.set(0, tableY, 0);
    }
    cam.near = 0.01;
    cam.far = 50;
    cam.updateProjectionMatrix();
  }, [cam, controls, camera, scene, mode, snapKey]);
  return null;
}

export default function SceneView3D(props: Props) {
  const { scene, dataBase, camera, evidence, picks, mode, snapKey, labels, useGltf, onOrbit, onGltfError } = props;
  const controlsRef = useRef<OrbitControlsImpl | null>(null);
  const labelRefs = useRef(new Map<string, HTMLSpanElement>());
  const gltfUrl = scene.gltf && useGltf ? `${dataBase}/${scene.scene_id}/${scene.gltf}` : null;
  const drawSolid = !gltfUrl;

  const startOrbit = () => {
    if (mode === "camera") {
      if (controlsRef.current) controlsRef.current.enabled = true;
      onOrbit();
    }
  };

  const items = useMemo<LabelItem[]>(
    () =>
      scene.objects
        .filter((o) => o.position && o.dimensions)
        .map((o) => {
          const [x, y, z] = o.position!;
          const half = Math.max(...o.dimensions!) / 2;
          return { id: o.id, name: o.name, pos: blenderToThree([x, y, z + half + 0.015]) };
        }),
    [scene],
  );
  // Evidence labels only when there are few (a count over ten objects would bury the view).
  const visible = (id: string) =>
    labels === "all" || (labels === "evidence" && (picks.has(id) || (evidence.has(id) && evidence.size <= 4)));

  return (
    <div className="canvas-wrap" onPointerDownCapture={startOrbit} onWheelCapture={startOrbit}>
      <Canvas shadows dpr={[1, 2]} gl={{ antialias: true, alpha: true }} camera={{ fov: 40, near: 0.01, far: 50, position: [0.9, 1.6, 1.25] }}>
        <hemisphereLight args={["#ffffff", "#8a8478", 0.9]} />
        <directionalLight
          position={[-1.2, 3.0, 1.6]}
          intensity={2.2}
          castShadow
          shadow-mapSize={[2048, 2048]}
          shadow-camera-left={-1}
          shadow-camera-right={1}
          shadow-camera-top={1}
          shadow-camera-bottom={-1}
          shadow-bias={-0.0005}
        />
        <gridHelper args={[4, 40, "#b9b5ab", "#d6d2c8"]} />
        {gltfUrl && (
          <GltfBoundary onError={onGltfError}>
            <Suspense fallback={null}>
              <GltfModel url={gltfUrl} />
            </Suspense>
          </GltfBoundary>
        )}
        <group rotation={BLENDER_FRAME}>
          {drawSolid && <Table scene={scene} />}
          <Gripper scene={scene} drawSolid={drawSolid} />
          <Zones scene={scene} />
          {scene.objects.map((o) => (
            <SceneObject key={o.id} obj={o} evidence={evidence.has(o.id)} pick={picks.has(o.id)} drawSolid={drawSolid} />
          ))}
          <Frusta scene={scene} active={camera?.camera_id ?? null} hideActive={mode === "camera"} />
        </group>
        <OrbitControls ref={controlsRef} makeDefault enabled={mode === "orbit"} enableDamping dampingFactor={0.12} />
        <CameraRig camera={camera} scene={scene} mode={mode} snapKey={snapKey} />
        <LabelProjector items={items} refs={labelRefs} />
      </Canvas>
      <div className="label-layer" aria-hidden>
        {items.map((it) => (
          <span
            key={it.id}
            ref={(el) => {
              if (el) labelRefs.current.set(it.id, el);
              else labelRefs.current.delete(it.id);
            }}
            className={`label3d${picks.has(it.id) ? " label3d-pick" : evidence.has(it.id) ? " label3d-ev" : ""}`}
            style={{ display: visible(it.id) ? undefined : "none" }}
          >
            {picks.has(it.id) && !evidence.has(it.id) ? `model: ${it.name}` : it.name}
          </span>
        ))}
      </div>
    </div>
  );
}
