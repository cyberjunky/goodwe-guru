---
name: battery-health-no-100pct
description: User does not want the battery charged/held at 100% — battery longevity is a priority
metadata:
  type: feedback
---

The user reacted strongly ("WTF this is not good") when the battery reached 100% SoC. Keep the battery below 100% wherever automations/settings control charging; never write charge actions that pin SoC at 100%.

**Why:** sustained 100% SoC ages home batteries; the user prioritises longevity.

**How to apply:**
- Any `eco_charge`/charge action must cap at a target SoC (default ≤90%, never 100). See the fix in [[goodwe-eco-charge-bug]].
- The ES has no native "max SoC for solar charging" in General mode — it fills to 100% from PV by default. Be honest that capping solar charge below 100% isn't natively possible on ES (platform 105); only forced charging via eco-mode is controllable.
- A single 100% charge is harmless (BMS caps at charge_v 53.0 V); the concern is being *held* at 100%.
