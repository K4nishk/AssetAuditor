import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

import { checkCallSites, checkSchemaDeclarations, runGate } from "./check-analytics-schema.mjs";

function withTempFile(contents, ext = ".ts") {
  const dir = mkdtempSync(path.join(os.tmpdir(), "analytics-gate-"));
  const file = path.join(dir, `fixture${ext}`);
  writeFileSync(file, contents, "utf8");
  return { dir, file };
}

test("real ANALYTICS_EVENT_SCHEMAS declaration has no forbidden fields", () => {
  const schemaFile = path.resolve(import.meta.dirname, "../src/lib/analyticsEvents.ts");
  assert.deepEqual(checkSchemaDeclarations(schemaFile), []);
});

test("real src/ has no track() call site with a forbidden field", () => {
  const srcDir = path.resolve(import.meta.dirname, "../src");
  const schemaFile = path.join(srcDir, "lib", "analyticsEvents.ts");
  assert.deepEqual(runGate({ srcDir, schemaFile }), []);
});

test("schema declaration flags a forbidden field name in the array", () => {
  const { dir, file } = withTempFile(
    `export const ANALYTICS_EVENT_SCHEMAS = {\n  statement_uploaded: ["institution", "amount"],\n} as const;\n`,
  );
  try {
    const violations = checkSchemaDeclarations(file);
    assert.equal(violations.length, 1);
    assert.match(violations[0].message, /amount/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("schema declaration flags a forbidden event name", () => {
  const { dir, file } = withTempFile(
    `export const ANALYTICS_EVENT_SCHEMAS = {\n  account_deleted: ["reason"],\n} as const;\n`,
  );
  try {
    const violations = checkSchemaDeclarations(file);
    assert.equal(violations.length, 1);
    assert.match(violations[0].message, /account_deleted/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("schema declaration passes a clean event list", () => {
  const { dir, file } = withTempFile(
    `export const ANALYTICS_EVENT_SCHEMAS = {\n  statement_uploaded: ["institution", "file_type"],\n} as const;\n`,
  );
  try {
    assert.deepEqual(checkSchemaDeclarations(file), []);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("call-site check flags a forbidden inline payload field", () => {
  const { dir, file } = withTempFile(
    `import { track } from "../lib/analytics";\nfunction onClick() {\n  track("statement_uploaded", { institution: "td", ticker: "XEQT" });\n}\n`,
    ".tsx",
  );
  try {
    const violations = checkCallSites(file);
    assert.equal(violations.length, 1);
    assert.match(violations[0].message, /ticker/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("schema declaration flags a computed key as unverifiable", () => {
  const { dir, file } = withTempFile(
    `const key = "statement_uploaded";\nexport const ANALYTICS_EVENT_SCHEMAS = {\n  [key]: ["institution"],\n} as const;\n`,
  );
  try {
    const violations = checkSchemaDeclarations(file);
    assert.equal(violations.length, 1);
    assert.match(violations[0].message, /unrecognized schema entry/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("call-site check flags a computed payload key as unverifiable", () => {
  const { dir, file } = withTempFile(
    `import { track } from "../lib/analytics";\nfunction onClick(key) {\n  track("dashboard_drilldown", { chart: "term_bucket", [key]: 1 });\n}\n`,
    ".tsx",
  );
  try {
    const violations = checkCallSites(file);
    assert.equal(violations.length, 1);
    assert.match(violations[0].message, /spread\/computed/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("call-site check flags a spread payload as unverifiable", () => {
  const { dir, file } = withTempFile(
    `import { track } from "../lib/analytics";\nfunction onClick(extra) {\n  track("dashboard_drilldown", { chart: "term_bucket", ...extra });\n}\n`,
    ".tsx",
  );
  try {
    const violations = checkCallSites(file);
    assert.equal(violations.length, 1);
    assert.match(violations[0].message, /spread\/computed/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("call-site check flags a non-literal payload argument", () => {
  const { dir, file } = withTempFile(
    `import { track } from "../lib/analytics";\nfunction onClick(payload) {\n  track("dashboard_drilldown", payload);\n}\n`,
    ".tsx",
  );
  try {
    const violations = checkCallSites(file);
    assert.equal(violations.length, 1);
    assert.match(violations[0].message, /not an inline object literal/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("call-site check passes a clean payload", () => {
  const { dir, file } = withTempFile(
    `import { track } from "../lib/analytics";\nfunction onClick() {\n  track("parse_confirmed", { row_count: 3, corrections: 1 });\n}\n`,
    ".tsx",
  );
  try {
    assert.deepEqual(checkCallSites(file), []);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("runGate walks nested directories and skips node_modules/dist", () => {
  const rootDir = mkdtempSync(path.join(os.tmpdir(), "analytics-gate-tree-"));
  try {
    const schemaFile = path.join(rootDir, "lib", "analyticsEvents.ts");
    mkdirSync(path.join(rootDir, "lib"), { recursive: true });
    writeFileSync(
      schemaFile,
      `export const ANALYTICS_EVENT_SCHEMAS = {\n  dashboard_drilldown: ["chart"],\n} as const;\n`,
      "utf8",
    );

    const nestedDir = path.join(rootDir, "routes", "nested");
    mkdirSync(nestedDir, { recursive: true });
    writeFileSync(
      path.join(nestedDir, "Widget.tsx"),
      `import { track } from "../../lib/analytics";\ntrack("dashboard_drilldown", { chart: "net_worth", accountBalance: 1 });\n`,
      "utf8",
    );

    const ignoredDir = path.join(rootDir, "node_modules", "pkg");
    mkdirSync(ignoredDir, { recursive: true });
    writeFileSync(
      path.join(ignoredDir, "index.ts"),
      `track("dashboard_drilldown", { chart: "net_worth", amount: 1 });\n`,
      "utf8",
    );

    const violations = runGate({ srcDir: rootDir, schemaFile });
    assert.equal(violations.length, 1);
    assert.match(violations[0].file, /Widget\.tsx$/);
    assert.match(violations[0].message, /accountBalance/);
  } finally {
    rmSync(rootDir, { recursive: true, force: true });
  }
});
