# Security policy

HiveNet runs code received over the network on volunteers' PCs, so security reports are taken seriously.

## Reporting a vulnerability

**Please do not open a public issue.** Instead, report it privately:

- through GitHub: **Security → Report a vulnerability** in this repository (private vulnerability reporting); or
- by contacting the maintainer privately through the GitHub profile [K1m1k](https://github.com/K1m1k).

Please include what you found, how to reproduce it, and the impact you expect. You will receive an acknowledgement as
soon as possible; fixes are prioritised by severity, and you will be credited in the release notes unless you prefer
otherwise.

## Scope

In scope: the orchestrator server (`orchestrator/`), the bee client and its sandbox (`bee-node/`), the computation
modules (`module-src/`) and the web panel.

Particularly relevant:
- escaping the bee sandbox or making it run code outside WebAssembly;
- making a bee accept unsigned or tampered modules;
- reading another user's texts, results or honey;
- forging results or metrics in a way that verification does not catch;
- getting paid twice or bypassing rate limits.

## Supported versions

Only the latest version on the `main` branch receives security fixes during the testing phase.

## Known limitations

These are documented design limits, not vulnerabilities (see [`docs/ROADMAP.md`](docs/ROADMAP.md)):
- the bee processing a cell can see that cell's data in plain text while computing it, which is why only
  non-sensitive texts may be used during testing;
- HTTPS on the server, invitation-only sign-up and an off-server module signing key are still to be completed before
  testing over the internet.
