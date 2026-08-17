# S1 transport identity incident — 2026-08-03 16:04Z

The configured public endpoint `157.180.125.52:22` no longer presents any of
the three host keys previously trusted for S1. Strict host-key checking blocked
all connections; `known_hosts` was not modified and no insecure override was
used.

| Host-key type | Trusted S1 fingerprint | Presented fingerprint | Result |
|---|---|---|---|
| ED25519 | `SHA256:untukeU0+oUIHLI+D3Zr9J1NGJLGwu24NgJ40wwuZps` | `SHA256:1mKHB6FrMaQZzPumeWadBljKG4d+sV+ueR5L/+9vJCM` | MISMATCH |
| ECDSA | `SHA256:gFTQVanXPNmAd7c8FEmm47LOjosJRB13piZSRy9kRi0` | `SHA256:IY5OhBMlX2GhTntqIACOVrIotdjJi9ZtTNAdUq9Zoi0` | MISMATCH |
| RSA | `SHA256:0Y2BG+OnmjaLH7F4U33hf8IipRh9hHuRJEK7zO/RpkE` | `SHA256:23ZDMV52f9w8N7axucvZ1+TE4PADoyxGZOCeqyX4NN0` | MISMATCH |

The trusted `localhost:2201` route simultaneously resets during key exchange,
and the gateway-to-`10.0.0.3` route times out during banner exchange. Therefore
S1 worker truth after the last successfully retrieved receipt cannot currently
be verified or changed from the Mac. The bounded sync request remains queued.

Do not accept the new public keys without independent infrastructure-level
confirmation that the public IP was deliberately reassigned to the real S1.
