from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List

CONFIG_PATH = Path('keyword_settings.json')

# Legacy files kept for backward compatibility (read-only)
LEGACY_KEYWORDS_TXT = Path('keywords.txt')
LEGACY_THRESHOLD_TXT = Path('keyword_threshold.txt')
LEGACY_THRESHOLDS_JSON = Path('keyword_thresholds.json')
LEGACY_WINDOW_TXT = Path('keyword_window.txt')
LEGACY_WINDOWS_JSON = Path('keyword_windows.json')
LEGACY_COUNTS_JSON = Path('keyword_counts.json')


def _ensure_positive_int(value: Any, default: int) -> int:
    try:
        ivalue = int(value)
        return ivalue if ivalue >= 1 else default
    except Exception:
        return default


def _load_legacy_keywords() -> List[str]:
    try:
        if LEGACY_KEYWORDS_TXT.exists():
            text = LEGACY_KEYWORDS_TXT.read_text(encoding='utf-8')
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            if lines:
                return lines
    except Exception:
        pass
    env_val = (os.getenv('KEYWORDS') or '').strip()
    return [seg.strip() for seg in env_val.split(',') if seg.strip()]


def _load_legacy_threshold() -> int | None:
    try:
        if LEGACY_THRESHOLD_TXT.exists():
            txt = LEGACY_THRESHOLD_TXT.read_text(encoding='utf-8').strip()
            if txt:
                return _ensure_positive_int(txt, 1)
    except Exception:
        pass
    env_val = (os.getenv('KEYWORD_THRESHOLD') or '').strip()
    if env_val:
        return _ensure_positive_int(env_val, 1)
    return None


def _load_legacy_window() -> int | None:
    try:
        if LEGACY_WINDOW_TXT.exists():
            txt = LEGACY_WINDOW_TXT.read_text(encoding='utf-8').strip()
            if txt:
                return _ensure_positive_int(txt, 60)
    except Exception:
        pass
    env_val = (os.getenv('KEYWORD_WINDOW') or '').strip()
    if env_val:
        return _ensure_positive_int(env_val, 60)
    return None


def _load_legacy_map(path: Path, env_var: str) -> Dict[str, int]:
    data: Dict[str, int] = {}
    try:
        if path.exists():
            raw = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if str(v).isdigit():
                        key = str(k).strip()
                        if key:
                            data[key] = max(1, int(v))
    except Exception:
        data = {}
    if not data:
        try:
            env_val = os.getenv(env_var)
            if env_val:
                raw = json.loads(env_val)
                if isinstance(raw, dict):
                    for k, v in raw.items():
                        if str(v).isdigit():
                            key = str(k).strip()
                            if key:
                                data[key] = max(1, int(v))
        except Exception:
            pass
    return data


def _sanitize_config(raw: Dict[str, Any]) -> Dict[str, Any]:
    config: Dict[str, Any] = {
        'keywords': [],
        'global_threshold': None,
        'per_keyword_thresholds': {},
        'global_window': None,
        'per_keyword_windows': {},
        'contains_keywords': [],
    }

    if not isinstance(raw, dict):
        raw = {}

    keywords = raw.get('keywords')
    if isinstance(keywords, list):
        config['keywords'] = [str(k).strip() for k in keywords if str(k).strip()]
    elif isinstance(keywords, str):
        config['keywords'] = [seg.strip() for seg in keywords.split(',') if seg.strip()]

    threshold = raw.get('global_threshold')
    if threshold is not None:
        config['global_threshold'] = _ensure_positive_int(threshold, 1)

    per_thresholds = raw.get('per_keyword_thresholds')
    if isinstance(per_thresholds, dict):
        sanitized: Dict[str, int] = {}
        for k, v in per_thresholds.items():
            key = str(k).strip()
            if key:
                sanitized[key] = _ensure_positive_int(v, 1)
        config['per_keyword_thresholds'] = sanitized

    window = raw.get('global_window')
    if window is not None:
        config['global_window'] = _ensure_positive_int(window, 60)

    per_windows = raw.get('per_keyword_windows')
    if isinstance(per_windows, dict):
        sanitized_w: Dict[str, int] = {}
        for k, v in per_windows.items():
            key = str(k).strip()
            if key:
                sanitized_w[key] = _ensure_positive_int(v, 60)
        config['per_keyword_windows'] = sanitized_w

    contains = raw.get('contains_keywords')
    sanitized_contains: List[Dict[str, Any]] = []
    if isinstance(contains, list):
        seen: set[str] = set()
        for item in contains:
            if isinstance(item, dict):
                keyword = str(item.get('keyword', '')).strip()
                if not keyword:
                    continue
                key_lower = keyword.lower()
                if key_lower in seen:
                    continue
                thr = _ensure_positive_int(item.get('threshold', 1), 1)
                win = _ensure_positive_int(item.get('window', 60), 60)
                sanitized_contains.append({
                    'keyword': keyword,
                    'threshold': thr,
                    'window': win,
                })
                seen.add(key_lower)
            elif isinstance(item, str):
                keyword = item.strip()
                if not keyword:
                    continue
                key_lower = keyword.lower()
                if key_lower in seen:
                    continue
                sanitized_contains.append({
                    'keyword': keyword,
                    'threshold': 1,
                    'window': 60,
                })
                seen.add(key_lower)
    config['contains_keywords'] = sanitized_contains

    return config


def _sanitize_counts(raw: Dict[str, Any]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    if not isinstance(raw, dict):
        return counts
    for k, v in raw.items():
        key = str(k).strip()
        if not key:
            continue
        try:
            ival = int(v)
        except Exception:
            continue
        if ival < 0:
            ival = 0
        counts[key.lower()] = ival
    return counts


def _sanitize_data(raw: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    data: Dict[str, Any] = {}
    if 'config' in raw or 'counts' in raw:
        data['config'] = _sanitize_config(raw.get('config', {}))
        data['counts'] = _sanitize_counts(raw.get('counts', {}))
    else:
        data['config'] = _sanitize_config(raw)
        data['counts'] = {}
    return data


def _load_legacy_counts() -> Dict[str, int]:
    try:
        if LEGACY_COUNTS_JSON.exists():
            raw = json.loads(LEGACY_COUNTS_JSON.read_text(encoding='utf-8'))
            return _sanitize_counts(raw)
    except Exception:
        pass
    return {}


def _load_data() -> Dict[str, Any]:
    if CONFIG_PATH.exists():
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
            data = _sanitize_data(raw)
            if data.get('counts') == {}:
                legacy_counts = _load_legacy_counts()
                if legacy_counts:
                    data['counts'] = legacy_counts
            return data
        except Exception:
            pass

    legacy_config = {
        'keywords': _load_legacy_keywords(),
        'global_threshold': _load_legacy_threshold(),
        'per_keyword_thresholds': _load_legacy_map(LEGACY_THRESHOLDS_JSON, 'KEYWORD_THRESHOLDS'),
        'global_window': _load_legacy_window(),
        'per_keyword_windows': _load_legacy_map(LEGACY_WINDOWS_JSON, 'KEYWORD_WINDOWS'),
    }
    data = {
        'config': _sanitize_config(legacy_config),
        'counts': _load_legacy_counts(),
    }
    return data


def _write_data(data: Dict[str, Any]) -> None:
    sanitized = {
        'config': _sanitize_config(data.get('config', {})),
        'counts': _sanitize_counts(data.get('counts', {})),
    }
    CONFIG_PATH.write_text(json.dumps(sanitized, ensure_ascii=False, indent=2), encoding='utf-8')


def load_config() -> Dict[str, Any]:
    return _load_data()['config']


def save_config(config: Dict[str, Any]) -> None:
    data = _load_data()
    data['config'] = _sanitize_config(config)
    _write_data(data)


def load_counts() -> Dict[str, int]:
    return _load_data()['counts']


def save_counts(counts: Dict[str, int]) -> None:
    data = _load_data()
    data['counts'] = _sanitize_counts(counts)
    _write_data(data)

