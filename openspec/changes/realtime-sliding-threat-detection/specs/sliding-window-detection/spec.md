# Spec Delta

## Purpose

Detects fast and slow device threats using continuously refreshed observation horizons without waiting for fixed bucket closure.

## ADDED Requirements

### Requirement: Concurrent sliding observation horizons
The system SHALL maintain separate, per-device sliding feature views covering at least the preceding 1 minute and 15 minutes, with explicit window duration, end time, and feature-version metadata.

#### Scenario: New attributed traffic
- **WHEN** a device produces an event
- **THEN** all applicable horizons reflect the new event and exclude events older than their respective durations

#### Scenario: Different event rates
- **WHEN** a device sends a rapid burst or a slow scan spanning several minutes
- **THEN** the short and long horizons remain separately available for detection and comparison

### Requirement: Evaluate new evidence without waiting for closure
The system SHALL evaluate an affected device's eligible live detection state when new evidence changes its window features, without waiting for the end of a 1- or 15-minute interval.

#### Scenario: Scan crosses threshold during a window
- **WHEN** a new packet supplies the evidence required for a scan decision
- **THEN** the system produces a detection state change without waiting for a fixed bucket boundary

#### Scenario: Evidence ages out during silence
- **WHEN** evidence leaves a sliding horizon and no new device packet arrives
- **THEN** the live detection state is reevaluated and can clear or change severity

### Requirement: Corrected and deduplicated detection state
The system SHALL distinguish updated detection state from new incidents and account for replayed or late traffic according to a documented lateness policy.

#### Scenario: Same attack appears in multiple horizons
- **WHEN** the short and long horizons both support the same ongoing attack
- **THEN** the system updates one correlated incident with evidence from both horizons

#### Scenario: Late or repeated event
- **WHEN** an event arrives after its event-time position or is replayed
- **THEN** the event is processed or classified as late under the configured policy and does not create a duplicate incident

### Requirement: Prompt notification after sufficient evidence
The system SHALL publish a detection when new evidence completes a decision, without waiting for a fixed window boundary or periodic inference and UI polling.

#### Scenario: Evidence-completing packet
- **WHEN** a representative rapid or slow attack crosses the configured decision boundary
- **THEN** the resulting incident is pushed to a connected browser through the live path without waiting for the 1- or 15-minute window to close
