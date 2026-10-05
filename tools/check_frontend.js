"use strict";

// Compile without running Leaflet/DOM code, and verify the browser's mission
// definitions against the generated variant manifest.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const root = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(root, "index.html"), "utf8");
let definitions;
let scripts = 0;
for (const match of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)) {
    if (/\bsrc\s*=/.test(match[1])) continue;
    const source = match[2];
    new vm.Script(source, { filename: "index.html" });
    scripts++;
    const prefix = source.match(/^[\s\S]*?const MAP_DEFINITIONS = \{[\s\S]*?^    \};/m);
    if (prefix) definitions = vm.runInNewContext(prefix[0] + "\nMAP_DEFINITIONS");
}
assert(scripts > 0, "No inline map script found");
assert(definitions, "Map definitions not found");
const variants = JSON.parse(fs.readFileSync(path.join(root, "presets.json"), "utf8")).variants;
const declared = new Set(variants.map(variant => variant.id));
const displayed = new Set();
for (const definition of Object.values(definitions)) {
    assert(definition.variants.includes(definition.defaultVariant));
    if (definition.supportsTeamSwap) {
        assert(definition.mirroredVariant !== definition.defaultVariant);
        assert(definition.variants.includes(definition.mirroredVariant));
    }
    for (const id of definition.variants) {
        assert(declared.has(id), `Undeclared browser variant: ${id}`);
        displayed.add(id);
        for (const base of ["map_data", "mission_logic"]) {
            const filename = id === "standard" ? `${base}.json` : `${base}_${id}.json`;
            assert(html.includes(`fetch("${filename}")`), `Missing browser fetch: ${filename}`);
            assert(fs.existsSync(path.join(root, filename)), `Missing variant file: ${filename}`);
        }
    }
}
assert.deepEqual([...displayed].sort(), [...declared].sort());

// Keep continuous height sampling when regenerating either terrain raster.
const terrainFunction = html.match(/    function terrainHeightAtMapPoint\([^]*?^    \}/m);
assert(terrainFunction, "Bilinear terrain sampler missing");
const sampleTerrain = vm.runInNewContext(
    "const IMG_SIZE = 2; const terrainGrid = {width: 2, height: 2, heights: [0, 10, 20, 30]};\n" +
    terrainFunction[0] + "\nterrainHeightAtMapPoint"
);
assert.equal(sampleTerrain(1, 1), 15, "Terrain centre should blend all four heights");
assert.equal(sampleTerrain(0.5, 1.5), 0, "Terrain rows must retain north/south orientation");
assert.equal(sampleTerrain(-1, 1), null, "Terrain outside the map should be unavailable");
for (const definition of Object.values(definitions)) {
    if (!definition.supportsTerrain) continue;
    const meta = JSON.parse(fs.readFileSync(path.join(root, definition.terrainMeta), "utf8"));
    assert.equal(meta.height_encoding.format, "rg16");
    assert.equal(meta.height_encoding.step_m, 0.25);
    assert.equal(meta.height_encoding.min_m, definition.terrainEncoding.min_m,
        `${definition.id}: initial terrain encoding differs from metadata`);
    assert.equal(meta.height_encoding.image, path.basename(definition.terrainImage));
    assert.equal(meta.world_bounds_m[0], definition.mapCoord0[0]);
    assert.equal(meta.world_bounds_m[1], definition.mapCoord1[0]);
    if (definition.supportsRoads) {
        const roads = JSON.parse(fs.readFileSync(path.join(root, definition.roadFile), "utf8"));
        assert(roads.length > 0, `${definition.id}: empty road network`);
        for (const road of roads) {
            assert(road.points.length >= 2, `${definition.id}: road needs at least two points`);
            assert(road.points.every(point => point.length === 3 && point.every(Number.isFinite)),
                `${definition.id}: invalid road coordinates`);
        }
    }
    if (definition.supportsNavmesh) {
        const mesh = JSON.parse(fs.readFileSync(path.join(root, definition.navmeshFile), "utf8"));
        assert(mesh.vertices.length > 0 && mesh.triangles.length > 0);
        for (const triangle of mesh.triangles) {
            assert(triangle.length === 3 && new Set(triangle).size === 3);
            assert(triangle.every(index => Number.isInteger(index) && index >= 0 && index < mesh.vertices.length));
        }
        if (meta.source_sha256 && mesh.source_sha256) {
            assert.equal(mesh.source_sha256, meta.source_sha256,
                `${definition.id}: heightmap and navigation mesh came from different level files`);
        }
    }
}
console.log(`Frontend passed: ${scripts} script(s), ${displayed.size} mission variants.`);
