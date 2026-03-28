"""
F1 25 → Hue Flag Lights
==========================================================
Listens to F1 25 UDP telemetry, drives a Philips Hue light.

Usage:
    python3 f1_hue.py

In-game: Settings → Telemetry Settings → UDP Telemetry: On
         Broadcast Mode: Off, Port: 20777
"""

import socket
import struct
import requests as req
import threading
import time
from datetime import datetime

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
config = {
    "bridge_ip":             "10.0.40.69",
    "hue_user":              "fvFqvEK4MgN5RgrYvOenFcygbiSQoXBkSlCRfCbf",
    "light_id":              "10",
    "udp_port":              20777,
    "player_gamertags":      ["Hamilton443866", "Aang5732"],
    "chequered_duration":    10,
    "fastest_lap_duration":  10,
    "yellow_flash_speed":    0.3,
    "blue_flash_speed":      0.3,
    "brightness":            127,
}

# ─────────────────────────────────────────────
# PACKET CONSTANTS  (F1 25 UDP spec v3)
# ─────────────────────────────────────────────
HEADER_FMT  = "<HBBBBBQfIIBB"
HEADER_SIZE = struct.calcsize(HEADER_FMT)

PACKET_SESSION      = 1
PACKET_EVENT        = 3
PACKET_PARTICIPANTS = 4
PACKET_CAR_STATUS   = 7

# Session packet — used only for safety car status now
SESSION_SC_STATUS_OFFSET = 124

SC_NONE      = 0
SC_FULL      = 1
SC_VIRTUAL   = 2
SC_FORMATION = 3

# Car Status packet — 55 bytes per car
# m_vehicleFiaFlags at offset 28 (int8, signed)
# -1 = invalid/unknown, 0 = none, 1 = green, 2 = blue, 3 = yellow
CAR_STATUS_SIZE = 55
FIA_FLAG_OFFSET = 28

FIA_UNKNOWN = -1
FIA_NONE    =  0
FIA_GREEN   =  1
FIA_BLUE    =  2
FIA_YELLOW  =  3

PARTICIPANT_SIZE        = 60
PARTICIPANT_NAME_OFFSET = 8
PARTICIPANT_NAME_LEN    = 32

EV_FASTEST_LAP   = b"FTLP"
EV_CHEQUERED     = b"CHQF"
EV_RED_FLAG      = b"RDFL"
EV_SCAR          = b"SCAR"
EV_SESSION_START = b"SSTA"
EV_SESSION_END   = b"SEND"

COLORS = {
    "green":  25500,
    "yellow": 12750,
    "red":    0,
    "blue":   46920,
    "purple": 50000,
}
WHITE_CT = 153

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
    _put({"on": True, "bri": config["brightness"], "ct": WHITE_CT,
          "transitiontime": 0})

def turn_off():
    _put({"on": False})

# ─────────────────────────────────────────────
# EFFECT ENGINE
# ─────────────────────────────────────────────
_effect_stop   = threading.Event()
_effect_thread = None
_effect_lock   = threading.Lock()

def _run_effect(fn):
    global _effect_thread, _effect_stop
    with _effect_lock:
        _effect_stop.set()
        if _effect_thread and _effect_thread.is_alive():
            _effect_thread.join(timeout=2)
        _effect_stop = threading.Event()
        t = threading.Thread(target=fn, args=(_effect_stop,), daemon=True)
        _effect_thread = t
    t.start()

def effect_solid(color_name):
    def _fn(stop):
        set_color(COLORS[color_name])
    _run_effect(_fn)

def effect_flash(color_name, speed_key="yellow_flash_speed"):
    def _fn(stop):
        while not stop.is_set():
            set_color(COLORS[color_name])
            if stop.wait(config[speed_key]): break
            turn_off()
            stop.wait(config[speed_key])
    _run_effect(_fn)

def effect_chequered():
    def _fn(stop):
        deadline = time.time() + config["chequered_duration"]
        while not stop.is_set() and time.time() < deadline:
            set_white()
            if stop.wait(0.25): break
            turn_off()
            stop.wait(0.25)
        if not stop.is_set():
            turn_off()
    _run_effect(_fn)

def effect_fastest_lap(revert_fn):
    def _fn(stop):
        set_color(COLORS["purple"])
        stop.wait(config["fastest_lap_duration"])
        if not stop.is_set():
            revert_fn()
    _run_effect(_fn)

def effect_off():
    _run_effect(lambda stop: turn_off())

# ─────────────────────────────────────────────
# SHARED STATE
# ─────────────────────────────────────────────
_state_lock         = threading.Lock()
_sc_status          = SC_NONE
_player_fia         = FIA_NONE
_red_flag_active    = False
_chequered_active   = False
_fastest_lap_active = False
_active_effect      = None
_participants       = {}

# ─────────────────────────────────────────────
# FLAG STATE MACHINE
#
# Priority (highest → lowest):
#   1. Chequered flag  — event packet CHQF
#   2. Red flag        — event packet RDFL (latched until session restart)
#   3. Fastest lap     — event packet FTLP (player only, timed)
#   4. Safety car      — session m_safetyCarStatus (full or virtual → solid yellow)
#   5. Formation lap   — session m_safetyCarStatus == 3
#   6. Player FIA flag — m_vehicleFiaFlags from Car Status packet
#                        This is the flag the game is showing specifically to
#                        the player's car based on its position on track.
#                        Yellow = incident ahead of this car specifically.
#                        Blue   = this car specifically is about to be lapped.
#                        NOT a global track-wide flag.
#   7. Off
# ─────────────────────────────────────────────

def _revert_after_fastest_lap():
    global _fastest_lap_active, _active_effect
    with _state_lock:
        _fastest_lap_active = False
        _active_effect = None
        _apply()

def _apply():
    global _active_effect

    if _chequered_active:
        effect = "chequered"
    elif _red_flag_active:
        effect = "red"
    elif _fastest_lap_active:
        effect = "fastest_lap"
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
        effect_chequered()
    elif effect == "red":
        log(RED,    "🔴 RED FLAG",       "flashing red")
        effect_flash("red", "yellow_flash_speed")
    elif effect == "fastest_lap":
        log(PURPLE, "💜 FASTEST LAP",    "solid purple")
        effect_fastest_lap(_revert_after_fastest_lap)
    elif effect == "yellow_solid":
        log(YELLOW, "🟡 SAFETY CAR",     "solid yellow")
        effect_solid("yellow")
    elif effect == "formation":
        log(GREEN,  "🟢 FORMATION LAP",  "flashing green")
        effect_flash("green", "yellow_flash_speed")
    elif effect == "yellow_flash":
        log(YELLOW, "🟡 YELLOW FLAG",    "flashing yellow — player's sector")
        effect_flash("yellow", "yellow_flash_speed")
    elif effect == "blue":
        log(BLUE,   "🔵 BLUE FLAG",      "flashing blue — player being lapped")
        effect_flash("blue", "blue_flash_speed")
    elif effect == "green":
        log(GREEN,  "🟢 GREEN FLAG",     "flashing green")
        effect_flash("green", "yellow_flash_speed")
    elif effect == "off":
        log(DIM,    "⚫ NO FLAG",        "light off")
        effect_off()

# ─────────────────────────────────────────────
# PACKET PARSERS
# ─────────────────────────────────────────────
def parse_session(payload):
    """Read safety car status only — marshal zone flags no longer used."""
    global _sc_status
    if len(payload) < SESSION_SC_STATUS_OFFSET + 1:
        return
    sc = payload[SESSION_SC_STATUS_OFFSET]
    with _state_lock:
        _sc_status = sc
        _apply()

def parse_car_status(payload, player_idx):
    """
    Read m_vehicleFiaFlags for the player's own car only.
    This is the flag the game is showing specifically to that car
    based on its position on track — not a global track-wide flag.
    -1 = unknown, 0 = none, 1 = green, 2 = blue, 3 = yellow
    """
    global _player_fia
    if player_idx >= 22:
        return
    offset = player_idx * CAR_STATUS_SIZE + FIA_FLAG_OFFSET
    if offset >= len(payload):
        return
    fia = struct.unpack_from("<b", payload, offset)[0]
    with _state_lock:
        _player_fia = fia
        _apply()

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
    _participants = new_map

def parse_event(payload):
    global _red_flag_active, _chequered_active, _fastest_lap_active, _active_effect
    if len(payload) < 4:
        return
    code = bytes(payload[0:4])

    if code == EV_FASTEST_LAP and len(payload) >= 6:
        vehicle_idx = payload[4]
        driver_name = _participants.get(vehicle_idx, f"Car {vehicle_idx}")
        if driver_name in config["player_gamertags"]:
            log(PURPLE, "💜 FASTEST LAP!", f"{driver_name}")
            with _state_lock:
                _fastest_lap_active = True
                _apply()
        else:
            log(DIM, f"Fastest lap: {driver_name}", "(not your driver)")

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
            _red_flag_active    = False
            _chequered_active   = False
            _fastest_lap_active = False
            _active_effect      = None
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
            elif packet_id == PACKET_EVENT:        parse_event(payload)
            elif packet_id == PACKET_PARTICIPANTS: parse_participants(payload)
            elif packet_id == PACKET_CAR_STATUS:   parse_car_status(payload, player_idx)

    except KeyboardInterrupt:
        print(f"\n{BOLD}Stopping...{RESET}")
        effect_off()
        time.sleep(0.5)
        print("Light off. Bye!\n")
    finally:
        sock.close()

if __name__ == "__main__":
    main()