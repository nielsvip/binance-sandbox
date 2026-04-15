# Hetzner Robot API — Auto-Reboot Plan for S1 / S2

## Current credential status

Searched (2026-04-15):
- `/Users/niels/Documents/binance/*.py` — no matches for `HETZNER`, `robot-ws`, `hetzner-api`.
- `/Users/niels/Documents/binance/.env.*` (`.env.bitget`, `.env.bybit`, `.env.gpg`) — no Hetzner variables.
- `~/.config` — nothing Hetzner-related.
- Only references are comments in `sweep_server_setup.sh` and `sweep_server_data.sh` describing Hetzner as the provisioning target — no credentials embedded.

**Status: BLOCKED — no Hetzner Robot API credentials are available on this MacBook.**

## What user must supply

Hetzner Robot (dedicated servers) uses HTTP Basic-Auth against `robot-ws.your-server.de`.

**IMPORTANT: The Robot webservice user is SEPARATE from the account 2FA.** It's a dedicated API credential pair used only by `robot-ws.your-server.de`. Account 2FA does NOT gate it. So auto-reboot is feasible even with 2FA enabled on the main Hetzner account.

Steps:

1. Log in to https://robot.hetzner.com/ (one-time, with 2FA — just to create the webservice user)
2. Navigate to `Settings -> Web service settings` (or `Web service and app settings` in new UI).
3. Enable Webservice access, create a dedicated API username + password (NOT the account password).
4. Store them locally, e.g. append to `~/.config/hetzner.env`:
   ```
   HETZNER_ROBOT_USER=...
   HETZNER_ROBOT_PASS=...
   HETZNER_S1_IP=<public S1 IP>
   HETZNER_S2_IP=<public S2 IP>
   ```
5. `chmod 600 ~/.config/hetzner.env`.

The public IPs (not the 10.0.0.x internal jump IPs) are required — the API keys reset based on the main public IP of the server, not the private network address.

## Planned reboot flow (implementable once credentials exist)

Endpoint: `POST https://robot-ws.your-server.de/reset/<server-ip>` with form body `type=hw` (hardware reset, closest to pressing the physical reset button) or `type=sw` (software reset).

Pseudocode for the supervisor:
```python
import requests, os
from requests.auth import HTTPBasicAuth

def hetzner_reset(ip: str, kind: str = "hw") -> bool:
    u = os.environ["HETZNER_ROBOT_USER"]
    p = os.environ["HETZNER_ROBOT_PASS"]
    r = requests.post(
        f"https://robot-ws.your-server.de/reset/{ip}",
        data={"type": kind},
        auth=HTTPBasicAuth(u, p),
        timeout=30,
    )
    return r.status_code == 200
```

## Supervisor trigger conditions (when to call this)

Only reboot a server when ALL of these are true for >10 minutes:
- `ssh <host> uptime` → connection timeout / refused (sshd dead).
- ICMP ping to the 10.0.0.x internal IP also fails.
- No heartbeat on the shared sweep-results dir (no new CSV mtimes).
- At least 15 minutes since the last reboot of that host (cooldown).

Hard guard: never reboot both S1 and S2 within the same 30-minute window. Never auto-reboot more than 3 times per 24 hours per host.

## Next steps (blocked on user)

1. **USER**: create Robot webservice user/pass, drop into `~/.config/hetzner.env`, chmod 600, list S1/S2 public IPs.
2. Once delivered, add a `hetzner_reset()` helper to `binance_supervisor.py` with the guards above.
3. Gate behind env var `HETZNER_AUTO_REBOOT_ENABLED=0` by default — user flips to 1 after a dry-run test.

The supervisor as written in this session does NOT call Hetzner. It only logs a warning `SSH_DEAD_>10m -> manual reboot required` when the condition is met.
