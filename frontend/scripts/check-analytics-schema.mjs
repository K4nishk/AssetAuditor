#!/usr/bin/env node
// Amplitude event schema review gate (mvp.md AA-28): fails CI if any
// declared event schema in src/lib/analyticsEvents.ts, or any inline
// analytics.track(...) call-site payload anywhere under src/, contains a
// field name that looks like an amount, ticker, or account identifier.
// Behaviour events only — CLAUDE.md rule #2, Assumption A13.
//
// Static AST analysis (via the `typescript` compiler API already a
// devDependency here) rather than importing/executing the source files —
// this runs standalone with plain Node, no build step required. Anything it
// can't statically prove safe (a spread, a computed key, a non-literal
// payload) is treated as a violation rather than silently passing, since a
// review gate that can be bypassed by making the payload dynamic isn't one.
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import ts from "typescript";

const FRONTEND_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SRC_DIR = path.join(FRONTEND_ROOT, "src");
const SCHEMA_FILE = path.join(SRC_DIR, "lib", "analyticsEvents.ts");

// Keep in sync with frontend/src/lib/analytics.ts's FORBIDDEN_FIELD_PATTERNS
// — duplicated deliberately so this script has no dependency on the app's
// own module graph/build config.
export const FORBIDDEN_FIELD_PATTERNS = [
  /amount/i,
  /ticker/i,
  /account/i,
  /balance/i,
  /price/i,
  /quantity/i,
  /symbol/i,
  /cusip/i,
  /isin/i,
];

function stripQuotes(text) {
  return text.replace(/^["']|["']$/g, "");
}

function parse(filePath) {
  const text = readFileSync(filePath, "utf8");
  const scriptKind = filePath.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  return ts.createSourceFile(filePath, text, ts.ScriptTarget.Latest, true, scriptKind);
}

function lineOf(source, node) {
  return source.getLineAndCharacterOfPosition(node.getStart(source)).line + 1;
}

function checkName(name, node, filePath, source, violations) {
  if (FORBIDDEN_FIELD_PATTERNS.some((pattern) => pattern.test(name))) {
    violations.push({
      file: filePath,
      line: lineOf(source, node),
      message: `"${name}" looks like a financial/account field — behaviour events only (CLAUDE.md rule #2)`,
    });
  }
}

// Walks src/lib/analyticsEvents.ts's ANALYTICS_EVENT_SCHEMAS declaration:
// every event name and every declared field name must pass the forbidden-
// pattern check, and the shape must be a plain `{ event: ["field", ...] }`
// object of string-literal arrays for this script to be able to verify it.
export function checkSchemaDeclarations(filePath) {
  const source = parse(filePath);
  const violations = [];

  function visit(node) {
    if (ts.isVariableDeclaration(node) && node.name.getText(source) === "ANALYTICS_EVENT_SCHEMAS" && node.initializer) {
      let objectLiteral = node.initializer;
      if (ts.isAsExpression(objectLiteral)) objectLiteral = objectLiteral.expression;

      if (!ts.isObjectLiteralExpression(objectLiteral)) {
        violations.push({
          file: filePath,
          line: lineOf(source, node),
          message: "ANALYTICS_EVENT_SCHEMAS is not an object literal — cannot statically verify",
        });
      } else {
        for (const prop of objectLiteral.properties) {
          if (!ts.isPropertyAssignment(prop)) {
            violations.push({
              file: filePath,
              line: lineOf(source, prop),
              message: "unrecognized schema entry — cannot statically verify",
            });
            continue;
          }
          const eventName = stripQuotes(prop.name.getText(source));
          checkName(eventName, prop.name, filePath, source, violations);

          if (!ts.isArrayLiteralExpression(prop.initializer)) {
            violations.push({
              file: filePath,
              line: lineOf(source, prop),
              message: `schema value for "${eventName}" is not an array literal — cannot statically verify`,
            });
            continue;
          }
          for (const element of prop.initializer.elements) {
            if (ts.isStringLiteralLike(element)) {
              checkName(element.text, element, filePath, source, violations);
            } else {
              violations.push({
                file: filePath,
                line: lineOf(source, element),
                message: `schema field for "${eventName}" is not a string literal — cannot statically verify`,
              });
            }
          }
        }
      }
    }
    ts.forEachChild(node, visit);
  }

  visit(source);
  return violations;
}

// Scans one source file for `track(eventName, { ... })` call sites and
// checks every payload property name against the forbidden patterns,
// independent of whatever analyticsEvents.ts declares — a call site that
// bypasses the schema types (e.g. via `as`) still fails this gate.
export function checkCallSites(filePath) {
  const text = readFileSync(filePath, "utf8");
  if (!text.includes("track(")) return [];

  const source = parse(filePath);
  const violations = [];

  function visit(node) {
    if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === "track") {
      const payloadArg = node.arguments[1];
      if (payloadArg) {
        if (!ts.isObjectLiteralExpression(payloadArg)) {
          violations.push({
            file: filePath,
            line: lineOf(source, payloadArg),
            message: "track() payload is not an inline object literal — cannot statically verify",
          });
        } else {
          for (const prop of payloadArg.properties) {
            if (ts.isPropertyAssignment(prop) || ts.isShorthandPropertyAssignment(prop)) {
              const key = stripQuotes(prop.name.getText(source));
              checkName(key, prop, filePath, source, violations);
            } else {
              violations.push({
                file: filePath,
                line: lineOf(source, prop),
                message: "track() payload contains a spread/computed field — cannot statically verify",
              });
            }
          }
        }
      }
    }
    ts.forEachChild(node, visit);
  }

  visit(source);
  return violations;
}

export function collectSourceFiles(dir) {
  const out = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === "node_modules" || entry.name === "dist") continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      out.push(...collectSourceFiles(full));
    } else if (/\.(ts|tsx)$/.test(entry.name)) {
      out.push(full);
    }
  }
  return out;
}

export function runGate({ srcDir = SRC_DIR, schemaFile = SCHEMA_FILE } = {}) {
  return [...checkSchemaDeclarations(schemaFile), ...collectSourceFiles(srcDir).flatMap(checkCallSites)];
}

function main() {
  const violations = runGate();

  if (violations.length > 0) {
    console.error("Amplitude event schema gate failed (mvp.md AA-28):\n");
    for (const violation of violations) {
      console.error(`  ${path.relative(FRONTEND_ROOT, violation.file)}:${violation.line}  ${violation.message}`);
    }
    console.error(
      "\nBehaviour events only — no amount, ticker, or account field may appear in an Amplitude payload " +
        "(CLAUDE.md rule #2, Assumption A13).",
    );
    process.exitCode = 1;
    return;
  }

  console.log("Amplitude event schema gate passed — no forbidden fields found.");
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  main();
}
