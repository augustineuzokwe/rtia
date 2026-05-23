<!--
Thanks for opening a PR. The checklist below mirrors the hard rules in
CLAUDE.md §4 — every PR in this repo follows them. Tick the boxes that
apply; explain any unchecked ones.
-->

## Summary

<!-- 1–3 bullet points describing what changed and why. -->

## Related issue

<!-- Use "Closes #N" so the merge auto-closes the issue. For partial work,
     use "Refs #N" instead. Every PR must link an issue (CLAUDE.md §4.4). -->

Closes #

## Test plan

- [ ] `uv run pytest -q`
- [ ] `uv run pre-commit run --all-files`
- [ ] Live exercise of the change (run the demo / hit the integration / trigger CI)
- [ ] For prompt or agent changes: ran the demo on all 3 samples (CLAUDE.md §4.1)

## Notes for reviewer

<!-- Anything worth flagging: deferred verification, follow-up tasks, cost
     implications, or behaviour the reviewer should pay extra attention to. -->
