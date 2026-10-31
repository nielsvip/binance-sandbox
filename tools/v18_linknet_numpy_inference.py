import numpy as np
from tools.v18_brain import cat_side_of


def production_probability_grid(scorer, layout, symbol_side, chunk_size=200000):
    """Original v1/counts production probability math, with no v2 or sparse-context forward work."""
    if scorer.prod not in ("v1", "hybrid_v1", "counts") or getattr(scorer.fz.fctx, "feature_mode", None) != "context_free":
        raise ValueError("NumPy probability path supports only context-free v1/hybrid_v1/counts")
    if cat_side_of(symbol_side) != scorer.cs:
        raise ValueError("symbol side differs from model category")
    row_keys, yellows = layout["row_keys"].tolist(), layout["yellows"].tolist()
    row, yellow = layout["cell_row"], layout["cell_yellow"]
    n, positive = layout["cell_n_measured"], layout["cell_pos_measured"]
    row_n, row_positive = layout["row_n_measured"], layout["row_pos_measured"]
    if row.ndim != 1 or yellow.shape != row.shape or n.shape != row.shape or positive.shape != row.shape or row_n.shape != (len(row_keys),) or row_positive.shape != row_n.shape:
        raise ValueError("compiled layout/count shape differs")
    if row.dtype.kind not in "iu" or yellow.dtype.kind not in "iu" or ((row < 0) | (row >= len(row_keys))).any() or ((yellow < 0) | (yellow >= len(yellows))).any():
        raise ValueError("compiled layout has invalid row/yellow indices")
    for measured, positives in ((n, positive), (row_n, row_positive)):
        if not np.isfinite(measured).all() or not np.isfinite(positives).all() or ((measured < 0) | (positives < 0) | (positives > measured)).any() or not np.equal(measured, np.floor(measured)).all() or not np.equal(positives, np.floor(positives)).all():
            raise ValueError("compiled evidence is not true nonnegative integer observations")
    row_set = np.array([scorer.set_ix.get(key, -1) for key in row_keys], np.int64)
    filter_id = np.array([scorer.filt_ix.get(header, -1) for header in yellows], np.int64)
    pair_key = row_set[row] * 1000003 + filter_id[yellow]
    known = (row_set[row] >= 0) & (filter_id[yellow] >= 0)
    pair_index = np.searchsorted(scorer.ev["pairs"], pair_key)
    expected_n, expected_positive = np.zeros(len(row), np.int64), np.zeros(len(row), np.int64)
    hits = known & (pair_index < len(scorer.ev["pairs"]))
    hits[hits] &= scorer.ev["pairs"][pair_index[hits]] == pair_key[hits]
    expected_n[hits] = scorer.ev["n_p"][pair_index[hits]]
    expected_positive[hits] = scorer.ev["p_p"][pair_index[hits]]
    if not np.array_equal(n, expected_n) or not np.array_equal(positive, expected_positive):
        raise ValueError("compiled pair counts differ from original dataset evidence")
    expected_row_n, expected_row_positive = np.zeros(len(row_keys), np.int64), np.zeros(len(row_keys), np.int64)
    known_rows = row_set >= 0
    expected_row_n[known_rows] = scorer.ev["n_setR"][row_set[known_rows]]
    expected_row_positive[known_rows] = scorer.ev["p_setR"][row_set[known_rows]]
    if not np.array_equal(row_n, expected_row_n) or not np.array_equal(row_positive, expected_row_positive):
        raise ValueError("compiled row counts differ from original dataset evidence")
    filter_prior = np.array([scorer.fo_rate[index] if index >= 0 else scorer.p0["F"] for index in filter_id])
    cell_prior = filter_prior[yellow]
    row_prior = np.full(len(row_keys), scorer.p0["R"])
    if scorer.prod == "counts":
        cell_probability = scorer.final_p(None, n, positive, cell_prior, "F").astype(np.float16)
        row_probability = scorer.final_p(None, row_n, row_positive, row_prior, "R").astype(np.float32)
    else:
        net = scorer.net1
        if net is None:
            raise ValueError("selected original v1 weights are absent")
        set_table = scorer.set_table(row_keys)
        filter_table, empty_filter = scorer.filt_table(yellows), scorer.filt_table([""])
        sets, filters, naked_filter = net.pool(*set_table), net.pool(*filter_table), net.pool(*empty_filter)
        contexts = {}
        for kind in ("R", "F"):
            table = scorer.table([scorer.fz.ctx_feats((kind, "UNK", symbol_side, "U", "", "log"))], 6)
            contexts[kind] = net.pool(*table)
        context_r = tuple(np.repeat(value[:1], len(row_keys), axis=0) for value in contexts["R"])
        filter_r = tuple(np.repeat(value[:1], len(row_keys), axis=0) for value in naked_filter)
        raw_row = net.heads(sets[0], filter_r[0], context_r[0], sets[1] + filter_r[1] + context_r[1], sets[2] + filter_r[2] + context_r[2])
        row_probability = scorer.final_p(1.0 / (1.0 + np.exp(-np.clip(raw_row[:, 0], -30, 30))), row_n, row_positive, row_prior, "R").astype(np.float32)
        cell_probability = np.empty(len(row), np.float16)
        for start in range(0, len(row), chunk_size):
            stop = min(len(row), start + chunk_size)
            row_ids, filter_ids = row[start:stop], yellow[start:stop]
            batch = stop - start
            switch = tuple(value[row_ids] for value in sets)
            filt = tuple(value[filter_ids] for value in filters)
            context = (np.broadcast_to(contexts["F"][0][0], (batch, contexts["F"][0].shape[1])), np.broadcast_to(contexts["F"][1][0], (batch, contexts["F"][1].shape[1])), np.full(batch, contexts["F"][2][0]))
            raw = net.heads(switch[0], filt[0], context[0], switch[1] + filt[1] + context[1], switch[2] + filt[2] + context[2])
            probability = 1.0 / (1.0 + np.exp(-np.clip(raw[:, 0], -30, 30)))
            cell_probability[start:stop] = scorer.final_p(probability, n[start:stop], positive[start:stop], cell_prior[start:stop], "F")
    return {"row_keys": layout["row_keys"], "yellows": layout["yellows"], "cell_row": row, "cell_yellow": yellow, "cell_n_measured": n, "cell_pos_measured": positive, "row_n_measured": row_n, "row_pos_measured": row_positive, "cell_p_pos": cell_probability[:, None], "row_p_pos": row_probability[:, None]}
