#!/usr/bin/env node

/**
 * Migration script: tapps-brain → tapps-brain-memory
 *
 * Detects the old "tapps-brain" plugin entry in openclaw.json, copies its
 * settings to the new "tapps-brain-memory" entry, removes the old entry,
 * and cleans up the orphaned extension directory.
 *
 * Usage:
 *   node openclaw-plugin/scripts/migrate-plugin-rename.mjs [--dry-run]
 */

import { readFileSync, writeFileSync, existsSync, rmSync } from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";

const OLD_NAME = "tapps-brain";
const NEW_NAME = "tapps-brain-memory";

const dryRun = process.argv.includes("--dry-run");

const openclawDir = join(homedir(), ".openclaw");
const configPath = join(openclawDir, "openclaw.json");
const oldExtDir = join(openclawDir, "extensions", OLD_NAME);

function log(msg) {
  console.log(dryRun ? `[dry-run] ${msg}` : msg);
}

// --- Step 1: Read openclaw.json ---

if (!existsSync(configPath)) {
  console.log(`No openclaw.json found at ${configPath} — nothing to migrate.`);
  process.exit(0);
}

let config;
try {
  config = JSON.parse(readFileSync(configPath, "utf-8"));
} catch (err) {
  console.error(`Failed to parse ${configPath}: ${err.message}`);
  process.exit(1);
}

const plugins = config.plugins ?? {};
const oldEntry = plugins[OLD_NAME];
const newEntry = plugins[NEW_NAME];

if (!oldEntry) {
  console.log(
    `No "${OLD_NAME}" plugin entry found in openclaw.json — nothing to migrate.`
  );
  process.exit(0);
}

// --- Step 2: Merge settings into the new entry ---

const merged = { ...oldEntry, ...newEntry };
// Remove fields that are identity-level (name, id) — the new plugin owns those.
delete merged.name;
delete merged.id;

log(`Migrating settings from "${OLD_NAME}" → "${NEW_NAME}":`);
for (const [key, value] of Object.entries(merged)) {
  log(`  ${key}: ${JSON.stringify(value)}`);
}

plugins[NEW_NAME] = merged;
delete plugins[OLD_NAME];
config.plugins = plugins;

// --- Step 3: Write updated config ---

if (!dryRun) {
  // Back up before writing
  const backupPath = configPath + ".bak";
  writeFileSync(backupPath, readFileSync(configPath, "utf-8"));
  log(`Backup saved to ${backupPath}`);

  writeFileSync(configPath, JSON.stringify(config, null, 2) + "\n");
  log(`Updated ${configPath}`);
} else {
  log(`Would update ${configPath}`);
}

// --- Step 4: Remove orphaned extension directory ---

if (existsSync(oldExtDir)) {
  if (!dryRun) {
    rmSync(oldExtDir, { recursive: true, force: true });
    log(`Removed orphaned directory: ${oldExtDir}`);
  } else {
    log(`Would remove orphaned directory: ${oldExtDir}`);
  }
} else {
  log(`No orphaned directory found at ${oldExtDir}`);
}

// --- Summary ---

console.log("\nMigration complete.");
console.log("Run `openclaw gateway restart` to load the updated configuration.");
