# Security Policy

## Supported versions

RTIA is pre-1.0. Only the `main` branch receives security fixes. No patch releases are issued for historical tags.

| Version | Supported |
|---------|-----------|
| `main` | Yes |
| Tagged releases | No |

## Reporting a vulnerability

**Preferred channel - GitHub Security Advisories**

Open a private advisory at:
[https://github.com/augustineuzokwe/rtia/security/advisories/new](https://github.com/augustineuzokwe/rtia/security/advisories/new)

GitHub keeps the report confidential until disclosure is coordinated. Do not open a public issue or pull request for security vulnerabilities.

**Fallback contact**

If GitHub Security Advisories are unavailable, email **augustine.uzokwe@gmail.com** with the subject line `[RTIA SECURITY] <short description>`.

## Response SLO

| Milestone | Target |
|-----------|--------|
| Acknowledgment | Within 72 hours of report |
| Triage / severity assignment | Within 7 days |
| Fix or mitigation | Best effort; coordinated with reporter |

We will keep the reporter informed at each milestone and coordinate public disclosure timing with them.

## Scope

The following are **in scope**:

| Area | Reference |
|------|-----------|
| Agent prompt-injection (user-controlled text influencing agent behaviour) | , `suspicious_input` flag |
| LLM output rendered without sanitization (XSS, markup injection) | , `docs/adr-0009-llm-fallback.md` |
| Runtime secret leakage via agent inputs | , secret-regex blocker |
| PII exfiltration through LangSmith tracing in production | , `docs/adr-0008-pii-langsmith.md` |
| Silent LLM-error fallbacks masking failures | , `docs/adr-0009-llm-fallback.md` |
| Pipeline graph correctness (wrong agent order producing incorrect artifacts) | Core pipeline |
| Eval suite ground-truth poisoning (malicious samples biasing benchmarks) | `evals/` directory |

The following are **out of scope** for this repository:

- Vulnerabilities in the Gemini model itself - report to [Google](https://bughunters.google.com/)
- Vulnerabilities in LangChain / LangGraph upstream - report to [their security page](https://github.com/langchain-ai/langchain/security)
- Customer-deployment hygiene (self-hosted infrastructure, key management)
- Denial-of-service via LLM cost amplification (no rate-limiting is promised pre-1.0)
- Social engineering of maintainers

## Security architecture references

- [`docs/adr-0008-pii-langsmith.md`](docs/adr-0008-pii-langsmith.md) - ADR refusing LangSmith tracing in production to prevent PII leakage
- [`docs/adr-0009-llm-fallback.md`](docs/adr-0009-llm-fallback.md) - ADR requiring structured failure instead of silent degradation
- [`GUARDRAILS.md`](GUARDRAILS.md) - Behavioural policies enforced by agents, each mapped to its agent / prompt / test
