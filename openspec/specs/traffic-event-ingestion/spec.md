# traffic-event-ingestion Specification

## Purpose

Provides a replayable stream of gateway-observed device traffic with trustworthy event times so both live and historical detection use the same evidence.

## Requirements

### Requirement: Timestamped device traffic
The system SHALL emit versioned traffic events with stable event identity, gateway and device identity, packet observation time, and ingest time.

#### Scenario: Captured device packet
- **WHEN** a gateway observes a packet that can be attributed to a device
- **THEN** downstream consumers receive a traffic event with the packet observation timestamp and identity needed to join that device's events in order

#### Scenario: Replay of a captured packet
- **WHEN** the same captured event is delivered again after recovery
- **THEN** its stable identity enables downstream consumers to avoid counting it twice

### Requirement: Durable delivery across broker interruption
The system SHALL report ingestion health and preserve unacknowledged events within a bounded local backlog until they can be safely published or an explicit loss condition is reported.

#### Scenario: Temporary broker outage
- **WHEN** the event broker is temporarily unavailable
- **THEN** the gateway queues captured events within its configured capacity and publishes them on recovery without silent loss

#### Scenario: Local capacity exhausted
- **WHEN** the bounded backlog cannot accept another event
- **THEN** the system exposes an explicit loss/degraded signal with a count and time range rather than claiming complete coverage

### Requirement: Replayable event history
The system SHALL make retained traffic events available for deterministic reprocessing with their original event times and identities.

#### Scenario: Reprocess a research interval
- **WHEN** an operator replays a retained traffic interval
- **THEN** downstream feature extraction receives the original event identity and observation time for each retained event
