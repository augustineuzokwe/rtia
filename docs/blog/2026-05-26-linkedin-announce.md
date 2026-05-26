# LinkedIn announce post — companion to the blog

**Hold until the blog is published; post within 24 h of the blog going live.** Per plan §7.8.

Word count: 197 words (target: ≤ 200).

---

I built an AI-first QA tool to find out which checks were missing from our standard SDLC. Three numbers stuck with me:

- **$0.03 per CI eval gate** — a non-deterministic agent test suite that costs less than a parking meter and refuses to merge a PR whose quality drops below floor.
- **10× cost cut on a single provider × model decision** — Anthropic Claude Opus 4.7 → Google Gemini 3.5 Flash, validated by re-running the same eval suite on the new provider before swapping.
- **8 credential patterns blocked at the input boundary** — a deterministic regex scanner that runs *before* any LLM call, so a leaked AWS key in a requirement document never leaves the local process.

The case study is at [BLOG URL] and the full code is at https://github.com/augustineuzokwe/rtia.

The most surprising lesson, in Section 4 of the post, has nothing to do with the LLM. It's about caching. Specifically the trap that makes your eval gate silently dishonest if you adopt the wrong cache defaults — and the three lines of design that close the trap.

If your team's process has a place for #1–#3 above but not yet for the other 5 augmentations the post covers, the post is for you.

---

## Posting checklist

- [ ] Replace `[BLOG URL]` with the live blog URL.
- [ ] Verify the GitHub URL is still correct (no rename).
- [ ] Pick a hero image — recommend a screenshot of the per-sample metric table from `docs/pipeline-baseline-2026-05-26.md` (it makes the "$0.03" claim viscerally real).
- [ ] Schedule for Tuesday–Thursday morning Western Europe time; LinkedIn algorithm bias for engineering content.
- [ ] First comment (algorithm boost): pin a link to one specific section of the blog — recommend Section 4 (caching trap) because it's the most quotable.

## Voice / tone notes

- Three numbers up top, no fluff, no buzzwords.
- The "most surprising lesson" sentence is the hook for the click-through. Keep it teasing without spoiling.
- The CTA is filter-based: "if your process already has X but not Y, this is for you." Self-qualifies the audience, reduces low-intent clicks.
- Avoid superlatives ("revolutionary," "game-changing"). Specific numbers do the work.
