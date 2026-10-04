# Changelog

## 0.1.0

First release.

- Six scenarios, each labelled with whether software watching that day should
  raise an alarm: `normal`, `fall`, `away`, `long_sleep`, `visitor_while_out`
  and `camera_offline`.
- Eight devices across two classes. Devices near a door see somebody leaving;
  devices inside see somebody living. That split is what makes an absence
  distinguishable from a person who has stopped moving, and it is the reason
  this exists.
- Days are built from where the person is, minute by minute, rather than from
  events directly. Events fall out of occupancy, which is why an absence needs
  no special handling: nothing inside the house fires while the zone is `out`.
- Motion is sampled from a Poisson process per device, with a per-device
  cooldown, so a busy room does not produce an unbroken stream.
- Seeded throughout. A given seed always produces the same stream.
- Event ids are a hash of the event's own timestamp, device and kind, so the
  same event always carries the same id across runs and consumers can
  deduplicate on it safely.
- JSON Lines output, with an optional device manifest, or use it as a library.
- Forty one checks covering ordering, determinism, cooldown, id stability and
  the behaviour each scenario promises.
