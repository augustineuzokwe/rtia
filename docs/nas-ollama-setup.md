# Running RTIA against a NAS-hosted Ollama (with auth)

This is a generic recipe for self-hosting Ollama on a home / lab NAS,
fronting it with a Caddy reverse proxy that adds Bearer-token auth, and
restricting access at the router so only a chosen client (your laptop)
can hit it. RTIA then reaches the NAS via the `RTIA_OLLAMA_HOST` and
`RTIA_OLLAMA_AUTH_TOKEN` env vars documented in
[`USAGE.md` §10.1](USAGE.md).

This file deliberately contains **no secrets, no IPs, no token values**.
The maintainer keeps those in a private runbook (gitignored under
`learning/`). The shape here is portable across any NAS that runs Docker
and any router with basic firewall rules.

## Why this shape

Three layers, each doing one job. If any one fails, the others still hold:

| Layer | What it does | Failure if missing |
|---|---|---|
| Router firewall | Restricts `:11435` access to your laptop's IP only | Anyone on your LAN could query the NAS Ollama |
| Caddy (reverse proxy) | Adds Bearer-token auth on top of Ollama's auth-less API | Anyone with the IP could query Ollama (Ollama has no built-in auth) |
| Docker isolation + hardening | Limits what a compromised Ollama process can reach on the NAS | A malicious model / prompt-injection chain could read your NAS files |

Ship all three; don't skip the firewall on the assumption "Caddy will catch it."

---

## 1. `docker-compose.yml`

One file: [`docs/nas-ollama/docker-compose.yml`](nas-ollama/docker-compose.yml).
The Caddyfile is inlined inside it via the Compose `configs:` block, so there's
nothing else to copy. Paste the whole file into the UGOS Docker "Create project"
Compose field, or `docker compose up -d` from the command line on any NAS OS.

**Before bringing it up, do two things:**

1. **Edit one line:** the `volumes` entry for the `ollama` service has
   `/CHANGE-ME/ollama-models` as the left-side bind-mount path. Change it to a
   directory on your fast disk (M.2 SSD strongly preferred since cold loads of
   4–8 GB models from HDD are noticeably slow).
2. **Set `OLLAMA_AUTH_TOKEN`** in the project environment (see
   [§2](#2-generating-the-token)). In the UGOS GUI that's the environment field
   on the Create-project screen; on the CLI an `.env` file next to the compose
   file, or `export OLLAMA_AUTH_TOKEN=...` before `docker compose up`.

### What the stack does, in one paragraph

Two services on a private Docker bridge. Only the `caddy` service binds a
host port (`:11435`); `ollama` has no `ports:` block, so it's only reachable
*through* Caddy. Ollama listens on `0.0.0.0:11434` inside the container (set
via the `OLLAMA_HOST` env var, because the default is localhost-only and
Caddy in a sibling container can't reach a sibling's loopback). Caddy
forwards authenticated requests over the bridge to `ollama:11434`. Both
services run with `cap_drop: ALL` and `security_opt: no-new-privileges`;
Ollama additionally gets resource limits.

### On hardening, three notes worth understanding before changing things

**Why neither service uses `read_only: true`.** It was tried on both and
dropped on both, for different reasons. On Ollama, `read_only` breaks startup
because Ollama writes runtime state (SSH-style keys, lock files, cached
binaries) in places a single bind mount + tmpfs doesn't cover (upstream
[ollama #7471](https://github.com/ollama/ollama/issues/7471) and similar on
Bazzite, NixOS). On Caddy, Docker refuses to deliver the inline `configs:`
Caddyfile into a read-only service (`cannot create config ... in read-only
service caddy: \`file\` is the sole supported option`), and the single-paste
deploy depends on that inline Caddyfile. Both were verified by actually
bringing the stack up, not just linting it. The remaining hardening
(`cap_drop: ALL`, `no-new-privileges`, network isolation, single host port)
carries the weight.

**Why neither service has a `user:` directive (and the UGOS PUID/PGID GUI
hint doesn't apply).** UGOS Docker UI suggests setting `PUID=1000 PGID=10`
in the environment. That hint is generic advice for LinuxServer.io-style
images. The official `ollama/ollama` and `caddy` images don't honour
`PUID`/`PGID` env vars; they run as root inside the container by default
and don't expose a clean way to drop privileges without breaking. Root
inside a hardened container is acceptable here because `cap_drop: ALL` +
`no-new-privileges` + no host port (for Ollama) + network isolation make
in-container root significantly weaker than UID 0 on the NAS itself. If
you need NAS-side file ownership to be a specific user (for SMB / NFS
export of the models directory, for example), `chown` the bind-mount
directory after the first `ollama pull` instead of fighting the container
image.

**Why the token line in the inlined Caddyfile uses `$$`.** Docker Compose
interpolates `$` in the compose file, so a bare `{$OLLAMA_AUTH_TOKEN}` in the
`configs:` content would be eaten by Compose before Caddy ever saw it. The
`$$` renders to a literal `$` in the materialised config, leaving Caddy's own
`{$OLLAMA_AUTH_TOKEN}` env-substitution intact. Leave the `$$` as-is.

---

## 2. Generating the token

Use a cryptographically random token, not a memorable string:

```bash
# Run this once on a trusted machine. Stash the output in your
# password manager AND in your private runbook (see learning/).
openssl rand -hex 32
```

Export it on the NAS before starting the stack:

```bash
export OLLAMA_AUTH_TOKEN=<paste-here>
docker compose up -d
```

Or persist it in the NAS OS's per-service env file so it survives a
reboot. The exact mechanism depends on your NAS OS (UGOS, Synology DSM,
TrueNAS, Unraid all do this differently; check your OS's docs).

---

## 3. Router-level guidance

The compose stack above gives you authenticated remote Ollama. The
firewall layer is what prevents a compromised LAN device (a guest phone,
an IoT bulb, the kid's laptop) from probing Ollama at all, even with
the right token.

Minimum rules to apply at your router:

1. **DHCP reservation for the NAS.** Pin the NAS to a stable LAN IP
   (e.g. `192.168.1.50`). The firewall rules below depend on the NAS IP
   not drifting.
2. **DHCP reservation for the laptop** (optional but recommended). Same
   reason: the firewall rule that allows your laptop to reach the NAS
   needs a stable client IP.
3. **VLAN isolation** if your router supports it (UniFi, OPNsense,
   pfSense yes; most consumer routers no). Put the NAS on its own VLAN
   so a compromise of any other device can't reach it laterally. The
   firewall rule below should be on this VLAN, not the main LAN.
4. **Firewall rule** (minimum):
   - **Allow:** `<laptop-IP> → <NAS-IP>:11435 TCP`
   - **Block:** everything else → `<NAS-IP>:11435` (on both LAN and any
     other internal VLANs)
   - **Block:** `WAN → <NAS-IP>:11435` explicitly (most routers default
     to this, but verify: a misconfigured port-forward could expose
     Ollama to the internet)

The port choice (`:11435`) is deliberately distinct from Ollama's
default `:11434`. If a misconfiguration ever exposed raw Ollama, it
would be visibly wrong because the port wouldn't match the documented
RTIA setup.

---

## 4. Verification probes (three curls)

Before pointing RTIA at the NAS, run these three from a terminal to
verify each layer holds. **All three must behave as described.** If any
one differs, the layer that fails is the one to fix before moving on.

### Probe 1: with token, from the laptop → expect `200 OK`

```bash
curl -sv \
  -H "Authorization: Bearer <YOUR-TOKEN>" \
  http://<NAS-IP>:11435/api/tags 2>&1 | tail -20
```

Expected: HTTP 200 with a JSON body listing pulled models (may be `[]`
if you haven't pulled any yet, that's fine, the connection itself is
what's being tested).

If 401: Caddy is reachable but the token is wrong. Re-check
`OLLAMA_AUTH_TOKEN` in the NAS environment matches what's in the curl.

If connection refused / timeout: see Probe 3 below.

### Probe 2: without token, from the laptop → expect `401 Unauthorized`

```bash
curl -sv http://<NAS-IP>:11435/api/tags 2>&1 | tail -20
```

Expected: HTTP 401 with body `Unauthorized`.

If 200: the auth layer isn't holding. Caddy may not be loading the env
var (check `OLLAMA_AUTH_TOKEN` is set in the project environment), or the
matcher in the inlined Caddyfile isn't catching the no-header case. Check
`docker compose logs caddy` and re-read the `configs:` block in the compose file.

### Probe 3: with token, from a different LAN device → expect blocked

From any other device on your LAN (phone, another laptop) that is *not*
the IP you allowed in the firewall rule:

```bash
curl -sv --max-time 5 \
  -H "Authorization: Bearer <YOUR-TOKEN>" \
  http://<NAS-IP>:11435/api/tags 2>&1 | tail -10
```

Expected: connection timeout or "no route to host", the firewall blocks
the packet before it reaches the NAS.

If 200: the firewall rule isn't holding. The other device's IP is
matching the allow rule, or the rule's source is too permissive. Check
the router's firewall log.

---

## 5. Pulling a model

After the three probes pass, pull the model you'll use. From your laptop:

```bash
# Verify Ollama is alive through Caddy
curl -H "Authorization: Bearer <TOKEN>" http://<NAS-IP>:11435/api/tags

# Pull a model (this takes 30-60 minutes for a 4-6 GB model on home broadband)
docker exec rtia-ollama ollama pull llama3.1:8b
```

Then verify it's listed:

```bash
curl -H "Authorization: Bearer <TOKEN>" http://<NAS-IP>:11435/api/tags
```

`llama3.1:8b` should appear in the JSON response.

---

## 6. Pointing RTIA at it

In your RTIA project's `.env`:

```bash
RTIA_LLM_PROVIDER=ollama
RTIA_OLLAMA_HOST=http://<NAS-IP>:11435
RTIA_OLLAMA_AUTH_TOKEN=<TOKEN>
RTIA_OLLAMA_MODEL=llama3.1:8b
```

Then run the demo:

```bash
uv run python scripts/run_pipeline_demo.py sample-01-well-structured.md
```

Expect 5–15 minutes for the full deep pipeline on a CPU-only NAS, this
is the dominant latency cost of the local stack.

---

## Out of scope

- **Off-LAN reach (Tailscale, WireGuard).** Phase 2 work, not covered
  here. Adding a tailnet on top of the above lets you reach the NAS
  from anywhere without opening any WAN port; ACLs on the tailnet
  restrict which devices can talk to the Ollama port. Done right, you
  can drop the home-LAN firewall rule and route everything (including
  on-LAN traffic) through the encrypted tunnel.
- **HTTPS / TLS termination.** Caddy makes this trivial (it auto-issues
  certs via Let's Encrypt) but the recipe above runs over plain HTTP on
  a private LAN, which is acceptable for a single-laptop home setup.
  Add `tls internal` to the Caddyfile and `https://` to RTIA's
  `RTIA_OLLAMA_HOST` if you want encrypted-on-LAN traffic.
- **Logging / observability.** Caddy's structured log goes to stdout
  (captured by `docker compose logs caddy`). For long-term audit, pipe
  it to a log aggregator. Out of scope here.
