---
name: Security
about: Report a security concern or hardening issue safely
title: "[Security]: "
labels: ''
assignees: ''
---

<!--
Use this template for security concerns that are safe to discuss publicly.

DO NOT post:
- API keys, tokens, passwords, cookies, recovery codes, or private data
- working exploit code or weaponized proof-of-concept steps
- secrets copied from logs, screenshots, config files, or environment files
- private vulnerability details that would put users at immediate risk

If the issue is a privately exploitable vulnerability, use GitHub's private security reporting / Security Advisory flow for this repository instead of a public issue.
-->

## Security concern summary

<!-- Describe the concern at a high level without including secrets or weaponized exploit details. -->


## Impact

<!-- What could an attacker, malicious prompt, unsafe command, compromised dependency, or misconfiguration potentially affect? -->

- [ ] Command execution / Command Guard
- [ ] File system access
- [ ] Process or window control
- [ ] OCR / screen monitoring / privacy
- [ ] AI prompt / tool injection
- [ ] API credentials / provider configuration
- [ ] Memory / stored local data
- [ ] Web RAG / remote content handling
- [ ] Dependency / supply-chain risk
- [ ] Startup / launcher / privilege behavior
- [ ] Other: 

## Affected version / environment

- **Agetha version:** 
- **Commit SHA (if known):** 
- **OS:** 
- **Architecture:** <!-- x64 / ARM64 / other -->
- **AI backend:** <!-- Groq / OpenRouter / Ollama / other -->
- **Relevant feature/configuration:** 

## Safe reproduction summary

<!-- Give only enough information to confirm the issue without publishing a harmful exploit. If detailed reproduction would enable abuse, leave it out and report privately instead. -->

1. 
2. 
3. 

## Expected security behavior

<!-- What protection or boundary did you expect Agetha to enforce? -->


## Observed security behavior

<!-- What happened instead? Keep this non-sensitive. -->


## Severity estimate

<!-- Your estimate only; maintainers may reclassify it. -->

- [ ] Low — hardening / defense-in-depth
- [ ] Moderate — security boundary weakness with limited impact
- [ ] High — meaningful compromise possible under realistic conditions
- [ ] Critical — immediate widespread compromise or secret exposure risk
- [ ] Unsure

## Suggested mitigation

<!-- Optional. Describe a defensive fix or safer design direction. -->


## Redacted evidence

<!-- Optional. Attach only sanitized logs/screenshots. Remove secrets, private paths, personal text, and exploit payloads. -->

```text
Paste redacted evidence here
```

## Disclosure check

- [ ] I have not included secrets, credentials, or private user data.
- [ ] I have not included working exploit code or weaponized steps.
- [ ] I understand that privately exploitable vulnerabilities should be reported through GitHub's private security reporting / Security Advisory flow instead of this public issue.
