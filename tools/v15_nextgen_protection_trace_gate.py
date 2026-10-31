"""Observe actual protection decisions without changing their inputs or return values."""
import argparse
import datetime
import functools
import hashlib
import importlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from v15_nextgen_baseline_guard import normalized


class ProtectionTrace:
    def __init__(self, engine, limit=20000):
        self.engine, self.limit, self.events, self.originals = engine, limit, [], []
        self.source = Path(engine.__file__).resolve()
        lines = self.source.read_text().splitlines()
        self.source_lines, self.previous_line = lines, {}
        markers = {'bar_close': 'px = close[i]', 'fixed_stop': '_fx_fire = ""', 'breakeven_phase': 'if _bge_on and pos is not None:', 'peak_phase': 'if _bel_on and pos is not None:', 'reentry_final_gates': 'if fire and _iso_on and has_closed_before'}
        self.lines = {}
        for phase, marker in markers.items():
            matches = [index + 1 for index, line in enumerate(lines) if marker in line]
            if len(matches) != 1:
                raise ValueError(f'protection trace marker is ambiguous/missing: {phase}')
            self.lines[matches[0]] = phase
        self.modules = {str(self.source): hashlib.sha256(self.source.read_bytes()).hexdigest()}

    def snapshot(self, frame, phase, value=None, inputs=None):
        if len(self.events) >= self.limit:
            raise SystemExit('bounded diagnostic trace full; no silent truncation')
        local = frame.f_locals
        scalar_keys = ('i', 'px', 'is_long', 'cd', 'fire', 'has_closed_before', 'live_pnl_pct', '_bge_g', '_bge_age', '_bge_grace', '_bge_fire', '_bge_why', '_bge_rs', '_bel_g', '_bel_on', '_bel_fee_pct', '_bel_thr', '_bel_ero_tf', '_bge_on', '_sr_on', '_sr_fired', '_fx_lvl', '_fx_px', '_fx_fire', '_fx_spec', '_fx_ref_default', '_fx_tight_on', 'half_fee')
        state = {key: local[key] for key in scalar_keys if key in local}
        index = local.get('i')
        if isinstance(index, int):
            for name, array_name in [('current_bar_close', 'close'), ('current_bar_timestamp', 'ts')]:
                array = local.get(array_name)
                if array is not None and 0 <= index < len(array):
                    state[name] = array[index]
            native_masks = {}
            for key in ('entry_sig', 'exit_sig'):
                mask = local.get(key)
                if mask is not None and 0 <= index < len(mask):
                    native_masks[key] = bool(mask[index])
            for key, mask in (local.get('_bt_exit_reason_masks') or {}).items():
                if mask is not None and 0 <= index < len(mask):
                    native_masks[key] = bool(mask[index])
            state['native_precomputed_signal_masks'] = native_masks
            npz = local.get('npz') or {}
            state['native_indicator_values'] = {key: npz[key][index] for key in ('stoch_k_15m', 'stoch_d_15m', 'stoch_low_15m', 'stoch_high_15m', 'dc_low_15m', 'dc_high_15m') if key in npz and 0 <= index < len(npz[key])}
            state['indicator_keys_absent'] = [key for key in ('stoch_low_15m', 'stoch_high_15m') if key not in npz]
        config = local.get('cfg')
        if config is not None:
            state['effective_fixed_stop_config'] = {key: getattr(config, key) for key in ('FIXED_STOP_REF_FIELD', 'FIXED_STOP_REF_TF', 'FIXED_STOP_BUFFER_PCT', 'FIXED_STOP_TIGHTEN_MODE', 'FIXED_STOP_TIGHTEN_TRIGGER_PCT') if hasattr(config, key)}
        position = local.get('pos')
        if isinstance(position, dict):
            state['position'] = {key: position[key] for key in ('entry_bar', 'entry_reason', 'avg_price', 'entry_price', 'qty', 'fees', 'realized', 'deployed', 'peak_pnl_pct', '_bge_mfe', '_bel_mfe', 'fixed_stop', 'fixed_stop_eff') if key in position}
        else:
            state['position'] = None
        self.events.append({'phase': phase, 'line': frame.f_lineno, 'state': normalized(state), 'native_return': normalized(value), 'native_inputs': inputs, 'observation_semantics': 'phase visited before line executes; predicate records occur after actual call returns; absent predicate event means not observed, never false'})

    def trace(self, frame, event, arg):
        if frame.f_code is not self.engine.simulate_one.__code__:
            return None
        if event == 'line':
            local = frame.f_locals
            current = {key: local[key] for key in ('i', 'px', 'cd', 'fire', '_sr_fired', '_ordinary_lr_flat_claim') if key in local}
            statement = self.source_lines[frame.f_lineno - 1]
            if current.get('fire') is True and local.get('pos') is None and statement.strip().split('#', 1)[0].strip() == 'continue':
                scalars = {key: normalized(value) for key, value in local.items() if value is None or isinstance(value, (bool, str, int, float)) or type(value).__module__.startswith('numpy') and getattr(value, 'ndim', 1) == 0}
                neighborhood = [{'line': index + 1, 'source': self.source_lines[index]} for index in range(max(0, frame.f_lineno - 5), min(len(self.source_lines), frame.f_lineno + 1))]
                self.snapshot(frame, 'native_flat_fire_continue', inputs={'executed_line': frame.f_lineno, 'executed_source': statement, 'source_neighborhood': neighborhood, 'native_scalar_locals': scalars, 'semantics': 'actual continue visited with native fireTrue and posNone; no inferred refusal reason'})
            previous = self.previous_line.get(id(frame))
            if previous is not None and previous['state'].get('i') == current.get('i') and previous['state'].get('fire') is True and current.get('fire') is False:
                line = previous['line']
                transition = {'executed_line': line, 'executed_source': self.source_lines[line - 1], 'before': normalized(previous['state']), 'after': normalized(current), 'semantics': 'native observed transition across executed line; no reconstructed refusal reason'}
                self.snapshot(frame, 'native_fire_true_to_false', inputs=transition)
            self.previous_line[id(frame)] = {'line': frame.f_lineno, 'state': current}
            if frame.f_lineno in self.lines:
                self.snapshot(frame, self.lines[frame.f_lineno])
        elif event == 'return':
            self.previous_line.pop(id(frame), None)
        return self.trace

    def __enter__(self):
        if sys.gettrace() is not None:
            raise ValueError('existing trace hook present; refuse replacement')
        targets = {'vec_decisions.breakeven_gain_erosion': ('in_grace', 'trend_veto', 'htf_veto', 'window_decision'), 'vec_decisions.stop_reclaim': ('fire', 'exclusive_blocks'), 'vec_decisions.reentry_triggers': ('fire',), 'vec_decisions.net_exit_gate': ('vector_exit_event_allowed',), 'vec_decisions.check_exit_candidates_crypto__peak_giveback': ('_peak_giveback_fires',), 'vec_decisions.fixed_stop': ('breached', 'ref_value', 'freeze_level_spec', 'freeze_software_level', 'tighten_candidate')}
        prepared = []
        for name, attributes in targets.items():
            module = importlib.import_module(name)
            path = Path(module.__file__).resolve()
            if self.source.parent not in path.parents:
                raise ValueError('protection predicate escaped frozen runtime')
            self.modules[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
            for attribute in attributes:
                original = getattr(module, attribute)
                prepared.append((module, attribute, original, name + '.' + attribute, inspect.signature(original)))
        try:
            for module, attribute, original, label, signature in prepared:
                @functools.wraps(original)
                def observe(*args, _original=original, _label=label, _signature=signature, **kwargs):
                    bound = _signature.bind(*args, **kwargs)
                    bound.apply_defaults()
                    inputs = {}
                    for key, argument in bound.arguments.items():
                        if argument is None or isinstance(argument, (bool, str, int, float)) or type(argument).__module__.startswith('numpy') and getattr(argument, 'ndim', 1) == 0:
                            inputs[key] = normalized(argument)
                        elif isinstance(argument, dict) and len(argument) <= 128:
                            inputs[key] = {field: normalized(item) if item is None or isinstance(item, (bool, str, int, float)) else {'omitted_non_scalar_type': type(item).__module__ + '.' + type(item).__qualname__} for field, item in argument.items() if isinstance(field, str)}
                        else:
                            inputs[key] = {'omitted_non_scalar_type': type(argument).__module__ + '.' + type(argument).__qualname__}
                    value = _original(*args, **kwargs)
                    caller = sys._getframe(1)
                    for _depth in range(12):
                        if caller is None or caller.f_code is self.engine.simulate_one.__code__:
                            break
                        caller = caller.f_back
                    if caller is not None and caller.f_code is self.engine.simulate_one.__code__:
                        self.snapshot(caller, _label, value, inputs)
                    return value
                self.originals.append((module, attribute, original))
                setattr(module, attribute, observe)
        except BaseException:
            for module, attribute, original in reversed(self.originals):
                setattr(module, attribute, original)
            self.originals.clear()
            raise
        sys.settrace(self.trace)
        return self

    def __exit__(self, *error):
        sys.settrace(None)
        for module, attribute, original in reversed(self.originals):
            setattr(module, attribute, original)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--oracle-runner', required=True)
    parser.add_argument('--expected-oracle', required=True)
    parser.add_argument('--trace-output', required=True)
    args, remaining = parser.parse_known_args()
    output = Path(args.trace_output)
    if output.exists():
        raise ValueError('trace output exists; preserve previous evidence')
    expected = json.loads(Path(args.expected_oracle).read_text())['previous_actual_engine_proof']
    spec = importlib.util.spec_from_file_location('protection_observed_oracle', args.oracle_runner)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    capture = runner.capture_actual
    def observed(*capture_args, **capture_kwargs):
        import v12_quick_engine as engine
        with ProtectionTrace(engine) as trace:
            result, proof = capture(*capture_args, **capture_kwargs)
        for key in ('effective_config_sha256', 'execution_ledger_sha256', 'result_sha256', 'input_cutoff_sha256'):
            if proof[key] != expected[key]:
                raise ValueError(f'instrumented actual baseline differs: {key}')
        receipt = {'schema': 'v15_protection_trace/1', 'at_utc': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'), 'status': 'ACTUAL_BASELINE_PARITY_WITH_OBSERVATION', 'modules_sha256': trace.modules, 'markers': trace.lines, 'baseline_hashes': {key: proof[key] for key in ('effective_config_sha256', 'execution_ledger_sha256', 'result_sha256', 'input_cutoff_sha256')}, 'events': trace.events, 'limitations': ['Native observed state and predicate returns only; no reconstructed strategy or counterfactual fills.', 'Phase line is captured before that line executes; stale locals are possible and must be interpreted by phase.', 'Closed-bar price/peak fields are not an executable net-profit guarantee; actual filled ledger is authoritative.', 'Intrabar high/low ordering and unobserved predicate branches remain unknown.', 'Historical cells/trades retain their original timestamps; no gain/promotion claim.']}
        with output.open('x') as stream:
            json.dump(receipt, stream, indent=2)
        return result, proof
    runner.capture_actual = observed
    sys.argv = [args.oracle_runner, *remaining]
    runner.main()


if __name__ == '__main__':
    main()
