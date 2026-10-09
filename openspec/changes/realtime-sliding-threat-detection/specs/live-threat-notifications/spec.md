# Spec Delta

## Purpose

Delivers timely, consistent threat state to the dashboard without requiring polling or generating duplicate alerts for a single attack.

## ADDED Requirements

### Requirement: Persisted incident state changes
The system SHALL record each new, updated, or resolved incident with stable identity and evidence before broadcasting it to connected clients.

#### Scenario: First detection
- **WHEN** live detection first identifies a threat for a device
- **THEN** one incident is stored with the device, evidence, horizons, rule ID and version, and detection time

#### Scenario: Further evidence from another horizon
- **WHEN** the ongoing threat gains evidence or changes severity
- **THEN** the same incident is updated rather than producing a new incident notification

### Requirement: Prompt browser delivery and recovery
The system SHALL push new incident state changes to connected dashboards and allow disconnected clients to recover missed changes from durable state.

#### Scenario: Connected dashboard
- **WHEN** an incident state change is committed
- **THEN** connected clients receive it without waiting for the periodic alert-feed refresh

#### Scenario: Browser reconnects
- **WHEN** a dashboard reconnects after missing incident updates
- **THEN** it can resume from its last acknowledged event or refresh a consistent current incident view without losing active alerts

### Requirement: Visible degraded delivery and coverage
The system SHALL show on the live incidents page when live delivery is interrupted or when ingestion reports loss or unattributed traffic, instead of implying complete coverage.

#### Scenario: Live connection lost
- **WHEN** the browser's live connection to the incident service drops
- **THEN** the page indicates that live delivery is degraded, keeps showing the last known incident state, and clears the indicator after it resumes and catches up

#### Scenario: Ingestion loss reported
- **WHEN** the latest ingestion health reports dropped or unattributed events
- **THEN** the page shows the affected gateway, the count and the time range
