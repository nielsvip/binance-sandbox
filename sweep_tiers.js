// sweep_tiers.js — JS compat shim for data/sweep_tiers.json + vec_sweep_tiers.py
// Followup gap noted "no sweep_tiers.js" while audits referenced sweep_tiers.js consistency.
// Canonical sources are data/sweep_tiers.json (88 crypto + 6 tradier params, 12 tiers) and
// vec_sweep_tiers.py (7 code tiers, e.g. mega_combo_v1 1005 configs).
// This file re-exports the JSON for JS consumers so `import tiers from './sweep_tiers.js'` works.

import { createRequire } from 'module';
import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const raw = readFileSync(join(__dirname, 'data/sweep_tiers.json'), 'utf8');
export const sweepTiers = JSON.parse(raw);
export default sweepTiers;

// Tier counts (parity with vec_sweep_tiers.py + data/sweep_tiers.json):
// crypto: TIER_1_ENTRY 8, TIER_1_EXIT 8, TIER_1_SIZING 4, TIER_2_ENTRY_TUNING 9, TIER_2_EXIT_TUNING 6, TIER_3_FINE_TUNING 11, TIER_A-DD 28, FIXED 9, DEAD 9 => 88 sweepable
// tradier: TIER_1 6
// vec_sweep_tiers.py code tiers: live_default_baseline 1, wt_dc_threshold_sweep 11, gr_entry_consensus_grid 22, entry_path_ablation 13, exit_path_ablation 13, mega_combo_v1 1005, grtf7_full 37
