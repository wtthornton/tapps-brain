# tapps-brain case studies

Production adopter case studies — how real teams run tapps-brain in their
agent fleets.

> **Want to be listed?** See [How to add yours](#how-to-add-yours) below.

---

## Published case studies

*No case studies published yet.* tapps-brain is actively looking for
early adopters. See the [Early Adopters section in the README](../../README.md#early-adopters)
for details on what you get and how to get in touch.

---

## What a case study covers

Each case study answers five questions:

| Section | What we want to know |
|---------|---------------------|
| **Deployment shape** | Container count, Postgres sizing, replicas, which docker-compose/K8s flavour |
| **Memory volume** | Approximate entry count per agent, total entries at peak, growth rate |
| **Agent topology** | Number of agents, agent types (coding, support, research, …), Hive usage |
| **Multi-tenancy setup** | `project_id` scheme, profile per project, RLS confirmation |
| **Measured outcome** | One concrete metric: latency, cost vs. alternative, recall quality, agent behaviour change |

You do not have to share business-sensitive numbers. Approximate ranges and
qualitative outcomes ("recall improved noticeably, we removed our ad-hoc
JSONL log") are fine. We'll draft the case study from a 20-minute call or
a filled-in template; you review before anything is published.

---

## How to add yours

1. **Open an issue** titled `Adopter: <your project name>` in the
   [tapps-brain GitHub repo](https://github.com/wtthornton/tapps-brain/issues).
   Briefly describe your deployment (agent type, rough scale, what you're
   storing). We'll respond within a few days.

2. **Or email** `tapp.thornton@gmail.com` with subject `tapps-brain adopter`.

3. We draft a case study from the template below and send it to you for
   review. You approve (or request changes) before anything is merged.

4. Once approved, the case study is published here and you're listed in the
   README "Early Adopters" section and in the
   [memory-systems scorecard](../research/memory-systems-scorecard.md).

---

## Template

Copying [TEMPLATE.md](TEMPLATE.md) is optional — just fill in the five
sections above in whatever format works for you.

---

## Scorecard impact

tapps-brain's D10 Momentum score is currently **1/5** because there are no
named external adopters. Three named production adopters moves the score to
**3/5** (+4.0 weighted points), which would push the overall total from
~80.6 toward ~84.6.

The rubric does not reward paid stars, fake adopters, or projects that only
used tapps-brain in a demo. The scoring is honest and conservative.
