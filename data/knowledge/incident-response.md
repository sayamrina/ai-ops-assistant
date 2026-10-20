# Incident Response for the Patient Platform

Owner: Engineering · Last review: 2026-09-05

## Severity levels
- SEV1: The platform is down or patient data may be exposed. Page the on-call engineer immediately and open the Teams channel #incident.
- SEV2: An important feature does not work for many users (for example, photo upload or payment). Respond within 30 minutes.
- SEV3: A minor bug with a workaround. Create a Jira ticket in the project PLAT; handle within the sprint.

## Communication
For SEV1 and SEV2, post a status update every 30 minutes in #incident. Customer-facing messages are written by Patient Support, not by engineers.

## After the incident
A blameless post-mortem is written in Confluence within 5 working days for every SEV1 and SEV2.
