# Archived: T8 alternative design — publicly trusted HTTPS via Let's Encrypt

**Status: not pursued.** Set aside by Fabian on 2026-07-30 in favour of keeping the local root CA and making its installation effortless (see T8 in `docs/roadmap-0.5.md`).

Reason for archiving rather than deleting: the design is sound and would become the right answer if the setup grows to many tablets, frequently changing server computers, or operators who cannot be walked through a one-time certificate install.

**Why it was set aside:**

1. A Let's Encrypt certificate is valid 90 days and depends on automation that must keep working (DNS provider API stability, credential rotation, connectivity at renewal time). A failure surfaces exactly when a session is running. The local root CA is valid 3650 days (`ssl_service.py:129`) and cannot expire mid-study. Much of the elaborate diagnostics below exist *because* the 90-day dependency was introduced.
2. Scope: per-installation identities, an automatic DNS updater, a six-step wizard, and three manuals are disproportionate for one server computer and one tablet.
3. With a portable root CA (export/import, now part of T8), the tablet install is a genuinely one-time ~3-minute step, not a recurring cost.

Authorship: original draft by Claude Fable 5, substantially rewritten by GPT 5.6 Sol (Codex), reviewed again by Claude Fable 5. Text below is Codex's version, unedited apart from the WebSocket correction noted at the end.

---

## T8 — Trusted local HTTPS with zero tablet certificate setup

**In plain terms:** Study Runner gives each server computer a stable HTTPS name, for example `https://sr-a1b2c3.study.reeze.one:3000`. The operator follows a short setup wizard once and then opens a QR code on the tablet. There is no `.crt`, `.pem`, trust profile, dedicated study WLAN, static IP, or router configuration. Windows and macOS are equal first-class server platforms.

### Decision and rationale

- A self-signed certificate encrypts traffic but is not trusted by a fresh tablet. Trusting it would require exactly the certificate/profile installation Fabian ruled out, and browser camera access may remain blocked because it is not a valid secure context.
- A public CA gives the tablet a trust chain it already knows. The certificate identifies a **hostname**, not today's LAN IP, so DHCP changes do not require a new certificate.
- Each Study Runner installation gets a random, persistent, non-personal installation ID and its own hostname (`sr-<id>.study.reeze.one`). Multiple Windows and Mac servers therefore coexist without sharing private keys or fighting over one DNS record.
- The hostname's public A/AAAA record points to that server's current private LAN address. The tablet resolves the public name but connects directly over the LAN; study traffic does not pass through a cloud relay.
- Because a private server cannot satisfy public HTTP-01 validation, certificate issuance uses automated DNS-01. Let's Encrypt explicitly supports DNS-01 for servers that are not publicly exposed and recommends narrowly scoped DNS credentials or delegated validation.
- This is the best fit for the stated constraints, but not magic: a WLAN can deliberately block public names resolving to private IPs (DNS-rebind protection). The product must detect that case and explain the two real fallbacks—tablet DNS configuration or an optional public relay—rather than silently presenting an untrusted certificate.

### Architecture

1. **Stable installation identity:** create one installation ID on first setup and persist it atomically. Derive the hostname from it; never use a participant, operator, machine-user, or MAC-address identifier. Provide an explicit "move this installation" flow so replacing a computer does not encourage copying private keys by hand.
2. **DNS address updater:** detect the active route/LAN address on startup and on network changes, update only this installation's A/AAAA record, then verify authoritative and client-facing resolution before advertising the URL. No static IP or DHCP reservation is assumed. Debounce network flapping and retain the last known-good record if detection is ambiguous.
3. **Certificate automation:** use a mature ACME implementation with DNS-01 rather than building the ACME protocol ourselves. First spike a small, packaged cross-platform client or TLS terminator (Caddy is the leading candidate) against the actual DNS provider. Delegate `_acme-challenge.study.reeze.one` to a restricted automation zone where possible. Store only least-privilege credentials through `secrets_service`; never return or log them.
4. **Runtime TLS contract:** Study Runner serves the per-installation hostname with its valid public chain and key. Renewal runs well before expiry, on boot and periodically, with staging support for tests. The app continues with the last valid certificate during temporary renewal failures. An expired/missing public certificate blocks tablet-ready status; it must **not** silently fall back to local-CA or Flask adhoc TLS.
5. **Local CA scope:** keep existing local-CA support only behind an explicit developer/advanced-mode choice with a clear "requires certificate installation on every client" warning. It is not a production recovery path and is never selected automatically.

### Step-based setup UI

Use one focused wizard, short copy, progressive disclosure, and one action per step:

1. **Welcome — "Set up secure tablet connection"**: explains in two sentences that Study Runner creates a trusted address and no tablet certificate is installed.
2. **Server name:** show the generated hostname and allow a short optional display label; keep the technical installation ID read-only under "Details".
3. **DNS access:** authenticate/configure the supported provider using the narrowest credential possible. Explain that this permission only maintains the secure Study Runner address. Never expose the saved secret again.
4. **Automatic checks:** a single progress view checks internet, current LAN address, DNS update, DNS-01 issuance, certificate chain, port reachability, and secure camera context. Each row becomes pending/running/done/action-required; technical logs stay behind "Details".
5. **Connect tablet:** show QR code plus the exact hostname URL. The tablet landing page runs a lightweight self-test for hostname resolution, certificate trust, API reachability, and camera permission, then reports success to the wizard.
6. **Ready:** concise confirmation, certificate validity/automatic renewal status, and "Open dashboard". Later settings show a compact health card with "Run connection check again"; they do not repeat the full wizard.

Error text must say what happened, whether recorded data is safe, and the single next action. Never ask the operator to understand ACME, SANs, PEM files, A records, or certificate chains in the primary UI.

### Failure behavior and diagnostics

- Distinguish: no internet for DNS/renewal, DNS credential rejected, DNS propagation pending, wrong/stale LAN IP, DNS-rebind blocking, server firewall/port blocked, system clock wrong, certificate near expiry, and tablet camera permission denied.
- A DNS-rebind diagnosis requires evidence: compare authoritative/public DNS with what the tablet reports. Do not label every reachability failure as rebind protection.
- If the current WLAN blocks the design, show that no certificate-free purely local workaround exists under the stated constraints. Offer guided tablet DNS configuration; keep a public relay as a separately approved future option because it changes privacy and internet-dependency guarantees.
- Existing valid certificates and local study operation continue during a temporary ACME/DNS-provider outage. New setup and expired-certificate cases fail closed with a plain-language explanation.

### Documentation

- Add an operator guide: the six UI steps, screenshots, normal renewal behavior, switching WLANs, adding another server, and the exact recovery actions surfaced by diagnostics.
- Add an administrator/security guide: trust model, direct-LAN data path, DNS-01 delegation, least-privilege credential scope, secret storage, certificate/key lifecycle, revocation, computer replacement, DNS-rebind limitation, firewall requirements, and privacy implications of the hostname/DNS record.
- Add a maintainer runbook: supported DNS provider/version matrix, ACME staging procedure, rate-limit-safe testing, renewal observability, log locations/redaction rules, and how to rotate the delegated DNS credential without downtime.
- State explicitly that a public DNS record containing a private address reveals only an installation hostname/private address, not study or participant data; use opaque installation IDs and avoid identifying labels in DNS.

### Acceptance tests

- Fresh iPadOS and Android tablets with no installed certificate/profile open the QR URL without a trust warning; camera and API checks pass.
- The same packaged workflow passes on supported Windows and macOS versions.
- Changing WLAN and DHCP address updates DNS automatically and restores the same hostname without reissuing the certificate.
- Two server computers operate simultaneously with distinct hostnames, records, keys, and status.
- ACME staging tests cover first issuance, renewal, delayed TXT propagation, rejected credentials, rate limiting, outage with a still-valid certificate, and impending/actual expiry.
- DNS update tests cover IPv4/IPv6 selection, VPN and virtual-adapter ambiguity, rapid network changes, stale-record prevention, and secret/log redaction.
- An actual rebind-blocking WLAN is detected from tablet feedback and produces the documented guided fallback instead of a generic connection error.
- UI copy is usability-tested with an operator who has no certificate/DNS knowledge: setup succeeds using only the visible steps and no external instructions.

### Work items

| Item | Size | Risk | Tier |
|---|---|---|---|
| DNS-provider, delegated-zone, Caddy/ACME, Windows/macOS packaging, and rebind spike | M | feasibility must be proven before architecture is committed | hard |
| Installation identity + least-privilege DNS updater + network-change handling | M | wrong adapter/IP selection; credential scope | hard |
| Public-certificate lifecycle + fail-closed runtime integration | L | renewal, expiry, secret and private-key handling | hard |
| Six-step setup wizard + tablet self-test + compact health card | L | cross-device diagnostics and copy quality | hard |
| Operator, security, and maintainer documentation | M | must match the shipped UI and failure behavior exactly | medium |

### Retired open questions

1. Who hosts `reeze.one` DNS, which least-privilege API scopes exist, and can `_acme-challenge.study.reeze.one` be delegated to an automation-only zone?
2. Do the actual lab/field WLANs resolve a public hostname to its private LAN IP, and can the tablet self-test distinguish DNS-rebind blocking from firewall/reachability failures?

---

## Review notes carried over

**Claude Fable 5 (2026-07-30):** The three core changes in this design were improvements over the earlier draft and remain valid on their own terms — fail-closed instead of a silent local-CA fallback (a fallback would produce exactly the certificate warning the owner wants gone, plus a broken camera), a per-installation hostname (the earlier draft assumed a single server), and an automatic DNS updater (assumes no router access, unlike a DHCP reservation).

Two cautions if this is ever revived:

- **Caddy must not be treated as pre-decided.** Its DNS-provider modules are **not** in the standard binary — DNS-01 requires a custom build via `xcaddy` (Go toolchain) or the module-picker download, so the project would have to produce and maintain a custom Caddy binary per release platform (windows-x86_64, macos-arm64, macos-x86_64, linux-x86_64) and keep it patched. Bundling and supervising a third-party binary inside frozen PyInstaller builds is the same failure class that made BrainBit unusable in every packaged release (T1). The pure-Python `acme` library is certbot's own core and freezes like any other dependency.
- **Factual correction applied above:** the app has no WebSockets — all client/server transport is `fetch`/POST polling (`web/scripts/api-client.js`). Step 5 and the first acceptance test originally said "API/WebSocket reachability".
