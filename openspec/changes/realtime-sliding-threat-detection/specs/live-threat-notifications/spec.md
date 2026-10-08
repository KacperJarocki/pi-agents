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

### Requirement: Coexistence with legacy alerts during migration
The system SHALL keep the legacy alert feed available unchanged during migration and SHALL label each streaming incident with its source. Streaming incidents SHALL NOT be merged with legacy alerts.

#### Scenario: Same attack detected on both paths
- **WHEN** legacy and streaming detections refer to the same active device attack
- **THEN** the legacy alert remains in the existing alert feed, the streaming incident appears once on the live incidents view with streaming source provenance, and neither view shows duplicates of its own incident

#### Scenario: Streaming path unavailable
- **WHEN** streaming delivery is degraded during the migration
- **THEN** the existing alert feed remains available and the dashboard indicates that live delivery is degraded
