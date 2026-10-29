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
// Language packs must expose identical keys with identical placeholders, and
// every literal key used by the page or its markup must exist in English.
const i18nDirectory = path.join(root, "i18n");
const packFiles = fs.readdirSync(i18nDirectory).filter(name => name.endsWith(".json"));
assert(packFiles.length > 0, "No language packs found");
const locales = packFiles.map(name => path.basename(name, ".json"));
const dictionaries = new Map(locales.map(locale => [
    locale,
    JSON.parse(fs.readFileSync(path.join(i18nDirectory, `${locale}.json`), "utf8"))
]));
const english = dictionaries.get("en");
assert(english, "English is required as the fallback pack");
const placeholders = text => [...String(text).matchAll(/\{(\w+)\}/g)]
    .map(match => match[1])
    .sort();
for (const locale of locales) {
    const pack = dictionaries.get(locale);
    assert.deepEqual(
        Object.keys(pack).sort(),
        Object.keys(english).sort(),
        `${locale}: keys differ from en`
    );
    for (const [key, value] of Object.entries(pack)) {
        assert(typeof value === "string" && value.trim().length > 0,
            `${locale}: empty value for ${key}`);
        assert.deepEqual(placeholders(value), placeholders(english[key]),
            `${locale}: placeholders differ for ${key}`);
    }
}
const usedKeys = new Set([
    ...[...html.matchAll(/\bt\(\s*"([\w.-]+)"/g)].map(match => match[1]),
    ...[...html.matchAll(/data-i18n(?:-aria|-title)?="([\w.-]+)"/g)].map(match => match[1])
]);
const unknownKeys = [...usedKeys].filter(key => !(key in english));
assert.equal(unknownKeys.length, 0, `Unknown i18n keys: ${unknownKeys.join(", ")}`);
// Localized name tables are generated data: a missing file, an empty table or
// a leftover zero-width space would silently degrade every card.
for (const name of ["unit_display_names.json", "unit_weapon_display_names.json"]) {
    if (!html.includes(`fetch("${name}")`)) continue;
    const file = path.join(root, name);
    assert(fs.existsSync(file), `Missing localized data file: ${name}`);
    const table = JSON.parse(fs.readFileSync(file, "utf8"));
    assert(table.en && typeof table.en === "object", `${name}: English table missing`);
    for (const [locale, entries] of Object.entries(table)) {
        assert(entries && typeof entries === "object",
            `${name}: ${locale} is not a table`);
        for (const [key, label] of Object.entries(entries)) {
            assert(typeof label === "string" && label.trim().length > 0,
                `${name}: empty ${locale} label for ${key}`);
            assert(!/[\u200b\u200c\u200d\ufeff]/.test(label),
                `${name}: ${locale}/${key} keeps an invisible separator`);
            if (key.endsWith("#short")) {
                const base = key.slice(0, -"#short".length);
                assert(entries[base], `${name}: ${locale}/${key} has no full name`);
            }
        }
    }
}

// Both tables are exported from the same localization pass, so they must cover
// the same locales; a mismatch means one extractor lost a language column.
const unitNames = JSON.parse(fs.readFileSync(path.join(root, "unit_display_names.json"), "utf8"));
const weaponNames = JSON.parse(fs.readFileSync(path.join(root, "unit_weapon_display_names.json"), "utf8"));
assert.deepEqual(
    Object.keys(weaponNames).sort(),
    Object.keys(unitNames).sort(),
    "Localized name tables expose different locales"
);
// The page only requests slot/portrait art listed in images/manifest.json, so
// a stale list would hide new artwork. Regenerate with
// tools/build_image_manifest.py.
const imageManifest = JSON.parse(fs.readFileSync(path.join(root, "images", "manifest.json"), "utf8"));
for (const [key, folder] of [["slots", "images/slots"], ["portraits", "images/portraits"]]) {
    const onDisk = fs.readdirSync(path.join(root, folder)).filter(name => name.endsWith(".png")).sort();
    assert.deepEqual(
        imageManifest[key],
        onDisk,
        `images/manifest.json is out of date for ${folder}: run python tools/build_image_manifest.py`
    );
}

console.log(`Frontend passed: ${scripts} script(s), ${displayed.size} mission variants, ` +
    `${locales.length} language pack(s) with ${Object.keys(english).length} keys.`);
