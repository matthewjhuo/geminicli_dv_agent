"""Project-local SQLite waveform indexing and bounded event queries.

VCD ingestion streams a whitespace-token subset in two passes and rejects unsupported
body tokens. FSDB sample input uses numeric nanoseconds; SQLite REAL uses floating
point, not arbitrary-precision simulator time. Pruning only recognizes clock names
when explicitly enabled and records constant first-observed times. Activity fractions
are not EDA bit toggle coverage. Backward windows are event history, not causal proof.
Database publication is atomic and does not overwrite existing files by default.
"""

from __future__ import annotations

import contextlib
import fnmatch
import json
import hashlib
import os
import tempfile
import math
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

_CLOCK_NAME_RE = re.compile(r"(^|_)(clk|clock|pclk|aclk|hclk|sclk)($|_|[0-9])", re.IGNORECASE)
_TIMESCALE_RE = re.compile(r"\$timescale\s+(.*?)\s+\$end", re.DOTALL | re.IGNORECASE)
_SCALAR_CHARS = frozenset("01xXzZ")


@dataclass
class _SignalDef:
    sig_id: int
    name: str
    varid: str
    vartype: str
    varsize: int
    varscope: str
    full_path: str


@dataclass
class _VarStats:
    initial_value: Optional[str] = None
    first_time: Optional[float] = None
    last_value: Optional[str] = None
    toggle_count: int = 0
    strictly_alternating_01: bool = True
    seen_non_01: bool = False


def _normalize_vcd_value(raw: str) -> str:
    val = raw.strip()
    if val.startswith(("b", "B")):
        bits = val[1:].lstrip("0") or "0"
        return "b" + bits.lower()
    if val.startswith(("r", "R")):
        return "r" + val[1:].strip()
    return val.lower()


def _value_matches(stored: str, target: str) -> bool:
    """Flexible lossless value matching supporting scalar, binary ``b...``, and hex ``0x...``."""
    s = (stored or "").strip().lower()
    t = (target or "").strip().lower()
    if s == t:
        return True
    if s.startswith("b") and s[1:] == t:
        return True
    if t.startswith("b") and t[1:] == s:
        return True
    # Compare integer interpretations for binary/hex/decimal representations without x/z
    def _to_int(v: str) -> Optional[int]:
        if v in {"0", "1"}:
            return int(v)
        if v.startswith("b"):
            body = v[1:]
            if body and set(body) <= {"0", "1"}:
                return int(body, 2)
            return None
        if v.startswith("0x"):
            try:
                return int(v, 16)
            except ValueError:
                return None
        if v.startswith("0b"):
            try:
                return int(v, 2)
            except ValueError:
                return None
        if v.isdigit():
            try:
                return int(v, 10)
            except ValueError:
                return None
        return None

    si = _to_int(s)
    ti = _to_int(t)
    return si is not None and ti is not None and si == ti


def _format_value_hex(stored: str, varsize: int) -> Optional[str]:
    if not stored:
        return None
    s = stored.strip().lower()
    if s.startswith("b"):
        body = s[1:]
        if body and set(body) <= {"0", "1"}:
            width_hex = max(1, (max(1, varsize) + 3) // 4)
            return f"0x{int(body, 2):0{width_hex}x}"
    elif s.startswith("0x"):
        return s
    return None


def _iter_vcd_header_tokens(lines: Iterable[str]) -> Tuple[str, List[_SignalDef], int]:
    """Parse VCD header definitions until ``$enddefinitions $end``."""
    header_chunks: List[str] = []
    body_start_line = 0
    for idx, raw_line in enumerate(lines, start=1):
        header_chunks.append(raw_line)
        if "$enddefinitions" in raw_line:
            body_start_line = idx
            break
    if not body_start_line or "$enddefinitions $end" not in " ".join("".join(header_chunks).split()):
        raise ValueError("VCD requires a terminated enddefinitions header")
    last_header_line = header_chunks[-1]
    if last_header_line.split("$enddefinitions", 1)[1].split("$end", 1)[1].strip():
        raise ValueError("Place VCD body on a new line after enddefinitions")
    header_text = "".join(header_chunks)

    ts_match = _TIMESCALE_RE.search(header_text)
    timescale = " ".join(ts_match.group(1).split()) if ts_match else "1ns"

    tokens = header_text.split()
    scope_stack: List[str] = []
    signals: List[_SignalDef] = []
    seen_paths: set[str] = set()
    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if tok == "$scope":
            # $scope <type> <identifier> $end
            if i + 2 < n:
                scope_stack.append(tokens[i + 2])
            i += 3
            while i < n and tokens[i] != "$end":
                i += 1
        elif tok == "$upscope":
            if scope_stack:
                scope_stack.pop()
            while i < n and tokens[i] != "$end":
                i += 1
        elif tok == "$var":
            # $var <type> <size> <id_code> <reference> [bit_range] $end
            end_i = i + 1
            while end_i < n and tokens[end_i] != "$end":
                end_i += 1
            parts = tokens[i + 1 : end_i]
            if len(parts) >= 4:
                vartype = parts[0]
                try:
                    varsize = int(parts[1])
                except ValueError:
                    varsize = 1
                varid = parts[2]
                name = parts[3]
                varscope = ".".join(scope_stack) if scope_stack else "top"
                full_path = f"{varscope}.{name}" if varscope else name
                if full_path not in seen_paths:
                    seen_paths.add(full_path)
                    signals.append(
                        _SignalDef(
                            sig_id=len(signals) + 1,
                            name=name,
                            varid=varid,
                            vartype=vartype,
                            varsize=varsize,
                            varscope=varscope,
                            full_path=full_path,
                        )
                    )
            i = end_i
        elif tok == "$enddefinitions":
            break
        i += 1

    return timescale, signals, body_start_line


def _parse_vcd_change_line(line: str) -> Optional[Tuple[str, str]]:
    """Return ``(varid, normalized_value)`` for a VCD value-change line, or None."""
    s = line.strip()
    if not s or s.startswith("$") or s.startswith("#"):
        return None
    first = s[0]
    if first in _SCALAR_CHARS and len(s) >= 2 and " " not in s:
        return s[1:], first.lower()
    if first in ("b", "B", "r", "R"):
        parts = s.split()
        if len(parts) >= 2:
            return parts[1], _normalize_vcd_value(parts[0])
    return None


def _iter_vcd_changes(lines):
    """Whitespace-token VCD body subset: timestamps, scalar/vector/real values and dump directives.

    Reject unsupported tokens instead of silently losing events. Escaped references live
    in the header; identifier codes here are opaque tokens.
    """
    tokens = iter(token for line in lines for token in line.split())
    timestamp = 0
    for token in tokens:
        if token.startswith("#"):
            try:
                value = int(token[1:])
            except ValueError as exc:
                raise ValueError(f"Invalid VCD timestamp: {token}") from exc
            if value < timestamp:
                raise ValueError("VCD timestamps must be nondecreasing")
            timestamp = value
        elif token in {"$dumpvars", "$dumpall", "$dumpon", "$dumpoff", "$end"}:
            continue
        elif token == "$comment":
            for comment in tokens:
                if comment == "$end":
                    break
            else:
                raise ValueError("Unterminated VCD comment")
        elif token[0] in "bBrR":
            try:
                code = next(tokens)
            except StopIteration as exc:
                raise ValueError("Missing VCD vector identifier") from exc
            yield timestamp, code, _normalize_vcd_value(token)
        elif token[0] in _SCALAR_CHARS and len(token) > 1:
            yield timestamp, token[1:], token[0].lower()
        else:
            raise ValueError(f"Unsupported or malformed VCD body token: {token!r}")


def _init_waveform_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS signal_changes;
        DROP TABLE IF EXISTS signal_metadata;
        DROP TABLE IF EXISTS ingestion_meta;

        CREATE TABLE signal_metadata (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            varid TEXT NOT NULL,
            vartype TEXT NOT NULL,
            varsize INTEGER NOT NULL,
            varscope TEXT NOT NULL,
            full_path TEXT NOT NULL UNIQUE,
            initial_value TEXT,
            first_time REAL,
            toggle_count INTEGER NOT NULL DEFAULT 0,
            pruned_reason TEXT
        );

        CREATE TABLE signal_changes (
            id INTEGER NOT NULL,
            time REAL NOT NULL,
            value TEXT NOT NULL,
            FOREIGN KEY(id) REFERENCES signal_metadata(id)
        );

        CREATE TABLE ingestion_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE INDEX idx_sm_scope_name ON signal_metadata(varscope, name);
        CREATE INDEX idx_sm_full_path ON signal_metadata(full_path);
        """
    )


def _create_change_indexes(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE INDEX idx_sc_id_time ON signal_changes(id, time);
        CREATE INDEX idx_sc_time ON signal_changes(time);
        """
    )


def _classify_pruning(
    signals: Sequence[_SignalDef],
    var_stats: Dict[str, _VarStats],
    total_timestamps: int,
    *,
    prune_clocks: bool,
    prune_zero_toggle: bool,
    clock_toggle_ratio_threshold: float,
) -> Dict[int, Optional[str]]:
    reasons: Dict[int, Optional[str]] = {}
    for sig in signals:
        st = var_stats.get(sig.varid)
        toggles = st.toggle_count if st else 0
        reason: Optional[str] = None
        if prune_clocks and sig.varsize == 1:
            name_is_clk = bool(_CLOCK_NAME_RE.search(sig.name))
            if name_is_clk and toggles >= 2:
                reason = "clock"
        if reason is None and prune_zero_toggle and toggles == 0:
            reason = "zero_toggle"
        reasons[sig.sig_id] = reason
    return reasons


def _ingest_vcd(
    vcd_path: Path | str,
    db_path: Path | str,
    *,
    prune_clocks: bool = True,
    prune_zero_toggle: bool = True,
    clock_toggle_ratio_threshold: float = 0.85,
    batch_size: int = 5000,
) -> Dict[str, Any]:
    """Stream-ingest a VCD file into a BTTF relational SQLite ``waves.db``.

    Uses a 2-pass streaming scan so RAM stays O(#signals) even on large dumps,
    and performs batched ``executemany`` writes for high throughput.
    """
    src = Path(vcd_path)
    dst = Path(db_path)
    if not src.is_file():
        raise FileNotFoundError(f"VCD file not found: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)

    # Pass 1: parse header and compute per-varid toggle statistics
    with src.open("r", encoding="utf-8", errors="replace") as fh:
        timescale, signals, body_start_line = _iter_vcd_header_tokens(fh)
        var_stats: Dict[str, _VarStats] = {s.varid: _VarStats() for s in signals}
        timestamps_seen = 0
        last_timestamp = None
        raw_change_count = 0
        for cur_time, varid, val in _iter_vcd_changes(fh):
            if cur_time != last_timestamp:
                timestamps_seen += 1
                last_timestamp = cur_time
            st = var_stats.get(varid)
            if st is None:
                raise ValueError(f"Undefined VCD identifier: {varid!r}")
            raw_change_count += 1
            if val not in {"0", "1"}:
                st.seen_non_01 = True
            if st.initial_value is None:
                st.initial_value = val
                st.first_time = cur_time
            elif val != st.last_value:
                st.toggle_count += 1
            st.last_value = val

    pruned_reasons = _classify_pruning(
        signals,
        var_stats,
        timestamps_seen,
        prune_clocks=prune_clocks,
        prune_zero_toggle=prune_zero_toggle,
        clock_toggle_ratio_threshold=clock_toggle_ratio_threshold,
    )

    active_varid_to_ids: Dict[str, List[int]] = {}
    for sig in signals:
        if pruned_reasons[sig.sig_id] is None:
            active_varid_to_ids.setdefault(sig.varid, []).append(sig.sig_id)

    # Pass 2: populate SQLite with batched executemany
    stored_change_count = 0
    conn = sqlite3.connect(str(dst))
    try:
        conn.execute("PRAGMA synchronous = OFF")
        conn.execute("PRAGMA temp_store = MEMORY")
        with conn:
            _init_waveform_schema(conn)
            meta_rows = [
                (
                    s.sig_id,
                    s.name,
                    s.varid,
                    s.vartype,
                    s.varsize,
                    s.varscope,
                    s.full_path,
                    var_stats[s.varid].initial_value,
                    var_stats[s.varid].first_time,
                    var_stats[s.varid].toggle_count,
                    pruned_reasons[s.sig_id],
                )
                for s in signals
            ]
            conn.executemany(
                """
                INSERT INTO signal_metadata
                    (id, name, varid, vartype, varsize, varscope, full_path, initial_value, first_time, toggle_count, pruned_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                meta_rows,
            )

            batch: List[Tuple[int, int, str]] = []
            last_emitted: Dict[str, str] = {}
            cur_time = 0
            with src.open("r", encoding="utf-8", errors="replace") as fh:
                for _ in range(body_start_line):
                    next(fh, None)
                for cur_time, varid, val in _iter_vcd_changes(fh):
                    sig_ids = active_varid_to_ids.get(varid)
                    if not sig_ids:
                        continue
                    if last_emitted.get(varid) == val:
                        continue
                    last_emitted[varid] = val
                    for sid in sig_ids:
                        batch.append((sid, cur_time, val))
                    if len(batch) >= batch_size:
                        conn.executemany("INSERT INTO signal_changes (id, time, value) VALUES (?, ?, ?)", batch)
                        stored_change_count += len(batch)
                        batch.clear()
            if batch:
                conn.executemany(
                    "INSERT INTO signal_changes (id, time, value) VALUES (?, ?, ?)", batch
                )
                stored_change_count += len(batch)
                batch.clear()

            _create_change_indexes(conn)

            pruned_clocks_n = sum(1 for r in pruned_reasons.values() if r == "clock")
            pruned_zt_n = sum(1 for r in pruned_reasons.values() if r == "zero_toggle")
            active_n = len(signals) - pruned_clocks_n - pruned_zt_n
            reduction_pct = (
                round((1.0 - (stored_change_count / float(raw_change_count))) * 100.0, 2)
                if raw_change_count > 0
                else 0.0
            )
            stats = {
                "timescale": timescale,
                "prune_clocks": bool(prune_clocks),
                "prune_zero_toggle": bool(prune_zero_toggle),
                "raw_signal_count": len(signals),
                "active_signal_count": active_n,
                "pruned_clock_signals": pruned_clocks_n,
                "pruned_zero_toggle_signals": pruned_zt_n,
                "raw_change_count": raw_change_count,
                "stored_change_count": stored_change_count,
                "change_reduction_pct": reduction_pct,
                "active_toggle_coverage_preserved": None,
                "coverage_limitations": "Signal activity fraction only; preservation is not independently measured and this is not EDA bit toggle coverage.",
            }
            conn.executemany(
                "INSERT INTO ingestion_meta (key, value) VALUES (?, ?)",
                [(k, json.dumps(v)) for k, v in stats.items()],
            )
        conn.execute("PRAGMA synchronous = FULL")
    finally:
        conn.close()

    return stats


def _ingest_fsdb_samples(
    samples: Sequence[Dict[str, Any]],
    db_path: Path | str,
    *,
    scope: str = "top",
    timescale: str = "1ns",
    prune_clocks: bool = True,
    prune_zero_toggle: bool = True,
    clock_toggle_ratio_threshold: float = 0.85,
) -> Dict[str, Any]:
    """Normalize Verdi ``extract_fsdb_slice`` sample rows into the same BTTF SQLite schema."""
    dst = Path(db_path)
    dst.parent.mkdir(parents=True, exist_ok=True)

    # Collect signal columns preserving order
    sig_names: List[str] = []
    for row in samples:
        for k in row.keys():
            if k in {"time_ns", "time"}:
                continue
            if k not in sig_names:
                sig_names.append(k)

    signals: List[_SignalDef] = []
    for idx, raw_name in enumerate(sig_names, start=1):
        if "." in raw_name:
            sig_scope, leaf = raw_name.rsplit(".", 1)
        else:
            sig_scope, leaf = scope, raw_name
        full_path = f"{sig_scope}.{leaf}" if sig_scope else leaf
        # Infer bit width heuristically from observed values
        inferred_size = 1
        for row in samples:
            val = row.get(raw_name)
            if val is None:
                continue
            sval = str(val).strip().lower()
            if sval.startswith("0x"):
                inferred_size = max(inferred_size, max(1, (len(sval) - 2) * 4))
            elif sval.startswith("b"):
                inferred_size = max(inferred_size, max(1, len(sval) - 1))
            elif sval not in {"0", "1", "x", "z"}:
                inferred_size = max(inferred_size, 32)
        signals.append(
            _SignalDef(
                sig_id=idx,
                name=leaf,
                varid=raw_name,
                vartype="wire",
                varsize=inferred_size,
                varscope=sig_scope,
                full_path=full_path,
            )
        )

    var_stats: Dict[str, _VarStats] = {s.varid: _VarStats() for s in signals}
    raw_change_count = 0
    sorted_rows = sorted(samples, key=lambda r: float(r.get("time_ns", r.get("time", 0))))
    for row in sorted_rows:
        for sig in signals:
            if sig.varid not in row:
                continue
            val = str(row[sig.varid]).strip().lower()
            raw_change_count += 1
            st = var_stats[sig.varid]
            if val not in {"0", "1"}:
                st.seen_non_01 = True
                st.strictly_alternating_01 = False
            if st.initial_value is None:
                st.initial_value = val
                st.first_time = float(row.get("time_ns", row.get("time", 0)))
                st.last_value = val
            elif val != st.last_value:
                st.toggle_count += 1
                st.last_value = val

    pruned_reasons = _classify_pruning(
        signals,
        var_stats,
        len(sorted_rows),
        prune_clocks=prune_clocks,
        prune_zero_toggle=prune_zero_toggle,
        clock_toggle_ratio_threshold=clock_toggle_ratio_threshold,
    )

    conn = sqlite3.connect(str(dst))
    stored_change_count = 0
    try:
        with conn:
            _init_waveform_schema(conn)
            conn.executemany(
                """
                INSERT INTO signal_metadata
                    (id, name, varid, vartype, varsize, varscope, full_path, initial_value, first_time, toggle_count, pruned_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        s.sig_id,
                        s.name,
                        s.varid,
                        s.vartype,
                        s.varsize,
                        s.varscope,
                        s.full_path,
                        var_stats[s.varid].initial_value,
                    var_stats[s.varid].first_time,
                        var_stats[s.varid].toggle_count,
                        pruned_reasons[s.sig_id],
                    )
                    for s in signals
                ],
            )
            last_emitted: Dict[int, str] = {}
            change_rows: List[Tuple[int, int, str]] = []
            for row in sorted_rows:
                t_val = float(row.get("time_ns", row.get("time", 0)))
                if not math.isfinite(t_val) or t_val < 0:
                    raise ValueError("Sample time must be finite and nonnegative")
                for sig in signals:
                    if pruned_reasons[sig.sig_id] is not None or sig.varid not in row:
                        continue
                    val = str(row[sig.varid]).strip().lower()
                    if last_emitted.get(sig.sig_id) == val:
                        continue
                    last_emitted[sig.sig_id] = val
                    change_rows.append((sig.sig_id, t_val, val))
            if change_rows:
                conn.executemany("INSERT INTO signal_changes (id, time, value) VALUES (?, ?, ?)", change_rows)
                stored_change_count = len(change_rows)
            _create_change_indexes(conn)

            pruned_clocks_n = sum(1 for r in pruned_reasons.values() if r == "clock")
            pruned_zt_n = sum(1 for r in pruned_reasons.values() if r == "zero_toggle")
            active_n = len(signals) - pruned_clocks_n - pruned_zt_n
            reduction_pct = (
                round((1.0 - (stored_change_count / float(raw_change_count))) * 100.0, 2)
                if raw_change_count > 0
                else 0.0
            )
            stats = {
                "timescale": timescale,
                "prune_clocks": bool(prune_clocks),
                "prune_zero_toggle": bool(prune_zero_toggle),
                "raw_signal_count": len(signals),
                "active_signal_count": active_n,
                "pruned_clock_signals": pruned_clocks_n,
                "pruned_zero_toggle_signals": pruned_zt_n,
                "raw_change_count": raw_change_count,
                "stored_change_count": stored_change_count,
                "change_reduction_pct": reduction_pct,
                "active_toggle_coverage_preserved": None,
                "coverage_limitations": "Signal activity fraction only; preservation is not independently measured and this is not EDA bit toggle coverage.",
            }
            conn.executemany(
                "INSERT INTO ingestion_meta (key, value) VALUES (?, ?)",
                [(k, json.dumps(v)) for k, v in stats.items()],
            )
    finally:
        conn.close()
    return stats


class WaveformStore:
    """Read-only deterministic SQLite query engine over a BTTF ``waves.db``."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        if not self.db_path.is_file():
            raise FileNotFoundError(f"Waveform SQLite database not found: {self.db_path}")

    @contextlib.contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path.resolve().as_uri() + "?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def stats(self) -> Dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute("SELECT key, value FROM ingestion_meta").fetchall()
        out: Dict[str, Any] = {}
        for r in rows:
            try:
                out[r["key"]] = json.loads(r["value"])
            except json.JSONDecodeError:
                out[r["key"]] = r["value"]
        return out

    def _resolve_signal(
        self, conn: sqlite3.Connection, signal_name: str, scope: Optional[str] = None
    ) -> sqlite3.Row:
        # 1. Exact full_path match
        row = conn.execute("SELECT * FROM signal_metadata WHERE full_path = ?", (signal_name,)).fetchone()
        if row is not None:
            return row
        # 2. Scope + leaf name match
        if scope:
            row = conn.execute(
                "SELECT * FROM signal_metadata WHERE varscope = ? AND name = ?",
                (scope, signal_name),
            ).fetchone()
            if row is not None:
                return row
            row = conn.execute(
                "SELECT * FROM signal_metadata WHERE full_path = ?",
                (f"{scope}.{signal_name}",),
            ).fetchone()
            if row is not None:
                return row
            raise KeyError(f"Signal {signal_name!r} not found in requested scope {scope!r}")
        # 3. Unique leaf name match across scopes
        rows = conn.execute("SELECT * FROM signal_metadata WHERE name = ?", (signal_name,)).fetchall()
        if len(rows) == 1:
            return rows[0]
        if len(rows) > 1:
            choices = [r["full_path"] for r in rows[:5]]
            raise ValueError(
                f"Ambiguous signal name {signal_name!r}; specify scope or full_path. Matches: {choices}"
            )
        raise KeyError(f"Signal not found in waveform store: {signal_name!r}")

    def find_signal_in_scope(
        self,
        pattern: str = "*",
        scope: Optional[str] = None,
        *,
        include_pruned: bool = True,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self._connect() as conn:
            sql = "SELECT * FROM signal_metadata"
            params: List[Any] = []
            clauses: List[str] = []
            if scope:
                clauses.append("(varscope = ? OR varscope LIKE ? ESCAPE '!')")
                params.extend([scope, scope.replace("!", "!!").replace("%", "!%").replace("_", "!_") + ".%"])
            if not include_pruned:
                clauses.append("pruned_reason IS NULL")
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY id ASC"
            rows = conn.execute(sql, params).fetchall()

        pat_low = (pattern or "*").lower()
        use_glob = any(ch in pat_low for ch in ("*", "?", "["))
        matches: List[Dict[str, Any]] = []
        for r in rows:
            name_l = r["name"].lower()
            full_l = r["full_path"].lower()
            hit = (
                fnmatch.fnmatch(name_l, pat_low) or fnmatch.fnmatch(full_l, pat_low)
                if use_glob
                else (pat_low in name_l or pat_low in full_l)
            )
            if hit:
                matches.append(dict(r))
                if len(matches) >= limit:
                    break
        return matches

    def get_value_at_time(
        self, signal_name: str, time: int, scope: Optional[str] = None
    ) -> Dict[str, Any]:
        t_query = _checked_time(time)
        with self._connect() as conn:
            sig = self._resolve_signal(conn, signal_name, scope=scope)
            if sig["pruned_reason"] == "zero_toggle":
                known = sig["first_time"] is not None and t_query >= sig["first_time"]
                val = sig["initial_value"] if known else None
                return {
                    "signal": sig["full_path"],
                    "varsize": sig["varsize"],
                    "query_time": t_query,
                    "sample_time": sig["first_time"] if known else None,
                    "value": val,
                    "hex_value": _format_value_hex(val or "", sig["varsize"]),
                    "pruned_reason": "zero_toggle",
                }
            if sig["pruned_reason"] == "clock":
                return {
                    "signal": sig["full_path"],
                    "varsize": sig["varsize"],
                    "query_time": t_query,
                    "sample_time": None,
                    "value": None,
                    "hex_value": None,
                    "pruned_reason": "clock",
                    "note": "Clock signal transitions were pruned during ingestion; re-ingest with prune_clocks=False if clock edges are needed.",
                }
            row = conn.execute(
                "SELECT time, value FROM signal_changes WHERE id = ? AND time <= ? ORDER BY time DESC, rowid DESC LIMIT 1",
                (sig["id"], t_query),
            ).fetchone()
            val = row["value"] if row is not None else None
            sample_t = row["time"] if row is not None else None
            return {
                "signal": sig["full_path"],
                "varsize": sig["varsize"],
                "query_time": t_query,
                "sample_time": sample_t,
                "value": val,
                "hex_value": _format_value_hex(val or "", sig["varsize"]),
                "pruned_reason": None,
            }

    def get_first_time_of_value(
        self,
        signal_name: str,
        value: str,
        *,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        scope: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self._connect() as conn:
            sig = self._resolve_signal(conn, signal_name, scope=scope)
            if sig["pruned_reason"] == "zero_toggle":
                first = max(_checked_time(start_time) if start_time is not None else 0, sig["first_time"] or 0)
                matched = sig["first_time"] is not None and _value_matches(sig["initial_value"] or "", value)
                matched = matched and (end_time is None or first <= _checked_time(end_time))
                return {
                    "signal": sig["full_path"],
                    "target_value": value,
                    "found": matched,
                    "first_time": first if matched else None,
                    "matched_value": sig["initial_value"] if matched else None,
                    "pruned_reason": "zero_toggle",
                }
            if sig["pruned_reason"] == "clock":
                return {
                    "signal": sig["full_path"],
                    "target_value": value,
                    "found": False,
                    "first_time": None,
                    "pruned_reason": "clock",
                }
            if start_time is not None:
                start = _checked_time(start_time)
                held = conn.execute("SELECT time, value FROM signal_changes WHERE id = ? AND time <= ? ORDER BY time DESC, rowid DESC LIMIT 1", (sig["id"], start)).fetchone()
                if held is not None and _value_matches(held["value"], value) and (end_time is None or start <= _checked_time(end_time)):
                    return {"signal": sig["full_path"], "target_value": value, "found": True, "first_time": start, "matched_value": held["value"], "pruned_reason": None}
            sql = "SELECT time, value FROM signal_changes WHERE id = ?"
            params: List[Any] = [sig["id"]]
            if start_time is not None:
                sql += " AND time >= ?"
                params.append(_checked_time(start_time))
            if end_time is not None:
                sql += " AND time <= ?"
                params.append(_checked_time(end_time))
            sql += " ORDER BY time ASC, rowid ASC"
            for row in conn.execute(sql, params):
                if _value_matches(row["value"], value):
                    return {
                        "signal": sig["full_path"],
                        "target_value": value,
                        "found": True,
                        "first_time": row["time"],
                        "matched_value": row["value"],
                        "hex_value": _format_value_hex(row["value"], sig["varsize"]),
                        "pruned_reason": None,
                    }
            return {
                "signal": sig["full_path"],
                "target_value": value,
                "found": False,
                "first_time": None,
                "pruned_reason": None,
            }

    def get_signal_toggles(
        self,
        signal_name: str,
        *,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        scope: Optional[str] = None,
        limit: int = 200,
    ) -> Dict[str, Any]:
        limit = max(1, min(int(limit), 2000))
        with self._connect() as conn:
            sig = self._resolve_signal(conn, signal_name, scope=scope)
            if sig["pruned_reason"] is not None:
                return {
                    "signal": sig["full_path"],
                    "pruned_reason": sig["pruned_reason"],
                    "total_toggles": sig["toggle_count"],
                    "window_toggles": None if sig["pruned_reason"] == "clock" else 0,
                    "truncated": False,
                    "transitions": [],
                }
            # Get preceding value before start_time if start_time is provided
            prev_val: Optional[str] = None
            if start_time is not None:
                prev_row = conn.execute(
                    "SELECT value FROM signal_changes WHERE id = ? AND time < ? ORDER BY time DESC, rowid DESC LIMIT 1",
                    (sig["id"], _checked_time(start_time)),
                ).fetchone()
                if prev_row is not None:
                    prev_val = prev_row["value"]

            sql = "SELECT time, value FROM signal_changes WHERE id = ?"
            params: List[Any] = [sig["id"]]
            if start_time is not None:
                sql += " AND time >= ?"
                params.append(_checked_time(start_time))
            if end_time is not None:
                sql += " AND time <= ?"
                params.append(_checked_time(end_time))
            sql += " ORDER BY time ASC, rowid ASC"

            transitions: List[Dict[str, Any]] = []
            total_in_window = 0
            for row in conn.execute(sql, params):
                val = row["value"]
                if prev_val is not None and val != prev_val:
                    total_in_window += 1
                    if len(transitions) < limit:
                        transitions.append(
                            {
                                "time": row["time"],
                                "from": prev_val,
                                "to": val,
                                "to_hex": _format_value_hex(val, sig["varsize"]),
                            }
                        )
                prev_val = val

            return {
                "signal": sig["full_path"],
                "pruned_reason": None,
                "total_toggles": sig["toggle_count"],
                "window_toggles": total_in_window,
                "truncated": total_in_window > len(transitions),
                "transitions": transitions,
            }

    def get_scope_toggle_coverage(self, scope: Optional[str] = None) -> Dict[str, Any]:
        with self._connect() as conn:
            sql = "SELECT varscope, name, full_path, toggle_count, pruned_reason FROM signal_metadata"
            params: List[Any] = []
            if scope:
                sql += " WHERE varscope = ? OR varscope LIKE ? ESCAPE '!'"
                params.extend([scope, scope.replace("!", "!!").replace("%", "!%").replace("_", "!_") + ".%"])
            rows = conn.execute(sql, params).fetchall()

        by_scope: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            sc = r["varscope"]
            bucket = by_scope.setdefault(
                sc,
                {
                    "scope": sc,
                    "total_signals": 0,
                    "non_clock_signals": 0,
                    "toggled_signals": 0,
                    "zero_toggle_signals": [],
                    "clock_signals": [],
                },
            )
            bucket["total_signals"] += 1
            if r["pruned_reason"] == "clock":
                bucket["clock_signals"].append(r["name"])
            else:
                bucket["non_clock_signals"] += 1
                if r["toggle_count"] > 0:
                    bucket["toggled_signals"] += 1
                else:
                    bucket["zero_toggle_signals"].append(r["name"])

        for bucket in by_scope.values():
            denom = bucket["non_clock_signals"]
            bucket["toggle_coverage_pct"] = (
                round((bucket["toggled_signals"] / float(denom)) * 100.0, 2) if denom > 0 else 100.0
            )
        return {
            "scope_filter": scope,
            "scopes": sorted(by_scope.values(), key=lambda x: x["scope"]),
            "metric": "fraction_of_non_clock_signals_with_any_transition",
            "limitations": "Not bitwise 0-to-1/1-to-0 EDA toggle coverage; no elaboration or reachability analysis",
        }

    def backward_trace_window(
        self,
        violation_time: int,
        signals: Sequence[str],
        *,
        window_before: int = 50,
        scope: Optional[str] = None,
        limit_per_signal: int = 50,
    ) -> Dict[str, Any]:
        """Backward event history over ``[max(0, violation_time - window_before), violation_time]``.

        Restricts event inspection (``t <= violation_time``) for SVA / scoreboard failures.
        """
        v_time = _checked_time(violation_time)
        start_t = max(0, v_time - max(0, _checked_time(window_before)))
        values_at_violation: Dict[str, Dict[str, Any]] = {}
        per_signal_transitions: Dict[str, List[Dict[str, Any]]] = {}
        causal_timeline: List[Dict[str, Any]] = []

        for sig_name in signals:
            val_info = self.get_value_at_time(sig_name, v_time, scope=scope)
            full_path = val_info["signal"]
            values_at_violation[full_path] = val_info
            tog = self.get_signal_toggles(
                full_path,
                start_time=start_t,
                end_time=v_time,
                limit=limit_per_signal,
            )
            per_signal_transitions[full_path] = tog["transitions"]
            for tr in tog["transitions"]:
                causal_timeline.append(
                    {
                        "time": tr["time"],
                        "delta_before_violation": v_time - tr["time"],
                        "signal": full_path,
                        "from": tr["from"],
                        "to": tr["to"],
                        "to_hex": tr["to_hex"],
                    }
                )

        causal_timeline.sort(key=lambda e: (e["time"], e["signal"]))
        return {
            "violation_time": v_time,
            "window": [start_t, v_time],
            "values_at_violation": values_at_violation,
            "transitions_by_signal": per_signal_transitions,
            "causal_timeline": causal_timeline,
            "event_timeline": causal_timeline,
            "limitations": "Time-ordered events only; no RTL driver, cone-of-influence or causal inference. causal_timeline is a legacy field alias.",
        }


def _checked_time(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("Time must be a finite nonnegative number")
    return value


def _atomic_ingest(builder, db_path, *, source=None, overwrite=False, **kwargs):
    dst = Path(db_path).resolve()
    if dst.exists() and not overwrite:
        raise FileExistsError(f"Waveform database already exists: {dst}; explicitly opt into overwrite")
    dst.parent.mkdir(parents=True, exist_ok=True)
    before = _file_digest(source) if source is not None else None
    fd, name = tempfile.mkstemp(prefix=".waves-", suffix=".db", dir=dst.parent)
    os.close(fd)
    tmp = Path(name)
    try:
        result = builder(tmp, **kwargs)
        if source is not None and _file_digest(source) != before:
            raise ValueError("Waveform source changed during ingestion")
        if overwrite:
            os.replace(tmp, dst)
        else:
            # Atomic no-clobber publication; both paths are on the same filesystem.
            if os.name == "nt":
                os.rename(tmp, dst)  # Windows rename fails atomically if destination exists.
            else:
                os.link(tmp, dst)
        return result
    finally:
        tmp.unlink(missing_ok=True)


def _file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def ingest_vcd(vcd_path, db_path, *, overwrite=False, **kwargs):
    return _atomic_ingest(lambda tmp, **opts: _ingest_vcd(vcd_path, tmp, **opts), db_path, source=vcd_path, overwrite=overwrite, **kwargs)


def ingest_fsdb_samples(samples, db_path, *, overwrite=False, **kwargs):
    timescale = kwargs.get("timescale", "1ns").replace(" ", "")
    if timescale != "1ns":
        raise ValueError("FSDB sample time_ns requires timescale=1ns")
    for row in samples:
        _checked_time(row.get("time_ns", row.get("time", 0)))
    return _atomic_ingest(lambda tmp, **opts: _ingest_fsdb_samples(samples, tmp, **opts), db_path, overwrite=overwrite, **kwargs)
