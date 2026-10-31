import functools
import hashlib
import io
import json
import math
from pathlib import Path
import re


def sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(4 << 20), b""):
            value.update(block)
    return value.hexdigest()


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class ProbabilitySurface:
    def __init__(self, path, entry, category):
        import numpy as np
        with np.load(path, allow_pickle=False) as archive:
            metadata = json.loads(archive["meta"].tobytes())
            self.rows = tuple(archive["row_keys"].tolist())
            self.yellows = tuple(archive["yellows"].tolist())
            self.regimes = tuple(archive["regimes"].tolist())
            row, yellow = archive["cell_row"], archive["cell_yellow"]
            self.probability, self.measured = archive["cell_p_pos"], archive["cell_n_measured"]
        if metadata.get("schema") != "v18_linknet/2" or metadata.get("cat_side") != category or metadata.get("label") != "PREDICTED_NOT_MEASURED":
            raise ValueError("surface schema/category/prediction label differs")
        if metadata.get("production_scorer") != entry["production_scorer"] or metadata.get("model_trained_at") != entry["model_trained_at"] or canonical_sha(metadata.get("platt")) != entry["calibration_sha256"]:
            raise ValueError("surface model/calibration identity differs")
        contexts = metadata.get("contexts")
        if entry.get("feature_mode") != "context_free" or metadata.get("production_scorer") not in ("v1", "hybrid_v1", "counts") or not isinstance(contexts, list) or len(contexts) != 1 or contexts[0].get("name") != "CAT_SIDE_DEFAULTS" or contexts[0].get("n_features") != 0:
            raise ValueError("surface is not the supported context-free model")
        if self.regimes != ("ALL", "FLAT", "UP", "DOWN", "STRONG_UP", "STRONG_DOWN") or len(set(self.rows)) != len(self.rows) or len(set(self.yellows)) != len(self.yellows):
            raise ValueError("surface regimes or keys are ambiguous")
        if row.ndim != 1 or yellow.shape != row.shape or self.measured.shape != row.shape or self.measured.dtype.kind not in "iu" or self.probability.shape != (len(row), len(self.regimes)) or not np.isfinite(self.probability).all() or ((self.probability < 0) | (self.probability > 1)).any() or (self.measured < 0).any():
            raise ValueError("surface probabilities/counts have invalid shapes or values")
        if row.dtype.kind not in "iu" or yellow.dtype.kind not in "iu" or ((row < 0) | (row >= len(self.rows))).any() or ((yellow < 0) | (yellow >= len(self.yellows))).any() or (row[1:] < row[:-1]).any():
            raise ValueError("surface indices are invalid or unordered")
        self.row_index, patterns = {}, {}
        start = 0
        while start < len(row):
            row_id = int(row[start])
            stop = int(np.searchsorted(row, row_id, side="right"))
            pattern = tuple(int(value) for value in yellow[start:stop])
            if len(set(pattern)) != len(pattern):
                raise ValueError("duplicate surface cell key")
            offsets = patterns.setdefault(pattern, {value: index for index, value in enumerate(pattern)})
            self.row_index[self.rows[row_id]] = (start, offsets)
            start = stop
        self.yellow_index = {header: index for index, header in enumerate(self.yellows)}
        self.regime_index = {regime: index for index, regime in enumerate(self.regimes)}
        self.probability.flags.writeable = False
        self.measured.flags.writeable = False

    def lookup(self, sheet, row_key, header, regime):
        item = self.row_index.get(str(sheet) + "|" + str(row_key))
        yellow = self.yellow_index.get(str(header))
        selected_regime = "ALL" if regime in (None, "UNK", "ALL") else regime
        column = self.regime_index.get(selected_regime)
        if item is None or yellow is None or column is None:
            return None
        offset = item[1].get(yellow)
        if offset is None:
            return None
        index = item[0] + offset
        probability = float(self.probability[index, column])
        return (probability, int(self.measured[index])) if math.isfinite(probability) and 0 <= probability <= 1 else None


@functools.lru_cache(maxsize=8)
def _cached_surface(path, surface_sha256, entry_json, category):
    path = Path(path)
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != surface_sha256:
        raise ValueError("surface artifact hash differs")
    return ProbabilitySurface(io.BytesIO(payload), json.loads(entry_json), category)


def load_surface(environment, model_path, category, root):
    manifest = environment.get("V18_LINKNET_SURFACE_MANIFEST")
    if not manifest:
        return None, "off"
    try:
        expected = environment.get("V18_LINKNET_SURFACE_SHA256", "")
        path = Path(manifest).resolve(strict=True)
        if not re.fullmatch("[0-9a-f]{64}", expected) or sha(path) != expected:
            raise ValueError("surface manifest identity differs")
        declaration = json.loads(path.read_text())
        if declaration.get("schema") != "v18_linknet_probability_surface/1":
            raise ValueError("surface manifest schema differs")
        entry = declaration["by_category"][category]
        if entry["graph_model_sha256"] != sha(model_path):
            raise ValueError("graph model identity differs")
        template = Path(root) / "SPREADSHEETS" / ("TEMPLATE_" + category + ".xlsx")
        if entry["template_sha256"] != sha(template):
            raise ValueError("serving template identity differs")
        artifact = (path.parent / entry["surface_path"]).resolve(strict=True)
        artifact.relative_to(path.parent)
        surface = _cached_surface(str(artifact), entry["surface_sha256"], json.dumps(entry, sort_keys=True), category)
        return surface, "loaded immutable calibrated prediction surface"
    except Exception as error:
        return None, "graph fallback: " + type(error).__name__ + ": " + str(error)
