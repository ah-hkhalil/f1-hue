# F1 25 → Philips Hue Flag Lights

Control a Philips Hue light in real time based on flag states in F1 25. Runs on a Raspberry Pi on the same network as your Xbox, Game PC, etc and Hue Bridge.

***Click Thumbnail To Watch The Video***

[![Watch the video](https://img.youtube.com/vi/14Vx-pVHvxk/hqdefault.jpg)](https://youtu.be/14Vx-pVHvxk)



## What it does

| Situation | Light |
|---|---|
| Green flag | Flashing green |
| Yellow flag (your sector) | Flashing yellow |
| Blue flag (you're being lapped) | Flashing blue |
| Full Safety Car | Solid yellow |
| Virtual Safety Car | Solid yellow |
| Formation lap | Flashing green |
| Red flag | Flashing red |
| Chequered flag | Flashing white (10s then off) |
| Fastest lap (your car) | Solid purple (10s then reverts) |
| Penalty issued | Solid white (10s then reverts) |
| DRS available | Single green flash |
| Race start lights (1–5) | Red pulse per light, holds on 5th |
| Lights out | Normal flag logic resumes |
| Quit to menu | Light turns off |

Flag states are per-driver — yellow and blue only trigger when **your car** is being shown that flag, not when any car on track is.

---

## What you need

- Raspberry Pi running Raspberry Pi OS, Ubuntu, etc
- Philips Hue Bridge (v2 recommended)
- One or more colour Hue lights 
- Xbox running F1 25
- All devices on the same network

---

## Step 1 — Find your Hue Bridge IP address

You can find the Bridge IP address in one of three ways:

**Option A — Hue app**
Open the Hue app → Settings → My Hue system → your Bridge → tap the info (ℹ) icon. The IP address is listed there.

**Option B — Router**
Log into your router's admin page and look for a device named `Philips-hue` in the connected devices list.

**Option C — Discovery URL**
From any browser on your network visit:
```
https://discovery.meethue.com
```
This returns JSON containing your Bridge's IP address:
```json
[{"id":"...","internalipaddress":"x.x.x.x"}]
```

Note down the IP — you'll need it throughout the setup.

---

## Step 2 — Generate a Hue API key

The Hue Bridge uses a local REST API. You need to generate a username (API key) by physically pressing the button on top of the Bridge.

**1. Press the button** on top of your Hue Bridge.

**2. Within 30 seconds**, run this command from your Pi (replace `YOUR_BRIDGE_IP`):

```bash
curl -X POST http://YOUR_BRIDGE_IP/api \
  -H "Content-Type: application/json" \
  -d '{"devicetype":"f1_hue#raspberrypi"}'
```

**3. You should get a response like this:**

```json
[{"success":{"username":"abc123XYZdef456..."}}]
```

Copy the `username` value — this is your API key. If you see an error like `link button not pressed`, press the button on the Bridge and try again within 30 seconds.

---

## Step 3 — Find your light ID

Run this command (replace both placeholder values):

```bash
curl http://YOUR_BRIDGE_IP/api/YOUR_API_KEY/lights
```

You'll get a JSON response listing all your lights. It can be hard to read — paste it into a JSON formatter like [jsonformatter.org](https://jsonformatter.org) or run this on the Pi to get a tidy table:

```bash
curl -s http://YOUR_BRIDGE_IP/api/YOUR_API_KEY/lights | python3 -c "
import json, sys
lights = json.load(sys.stdin)
print(f'{'ID':<5} {'Name':<30} {'Type':<25} {'On':<5}')
print('-' * 70)
for id, l in lights.items():
    state = l.get('state', {})
    print(f'{id:<5} {l[\"name\"]:<30} {l[\"type\"]:<25} {str(state.get(\"on\",\"?\")):<5}')
"
```

Example output:
```
ID    Name                           Type                      On
----------------------------------------------------------------------
1     Bedroom Lamp                   Extended color light      False
4     TV Lightstrip                  Color temperature light   True
10    Gaming Strip                   Extended color light      True
```

Note the ID of the light you want to control — must be a colour capable lamp.

---

## Step 4 — Install the script on your Pi

```bash
# Install dependencies
pip3 install requests --break-system-packages

# Clone the repo
git clone https://github.com/ah-hkhalil/f1-hue.git
cd f1-hue
```

Edit `f1_hue.py` and fill in the config section at the top:

```python
config = {
    "bridge_ip":        "x.x.x.x",           # your Bridge IP from Step 1
    "hue_user":         "abc123XYZdef456",   # your API key from Step 2
    "light_id":         "x",                # your light ID from Step 3
    "player_gamertags": ["YourGamertag"],    # your Xbox gamertag as shown in-game
    ...
}
```

---

## Step 5 — Configure F1 25 on Xbox

In F1 25:
**Options → Settings → Telemetry Settings**

| Setting | Value |
|---|---|
| UDP Telemetry | On |
| Broadcast Mode | Off |
| IP Address | Your Pi's IP address |
| UDP Port | 20777 |
| Send Rate | 20Hz (or higher - I've been using 60Hz) |

To find your Pi's IP address, run `hostname -I` on the Pi.

---

## Step 6 — Run the script

```bash
python3 f1_hue.py
```

You should see:
```
╔══════════════════════════════════════════╗
║       F1 25 → Hue Flag Lights            ║
╚══════════════════════════════════════════╝
  Bridge  : 10.0.40.69   Light : 10
  UDP     : port 20777
  Players : YourGamertag

Waiting for packets...

✓ Receiving telemetry from x.x.x.x
```

Once you start a race the light will respond to flag states automatically. If you want to have the script run automatically when you boot the Raspberry Pi, see Step 7.

---

## Step 7 — Run automatically on boot (optional)

Run the install script to set up a systemd service that starts the script on boot:

```bash
bash install.sh
```

Useful commands after installation:

```bash
sudo systemctl status f1hue        # check if running
sudo systemctl stop f1hue          # stop the service
sudo systemctl start f1hue         # start the service
sudo journalctl -u f1hue -f        # live log output
```

---

## Troubleshooting

**Script starts but light doesn't respond**
- Check the Pi IP in F1 25 telemetry settings matches `hostname -I` on the Pi
- Make sure Broadcast Mode is **Off** and the IP is set to your Pi
- Check the Hue Bridge IP and API key in config are correct
- Try running the curl command from Step 3 to verify the Bridge is reachable

**Yellow/blue flag triggers for other cars, not just yours**
- This is fixed in the current version — flags use `m_vehicleFiaFlags` which is per-driver
- If you see unexpected triggers check the terminal output for the flag state being reported

**Purple light doesn't trigger for fastest lap**
- Make sure your gamertag in config exactly matches what appears in-game (case sensitive)
- The script also matches by vehicle index, so it should work even if the name is garbled

**Light stays on after quitting a race**
- This is handled by the `SEND` (session end) event — make sure you're not running an older version of the script

---

## Configuration reference

| Key | Default | Description |
|---|---|---|
| `bridge_ip` | — | IP address of your Hue Bridge |
| `hue_user` | — | Hue API key (generated in Step 2) |
| `light_id` | — | ID of the light to control |
| `udp_port` | 20777 | Must match F1 25 telemetry port setting |
| `player_gamertags` | — | List of Xbox gamertags for fastest lap detection |
| `chequered_duration` | 10 | Seconds to flash white after chequered flag |
| `fastest_lap_duration` | 10 | Seconds to show purple after fastest lap |
| `penalty_duration` | 10 | Seconds to show white after a penalty |
| `yellow_flash_speed` | 0.3 | Flash interval in seconds for yellow/red/green flags |
| `blue_flash_speed` | 0.3 | Flash interval in seconds for blue flag |
| `brightness` | 254 | Light brightness, 1–254 |

---

## How it works

F1 25 broadcasts UDP telemetry packets on your local network. This script listens on port 20777 and parses three packet types:

- **Session packet** — reads safety car status (2/sec)
- **Car Status packet** — reads `m_vehicleFiaFlags` for the player's own car (the flag the game is showing specifically to that driver based on their position on track)
- **Event packet** — receives instant notifications for chequered flag, red flag, fastest lap, safety car deployment, start lights, DRS, and session start/end

All light changes are processed through a single worker thread fed by a queue, which prevents race conditions and ensures effects transition cleanly.

---

## Acknowledgements

- EA / Codemasters for publishing the [F1 25 UDP telemetry specification](https://forums.ea.com/en/f1/f1-25/)
- Philips Hue for the local REST API
- user: richardvinger for his Home Assistant version of this project: https://github.com/richardvinger/f1-25-telemetry-for-home-assistant/
