# Security Policy

## Supported Versions

Security fixes are provided for the latest stable Agetha Mod release.

| Version | Supported |
| --- | --- |
| 5.7.x | :white_check_mark: |
| 5.6.x and older | :x: |

The `main` branch may contain unreleased security hardening, but it is not a supported end-user release. Users should upgrade to the latest stable release before reporting an issue that may already have been fixed.

## Reporting a Vulnerability

**Do not report privately exploitable vulnerabilities in a public GitHub issue.**

This repository has GitHub Private Vulnerability Reporting enabled. For vulnerabilities that could expose credentials or private data, bypass a security boundary, or provide a realistic path to code execution or unauthorized OS actions, use:

**Repository → Security → Advisories → Report a vulnerability**

Public issues may still be used for security hardening concerns that are safe to discuss openly. Use the repository's **Security** issue template and keep all evidence redacted.

### Please include

Provide enough information to reproduce and assess the problem without exposing unrelated private data:

- affected Agetha version and commit SHA, if known;
- operating system and architecture;
- AI backend or provider involved, if relevant;
- affected feature or configuration;
- expected security boundary or behavior;
- observed behavior and realistic impact;
- minimal reproduction steps or a non-weaponized proof of concept;
- sanitized logs, screenshots, or traces where useful;
- any mitigation or fix you have already identified.

Never include API keys, tokens, passwords, cookies, recovery codes, private prompts, unredacted OCR content, or other users' data.

## Security Scope

Examples of issues that are especially relevant to Agetha include:

- bypassing Command Guard, confirmation, or protected-process restrictions;
- unauthorized command execution, process control, or window control;
- file-system boundary bypasses, unsafe path handling, traversal, or symlink/reparse-point issues;
- exposure of API credentials, provider configuration, or sensitive local data;
- OCR, screenshot, excluded-window, or privacy-boundary bypasses;
- prompt injection or tool injection that crosses an intended execution or consent boundary;
- unsafe handling of web, document, memory, tool, or other untrusted external context;
- startup, launcher, privilege, or persistence behavior that exceeds documented consent;
- dependency or supply-chain vulnerabilities that materially affect Agetha users.

A model producing an incorrect, strange, or unwanted conversational response by itself is generally not a security vulnerability unless it also crosses a documented security, privacy, execution, or consent boundary.

## Out of Scope

The following are generally outside this policy unless they lead to a concrete Agetha security impact:

- provider outages, rate limits, or service-side behavior outside this repository's control;
- unsupported platforms or historical compatibility code, including macOS;
- attacks that require the reporter to first fully compromise the user's operating-system account, unless Agetha then exposes an additional security boundary;
- denial-of-service testing against third-party AI providers or services;
- social engineering with no underlying software vulnerability.

## Safe Research Expectations

Please test only on systems, accounts, files, and credentials you own or have explicit permission to use.

Do not intentionally access other users' data, disrupt third-party services, publish live credentials, or continue testing after discovering unintended sensitive data exposure. Use the minimum proof necessary to demonstrate the issue.

## What Happens After a Report

Reports are reviewed for reproducibility, impact, affected versions, and whether an intended security boundary was crossed. The maintainer may ask for additional details or a reduced reproduction case.

If a report is accepted, the issue may be fixed privately first and documented in a release note or GitHub Security Advisory when appropriate. If it is declined or treated as a non-security bug or hardening request, the reason will be explained when practical.

Please allow time for a fix to be prepared and released before publicly disclosing details that could put users at risk. Coordinated disclosure is appreciated.

## Security Updates

Security fixes normally target the newest stable release. Older releases may not receive backported fixes, so users of unsupported versions should upgrade to the latest stable version.
