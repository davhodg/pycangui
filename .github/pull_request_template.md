<!-- Thank you for contributing. The policy is in CONTRIBUTING.md; this is its short form. -->

## What this changes, and why

<!-- One change per pull request. Link the issue it closes, if there is one: "Closes #123". -->

## Checklist

- [ ] An issue was opened first, if this is a large change
- [ ] Tests come with it, and a fix has one that fails without it
- [ ] The full suite and the linter pass (`pytest -n auto tests`, `ruff check`, `ruff format --check`)
- [ ] A line in `pycangui/help/changelog.md` under *Unreleased*, if a user would notice it
- [ ] The help page is updated, if the behaviour it describes changed
- [ ] Anything that can disturb equipment asks first, through pycangui's confirmations
- [ ] A change to what is on screen keeps to [ACCESSIBILITY.md](../ACCESSIBILITY.md): no font
      or pixel size written into a pane, colour never the only signal, every new control
      reachable by keyboard and named; tried with large text and with the keyboard alone
- [ ] Any new dependency is explained here and is not GPL-only
- [ ] No proprietary material: no company DBC, EDS, A2L or log files, identifiers or code

## Tested on

<!-- Windows, Linux or macOS; the adapter and devices, if real hardware was used. -->
