# Contributing

Contributions are welcome. This document exists because three pull requests were
closed for reasons that were never written down anywhere — that was a failure of
this file's absence, not of the contributors.

## Running the tests

```bash
pip install -r requirements-test.txt
pytest
ruff check .
```

The suite stubs Home Assistant rather than installing it, so it runs anywhere in
under a second. There is no hardware in the loop.

CI additionally runs **hassfest** and **HACS validation**. Both catch things
pytest cannot — invalid translation keys, manifest problems — and both must pass.

## What gets merged

**Fixes with a test.** If the bug reached a user, the test that would have caught
it matters more than the fix. `tests/test_regressions.py` is exactly that: one
test per released bug.

**Features built as pure functions where possible.** `surplus_decision.py`,
`charge_planner.py` and `session_costing.py` take plain values and return plain
values, with no Home Assistant import. The controller keeps the I/O and the
state. That is what makes any of it testable, and it is the pattern every
feature since 2.5.0 has followed.

**DP findings from hardware nobody here owns.** A dump from a model that behaves
differently is genuinely valuable and hard to get. Open a device report issue
rather than a PR — the data is the contribution.

## What does not get merged

**Removing the surplus module.** Three closed PRs did this, apparently as a side
effect of syncing from a fork that had removed it. Surplus mode is the reason a
good part of this integration exists; a PR that deletes it will be closed
regardless of what else it contains.

If you maintain a fork without it, that is entirely reasonable — but the merge
has to go one way, not both.

**Behaviour changes with no test.** Not out of ceremony: this integration talks
to a device most people cannot test against, so a change nobody can verify is a
change nobody can safely keep.

**Anything that holds the local connection open.** A Tuya charger accepts
**one** local connection. Keeping a socket open locks out the Smart Life app and
every other client. Push-based updates were considered and deliberately
dropped for this reason — see `docs/ROADMAP.md`, item B6.

## Before opening a PR

- Read [`docs/ROADMAP.md`](docs/ROADMAP.md). It records what is planned, what is
  done, and what was dropped on purpose, so a PR does not re-litigate a settled
  decision by accident.
- Say which charger you tested on, and whether you tested a write. Write paths
  are the risky ones: every DP write makes the charger beep, and some interrupt
  an active charge.
- Keep the diff to one subject. A fix bundled with a refactor is hard to accept
  and hard to revert.

## Style

Match the file you are editing. Concretely:

- comments explain *why*, not *what* — the code already says what
- when a decision could reasonably have gone the other way, the comment says
  which way it went and what the alternative would have cost
- no comment for the obvious

## Credit

Contributors whose ideas were adopted are named in the release notes and in the
roadmap, including where the code was rewritten rather than merged:
[@alexsxb](https://github.com/alexsxb),
[@algirdasc](https://github.com/algirdasc),
[@1ud0v1c0](https://github.com/1ud0v1c0).
