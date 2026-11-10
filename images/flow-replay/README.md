# flow-replay

Copies the last 24 hours of `traffic.flows.v2` from the prod Kafka
(`iot-security`) to the dev Kafka (`iot-security-dev`), so dev runs the
detection job, projector and `/live` page on real traffic. It runs as the
CronJob `pi-agents-flow-replay` in dev, daily at 05:00 Europe/Warsaw
(`flowReplay` in the chart, enabled in `k8s/envs/dev/values.yaml`).

- Records are copied byte for byte (key = device key, value, timestamp,
  headers) plus a `pi-agents-replayed-from: prod` header. Event times are not
  rewritten: the window ends at the start of the run, so daily runs cover
  adjacent windows and Flink sees each device's events in order.
- Prod is only read: partitions are assigned by hand, offsets never committed.
- `MAX_RATE` (records/s, default 2000) keeps the single dev TaskManager from
  drowning; `MAX_MESSAGES` caps a run (0 = whole window).
- Re-running the same window within minutes is harmless (Flink dedupes by
  event ID); later reruns land in `traffic.late.v2`.

Run it by hand:

```bash
kubectl -n iot-security-dev create job --from=cronjob/pi-agents-flow-replay flow-replay-manual
kubectl -n iot-security-dev logs -f job/flow-replay-manual
```

The last log line is a JSON summary (`copied`, `bytes`, per-partition counts,
`delivery_errors`). The job exits non-zero when any record was not delivered.
