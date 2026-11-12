# Red team: do the loom-fun cards reproduce?

One branch per model, `redteam/<slug>`, whose `target.json` names a checkpoint that is *not* the card's own
repo (another quantizer's Q4_K_M of the same model, or a finetune of the same architecture), so the skills
have to port it rather than fetch the published package. Pushing `target.json` runs
`.github/workflows/redteam.yml`: one `weaver optimize` (local agent, the vendored loom-skills) on a DevLab
Dispatch Strix Halo runner, then the frozen-evaluator regrade. Compare its report with the card.
