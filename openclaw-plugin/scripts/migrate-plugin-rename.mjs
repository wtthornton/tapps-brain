#!/usr/bin/env node

/**
 * Migration script: tapps-brain → tapps-brain-memory
 *
 * Detects the old "tapps-brain" plugin entry in openclaw.json, copies its
 * settings to the new "tapps-brain-memory" entry, removes the old entry,
 * and cleans up the orphaned extension directory.
 *
 * Supports both the current OpenClaw config format (config.plugins.entries / installs)
 * and older flat formats (config.plugins[name]) for backward compatibility.
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

// --- Step 2: Detect config format and locate the old entry ---
//
// Current OpenClaw format: config.plugins.entries[name] + config.plugins.installs[name]
// Older flat format:        config.plugins[name]

const hasEntriesFormat = plugins.entries !== undefined && typeof plugins.entries === "object";

let oldEntry;
let oldInstall;
let configFormat;

if (hasEntriesFormat) {
  // Current format: nested entries + installs
  oldEntry = plugins.entries[OLD_NAME];
  oldInstall = (plugins.installs ?? {})[OLD_NAME];
  configFormat = "entries/installs";
} else {
  // Backward-compatible: flat config.plugins[name]
  oldEntry = plugins[OLD_NAME];
  oldInstall = undefined;
  configFormat = "flat (legacy)";
}

if (!oldEntry) {
  console.log(
    `No "${OLD_NAME}" plugin entry found in openclaw.json (checked ${configFormat} format) — nothing to migrate.`
  );
  process.exit(0);
}

log(`Detected config format: ${configFormat}`);

// --- Step 3: Merge settings into the new entry ---

if (hasEntriesFormat) {
  const existingNewEntry = (plugins.entries ?? {})[NEW_NAME];
  const merged = { ...oldEntry, ...existingNewEntry };
  // Remove fields that are identity-level (name, id) — the new plugin owns those.
  delete merged.name;
  delete merged.id;

  log(`Migrating entries["${OLD_NAME}"] → entries["${NEW_NAME}"]:`);
  for (const [key, value] of Object.entries(merged)) {
    log(`  ${key}: ${JSON.stringify(value)}`);
  }

  if (!plugins.entries) plugins.entries = {};
  plugins.entries[NEW_NAME] = merged;
  delete plugins.entries[OLD_NAME];

  // Migrate installs section if the old entry exists there
  if (oldInstall !== undefined) {
    if (!plugins.installs) plugins.installs = {};
    const existingNewInstall = plugins.installs[NEW_NAME];
    const mergedInstall = { ...oldInstall, ...existingNewInstall };
    delete mergedInstall.name;
    delete mergedInstall.id;

    log(`Migrating installs["${OLD_NAME}"] → installs["${NEW_NAME}"]:`);
    for (const [key, value] of Object.entries(mergedInstall)) {
      log(`  ${key}: ${JSON.stringify(value)}`);
    }

    plugins.installs[NEW_NAME] = mergedInstall;
    delete plugins.installs[OLD_NAME];
  } else {
    log(`No installs["${OLD_NAME}"] entry found — skipping installs migration.`);
  }
} else {
  // Legacy flat format: migrate config.plugins[OLD_NAME] → config.plugins[NEW_NAME]
  const existingNewEntry = plugins[NEW_NAME];
  const merged = { ...oldEntry, ...existingNewEntry };
  delete merged.name;
  delete merged.id;

  log(`Migrating plugins["${OLD_NAME}"] → plugins["${NEW_NAME}"] (legacy flat format):`);
  for (const [key, value] of Object.entries(merged)) {
    log(`  ${key}: ${JSON.stringify(value)}`);
  }

  plugins[NEW_NAME] = merged;
  delete plugins[OLD_NAME];
}

config.plugins = plugins;

// --- Step 4: Write updated config ---

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

// --- Step 5: Remove orphaned extension directory ---

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
