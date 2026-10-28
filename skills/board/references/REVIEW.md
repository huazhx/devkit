# Reviewing a period from the board

Companion to the board skill for a weekly (or any period) review: what to read from the board, in what order, and how not to misread it. First written on 2026-10-23 by Hermes's skill curator from a review session, inside Hermes's `weekly-review-planning` skill; kept here since 2026-10-29 so it changes together with the board.

## Evidence, in this order (keep it bounded)

```sh
python3 <skill-dir>/scripts/board.py time --days 7     # where the time went: per day, switches, which cards
python3 <skill-dir>/scripts/board.py list              # skeleton: ready / running / blocked, with due dates
python3 <skill-dir>/scripts/board.py ops --limit 30    # who changed what and when: what actually happened this period
python3 <skill-dir>/scripts/board.py todo list         # open to-dos with card, dates, waiting; report only the 2–3 that most need handling
python3 <skill-dir>/scripts/board.py todo list --all   # including ticked ones: how many small things got done
```

## Reading rules

- **Timed durations are an upper bound, not a measurement.** A session that hits the 6-hour auto-pause is recorded as exactly 6h00m and flagged "includes capped estimates". Two of five tracked days at exactly 6h means cards were never closed: say the figure is distorted and name those days instead of averaging them into a weekly total.
- **Fulfillment rate = done among what came due / everything that came due.** "3 cards completed this week" hides the one that expired; the honest pair is "3 done; 2 came due and were missed → 0/2 on the commitments that came due".
- **Switch count is the capacity signal.** Three switches a day alongside long hours means fragmentation, not throughput; it argues for fixed blocks next week.
- **Name each workstream by its deliverable before ranking it.** Two lines that share a category word are not one line: a dated talk (deliverable: slides) and a submitted paper with no open action are different commitments. For each line state the deliverable, whether it has a live card, and the next doable action; a line with no live card cannot be "prioritized", only started or left alone.
- **To-dos are counted apart from cards.** Report ticked to-dos separately. A to-do picked for Today on several days and never ticked is stalled: name it and ask keep or drop. Do not re-rank the to-do list during a review; the daily order belongs to whoever picks Today (operating manual §1.1).
