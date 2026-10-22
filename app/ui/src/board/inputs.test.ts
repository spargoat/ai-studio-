import assert from "node:assert/strict";
import { CREATE_INTEL_TYPES, INSPECTOR_FIELDS } from "./inputs.ts";

for (const type of CREATE_INTEL_TYPES) {
  assert.ok(type in INSPECTOR_FIELDS, `${type} is missing from the inspector`);
}

for (const type of ["brief", "script", "voice", "still", "video", "landing", "funnel_route", "funnel_step"]) {
  const fields = INSPECTOR_FIELDS[type];
  assert.ok(fields.length > 0, `${type} has no editable inputs`);
  for (const field of fields) {
    assert.ok(field.key && field.label && field.aria, `${type} field is incomplete`);
    assert.ok(field.control === "text" || field.control === "textarea" || field.control === "select");
  }
}

assert.equal(INSPECTOR_FIELDS.script.find((field) => field.key === "script")?.control, "textarea");
assert.equal(INSPECTOR_FIELDS.brief.find((field) => field.key === "offer")?.control, "text");
assert.equal(INSPECTOR_FIELDS.voice[0].optionsFrom, "voices");
assert.equal(INSPECTOR_FIELDS.funnel_route[0].key, "funnel_id");
assert.deepEqual(INSPECTOR_FIELDS.ad_breakdown, []);
assert.deepEqual(INSPECTOR_FIELDS.teardown, []);

const page = await import("node:fs").then((fs) => fs.readFileSync(new URL("../pages/BoardPage.tsx", import.meta.url), "utf8"));
assert.equal(page.includes("takes its stage settings from the template"), false);
assert.equal(page.includes("INSPECTOR_FIELDS"), true);
assert.equal(page.includes("<textarea"), true);
