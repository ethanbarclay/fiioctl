# Security policy

## Reporting a vulnerability

Report privately through GitHub: **Security → Advisories → Report a
vulnerability** on this repository. That opens a draft advisory only the
maintainer can see. Please do not open a public issue for anything that looks
exploitable.

Include the `fiioctl` version, your OS, and what the tool was talking to (a
device, FiiO's servers, or neither). A reply should come within a week.

## Supported versions

The latest 0.x release is the only supported one. There is no backporting while
the project is pre-1.0.

## What this tool actually does

Worth knowing when judging a report:

- **It writes vendor commands to USB HID devices.** Every write goes to a
  device the user has physical possession of, selected explicitly. The firmware
  range-checks what it accepts, which is why probing is safe, but a malicious
  profile could still set an unpleasant EQ. There is no firmware flashing in
  this release, so nothing here can brick a device.
- **It fetches from two third parties** when asked to search for headphones:
  the AutoEq repository over HTTPS, and FiiO's own catalogue API. Both are
  read-only, neither needs an account, and nothing about the user is sent
  beyond what the query itself contains.
- **It caches to disk** - the AutoEq indexes and individual curves. Cache
  contents are parsed as data, never executed.
- **It stores no credentials.** Publishing presets to FiiO needs a login and is
  deliberately not implemented, so there is nothing to steal.

## Known findings that are not bugs

**AES-256-ECB in `fiioctl/cloud.py`** (CodeQL `py/weak-cryptographic-algorithm`,
flagged twice). ECB is genuinely a weak mode, and it is not our choice: FiiO's
`/ucenter-api` accepts exactly one envelope, `base64(AES-256-ECB(json))` with
the key RSA-wrapped, and rejects anything else. Switching mode means losing
catalogue search entirely.

Three things bound what that costs. HTTPS carries the real confidentiality. The
key is sixteen fresh random bytes per request, so nothing is reused across
messages. And the plaintext is a public catalogue query, so the block-equality
that ECB leaks reveals only that a headphone search looks like a headphone
search. Reports of this alert will be closed with a link here; a report showing
a concrete attack on it is very welcome and would change this answer.
