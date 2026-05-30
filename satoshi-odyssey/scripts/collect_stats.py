#!/usr/bin/env python3
"""
collect_stats.py
================
Motor de recolección de estadísticas históricas del nodo Lightning Network.
Escribe en SQLite con política de retención automática.

Métricas recolectadas:
  - Fees gastados en rebalanceos (listpayments)
  - Tiempo en línea / desconexiones (synced_to_chain por snapshot)
  - Canales abiertos, sats por canal (listchannels)
  - Liquidez total y ratio local/remoto
  - Nº transacciones y volumen enrutado (fwdinghistory)
  - Comisiones ganadas por enrutamiento
  - Rentabilidad neta (ganado - gastado)
  - Capital inactivo / canales zombie
  - Eficiencia de capital, diversidad de peers, concentración
"""

import os
import sys
import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

# ── Ajuste de sys.path para importar el paquete de gamificación ──────────────
# El paquete 'gamification/' reside en satoshi-odyssey/, un nivel arriba de scripts/.
# Se inserta en sys.path para que el import funcione tanto al llamar directamente
# (python3 scripts/collect_stats.py) como al invocar desde satoshi-odyssey/.
_SCRIPTS_DIR  = Path(__file__).resolve().parent          # satoshi-odyssey/scripts/
_LW_DIR       = _SCRIPTS_DIR.parent                       # satoshi-odyssey/
if str(_LW_DIR) not in sys.path:
    sys.path.insert(0, str(_LW_DIR))

from gamification.game_engine import evaluate_game_state, init_gamification_tables

# ── Configuración ──────────────────────────────────────────────────────────────
NETWORK   = os.environ.get("NETWORK",   "testnet4")
LNCLI_BIN = os.environ.get("LNCLI_BIN", "lncli-debug")

BASE_DIR = Path(__file__).parent.parent.resolve()
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH  = DATA_DIR / "node_history.db"

# Política de retención
MAX_SNAPSHOTS      = 5_000
MAX_DAILY_DAYS     = 365
MAX_HOURLY_HOURS   = 168   # 7 días

# Umbral de zombie: canal activo sin crecer en num_updates
ZOMBIE_LIFETIME_DAYS = 7
ZOMBIE_MIN_UPDATES   = 5   # mínimo esperado en ese período

# ── Utilidades ─────────────────────────────────────────────────────────────────
def now_ts():
    return int(datetime.now(timezone.utc).timestamp())

def run_lncli(*args, timeout=30):
    cmd = [LNCLI_BIN, f"-network={NETWORK}"] + list(args)
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=timeout)
        return json.loads(out)
    except Exception as e:
        print(f"  [WARN] lncli {args[0]}: {e}", file=sys.stderr)
        return None

# ── Schema ─────────────────────────────────────────────────────────────────────
SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Un registro por ejecución del colector
CREATE TABLE IF NOT EXISTS snapshots (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                      INTEGER NOT NULL,
    -- Nodo
    block_height            INTEGER DEFAULT 0,
    alias                   TEXT    DEFAULT '',
    pubkey                  TEXT    DEFAULT '',
    synced_to_chain         INTEGER DEFAULT 0,
    synced_to_graph         INTEGER DEFAULT 0,
    num_peers               INTEGER DEFAULT 0,
    -- Canales
    channels_active         INTEGER DEFAULT 0,
    channels_inactive       INTEGER DEFAULT 0,
    channels_pending_open   INTEGER DEFAULT 0,
    channels_pending_close  INTEGER DEFAULT 0,
    channels_total          INTEGER DEFAULT 0,
    -- Balances (sats)
    capacity_total          INTEGER DEFAULT 0,
    balance_local           INTEGER DEFAULT 0,
    balance_remote          INTEGER DEFAULT 0,
    wallet_confirmed        INTEGER DEFAULT 0,
    wallet_unconfirmed      INTEGER DEFAULT 0,
    -- Ratios
    liquidity_ratio         REAL    DEFAULT 0.0,
    capital_concentration   REAL    DEFAULT 0.0,
    -- Enrutamiento acumulado
    fwd_count_cum           INTEGER DEFAULT 0,
    fwd_amt_cum_sat         INTEGER DEFAULT 0,
    fwd_fees_cum_msat       INTEGER DEFAULT 0,
    fwd_avg_size_sat        INTEGER DEFAULT 0,
    -- Pagos salientes acumulados (rebalanceos incluidos)
    payments_count_cum      INTEGER DEFAULT 0,
    payments_fees_cum_msat  INTEGER DEFAULT 0,
    -- Capital inactivo
    zombie_channels         INTEGER DEFAULT 0,
    inactive_capital_sat    INTEGER DEFAULT 0,
    -- Eficiencia
    capital_efficiency      REAL    DEFAULT 0.0
);

-- Detalle por canal en cada snapshot
CREATE TABLE IF NOT EXISTS channel_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id     INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    ts              INTEGER NOT NULL,
    chan_id         TEXT    DEFAULT '',
    chan_point      TEXT    DEFAULT '',
    peer_alias      TEXT    DEFAULT '',
    remote_pubkey   TEXT    DEFAULT '',
    capacity        INTEGER DEFAULT 0,
    local_balance   INTEGER DEFAULT 0,
    remote_balance  INTEGER DEFAULT 0,
    local_ratio     REAL    DEFAULT 0.0,
    active          INTEGER DEFAULT 0,
    num_updates     INTEGER DEFAULT 0,
    updates_delta   INTEGER DEFAULT 0,
    uptime_sec      INTEGER DEFAULT 0,
    lifetime_sec    INTEGER DEFAULT 0,
    is_zombie       INTEGER DEFAULT 0
);

-- Resumen diario (hasta 365 días)
CREATE TABLE IF NOT EXISTS daily_stats (
    date                 TEXT    PRIMARY KEY,
    -- Deltas del día
    fwd_count_delta      INTEGER DEFAULT 0,
    fwd_amt_delta_sat    INTEGER DEFAULT 0,
    fees_earned_msat     INTEGER DEFAULT 0,
    fees_paid_msat       INTEGER DEFAULT 0,
    net_profit_msat      INTEGER DEFAULT 0,
    -- Promedios del día
    avg_liquidity_ratio  REAL    DEFAULT 0.0,
    avg_active_channels  REAL    DEFAULT 0.0,
    avg_capacity_sat     INTEGER DEFAULT 0,
    -- Eventos
    disconnections       INTEGER DEFAULT 0,
    zombie_channels      INTEGER DEFAULT 0,
    inactive_capital_sat INTEGER DEFAULT 0,
    -- Eficiencia
    capital_efficiency   REAL    DEFAULT 0.0,
    sample_count         INTEGER DEFAULT 0
);

-- Resumen horario (últimas 168h = 7 días)
CREATE TABLE IF NOT EXISTS hourly_stats (
    hour_ts              INTEGER PRIMARY KEY,
    fwd_count_delta      INTEGER DEFAULT 0,
    fwd_amt_delta_sat    INTEGER DEFAULT 0,
    fees_earned_msat     INTEGER DEFAULT 0,
    fees_paid_msat       INTEGER DEFAULT 0,
    avg_liquidity_ratio  REAL    DEFAULT 0.0,
    active_channels      INTEGER DEFAULT 0,
    zombie_channels      INTEGER DEFAULT 0,
    sample_count         INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_snap_ts      ON snapshots(ts);
CREATE INDEX IF NOT EXISTS idx_chansnap_sid ON channel_snapshots(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_chansnap_cid ON channel_snapshots(chan_id);

-- Logros/trofeos desbloqueables del sistema de gamificacion
CREATE TABLE IF NOT EXISTS achievements (
    id          TEXT    PRIMARY KEY,  -- identificador unico del logro (ej. 'primera_sangre')
    name        TEXT    NOT NULL,     -- nombre legible del logro
    description TEXT    DEFAULT '',   -- descripcion de como se obtiene
    emoji       TEXT    DEFAULT '🏆', -- icono visual
    unlocked_at INTEGER DEFAULT NULL, -- timestamp cuando se desbloqueo (NULL = bloqueado)
    snapshot_id INTEGER DEFAULT NULL  -- snapshot que detonó el desbloqueo
);

-- Records personales: maximos historicos de metricas clave
CREATE TABLE IF NOT EXISTS records (
    key        TEXT PRIMARY KEY,  -- nombre del record (ej. 'max_single_forward_sat')
    value      REAL DEFAULT 0,    -- valor numerico del record
    achieved_at INTEGER DEFAULT 0 -- timestamp cuando se registro
);
"""

# ── DB helpers ─────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn

def meta_get(conn, key, default=""):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default

def meta_set(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, str(value)))

def last_snapshot(conn):
    return conn.execute("SELECT * FROM snapshots ORDER BY ts DESC LIMIT 1").fetchone()

def prev_chan_updates(conn):
    """Devuelve {chan_id: num_updates} del snapshot más reciente."""
    rows = conn.execute("""
        SELECT cs.chan_id, cs.num_updates
        FROM channel_snapshots cs
        WHERE cs.snapshot_id = (SELECT id FROM snapshots ORDER BY ts DESC LIMIT 1)
    """).fetchall()
    return {r["chan_id"]: r["num_updates"] for r in rows}

# ── Agregados ──────────────────────────────────────────────────────────────────
def update_daily(conn, ts, deltas):
    date = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    existing = conn.execute(
        "SELECT * FROM daily_stats WHERE date=?", (date,)
    ).fetchone()

    disc = 1 if deltas["synced_to_chain"] == 0 else 0
    n = (existing["sample_count"] or 0) + 1 if existing else 1

    if existing:
        new_lr = ((existing["avg_liquidity_ratio"] * (n - 1)) + deltas["liquidity_ratio"]) / n
        new_ac = ((existing["avg_active_channels"] * (n - 1)) + deltas["channels_active"]) / n
        new_cap= ((existing["avg_capacity_sat"]    * (n - 1)) + deltas["capacity_total"]) / n
        fees_e = (existing["fees_earned_msat"] or 0) + deltas["fwd_fees_new"]
        fees_p = (existing["fees_paid_msat"]   or 0) + deltas["fees_paid_new"]
        fwd_c  = (existing["fwd_count_delta"]  or 0) + deltas["fwd_count_new"]
        fwd_a  = (existing["fwd_amt_delta_sat"]or 0) + deltas["fwd_amt_new"]
        net_p  = fees_e - fees_p
        cap_eff= round(fwd_a / new_cap, 6) if new_cap > 0 else 0.0
        conn.execute("""
            UPDATE daily_stats SET
                fwd_count_delta=?, fwd_amt_delta_sat=?,
                fees_earned_msat=?, fees_paid_msat=?, net_profit_msat=?,
                avg_liquidity_ratio=?, avg_active_channels=?, avg_capacity_sat=?,
                disconnections=disconnections+?,
                zombie_channels=?, inactive_capital_sat=?,
                capital_efficiency=?, sample_count=?
            WHERE date=?
        """, (fwd_c, fwd_a, fees_e, fees_p, net_p,
              round(new_lr, 2), round(new_ac, 2), int(new_cap),
              disc, deltas["zombie_count"], deltas["inactive_capital"],
              cap_eff, n, date))
    else:
        fees_e = deltas["fwd_fees_new"]
        fees_p = deltas["fees_paid_new"]
        conn.execute("""
            INSERT INTO daily_stats
                (date, fwd_count_delta, fwd_amt_delta_sat,
                 fees_earned_msat, fees_paid_msat, net_profit_msat,
                 avg_liquidity_ratio, avg_active_channels, avg_capacity_sat,
                 disconnections, zombie_channels, inactive_capital_sat,
                 capital_efficiency, sample_count)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (date,
              deltas["fwd_count_new"], deltas["fwd_amt_new"],
              fees_e, fees_p, fees_e - fees_p,
              deltas["liquidity_ratio"], deltas["channels_active"],
              deltas["capacity_total"],
              disc, deltas["zombie_count"], deltas["inactive_capital"],
              round(deltas["fwd_amt_new"] / max(deltas["capacity_total"], 1), 6),
              1))


def update_hourly(conn, ts, deltas):
    hour_ts = (ts // 3600) * 3600
    existing = conn.execute(
        "SELECT * FROM hourly_stats WHERE hour_ts=?", (hour_ts,)
    ).fetchone()
    n = (existing["sample_count"] or 0) + 1 if existing else 1

    if existing:
        new_lr = ((existing["avg_liquidity_ratio"] * (n - 1)) + deltas["liquidity_ratio"]) / n
        conn.execute("""
            UPDATE hourly_stats SET
                fwd_count_delta=fwd_count_delta+?,
                fwd_amt_delta_sat=fwd_amt_delta_sat+?,
                fees_earned_msat=fees_earned_msat+?,
                fees_paid_msat=fees_paid_msat+?,
                avg_liquidity_ratio=?,
                active_channels=?,
                zombie_channels=?,
                sample_count=?
            WHERE hour_ts=?
        """, (deltas["fwd_count_new"], deltas["fwd_amt_new"],
              deltas["fwd_fees_new"], deltas["fees_paid_new"],
              round(new_lr, 2), deltas["channels_active"],
              deltas["zombie_count"], n, hour_ts))
    else:
        conn.execute("""
            INSERT INTO hourly_stats
                (hour_ts, fwd_count_delta, fwd_amt_delta_sat,
                 fees_earned_msat, fees_paid_msat,
                 avg_liquidity_ratio, active_channels, zombie_channels, sample_count)
            VALUES (?,?,?,?,?,?,?,?,1)
        """, (hour_ts,
              deltas["fwd_count_new"], deltas["fwd_amt_new"],
              deltas["fwd_fees_new"], deltas["fees_paid_new"],
              deltas["liquidity_ratio"], deltas["channels_active"],
              deltas["zombie_count"]))


def cleanup(conn):
    """Rotación: eliminar registros más allá del límite de retención."""
    # Snapshots: conservar los últimos MAX_SNAPSHOTS
    conn.execute(f"""
        DELETE FROM snapshots
        WHERE id NOT IN (
            SELECT id FROM snapshots ORDER BY ts DESC LIMIT {MAX_SNAPSHOTS}
        )
    """)
    # daily_stats: conservar últimos MAX_DAILY_DAYS días
    conn.execute(f"""
        DELETE FROM daily_stats
        WHERE date NOT IN (
            SELECT date FROM daily_stats ORDER BY date DESC LIMIT {MAX_DAILY_DAYS}
        )
    """)
    # hourly_stats: conservar últimas MAX_HOURLY_HOURS horas
    cutoff_hour = ((now_ts() // 3600) - MAX_HOURLY_HOURS) * 3600
    conn.execute("DELETE FROM hourly_stats WHERE hour_ts < ?", (cutoff_hour,))


# ── Recolección principal ──────────────────────────────────────────────────────
# NOTA: El código de gamificación (ALL_ACHIEVEMENTS, seed_achievements,
# unlock_achievement, update_record, evaluate_achievements) fue migrado al
# paquete modular satoshi-odyssey/gamification/. Ver:
#   gamification/game_engine.py   — Orquestador con acceso a DB
#   gamification/achievements.py  — Catálogo y evaluación de logros
#   gamification/scoring.py       — XP, HP y Rangos
#   gamification/quests.py        — Misiones y desafíos

# ── Recolección principal ──────────────────────────────────────────────────────
def collect():
    conn = get_db()
    ts   = now_ts()

    # ── 1. getinfo ────────────────────────────────────────────────────────────
    info         = run_lncli("getinfo", timeout=15) or {}
    alias        = info.get("alias", "")
    pubkey       = info.get("identity_pubkey", "")
    block_height = int(info.get("block_height", 0))
    synced_chain = 1 if info.get("synced_to_chain") else 0
    synced_graph = 1 if info.get("synced_to_graph") else 0
    num_peers    = int(info.get("num_peers", 0))

    # ── 2. listchannels ───────────────────────────────────────────────────────
    lc           = run_lncli("listchannels", timeout=20) or {}
    channels     = lc.get("channels", [])
    ch_active    = sum(1 for c in channels if c.get("active"))
    ch_inactive  = sum(1 for c in channels if not c.get("active"))
    cap_total    = sum(int(c.get("capacity", 0)) for c in channels)
    bal_local    = sum(int(c.get("local_balance", 0)) for c in channels)
    bal_remote   = sum(int(c.get("remote_balance", 0)) for c in channels)

    liq_ratio = 0.0
    if bal_local + bal_remote > 0:
        liq_ratio = round(bal_local / (bal_local + bal_remote) * 100, 2)

    # Concentración de capital: % en el canal más grande
    max_cap = max((int(c.get("capacity", 0)) for c in channels), default=0)
    concentration = round(max_cap / cap_total * 100, 2) if cap_total > 0 else 0.0

    # ── 3. pendingchannels ────────────────────────────────────────────────────
    pc         = run_lncli("pendingchannels", timeout=15) or {}
    pend_open  = len(pc.get("pending_open_channels", []))
    pend_close = len(
        pc.get("pending_closing_channels", []) +
        pc.get("pending_force_closing_channels", []) +
        pc.get("waiting_close_channels", [])
    )
    ch_total = len(channels) + pend_open + pend_close

    # ── 4. walletbalance ──────────────────────────────────────────────────────
    wb            = run_lncli("walletbalance", timeout=10) or {}
    wallet_conf   = int(wb.get("confirmed_balance", 0))
    wallet_unconf = int(wb.get("unconfirmed_balance", 0))

    # ── 5. fwdinghistory (incrementales) ──────────────────────────────────────
    last_fwd_ts = meta_get(conn, "last_fwd_ts", "0")
    fwd_data    = run_lncli("fwdinghistory",
                            f"--start_time={last_fwd_ts}",
                            "--max_events=10000",
                            timeout=30) or {}
    fwd_events     = fwd_data.get("forwarding_events", [])
    fwd_count_new  = len(fwd_events)
    fwd_amt_new    = sum(int(e.get("amt_out", 0)) for e in fwd_events)
    fwd_fees_new   = sum(int(e.get("fee_msat", 0)) for e in fwd_events)

    if fwd_events:
        max_fwd_ts = max(int(e.get("timestamp", 0)) for e in fwd_events)
        meta_set(conn, "last_fwd_ts", str(max_fwd_ts + 1))

    prev = last_snapshot(conn)
    fwd_count_cum = (prev["fwd_count_cum"] or 0) + fwd_count_new  if prev else fwd_count_new
    fwd_amt_cum   = (prev["fwd_amt_cum_sat"] or 0) + fwd_amt_new  if prev else fwd_amt_new
    fwd_fees_cum  = (prev["fwd_fees_cum_msat"] or 0) + fwd_fees_new if prev else fwd_fees_new
    fwd_avg_size  = fwd_amt_cum // fwd_count_cum if fwd_count_cum > 0 else 0

    # ── 6. listpayments (rebalanceos / pagos salientes) ───────────────────────
    pay_data       = run_lncli("listpayments",
                               "--include_incomplete=false",
                               timeout=20) or {}
    payments       = [p for p in pay_data.get("payments", [])
                      if p.get("status") == "SUCCEEDED"]
    pay_count_cum  = len(payments)
    pay_fees_cum   = sum(int(p.get("fee_msat", 0)) for p in payments)

    fees_paid_new = max(0, pay_fees_cum - (prev["payments_fees_cum_msat"] or 0 if prev else 0))

    # ── 7. Detección de canales zombie ────────────────────────────────────────
    prev_updates = prev_chan_updates(conn)
    zombie_count   = 0
    inactive_cap   = 0
    chan_rows      = []

    for ch in channels:
        chan_id    = ch.get("chan_id", "")
        chan_point = ch.get("channel_point", "")
        peer_alias = ch.get("peer_alias", "")
        remote_pk  = ch.get("remote_pubkey", "")
        cap        = int(ch.get("capacity", 0))
        local      = int(ch.get("local_balance", 0))
        remote     = int(ch.get("remote_balance", 0))
        active     = 1 if ch.get("active") else 0
        num_upd    = int(ch.get("num_updates", 0))
        uptime_s   = int(ch.get("uptime", 0))
        lifetime_s = int(ch.get("lifetime", 0))
        local_ratio= round(local / cap * 100, 2) if cap > 0 else 0.0

        prev_upd      = prev_updates.get(chan_id)
        updates_delta = (num_upd - prev_upd) if prev_upd is not None else 0

        is_zombie = 0
        if active and lifetime_s > ZOMBIE_LIFETIME_DAYS * 86400:
            if prev_upd is not None and updates_delta < ZOMBIE_MIN_UPDATES:
                is_zombie = 1
                zombie_count += 1
                inactive_cap += cap

        chan_rows.append({
            "chan_id": chan_id, "chan_point": chan_point,
            "peer_alias": peer_alias, "remote_pubkey": remote_pk,
            "capacity": cap, "local_balance": local, "remote_balance": remote,
            "local_ratio": local_ratio, "active": active,
            "num_updates": num_upd, "updates_delta": updates_delta,
            "uptime_sec": uptime_s, "lifetime_sec": lifetime_s,
            "is_zombie": is_zombie,
        })

    cap_efficiency = round(fwd_amt_new / cap_total, 6) if cap_total > 0 else 0.0

    # ── 8. Insertar snapshot principal ────────────────────────────────────────
    cur = conn.execute("""
        INSERT INTO snapshots (
            ts, block_height, alias, pubkey,
            synced_to_chain, synced_to_graph, num_peers,
            channels_active, channels_inactive,
            channels_pending_open, channels_pending_close, channels_total,
            capacity_total, balance_local, balance_remote,
            wallet_confirmed, wallet_unconfirmed,
            liquidity_ratio, capital_concentration,
            fwd_count_cum, fwd_amt_cum_sat, fwd_fees_cum_msat, fwd_avg_size_sat,
            payments_count_cum, payments_fees_cum_msat,
            zombie_channels, inactive_capital_sat, capital_efficiency
        ) VALUES (
            :ts, :block_height, :alias, :pubkey,
            :synced_chain, :synced_graph, :num_peers,
            :ch_active, :ch_inactive,
            :pend_open, :pend_close, :ch_total,
            :cap_total, :bal_local, :bal_remote,
            :wallet_conf, :wallet_unconf,
            :liq_ratio, :concentration,
            :fwd_count_cum, :fwd_amt_cum, :fwd_fees_cum, :fwd_avg_size,
            :pay_count_cum, :pay_fees_cum,
            :zombie_count, :inactive_cap, :cap_efficiency
        )
    """, {
        "ts": ts, "block_height": block_height, "alias": alias, "pubkey": pubkey,
        "synced_chain": synced_chain, "synced_graph": synced_graph, "num_peers": num_peers,
        "ch_active": ch_active, "ch_inactive": ch_inactive,
        "pend_open": pend_open, "pend_close": pend_close, "ch_total": ch_total,
        "cap_total": cap_total, "bal_local": bal_local, "bal_remote": bal_remote,
        "wallet_conf": wallet_conf, "wallet_unconf": wallet_unconf,
        "liq_ratio": liq_ratio, "concentration": concentration,
        "fwd_count_cum": fwd_count_cum, "fwd_amt_cum": fwd_amt_cum,
        "fwd_fees_cum": fwd_fees_cum, "fwd_avg_size": fwd_avg_size,
        "pay_count_cum": pay_count_cum, "pay_fees_cum": pay_fees_cum,
        "zombie_count": zombie_count, "inactive_cap": inactive_cap,
        "cap_efficiency": cap_efficiency,
    })
    snap_id = cur.lastrowid

    # ── 9. Insertar snapshots de canales ──────────────────────────────────────
    conn.executemany("""
        INSERT INTO channel_snapshots (
            snapshot_id, ts, chan_id, chan_point, peer_alias, remote_pubkey,
            capacity, local_balance, remote_balance, local_ratio,
            active, num_updates, updates_delta, uptime_sec, lifetime_sec, is_zombie
        ) VALUES (
            :snapshot_id, :ts, :chan_id, :chan_point, :peer_alias, :remote_pubkey,
            :capacity, :local_balance, :remote_balance, :local_ratio,
            :active, :num_updates, :updates_delta, :uptime_sec, :lifetime_sec, :is_zombie
        )
    """, [{**r, "snapshot_id": snap_id, "ts": ts} for r in chan_rows])

    # ── 10. Actualizar agregados ───────────────────────────────────────────────
    deltas = {
        "fwd_count_new": fwd_count_new,
        "fwd_amt_new":   fwd_amt_new,
        "fwd_fees_new":  fwd_fees_new,
        "fees_paid_new": fees_paid_new,
        "liquidity_ratio":  liq_ratio,
        "channels_active":  ch_active,
        "capacity_total":   cap_total,
        "synced_to_chain":  synced_chain,
        "zombie_count":     zombie_count,
        "inactive_capital": inactive_cap,
    }
    update_daily(conn, ts, deltas)
    update_hourly(conn, ts, deltas)

    conn.commit()

    # ── 11. Rotación ──────────────────────────────────────────────────────────
    cleanup(conn)
    conn.commit()

    # ── 12. Gamificación: evaluar logros, XP, HP y misiones ───────────────────
    # Construir el dict de snapshot con todos los campos que el motor necesita.
    # Leer los últimos 7 días de daily_stats para calcular scoring y quests.
    daily = [
        dict(r) for r in conn.execute(
            "SELECT * FROM daily_stats ORDER BY date DESC LIMIT 7"
        ).fetchall()
    ]
    # Revertir para que daily[0] = día más antiguo y daily[-1] = más reciente
    daily.reverse()

    snap_dict = {
        # Identificadores del nodo
        "fwd_count_cum":     fwd_count_cum,
        "fwd_fees_cum_msat": fwd_fees_cum,
        "channels_active":   ch_active,
        "zombie_channels":   zombie_count,
        "liquidity_ratio":   liq_ratio,
        "wallet_confirmed":  wallet_conf,
        "capacity_total":    cap_total,
    }

    # Convertir chan_rows de lista de dicts a formato que el motor espera
    # (agrega local_ratio calculado si no está presente)
    engine_chan_rows = [
        {
            "active":      c.get("active", 0),
            "local_ratio": c.get("local_ratio", 0.0),
            "capacity":    c.get("capacity", 0),
            "is_zombie":   c.get("is_zombie", 0),
        }
        for c in chan_rows
    ]

    # Invocar el motor modular de gamificación
    evaluate_game_state(conn, snap_id, ts, snap_dict, engine_chan_rows, daily)
    conn.commit()

    conn.close()

    print(
        f"[{datetime.now().strftime('%H:%M:%S')}] ✅ snap_id={snap_id} | "
        f"Canales: {ch_active}a/{ch_inactive}i | "
        f"Ratio: {liq_ratio:.1f}% | "
        f"Zombies: {zombie_count} | "
        f"Forwards: +{fwd_count_new} | "
        f"Fees ganadas: +{fwd_fees_new} msat | "
        f"Fees pagadas: +{fees_paid_new} msat"
    )


if __name__ == "__main__":
    collect()
