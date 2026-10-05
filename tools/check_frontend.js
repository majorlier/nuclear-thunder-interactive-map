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
console.log(`Frontend passed: ${scripts} script(s), ${displayed.size} mission variants.`);
