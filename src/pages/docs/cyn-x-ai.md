# CYN-X AI

> Human-centered local intelligence by BARKLY LABS.

**Status:** Active Development  
**Project:** CYN-X  
**Organization:** BARKLY LABS  
**Primary Model:** `cyn-x:latest`  
**Inference Runtime:** Ollama  
**API Layer:** FastAPI  
**Frontend:** Astro + TypeScript

---

## 1. What Is CYN-X?

CYN-X is the intelligent-system project developed by BARKLY LABS.

It is not intended to be only another chatbot.

CYN-X is being developed as a human-centered AI system that can work alongside a person, their projects, their tools, their software, and eventually their physical environment.

The central design principle is:

> **Technology should adapt to humans.**

CYN-X explores what happens when an AI system is designed around an ongoing relationship between:

- people;
- projects;
- tools;
- software;
- hardware;
- environments;
- knowledge;
- interfaces.

The long-term goal is an ecosystem in which one intelligent system can operate through many different interfaces.

> **One intelligent ecosystem → many interfaces.**

---

# 2. Design Philosophy

CYN-X follows the broader BARKLY LABS philosophy.

## Humans are not machines

People have different:

- energy levels;
- abilities;
- working styles;
- interests;
- needs;
- limitations;
- communication preferences.

CYN-X should therefore reduce unnecessary effort rather than create additional burdens.

---

## Accessibility is foundational

Accessibility should be considered during system design rather than added after the system is finished.

This includes:

- readable interfaces;
- understandable terminology;
- clear navigation;
- predictable layouts;
- progressive disclosure;
- reduced cognitive load;
- useful visual hierarchy;
- understandable system feedback.

CYN-X should be powerful without requiring a person to understand every internal mechanism before they can use it.

---

## Technology should remain understandable

CYN-X should not become an unexplained black box.

Important system behavior should be documented.

Documentation is part of engineering.

The goal is for people to understand:

- what CYN-X is doing;
- what tools it is using;
- what information it has;
- what information it does not have;
- where decisions are coming from;
- what limitations exist.

---

# 3. Current Architecture

The current CYN-X development stack is organized around several layers.

```text
┌──────────────────────────────────────────┐
│              BARKLY LABS                 │
│          CYN-X User Experience           │
└────────────────────┬─────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────┐
│          Astro + TypeScript              │
│            Web Interface                 │
└────────────────────┬─────────────────────┘
                     │
                     │ HTTP / fetch
                     ▼
┌──────────────────────────────────────────┐
│               FastAPI                    │
│             API Layer                    │
└────────────────────┬─────────────────────┘
                     │
                     │ local inference
                     ▼
┌──────────────────────────────────────────┐
│                Ollama                    │
│          Local Model Runtime             │
└────────────────────┬─────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────┐
│             cyn-x:latest                 │
│              CYN-X Model                 │
└──────────────────────────────────────────┘