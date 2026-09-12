#!/usr/bin/env node
// upgrade-policy: managed-block. Edits made inside this BEGIN/END block are regenerated and lost on the next tapps_upgrade — put project-specific customizations below the END marker instead, where they survive every upgrade untouched.
// BEGIN: tapps-skill-asset orchestration-prompt/scripts/check-learnings-size.js v3.12.83
// Measure a learnings.md file against the byte/bullet/per-bullet ceilings and
// the required trailing-date house style, mirroring nlt-orchestrator's
// check-learnings-size.js (TAP-7078 box 6). See
// tapps_mcp.pipeline.skill_managed_block.LEARNINGS_CEILING_BYTES /
// LEARNINGS_CEILING_BULLETS for the Python-side constants this parallels.
//
// Usage: node scripts/check-learnings-size.js <learnings.md> [--bytes N] [--bullets N] [--bullet-bytes N]
"use strict";

const fs = require("fs");

// A lesson is `- [conf] ...` or a `- **bolded**` lead; prose lines are not lessons.
const LESSON = /^- (\[(high|med|medium|low)\]|\*\*)/;
// Trailing provenance: an em-dash, then a parenthesis carrying an ISO date.
const DATED = /— \([^()]*\d{4}-\d{2}-\d{2}[^()]*\)\s*$/;

function flag(args, name, dflt) {
  const i = args.indexOf(name);
  if (i < 0) return dflt;
  const v = Number(args[i + 1]);
  if (!Number.isFinite(v) || v <= 0) {
    console.error(`${name} needs a positive number`);
    process.exit(2);
  }
  return v;
}

function main(argv) {
  const args = argv.slice(2);
  const path = args.find((a) => !a.startsWith("--"));
  if (!path) {
    console.error(
      "usage: check-learnings-size.js <learnings.md> [--bytes N] [--bullets N] [--bullet-bytes N]"
    );
    process.exit(2);
  }
  const maxBytes = flag(args, "--bytes", 48 * 1024);
  const maxBullets = flag(args, "--bullets", 90);
  const maxBulletBytes = flag(args, "--bullet-bytes", 900);

  let text, bytes;
  try {
    text = fs.readFileSync(path, "utf8");
    bytes = Buffer.byteLength(text, "utf8");
  } catch (err) {
    console.error(`cannot read ${path}: ${err.message}`);
    process.exit(2);
  }

  const lines = text.split("\n");
  const bullets = [];
  for (let i = 0; i < lines.length; i++) {
    if (!LESSON.test(lines[i])) continue;
    let j = i + 1;
    while (j < lines.length && /^  /.test(lines[j])) j++;
    bullets.push({ line: i + 1, text: lines.slice(i, j).join("\n") });
    i = j - 1;
  }

  const over = [];
  if (bytes > maxBytes) over.push(`${bytes} bytes > ${maxBytes}`);
  if (bullets.length > maxBullets) over.push(`${bullets.length} lesson bullets > ${maxBullets}`);
  for (const b of bullets) {
    const size = Buffer.byteLength(b.text, "utf8");
    if (size > maxBulletBytes) {
      over.push(
        `line ${b.line}: ${size} B bullet > ${maxBulletBytes} -- a narration, not a lesson; cut to the rule + its detector`
      );
    }
    if (!DATED.test(b.text)) {
      over.push(
        `line ${b.line}: no trailing — (source, YYYY-MM-DD) -- a bullet with no date can never be retired on evidence`
      );
    }
  }

  if (over.length > 0) {
    console.error(`over ceiling:\n  ${over.join("\n  ")}`);
    process.exit(1);
  }
  const maxBulletSize = bullets.length ? Math.max(...bullets.map((b) => Buffer.byteLength(b.text, "utf8"))) : 0;
  console.log(
    `ok: ${bytes}B, ${bullets.length} lesson bullets, max bullet ${maxBulletSize}B -- within ceiling (${maxBytes}B / ${maxBullets} / ${maxBulletBytes}B each)`
  );
  process.exit(0);
}

main(process.argv);
// END: tapps-skill-asset
