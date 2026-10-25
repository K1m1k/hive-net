# Contributing to HiveNet

Thank you for your interest in HiveNet. Bug reports, test results from your own machines, ideas and code are all
welcome.

## Before you start

- Read [`README.md`](README.md), [`TESTING.md`](TESTING.md) and [`docs/ROADMAP.md`](docs/ROADMAP.md).
- For anything bigger than a small fix, open an issue first so we can agree on the approach.
- Security problems: please **do not** open a public issue; contact the maintainer privately through the GitHub
  profile [K1m1k](https://github.com/K1m1k).

## Project principles

- **Security first**, including during testing: participants lend their PCs and their data.
- **Zero waste**: if something can be done with less CPU, network, memory or energy, it is done that way.
- **Measured, not assumed**: performance changes come with before/after figures from the performance report.

## Pull requests

1. Keep each pull request focused on one change.
2. Run every test before submitting:
   ```sh
   cd bee-node && npm test && cd ..
   cd orchestrator && ../.venv/bin/python -m unittest discover -s tests -v && cd ..
   ```
3. Add or update tests for the behaviour you change.
4. Write code, comments, messages and documentation in clear English.
5. Do not add external dependencies without discussing it first: the bee client deliberately has none.

## Licensing of contributions

HiveNet is dual-licensed: GNU AGPL-3.0-or-later for everyone, plus a commercial licence offered by the copyright
holder (see [`COMMERCIAL-LICENSE.md`](COMMERCIAL-LICENSE.md)). To keep this possible, every contribution must be
offered under both.

By submitting a contribution (code, documentation or any other material) you confirm that:

1. the contribution is your own original work, or you have the right to submit it;
2. you license it to the public under the **GNU AGPL-3.0-or-later**; and
3. you grant the HiveNet copyright holder (K1m1k) a perpetual, worldwide, non-exclusive, royalty-free, irrevocable
   licence to use, modify, sublicense and distribute your contribution under **any licence terms, including
   commercial ones**.

You keep the copyright in your contribution. To confirm your agreement, add the following line to the description of
your pull request:

```
I agree to the licensing terms in CONTRIBUTING.md.
```

Pull requests without this line cannot be merged.

*This section is not legal advice.*
