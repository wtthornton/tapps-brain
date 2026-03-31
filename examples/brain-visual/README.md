# Brain visual demo (static bento)

1. From a project with tapps-brain installed (`uv sync --extra cli`):

   ```bash
   tapps-brain visual export -o brain-visual.json
   ```

   Use `--skip-diagnostics` for a faster export without circuit/composite fields.

2. Open `index.html` in a browser and use **Load snapshot** to pick `brain-visual.json`.

No memory text or keys are included in the snapshot — only aggregated metadata. See `docs/planning/brain-visual-implementation-plan.md`.
