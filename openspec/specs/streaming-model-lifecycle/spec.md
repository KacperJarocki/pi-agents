# streaming-model-lifecycle Specification

## Purpose

Keeps live threat scoring compatible with periodically trained device baselines while preserving model provenance and safe recovery.

## Requirements

### Requirement: Periodic window-compatible training
The system SHALL train device detection models on configurable historical lookback using the same window durations, feature definitions, and sampling policy as live scoring; daily training SHALL be supported.

#### Scenario: Daily scheduled refresh
- **WHEN** a scheduled training run completes with sufficient valid history
- **THEN** it produces versioned model candidates per supported device and horizon without interrupting live scoring

#### Scenario: Insufficient baseline history
- **WHEN** a device lacks enough valid samples for a horizon
- **THEN** no unvalidated model for that horizon is activated and the device's coverage status is visible

### Requirement: Selected primary model drives live ML decisions
The system SHALL evaluate live window features with an active, compatible primary model for their device and horizon; shadow-model research SHALL NOT silently change production risk or alert decisions.

#### Scenario: Live feature change
- **WHEN** a compatible primary model exists and a device's live features change
- **THEN** its updated primary score and model version are available to the detection decision without waiting for the next training run

#### Scenario: No compatible model
- **WHEN** no active model matches the live feature and horizon versions
- **THEN** the system reports reduced ML coverage and does not label an incompatible score as a primary-model detection

### Requirement: Safe version activation and comparison
The system SHALL record model and feature versions for every score and SHALL support validating a candidate before activation and restoring the previous compatible version.

#### Scenario: Failed candidate validation
- **WHEN** a new model fails compatibility or quality validation
- **THEN** live scoring continues with the previous compatible model

#### Scenario: Dual-run quality comparison
- **WHEN** old and new pipelines process the same labeled research interval
- **THEN** results can be compared by model, horizon, version, detection delay, and false-positive/false-negative outcome without mixing shadow and production decisions
