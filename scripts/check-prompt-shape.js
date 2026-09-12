#!/usr/bin/env node
// upgrade-policy: managed-block. Edits made inside this BEGIN/END block are regenerated and lost on the next tapps_upgrade — put project-specific customizations below the END marker instead, where they survive every upgrade untouched.
// BEGIN: tapps-skill-asset orchestration-prompt/scripts/check-prompt-shape.js v3.12.83
// Validate an emitted orchestration prompt carries every required shape
// element before it is handed to a runner. Exit 0 when the shape holds;
// exit 1 naming every defect found; exit 2 on a usage/read error.
//
// Usage: node scripts/check-prompt-shape.js <prompt.md> [--driver-rows N]
"use strict";

const fs = require("fs");

// Legacy substring checks, kept for backward compatibility with earlier callers.
const REQUIRED_SECTIONS = [
  ["## Goal", "no stated Goal"],
  ["## Loop", "no Loop section -- the goal loop has no repeatable body"],
  ["Done-when", "no Done-when clause -- the loop cannot terminate"],
  ["Sub-goal 0", "no Sub-goal 0 -- self-healing preconditions are missing"],
  [
    "Lessons learned",
    "no Lessons learned section -- the run-time learnings write is missing",
  ],
];

// The full structural list the orchestrator's reference enforces.
const REQUIRED_HEADINGS = [
  "## Driver discipline",
  "## Prerequisites / Wayfind gate",
  "## How to run",
  "## Done-when",
  "## Plane map",
  "### Parallel wave schedule",
  "## Parallelization plan",
  "## Loop",
  "## Guardrails",
  "## Autonomy",
  "## Lessons learned",
  "## Run-as",
  "## Unverified assumptions",
  "## Lane briefs",
];

function section(lines, heading) {
  const s = lines.findIndex((l) => l.startsWith(heading));
  if (s < 0) return "";
  const e = lines.findIndex((l, i) => i > s && /^#{1,3} /.test(l));
  return lines.slice(s, e < 0 ? undefined : e).join("\n");
}

function main(argv) {
  const args = argv.slice(2);
  const path = args.find((a) => !a.startsWith("--"));
  const rowsFlag = args.indexOf("--driver-rows");
  const driverCap = rowsFlag >= 0 ? Number(args[rowsFlag + 1]) : 5;

  if (!path) {
    console.error("usage: check-prompt-shape.js <prompt.md> [--driver-rows N]");
    process.exit(2);
  }
  if (!Number.isInteger(driverCap) || driverCap < 5) {
    console.error("--driver-rows must be an integer >= 5");
    process.exit(2);
  }

  let text;
  try {
    text = fs.readFileSync(path, "utf8");
  } catch (err) {
    console.error(`cannot read ${path}: ${err.message}`);
    process.exit(2);
  }
  const lines = text.split("\n");
  const problems = [];

  for (const [marker, reason] of REQUIRED_SECTIONS) {
    if (!text.includes(marker)) problems.push(`missing: ${marker} -- ${reason}`);
  }

  for (const h of REQUIRED_HEADINGS) {
    if (!lines.some((l) => l.startsWith(h))) problems.push(`missing required heading: ${h}`);
  }

  // A prompt that dispatches lanes needs a table naming their briefs, or
  // the lanes it names have nothing to run (TAP-7078 ruling 12).
  const dispatchesLanes = /dispatch-lane\.sh|claude -p/.test(text);
  if (dispatchesLanes && !lines.some((l) => l.startsWith("## Lane briefs"))) {
    problems.push("missing: ## Lane briefs -- a prompt that dispatches lanes needs a briefs table");
  }

  // Plane map detectors.
  const tableRows = lines.filter((l) => /^\|/.test(l)).map((l) => l.split("|").map((c) => c.trim()));
  const planeRows = tableRows.filter(
    (cells) => cells.length >= 9 && /^\*{0,2}(driver|delegate|operator)\*{0,2}$/.test(cells[2])
  );
  const setup = lines.find((l) => /^- \*\*Session setup/.test(l)) ?? "";
  if (planeRows.length === 0) {
    problems.push(
      "Plane map has no rows with an Owner column (driver|delegate|operator) -- the Owner column is what makes Driver discipline auditable"
    );
  } else {
    const driverRows = planeRows.filter((c) => /driver/.test(c[2]));
    if (driverRows.length > driverCap) {
      problems.push(
        `detector 1: ${driverRows.length} driver-owned Plane-map rows, cap ${driverCap} -- a row nobody was dispatched for is work the top session does. ` +
          `Delegate it, or declare the exception in Driver discipline and pass --driver-rows ${driverRows.length}.`
      );
    }
    if (driverRows.length > 5 && !/\*\*Exception, named:?\*\*|named exception/i.test(text)) {
      problems.push("detector 1: more than five driver rows but Driver discipline names no exception");
    }
    const effortCells = planeRows.map((c) => c[7] ?? "—");
    const anyEffort = effortCells.some((e) => e && e !== "—" && e !== "-");
    const declaresNoWorkflow = /no Workflow|effort control (was|is) surrendered|Agent tool has no effort/i.test(
      text
    );
    if (!anyEffort && !declaresNoWorkflow) {
      problems.push(
        "detector 2: the effort column is all — and the prompt does not say it has no Workflow -- effort control was surrendered silently"
      );
    }

    const driverRowText = driverRows.map((c) => c.join(" ")).join("\n");
    const driverIsAboveFloor = /gh pr merge|\bmerge\b|deploy|plugin install|scop\w+ (a )?(continuation|fix)/i.test(
      driverRowText
    );
    if (driverIsAboveFloor && /`\/model (haiku|sonnet)`/.test(setup)) {
      problems.push(
        "driver rows mention merge/deploy/install/scoping a fix but Session setup pins `/model sonnet` -- that driver is above the floor by construction"
      );
    }

    for (const c of planeRows) {
      if (/driver|operator/.test(c[2])) continue;
      const model = (c[6] ?? "").replace(/[`*]/g, "").trim();
      const effort = (c[7] ?? "").replace(/[`*]/g, "").trim();
      const notes = (c[8] ?? "").trim();
      const above = /^(opus|fable)$/.test(model) || /^(high|xhigh|max)$/.test(effort);
      if (above && notes.length < 8) {
        problems.push(
          `Plane-map row "${(c[1] ?? "").slice(0, 50)}" is above the floor (${model || "—"}/${
            effort || "—"
          }) with no reason in Notes -- an unpriced default, not a decision`
        );
      }
    }
  }

  // Session setup carries concrete /model and /effort.
  const modelOk = /`\/model (haiku|sonnet|opus|fable)`/.test(setup);
  const effortOk = /`\/effort (low|medium|high|xhigh)`/.test(setup);
  if (!modelOk) {
    problems.push(
      "Session setup line has no concrete `/model <haiku|sonnet|opus|fable>` -- the runner would inherit the pasting session's tier"
    );
  }
  if (!effortOk) problems.push("Session setup line has no concrete `/effort <low|medium|high|xhigh>`");
  if (/`\/model <|`\/effort </.test(setup)) problems.push("Session setup line still carries a template placeholder");

  // Must-not-shrink + lessons-learned gate inside Done-when.
  const doneStart = lines.findIndex((l) => l.startsWith("## Done-when"));
  const doneEnd = lines.findIndex((l, i) => i > doneStart && /^## /.test(l));
  const done = doneStart >= 0 ? lines.slice(doneStart, doneEnd < 0 ? undefined : doneEnd).join("\n") : "";
  if (doneStart >= 0 && !/(≥|>=|must not shrink|not shrink|no fewer|at least)/i.test(done)) {
    problems.push(
      "Done-when has no must-not-shrink clause (≥ N / \"must not shrink\") -- the goal is satisfiable by deleting what is measured"
    );
  }
  if (doneStart >= 0 && !/lessons/i.test(done)) {
    problems.push(
      "Done-when does not gate on the lessons-learned pass -- without a clause it is advisory and gets dropped when the goal goes green"
    );
  }

  // Parallelization plan must enumerate the derived order.
  if (!/order-forced-by/i.test(section(lines, "## Parallelization plan"))) {
    problems.push(
      "Parallelization plan has no `order-forced-by:` line -- derived shared state between lanes was not enumerated"
    );
  }

  // Validation contract, when present, must be Workflow-shaped.
  const contract = section(lines, "## Validation contract");
  if (contract) {
    const header = contract.split("\n").find((l) => /^\|\s*ID\s*\|/i.test(l)) ?? "";
    if (!/\|\s*kind\s*\|/i.test(header)) {
      problems.push("Validation contract table has no `kind` column -- the verify Workflow cannot tier a VAL it cannot classify");
    }
    if (!/negative control/i.test(contract) || !/positive control/i.test(contract)) {
      problems.push(
        "Validation contract never names both a negative control and a positive control -- the Workflow refuses a VAL without both, so the driver would invent them at verify time"
      );
    }
  }

  if (problems.length > 0) {
    for (const p of problems) console.error(p);
    process.exit(1);
  }
  console.log(`ok: ${path} carries every required section`);
  process.exit(0);
}

main(process.argv);
// END: tapps-skill-asset
