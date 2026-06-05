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

Drop this in a directory on your NAS (e.g. `/volume1/docker/ollama/` or
the equivalent on your NAS OS). Adjust the bind-mount path to where you
want Ollama to store models. An SSD path is strongly preferred so cold
loads aren't HDD-bound.

```yaml
# RTIA local-LLM stack: Ollama behind a Caddy Bearer-token proxy.
#
# Two services on a private bridge network. Only Caddy exposes a host
# port; Ollama itself is unreachable from outside the bridge - the
# Bearer-token check happens at Caddy, not Ollama, because Ollama has
# no native auth (https://github.com/ollama/ollama/issues/849).

services:
  ollama:
    image: ollama/ollama:latest
    container_name: rtia-ollama
    # No `ports:` block on purpose. Ollama is only reachable inside the
    # internal bridge; Caddy proxies to it via the service name. This is
    # what makes the "Caddy in front" claim true - bypassing Caddy is
    # not possible without `docker exec`.
    volumes:
      # Model storage. CHANGE THE LEFT SIDE to a directory on your fast
      # disk (SSD / M.2). Models are 4-8 GB each; cold load latency from
      # spinning disk is noticeable.
      - /CHANGE-ME/ollama-models:/root/.ollama
    # Hardening flags. None of these are required for Ollama to function;
    # all of them shrink the blast radius of a compromised container.
    cap_drop:
      - ALL                # No Linux capabilities. Ollama is a userspace
                           # HTTP server - it needs none.
    read_only: true        # Root filesystem is immutable. Anything that
                           # tries to write outside the named volumes fails.
    tmpfs:
      - /tmp               # Ollama writes to /tmp during inference; tmpfs
                           # gives it a writable scratch area without
                           # breaking read_only.
    security_opt:
      - no-new-privileges:true   # Setuid binaries inside the image can't
                                  # elevate privileges. Defence in depth.
    # Resource limits. Tune for your NAS's RAM/CPU. The numbers below
    # assume a 32 GB NAS - leave at least 4 GB for the OS + other services.
    # Without these, a misbehaving model could OOM the NAS.
    mem_limit: 24g
    cpus: "4"
    restart: unless-stopped
    networks:
      - rtia-llm

  caddy:
    image: caddy:latest
    container_name: rtia-caddy
    # Port :11435 is deliberately distinct from Ollama's default :11434.
    # A misconfiguration that exposes raw Ollama would be visibly wrong
    # (different port). If you must expose on a different port, change
    # both this line and RTIA_OLLAMA_HOST on the client.
    ports:
      - "11435:11435"
    volumes:
      # Caddyfile is mounted read-only so a compromised Caddy can't
      # rewrite its own config to disable the auth gate.
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
    environment:
      # Token value is injected from the operator's environment. Don't
      # hardcode it in Caddyfile or commit it. See "Generating the token"
      # below.
      - OLLAMA_AUTH_TOKEN
    cap_drop:
      - ALL
    cap_add:
      - NET_BIND_SERVICE   # Required to bind a privileged port if you
                           # ever move :11435 → :443. Drop if staying on
                           # high ports.
    read_only: true
    tmpfs:
      - /tmp
    security_opt:
      - no-new-privileges:true
    restart: unless-stopped
    networks:
      - rtia-llm
    depends_on:
      - ollama

networks:
  rtia-llm:
    # Private bridge - only the two services on it can see each other.
    driver: bridge

volumes:
  caddy_data:
  caddy_config:
```

**Verify the YAML before deploying:** `docker compose config` parses and
expands the file. A syntax error here is much cheaper to find on your
laptop than after it's running on the NAS.

---

## 2. `Caddyfile`

Drop this next to the compose file. Caddy reads `OLLAMA_AUTH_TOKEN` from
the environment so the token isn't in the Caddyfile itself (the file is
mounted read-only and may end up in backups; the env var is process-local).

```caddyfile
# Listens on :11435 inside the container; the compose file maps that to
# :11435 on the host. Forwards to ollama:11434 (Docker service name) over
# the private bridge.
#
# Auth gate: every request must carry `Authorization: Bearer <token>`.
# A request without the header (or with the wrong token) gets 401 - no
# proxy pass happens, Ollama never sees the request.

:11435 {
    # Structured logging so you can audit who hit what and when. The
    # token itself is never logged - Caddy redacts the Authorization
    # header by default.
    log {
        output stdout
        format console
        level INFO
    }

    # Bearer-token gate. The `forward_auth` directive isn't right here
    # because we don't have a separate auth service; we just check the
    # header value against the env-var token via the `vars` directive
    # and the `@authorized` matcher.
    @authorized header Authorization "Bearer {$OLLAMA_AUTH_TOKEN}"

    handle @authorized {
        reverse_proxy ollama:11434 {
            # Pass through long-lived streaming responses. Ollama's
            # /api/generate streams tokens as they're produced.
            flush_interval -1
        }
    }

    handle {
        # Anything not matching @authorized gets 401. Don't include the
        # token in the error body - just a bare 401.
        respond "Unauthorized" 401
    }
}
```

### Generating the token

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

The compose + Caddyfile above give you authenticated remote Ollama. The
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
var, or the Caddyfile matcher isn't catching the no-header case. Check
`docker compose logs caddy` and re-read the Caddyfile.

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
