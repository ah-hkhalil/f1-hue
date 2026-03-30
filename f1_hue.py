"""
F1 25 → Hue Flag Lights
==========================================================
Listens to F1 25 UDP telemetry and drives a Philips Hue light
to reflect the current flag state in real time.

See README.md for full setup instructions.

Usage:
    python3 f1_hue.py

In-game: Settings → Telemetry Settings
         UDP Telemetry : On
         Broadcast Mode: Off
         IP Address    : 10.0.40.66
         Port          : 20777
"""

import socket
import struct
import requests as req
import threading
import queue
import time
from datetime import datetime

# ─────────────────────────────────────────────
# CONFIG — edit these values before running
# See README.md for how to find each value
# ─────────────────────────────────────────────
config = {
    # IP address of your Philips Hue Bridge (find in your router or Hue app)
    "bridge_ip":             "10.0.40.69",

    # Hue API username — generated during setup (see README.md)
    "hue_user":              "fvFqvEK4MgN5RgrYvOenFcygbiSQoXBkSlCRfCbf",

    # Light ID to control — run the lights discovery command in README.md to find this
    "light_id":              "10",

    # UDP port — must match the port set in the F1 25 telemetry settings
    "udp_port":              20777,

    # Your Xbox gamertag(s) as they appear in-game — used for fastest lap detection
    # Add both if you use multiple accounts, e.g. ["Gamertag1", "Gamertag2"]
    "player_gamertags":      ["Hamilton443866", "Aang5732"],

    # How long (seconds) to flash white after chequered flag
    "chequered_duration":    10,

    # How long (seconds) to show purple after setting fastest lap
    "fastest_lap_duration":  5,

    # How long (seconds) to show white after receiving a penalty
    "penalty_duration":      3,

    # Flash speed in seconds for yellow and blue flags (lower = faster)
    "yellow_flash_speed":    0.3,
    "blue_flash_speed":      0.3,

    # Light brightness: 1 (dim) to 254 (full)
    "brightness":            254,

    # Seconds of silence from the Car Status packet before resetting flag state.
    # Prevents getting stuck in flashing yellow when the game stops sending data.
    "packet_timeout":        3,
}

# ─────────────────────────────────────────────
# PACKET CONSTANTS  (F1 25 UDP spec v3)
# Offsets verified against F1 25 UDP Spec v3 — check on game update
# ─────────────────────────────────────────────
HEADER_FMT  = "<HBBBBBQfIIBB"
HEADER_SIZE = struct.calcsize(HEADER_FMT)

PACKET_SESSION      = 1
PACKET_EVENT        = 3
PACKET_PARTICIPANTS = 4
PACKET_CAR_STATUS   = 7

SESSION_SC_STATUS_OFFSET = 124

SC_NONE      = 0
SC_FULL      = 1
SC_VIRTUAL   = 2
SC_FORMATION = 3

# Car Status packet: 55 bytes per car
# m_vehicleFiaFlags at offset 28 (int8): -1=unknown, 0=none, 1=green, 2=blue, 3=yellow
# m_drsAllowed      at offset 22 (uint8): 0=not allowed, 1=allowed
CAR_STATUS_SIZE    = 55
FIA_FLAG_OFFSET    = 28
DRS_ALLOWED_OFFSET = 22

FIA_UNKNOWN = -1
FIA_NONE    =  0
FIA_GREEN   =  1
FIA_BLUE    =  2
FIA_YELLOW  =  3

PARTICIPANT_SIZE        = 60
PARTICIPANT_NAME_OFFSET = 7
PARTICIPANT_NAME_LEN    = 32

EV_FASTEST_LAP   = b"FTLP"
EV_CHEQUERED     = b"CHQF"
EV_RED_FLAG      = b"RDFL"
EV_SCAR          = b"SCAR"
EV_SESSION_START = b"SSTA"
EV_SESSION_END   = b"SEND"
EV_START_LIGHTS  = b"STLG"
EV_LIGHTS_OUT    = b"LGOT"
EV_PENALTY       = b"PENA"

START_LIGHTS_TIMEOUT = 15

COLORS = {
    "green":  25500,
    "yellow": 12750,
    "red":    0,
    "blue":   46920,
    "purple": 50000,
    "white":  60000,
}
WHITE_SAT = 50
WHITE_CT  = 153

# ─────────────────────────────────────────────
# TERMINAL COLOURS
# ─────────────────────────────────────────────
RESET  = "\033[0m";  BOLD   = "\033[1m";  DIM    = "\033[2m"
GREEN  = "\033[92m"; YELLOW = "\033[93m"; RED    = "\033[91m"
BLUE   = "\033[94m"; CYAN   = "\033[96m"; WHITE  = "\033[97m"
PURPLE = "\033[95m"

def ts():
    return datetime.now().strftime("%H:%M:%S")

def log(colour, label, detail=""):
    print(f"[{ts()}] {colour}{BOLD}{label}{RESET}  {DIM}{detail}{RESET}")

# ─────────────────────────────────────────────
# HUE CONTROL
# ─────────────────────────────────────────────
def _put(payload):
    url = (f"http://{config['bridge_ip']}/api/{config['hue_user']}"
           f"/lights/{config['light_id']}/state")
    try:
        req.put(url, json=payload, timeout=2)
    except Exception as e:
        print(f"  [Hue error] {e}")

def set_color(hue_val, sat=254):
    _put({"on": True, "bri": config["brightness"], "hue": hue_val,
          "sat": sat, "transitiontime": 0})

def set_white():
    """Use colour temperature mode — works on bulbs that support it."""
    _put({"on": True, "bri": config["brightness"], "ct": WHITE_CT,
          "transitiontime": 0})

def set_white_color():
    """Near-white using hue/sat — works on all colour light strips."""
    set_color(COLORS["white"], sat=WHITE_SAT)

def turn_off():
    _put({"on": False})

# ─────────────────────────────────────────────
# SINGLE EFFECT WORKER
# ─────────────────────────────────────────────
_effect_queue  = queue.Queue()
_effect_serial = 0
_serial_lock   = threading.Lock()

def _send_effect(effect_name, **kwargs):
    global _effect_serial
    with _serial_lock:
        _effect_serial += 1
        serial = _effect_serial
    _effect_queue.put((serial, effect_name, kwargs))

def _effect_worker():
    current_serial = 0

    def interrupted():
        return _effect_serial != current_serial

    while True:
        try:
            serial, name, kwargs = _effect_queue.get(timeout=1)
        except queue.Empty:
            continue

        current_serial = serial

        if name == "solid":
            set_color(COLORS[kwargs["color"]])

        elif name == "flash":
            speed_key = kwargs.get("speed_key", "yellow_flash_speed")
            color     = kwargs["color"]
            while not interrupted():
                set_color(COLORS[color])
                end = time.time() + config[speed_key]
                while time.time() < end:
                    if interrupted(): break
                    time.sleep(0.02)
                if interrupted(): break
                turn_off()
                end = time.time() + config[speed_key]
                while time.time() < end:
                    if interrupted(): break
                    time.sleep(0.02)

        elif name == "chequered":
            deadline = time.time() + config["chequered_duration"]
            while not interrupted() and time.time() < deadline:
                set_white()
                end = time.time() + 0.25
                while time.time() < end:
                    if interrupted(): break
                    time.sleep(0.02)
                if interrupted(): break
                turn_off()
                end = time.time() + 0.25
                while time.time() < end:
                    if interrupted(): break
                    time.sleep(0.02)
            if not interrupted():
                turn_off()

        elif name == "fastest_lap":
            set_color(COLORS["purple"])
            deadline = time.time() + config["fastest_lap_duration"]
            while not interrupted() and time.time() < deadline:
                time.sleep(0.05)
            if not interrupted():
                with _state_lock:
                    if _fastest_lap_active:
                        _revert_fastest_lap_state()

        elif name == "penalty":
            set_white_color()
            deadline = time.time() + config["penalty_duration"]
            while not interrupted() and time.time() < deadline:
                time.sleep(0.05)
            if not interrupted():
                with _state_lock:
                    if _penalty_active:
                        _revert_penalty_state()

        elif name == "start_light_pulse":
            num = kwargs["num_lights"]
            set_color(COLORS["red"])
            if num < 5:
                end = time.time() + 0.15
                while time.time() < end:
                    if interrupted(): break
                    time.sleep(0.02)
                if not interrupted():
                    turn_off()

        elif name == "drs_flash":
            restore        = kwargs.get("restore_effect")
            restore_kwargs = kwargs.get("restore_kwargs", {})
            set_color(COLORS["green"])
            end = time.time() + 0.4
            while time.time() < end:
                if interrupted(): break
                time.sleep(0.02)
            if not interrupted():
                if restore == "solid":
                    set_color(COLORS[restore_kwargs["color"]])
                elif restore == "flash":
                    _send_effect("flash", **restore_kwargs)
                elif restore == "off":
                    turn_off()

        elif name == "off":
            turn_off()


def _revert_fastest_lap_state():
    """Must be called with _state_lock held."""
    global _fastest_lap_active, _active_effect
    _fastest_lap_active = False
    _active_effect = None
    _apply()

def _revert_penalty_state():
    """Must be called with _state_lock held."""
    global _penalty_active, _active_effect
    _penalty_active = False
    _active_effect = None
    _apply()


_worker_thread = threading.Thread(target=_effect_worker, daemon=True)
_worker_thread.start()

# ─────────────────────────────────────────────
# SHARED STATE
# ─────────────────────────────────────────────
_state_lock           = threading.Lock()
_sc_status            = SC_NONE
_player_fia           = FIA_NONE
_player_drs_allowed   = 0
_red_flag_active      = False
_chequered_active     = False
_fastest_lap_active   = False
_penalty_active       = False
_start_lights_active  = False
_start_lights_ts      = 0.0
_active_effect        = None
_active_effect_kwargs = {}
_participants         = {}

# Watchdog — timestamp of last Car Status packet received
_last_car_status_ts   = 0.0

# ─────────────────────────────────────────────
# WATCHDOG THREAD
# Monitors Car Status packet arrival. If silent for packet_timeout
# seconds, resets per-driver flag state to prevent getting stuck.
# ─────────────────────────────────────────────
def _watchdog():
    global _player_fia, _player_drs_allowed, _active_effect
    while True:
        time.sleep(1)
        if _last_car_status_ts == 0.0:
            continue   # haven't received any packets yet
        gap = time.time() - _last_car_status_ts
        if gap > config["packet_timeout"]:
            with _state_lock:
                if _player_fia != FIA_NONE or _player_drs_allowed != 0:
                    log(DIM, "Watchdog", f"no Car Status for {gap:.0f}s — resetting flag state")
                    _player_fia         = FIA_NONE
                    _player_drs_allowed = 0
                    _active_effect      = None
                    _apply()

_watchdog_thread = threading.Thread(target=_watchdog, daemon=True)
_watchdog_thread.start()

# ─────────────────────────────────────────────
# FLAG STATE MACHINE
#
# Priority (highest → lowest):
#   1. Chequered flag    — event CHQF
#   2. Red flag          — event RDFL (latched until session restart)
#   3. Fastest lap       — event FTLP (player only, timed)
#   4. Penalty           — event PENA (timed)
#   5. Start lights      — events STLG / LGOT (with timeout fallback)
#   6. Safety car        — session m_safetyCarStatus (full or virtual → solid yellow)
#   7. Formation lap     — session m_safetyCarStatus == 3
#   8. Player FIA flag   — m_vehicleFiaFlags (per-driver, not track-wide)
#   9. Off
#
# DRS flash is a non-interrupting overlay triggered on m_drsAllowed 0→1.
# Watchdog resets FIA flag if Car Status packets stop arriving.
# ─────────────────────────────────────────────

def _apply():
    global _active_effect, _active_effect_kwargs, _start_lights_active

    if _start_lights_active:
        if time.time() - _start_lights_ts > START_LIGHTS_TIMEOUT:
            log(DIM, "Start lights timed out", "LGOT may have been missed")
            _start_lights_active = False

    if _chequered_active:
        effect = "chequered"
    elif _red_flag_active:
        effect = "red"
    elif _fastest_lap_active:
        effect = "fastest_lap"
    elif _penalty_active:
        effect = "penalty"
    elif _start_lights_active:
        return
    elif _sc_status in (SC_FULL, SC_VIRTUAL):
        effect = "yellow_solid"
    elif _sc_status == SC_FORMATION:
        effect = "formation"
    elif _player_fia == FIA_YELLOW:
        effect = "yellow_flash"
    elif _player_fia == FIA_BLUE:
        effect = "blue"
    elif _player_fia == FIA_GREEN:
        effect = "green"
    else:
        effect = "off"

    if effect == _active_effect:
        return
    _active_effect = effect

    if effect == "chequered":
        log(WHITE,  "🏁 CHEQUERED FLAG", "flashing white")
        kw = {}
        _send_effect("chequered", **kw)
    elif effect == "red":
        log(RED,    "🔴 RED FLAG",       "flashing red")
        kw = {"color": "red", "speed_key": "yellow_flash_speed"}
        _send_effect("flash", **kw)
    elif effect == "fastest_lap":
        log(PURPLE, "💜 FASTEST LAP",    "solid purple")
        kw = {}
        _send_effect("fastest_lap", **kw)
    elif effect == "penalty":
        log(WHITE,  "⬜ PENALTY",        "solid white")
        kw = {}
        _send_effect("penalty", **kw)
    elif effect == "yellow_solid":
        log(YELLOW, "🟡 SAFETY CAR",     "solid yellow")
        kw = {"color": "yellow"}
        _send_effect("solid", **kw)
    elif effect == "formation":
        log(GREEN,  "🟢 FORMATION LAP",  "flashing green")
        kw = {"color": "green", "speed_key": "yellow_flash_speed"}
        _send_effect("flash", **kw)
    elif effect == "yellow_flash":
        log(YELLOW, "🟡 YELLOW FLAG",    "flashing yellow — player's sector")
        kw = {"color": "yellow", "speed_key": "yellow_flash_speed"}
        _send_effect("flash", **kw)
    elif effect == "blue":
        log(BLUE,   "🔵 BLUE FLAG",      "flashing blue — player being lapped")
        kw = {"color": "blue", "speed_key": "blue_flash_speed"}
        _send_effect("flash", **kw)
    elif effect == "green":
        log(GREEN,  "🟢 GREEN FLAG",     "flashing green")
        kw = {"color": "green", "speed_key": "yellow_flash_speed"}
        _send_effect("flash", **kw)
    elif effect == "off":
        log(DIM,    "⚫ NO FLAG",        "light off")
        kw = {}
        _send_effect("off", **kw)

    _active_effect_kwargs = kw

def _trigger_drs_flash():
    """
    Fire a non-interrupting green flash for DRS activatable.
    Must be called without _state_lock held.
    """
    with _state_lock:
        restore    = _active_effect
        restore_kw = dict(_active_effect_kwargs)

    if restore in ("chequered", "fastest_lap", "penalty", "red"):
        return

    log(GREEN, "🟢 DRS ACTIVATABLE", "within 1s — green flash")

    if restore in ("green", "formation", "yellow_flash", "blue", "red"):
        restore_worker = "flash"
    elif restore == "yellow_solid":
        restore_worker = "solid"
        restore_kw = {"color": "yellow"}
    else:
        restore_worker = restore or "off"

    _send_effect("drs_flash",
                 restore_effect=restore_worker,
                 restore_kwargs=restore_kw)

# ─────────────────────────────────────────────
# PACKET PARSERS
# ─────────────────────────────────────────────
def parse_session(payload):
    global _sc_status
    if len(payload) < SESSION_SC_STATUS_OFFSET + 1:
        return
    sc = payload[SESSION_SC_STATUS_OFFSET]
    with _state_lock:
        _sc_status = sc
        _apply()

def parse_car_status(payload, player_idx):
    global _player_fia, _player_drs_allowed, _last_car_status_ts
    if player_idx >= 22:
        return

    fia_offset = player_idx * CAR_STATUS_SIZE + FIA_FLAG_OFFSET
    if fia_offset >= len(payload):
        return
    fia = struct.unpack_from("<b", payload, fia_offset)[0]

    drs_offset = player_idx * CAR_STATUS_SIZE + DRS_ALLOWED_OFFSET
    if drs_offset >= len(payload):
        return
    drs_allowed = payload[drs_offset]

    # Update watchdog timestamp on every valid Car Status packet
    _last_car_status_ts = time.time()

    with _state_lock:
        prev_drs            = _player_drs_allowed
        _player_fia         = fia
        _player_drs_allowed = drs_allowed
        _apply()

    # Trigger DRS flash on 0 → 1 transition, outside the lock
    if prev_drs == 0 and drs_allowed == 1:
        _trigger_drs_flash()

def parse_participants(payload):
    global _participants
    if len(payload) < 1:
        return
    num_cars = payload[0]
    new_map = {}
    for i in range(min(num_cars, 22)):
        offset = 1 + i * PARTICIPANT_SIZE + PARTICIPANT_NAME_OFFSET
        if offset + PARTICIPANT_NAME_LEN > len(payload):
            break
        raw  = payload[offset: offset + PARTICIPANT_NAME_LEN]
        name = raw.split(b"\x00")[0].decode("utf-8", errors="replace")
        new_map[i] = name
    with _state_lock:
        _participants = new_map

def parse_event(payload, player_idx):
    global _red_flag_active, _chequered_active, _fastest_lap_active
    global _penalty_active, _start_lights_active, _start_lights_ts, _active_effect

    if len(payload) < 4:
        return
    code = bytes(payload[0:4])

    if code == EV_PENALTY:
        with _state_lock:
            if _penalty_active:
                return
            log(WHITE, "⬜ PENALTY ISSUED", "solid white")
            _penalty_active = True
            _apply()

    elif code == EV_START_LIGHTS and len(payload) >= 5:
        num_lights = payload[4]
        log(RED, f"🔴 START LIGHT {num_lights}/5", f"pulse {num_lights}")
        with _state_lock:
            _start_lights_active = True
            _start_lights_ts     = time.time()
            _active_effect       = "start_lights"
        _send_effect("start_light_pulse", num_lights=num_lights)

    elif code == EV_LIGHTS_OUT:
        log(GREEN, "🟢 LIGHTS OUT", "go go go!")
        with _state_lock:
            _start_lights_active = False
            _active_effect       = None
            _apply()

    elif code == EV_FASTEST_LAP and len(payload) >= 6:
        vehicle_idx = payload[4]
        with _state_lock:
            driver_name = _participants.get(vehicle_idx, "")
        is_player = (vehicle_idx == player_idx or
                     driver_name in config["player_gamertags"])
        if is_player:
            log(PURPLE, "💜 FASTEST LAP!", f"vehicle {vehicle_idx}")
            with _state_lock:
                _fastest_lap_active = True
                _apply()
        else:
            log(DIM, f"Fastest lap: {driver_name or vehicle_idx}", "(not your driver)")

    elif code == EV_CHEQUERED:
        with _state_lock:
            _chequered_active = True
            _apply()

    elif code == EV_RED_FLAG:
        with _state_lock:
            _red_flag_active = True
            _apply()

    elif code in (EV_SESSION_START, EV_SESSION_END):
        with _state_lock:
            _red_flag_active     = False
            _chequered_active    = False
            _fastest_lap_active  = False
            _penalty_active      = False
            _start_lights_active = False
            _active_effect       = None
            _apply()

    elif code == EV_SCAR and len(payload) >= 6:
        sc_names = {0: "No SC", 1: "Full SC", 2: "Virtual SC", 3: "Formation Lap SC"}
        ev_names = {0: "Deployed", 1: "Returning", 2: "Returned", 3: "Resume Race"}
        log(YELLOW, "Safety Car event",
            f"{sc_names.get(payload[4], '?')} → {ev_names.get(payload[5], '?')}")

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    print(f"\n{BOLD}{CYAN}╔══════════════════════════════════════════╗{RESET}")
    print(f"{BOLD}{CYAN}║       F1 25 → Hue Flag Lights            ║{RESET}")
    print(f"{BOLD}{CYAN}╚══════════════════════════════════════════╝{RESET}")
    print(f"  Bridge  : {config['bridge_ip']}   Light : {config['light_id']}")
    print(f"  UDP     : port {config['udp_port']}")
    print(f"  Players : {', '.join(config['player_gamertags'])}")
    print(f"  Ctrl+C to quit\n")
    print(f"{DIM}Waiting for packets...{RESET}\n")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", config["udp_port"]))
    sock.settimeout(1.0)

    packets = 0
    try:
        while True:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue

            if packets == 0:
                print(f"{GREEN}✓ Receiving telemetry from {addr[0]}{RESET}\n")
            packets += 1

            if len(data) < HEADER_SIZE:
                continue

            try:
                hdr = struct.unpack_from(HEADER_FMT, data, 0)
            except struct.error:
                continue

            packet_id  = hdr[5]
            player_idx = hdr[10]
            payload    = data[HEADER_SIZE:]

            if   packet_id == PACKET_SESSION:      parse_session(payload)
            elif packet_id == PACKET_EVENT:        parse_event(payload, player_idx)
            elif packet_id == PACKET_PARTICIPANTS: parse_participants(payload)
            elif packet_id == PACKET_CAR_STATUS:   parse_car_status(payload, player_idx)

    except KeyboardInterrupt:
        print(f"\n{BOLD}Stopping...{RESET}")
        _send_effect("off")
        time.sleep(0.5)
        print("Light off. Bye!\n")
    finally:
        sock.close()

if __name__ == "__main__":
    main()
