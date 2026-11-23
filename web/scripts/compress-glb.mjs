#!/usr/bin/env node
// Lean display glTF for the Task Explorer: meshopt geometry compression (EXT_meshopt_compression,
// decoded in the browser by three-stdlib's bundled MeshoptDecoder, no CDN) and WebP textures
// (EXT_texture_webp) at most 1024 px. No Draco: its decoder would come from a CDN.
//
//   node web/scripts/compress-glb.mjs <in.glb> <out.glb> [<in.glb> <out.glb> ...]
//
// The exporter (vtf.package.explorer) calls this once per export with every scene's pair. Each
// pair prints one JSON line: {"in", "out", "ok", "bytes_in", "bytes_out", "error"}. A pair that
// fails writes nothing, and the exporter copies the plain glb instead. Exit code 0 unless the
// arguments are wrong or the libraries are missing.

import { stat } from "node:fs/promises";

const args = process.argv.slice(2);
if (args.length === 0 || args.length % 2 !== 0) {
  console.error("usage: compress-glb.mjs <in.glb> <out.glb> [<in.glb> <out.glb> ...]");
  process.exit(2);
}

let core, functions, extensions, meshoptimizer, sharp;
try {
  core = await import("@gltf-transform/core");
  functions = await import("@gltf-transform/functions");
  extensions = await import("@gltf-transform/extensions");
  meshoptimizer = await import("meshoptimizer");
  sharp = (await import("sharp")).default;
} catch (err) {
  console.error(`compress-glb: libraries missing (run npm install in web/): ${err.message}`);
  process.exit(3);
}

const { Logger, NodeIO } = core;
const { dedup, prune, meshopt, textureCompress } = functions;
const { MeshoptEncoder, MeshoptDecoder } = meshoptimizer;

await MeshoptEncoder.ready;
await MeshoptDecoder.ready;

const io = new NodeIO()
  .registerExtensions(extensions.ALL_EXTENSIONS)
  .registerDependencies({ "meshopt.encoder": MeshoptEncoder, "meshopt.decoder": MeshoptDecoder });

for (let i = 0; i < args.length; i += 2) {
  const [src, dst] = [args[i], args[i + 1]];
  const row = { in: src, out: dst, ok: false, bytes_in: null, bytes_out: null, error: null };
  try {
    row.bytes_in = (await stat(src)).size;
    const doc = await io.read(src);
    doc.setLogger(new Logger(Logger.Verbosity.WARN));
    await doc.transform(
      dedup(),
      prune(),
      textureCompress({ encoder: sharp, targetFormat: "webp", quality: 80, resize: [1024, 1024] }),
      meshopt({ encoder: MeshoptEncoder, level: "medium" }),
    );
    await io.write(dst, doc);
    row.bytes_out = (await stat(dst)).size;
    row.ok = true;
  } catch (err) {
    row.error = String(err && err.message ? err.message : err).slice(0, 400);
  }
  console.log(JSON.stringify(row));
}
