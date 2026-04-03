# CLAUDE.md — F1 25 Hue Flag Lights

This file provides context for AI assistants working on this project.

---

## What this project does

Listens to UDP telemetry broadcast by F1 25 on Xbox and drives a Philips Hue light strip to reflect real-time race flag states. Runs on a Raspberry Pi on the same local network as the Xbox and Hue Bridge.

---

## Tech stack

- **Python 3** — single script, no framework
- **Philips Hue local REST API** — direct HTTP calls to the Bridge, no cloud
- **F1 25 UDP telemetry** — EA/Codemasters spec v3, port 20777
- **Raspberry Pi OS** — runs as a systemd service on boot

---

## Key design decisions

### Packet sources
Three packet types are parsed:

| Packet | ID | Used for |
|---|---|---|
| Session | 1 | Safety car status only (`m_safetyCarStatus` at offset 124) |
| Event | 3 | Chequered, red flag, fastest lap, penalty, start lights, DRS, session start/end |
| Participants | 4 | Vehicle index → name map for player identification |
| Car Status | 7 | Per-driver flag (`m_vehicleFiaFlags`) and DRS allowed (`m_drsAllowed`) |

### Why `m_vehicleFiaFlags` and not marshal zone flags
Early versions used marshal zone flags from the Session packet to detect yellow and blue flags. This caused false triggers — yellow fired whenever any zone on track was yellow, and blue fired for any car being lapped, not just the player's car. The fix was to use `m_vehicleFiaFlags` from the Car Status packet, which is specific to each car's position on track.

### Why `m_drsAllowed` and not the `DRSE` event
The `DRSE` event fires when race control opens DRS for the whole field — it doesn't account for whether the player is within 1 second of the car ahead. `m_drsAllowed` in the Car Status packet is per-car and is only `1` when that specific car can actually activate DRS. The DRS flash triggers on the `0 → 1` transition of this field.

### Threading model
A single effect worker thread processes all light changes from a queue. This replaced an earlier model that spawned a new thread per effect, which caused race conditions and the "cannot join current thread" error. A serial number system allows new effects to interrupt running ones cleanly.

### Penalty and fastest lap filtering
Both `PENA` and `FTLP` events include a `vehicleIdx` field. The script filters these against `player_idx` from the packet header (reliable) and the gamertags list (fallback). This prevents other cars' penalties and fastest laps triggering the light.

### Watchdog
A background thread monitors the timestamp of the last Car Status packet. If silent for `packet_timeout` seconds (default 3), it resets `_player_fia` and `_player_drs_allowed` to prevent getting stuck in flashing yellow after the game stops sending data (e.g. quitting mid-race).

### Participant name parsing
`PARTICIPANT_NAME_OFFSET = 7` (not 8 as the spec might suggest from naive struct reading). The name is 32 bytes, null-terminated. On Xbox, names are always driver names for AI cars; for the player's own car it uses the gamertag. Name parsing can be unreliable so the primary player identification method is `vehicle_idx == player_idx` from the packet header.

---

## Packet offsets (F1 25 UDP spec v3)

All verified against the official EA spec. Check these on game updates.

```
Header:               29 bytes (struct "<HBBBBBQfIIBB")
Session SC status:    payload offset 124
Car Status per car:   55 bytes
  m_drsAllowed:       offset 22 (uint8)
  m_vehicleFiaFlags:  offset 28 (int8)  -1=unknown 0=none 1=green 2=blue 3=yellow
Participant per car:  60 bytes
  name:               offset 7, 32 bytes null-terminated
```

---

## Effect priority (highest to lowest)

1. Chequered flag — flashing white, timed
2. Red flag — flashing red, latched until session restart
3. Fastest lap — solid purple, timed, player only
4. Penalty — solid white (via `set_white_color()`), timed, player only
5. Start lights — red pulse per light, holds on 5th, clears on LGOT
6. Safety car / VSC — solid yellow
7. Formation lap — flashing green
8. Yellow flag — flashing yellow (player's sector only)
9. Blue flag — flashing blue (player being lapped only)
10. Green flag — flashing green
11. Off

DRS activatable is a non-interrupting overlay — pulses green then restores the previous effect.

---

## Known issues / gotchas

- **Penalty `PENA` fires multiple times** — the game sends repeat events for the same penalty. Handled by ignoring subsequent events while `_penalty_active` is True.
- **Fastest lap fires during pit stops** — the game incorrectly fires `FTLP` when a car sets its in-lap time in the pits. Handled by checking `vehicle_idx == player_idx`.
- **`set_white()` (colour temperature) may not work on all light strips** — `set_white_color()` uses hue/sat as a fallback for the penalty effect. The chequered flag uses `set_white()` and works correctly on the test hardware (light ID 10).
- **Participant names are sometimes garbled** — the primary identification is always `vehicle_idx == player_idx`, not name matching.
- **Marshal zone flags were abandoned** — do not re-introduce zone flag parsing for yellow/blue detection.

---

## Hardware in use

- Raspberry Pi on the same local network as the Xbox and Hue Bridge
- Philips Hue Bridge (local REST API, no cloud)
- Hue light strip (extended colour light)
- Xbox running F1 25, telemetry pointed directly at Pi IP, port 20777, broadcast off

---

## File structure

```
f1_hue.py        — main script
install.sh       — systemd service setup
requirements.txt — pip dependencies (requests only)
README.md        — end-user setup guide
CHANGELOG.md     — version history
CLAUDE.md        — this file
LICENSE          — project licence
.gitignore       — excludes credentials, caches, editor files
```

---

## Running

```bash
python3 f1_hue.py
```

Or as a service:
```bash
sudo systemctl start f1hue
sudo journalctl -u f1hue -f
```
