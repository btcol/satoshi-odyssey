"""
tests/test_game_engine.py
==========================
Pruebas de integración para gamification/game_engine.py.

A diferencia de los tests de los módulos puros, estas pruebas SÍ usan
una base de datos SQLite en memoria (:memory:) para validar la integración
completa del motor con la persistencia.

Cobertura:
  - init_gamification_tables(): crea tablas e inserta catálogos.
  - evaluate_game_state(): ciclo completo con logros, XP/HP y misiones.
  - Lógica de no-re-desbloqueo en ciclos sucesivos.
  - Reset semanal de misiones weekly.
  - get_ui_gamification_payload(): estructura completa del payload de la API.

Cómo ejecutar:
  python3 tests/test_game_engine.py
  python3 -m pytest tests/test_game_engine.py -v
"""

import sys
import os
import sqlite3

# ── Ajuste de path ────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Importar el schema de collect_stats para tener una DB completa
# (necesitamos las tablas snapshots, daily_stats, achievements, records)
from gamification.game_engine import (
    init_gamification_tables,
    evaluate_game_state,
    get_ui_gamification_payload,
    _get_unlocked_achievement_ids,
    _had_zombies_before,
    _get_total_net_msat,
)
from gamification.achievements import ALL_ACHIEVEMENTS
from gamification.quests       import ALL_QUESTS, get_current_week_id


# =============================================================================
# SCHEMA MÍNIMO DE node_history.db (para pruebas en memoria)
# =============================================================================

# Esquema mínimo que replica las tablas que game_engine lee/escribe.
# Refleja el subset relevante de collect_stats.py SCHEMA.
MINIMAL_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS snapshots (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                      INTEGER NOT NULL,
    block_height            INTEGER DEFAULT 0,
    alias                   TEXT    DEFAULT '',
    pubkey                  TEXT    DEFAULT '',
    synced_to_chain         INTEGER DEFAULT 0,
    synced_to_graph         INTEGER DEFAULT 0,
    num_peers               INTEGER DEFAULT 0,
    channels_active         INTEGER DEFAULT 0,
    channels_inactive       INTEGER DEFAULT 0,
    channels_pending_open   INTEGER DEFAULT 0,
    channels_pending_close  INTEGER DEFAULT 0,
    channels_total          INTEGER DEFAULT 0,
    capacity_total          INTEGER DEFAULT 0,
    balance_local           INTEGER DEFAULT 0,
    balance_remote          INTEGER DEFAULT 0,
    wallet_confirmed        INTEGER DEFAULT 0,
    wallet_unconfirmed      INTEGER DEFAULT 0,
    liquidity_ratio         REAL    DEFAULT 0.0,
    capital_concentration   REAL    DEFAULT 0.0,
    fwd_count_cum           INTEGER DEFAULT 0,
    fwd_amt_cum_sat         INTEGER DEFAULT 0,
    fwd_fees_cum_msat       INTEGER DEFAULT 0,
    fwd_avg_size_sat        INTEGER DEFAULT 0,
    payments_count_cum      INTEGER DEFAULT 0,
    payments_fees_cum_msat  INTEGER DEFAULT 0,
    zombie_channels         INTEGER DEFAULT 0,
    inactive_capital_sat    INTEGER DEFAULT 0,
    capital_efficiency      REAL    DEFAULT 0.0
);

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

CREATE TABLE IF NOT EXISTS daily_stats (
    date                 TEXT PRIMARY KEY,
    fwd_count_delta      INTEGER DEFAULT 0,
    fwd_amt_delta_sat    INTEGER DEFAULT 0,
    fees_earned_msat     INTEGER DEFAULT 0,
    fees_paid_msat       INTEGER DEFAULT 0,
    net_profit_msat      INTEGER DEFAULT 0,
    avg_liquidity_ratio  REAL    DEFAULT 0.0,
    avg_active_channels  REAL    DEFAULT 0.0,
    avg_capacity_sat     INTEGER DEFAULT 0,
    disconnections       INTEGER DEFAULT 0,
    zombie_channels      INTEGER DEFAULT 0,
    inactive_capital_sat INTEGER DEFAULT 0,
    capital_efficiency   REAL    DEFAULT 0.0,
    sample_count         INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS achievements (
    id          TEXT    PRIMARY KEY,
    name        TEXT    NOT NULL,
    description TEXT    DEFAULT '',
    emoji       TEXT    DEFAULT '🏆',
    unlocked_at INTEGER DEFAULT NULL,
    snapshot_id INTEGER DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS records (
    key        TEXT PRIMARY KEY,
    value      REAL DEFAULT 0,
    achieved_at INTEGER DEFAULT 0
);
"""


# =============================================================================
# FIXTURE: DB EN MEMORIA
# =============================================================================

def _make_db() -> sqlite3.Connection:
    """
    Crea una base de datos SQLite en memoria con el schema mínimo necesario.
    Cada prueba recibe una DB limpia e independiente.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(MINIMAL_SCHEMA)
    conn.commit()
    return conn


def _insert_snapshot(conn, snap_id=1, ts=1_700_000_000, **overrides) -> int:
    """
    Inserta un snapshot de prueba en la DB y retorna su rowid.
    Los valores por defecto representan un nodo básico sin actividad.
    """
    defaults = {
        "ts": ts,
        "channels_active": 2,
        "zombie_channels": 0,
        "fwd_count_cum": 0,
        "fwd_fees_cum_msat": 0,
        "liquidity_ratio": 50.0,
        "wallet_confirmed": 0,
        "capacity_total": 1_000_000,
    }
    defaults.update(overrides)

    cur = conn.execute("""
        INSERT INTO snapshots (ts, channels_active, zombie_channels,
            fwd_count_cum, fwd_fees_cum_msat, liquidity_ratio,
            wallet_confirmed, capacity_total)
        VALUES (:ts, :channels_active, :zombie_channels,
            :fwd_count_cum, :fwd_fees_cum_msat, :liquidity_ratio,
            :wallet_confirmed, :capacity_total)
    """, defaults)
    conn.commit()
    return cur.lastrowid


def _make_snap_dict(**overrides) -> dict:
    """Genera un dict de snapshot con valores configurables para pasar al motor."""
    defaults = {
        "fwd_count_cum":     0,
        "fwd_fees_cum_msat": 0,
        "channels_active":   2,
        "wallet_confirmed":  0,
        "zombie_channels":   0,
        "liquidity_ratio":   50.0,
    }
    defaults.update(overrides)
    return defaults


def _make_daily_7d(n=7, fwd=0, fees_earned=0, fees_paid=0, disc=0, avg_channels=2.0) -> list:
    """Genera n días de historial simulado con valores configurables."""
    return [{
        "fwd_count_delta":     fwd,
        "fees_earned_msat":    fees_earned,
        "fees_paid_msat":      fees_paid,
        "disconnections":      disc,
        "avg_active_channels": avg_channels,
    } for _ in range(n)]


# =============================================================================
# PRUEBAS DE init_gamification_tables()
# =============================================================================

def test_init_crea_tablas():
    """init_gamification_tables() debe crear quests_progress y gamification_state."""
    conn = _make_db()
    init_gamification_tables(conn)

    tablas = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}

    assert "quests_progress"    in tablas, "Falta tabla quests_progress"
    assert "gamification_state" in tablas, "Falta tabla gamification_state"


def test_init_siembra_logros():
    """init_gamification_tables() debe insertar todos los logros del catálogo."""
    conn = _make_db()
    init_gamification_tables(conn)

    count = conn.execute("SELECT COUNT(*) FROM achievements").fetchone()[0]
    assert count == len(ALL_ACHIEVEMENTS), \
        f"Se esperaban {len(ALL_ACHIEVEMENTS)} logros, hay {count}"


def test_init_siembra_misiones():
    """init_gamification_tables() debe insertar todas las misiones del catálogo."""
    conn = _make_db()
    init_gamification_tables(conn)

    count = conn.execute("SELECT COUNT(*) FROM quests_progress").fetchone()[0]
    assert count == len(ALL_QUESTS), \
        f"Se esperaban {len(ALL_QUESTS)} misiones, hay {count}"


def test_init_es_idempotente():
    """Llamar a init_gamification_tables() dos veces no debe duplicar filas."""
    conn = _make_db()
    init_gamification_tables(conn)
    init_gamification_tables(conn)  # Segunda llamada

    count_ach   = conn.execute("SELECT COUNT(*) FROM achievements").fetchone()[0]
    count_quest = conn.execute("SELECT COUNT(*) FROM quests_progress").fetchone()[0]
    assert count_ach   == len(ALL_ACHIEVEMENTS)
    assert count_quest == len(ALL_QUESTS)


# =============================================================================
# PRUEBAS DE evaluate_game_state()
# =============================================================================

def test_evaluate_retorna_estructura_correcta():
    """evaluate_game_state() debe retornar dict con new_achievements, score y quests."""
    conn    = _make_db()
    snap_id = _insert_snapshot(conn)
    snap    = _make_snap_dict()

    result = evaluate_game_state(conn, snap_id, 1_700_000_000, snap, [], [])

    assert "new_achievements" in result
    assert "score"            in result
    assert "quests"           in result
    assert isinstance(result["new_achievements"], list)
    assert "xp" in result["score"]
    assert "hp" in result["score"]
    assert "rank" in result["score"]


def test_evaluate_desbloquea_bautismo_con_sats():
    """El logro 'bautismo_de_fuego' debe desbloquearse cuando la wallet tiene sats."""
    conn    = _make_db()
    snap    = _make_snap_dict(wallet_confirmed=10_000)
    snap_id = _insert_snapshot(conn, wallet_confirmed=10_000)

    result = evaluate_game_state(conn, snap_id, 1_700_000_000, snap, [], [])

    assert "bautismo_de_fuego" in result["new_achievements"]


def test_evaluate_desbloquea_primera_sangre():
    """El logro 'primera_sangre' debe desbloquearse con al menos 1 forward."""
    conn    = _make_db()
    snap    = _make_snap_dict(fwd_count_cum=1, wallet_confirmed=1_000)
    snap_id = _insert_snapshot(conn, fwd_count_cum=1)

    result = evaluate_game_state(conn, snap_id, 1_700_000_000, snap, [], [])

    assert "primera_sangre" in result["new_achievements"]


def test_evaluate_no_re_desbloquea_en_segundo_ciclo():
    """En el segundo ciclo, los logros ya desbloqueados no deben aparecer de nuevo."""
    conn = _make_db()
    snap = _make_snap_dict(fwd_count_cum=1, wallet_confirmed=5_000)
    ts   = 1_700_000_000

    # Primer ciclo: desbloquea logros
    snap_id_1 = _insert_snapshot(conn, fwd_count_cum=1, wallet_confirmed=5_000)
    result_1  = evaluate_game_state(conn, snap_id_1, ts, snap, [], [])
    assert len(result_1["new_achievements"]) > 0

    # Segundo ciclo: sin cambios en el estado
    snap_id_2 = _insert_snapshot(conn, ts=ts + 3600, fwd_count_cum=1, wallet_confirmed=5_000)
    result_2  = evaluate_game_state(conn, snap_id_2, ts + 3600, snap, [], [])
    assert result_2["new_achievements"] == [], \
        f"Se esperaba lista vacía en el 2do ciclo, se obtuvo: {result_2['new_achievements']}"


def test_evaluate_persiste_estado_en_gamification_state():
    """evaluate_game_state() debe insertar una fila en gamification_state."""
    conn    = _make_db()
    snap    = _make_snap_dict()
    snap_id = _insert_snapshot(conn)

    evaluate_game_state(conn, snap_id, 1_700_000_000, snap, [], [])

    count = conn.execute("SELECT COUNT(*) FROM gamification_state").fetchone()[0]
    assert count == 1, f"Se esperaba 1 fila en gamification_state, hay {count}"


def test_evaluate_xp_aumenta_con_actividad():
    """El XP debe ser mayor cuando el nodo tiene más forwards y fees."""
    conn = _make_db()
    ts   = 1_700_000_000

    snap_base  = _make_snap_dict(fwd_count_cum=0, fwd_fees_cum_msat=0)
    snap_activ = _make_snap_dict(fwd_count_cum=200, fwd_fees_cum_msat=5_000_000)

    snap_id_base  = _insert_snapshot(conn)
    result_base   = evaluate_game_state(conn, snap_id_base, ts, snap_base, [], [])

    snap_id_activ = _insert_snapshot(conn, ts=ts+1, fwd_count_cum=200)
    result_activ  = evaluate_game_state(conn, snap_id_activ, ts+1, snap_activ, [], [])

    assert result_activ["score"]["xp"] > result_base["score"]["xp"], \
        "XP con actividad debe ser mayor que XP sin actividad"


def test_evaluate_hp_baja_con_zombies():
    """HP debe bajar cuando hay canales zombie en el snapshot."""
    conn = _make_db()
    ts   = 1_700_000_000

    snap_ok      = _make_snap_dict(zombie_channels=0)
    snap_zombies = _make_snap_dict(zombie_channels=3)

    snap_id_ok = _insert_snapshot(conn)
    result_ok  = evaluate_game_state(conn, snap_id_ok, ts, snap_ok, [], [])

    snap_id_z = _insert_snapshot(conn, ts=ts+1, zombie_channels=3)
    result_z  = evaluate_game_state(conn, snap_id_z, ts+1, snap_zombies, [], [])

    assert result_z["score"]["hp"] < result_ok["score"]["hp"], \
        "HP con zombies debe ser menor que HP sin zombies"


def test_evaluate_actualiza_progreso_de_misiones():
    """El progreso de 'el_repartidor' debe reflejar los forwards del historial."""
    conn    = _make_db()
    snap    = _make_snap_dict()
    snap_id = _insert_snapshot(conn)
    # 7 días con 20 forwards cada uno = 140 transacciones (meta=100 → completado)
    daily   = _make_daily_7d(n=7, fwd=20, avg_channels=2.0)

    result = evaluate_game_state(conn, snap_id, 1_700_000_000, snap, [], daily)

    assert result["quests"]["el_repartidor"]["completed"] is True
    assert result["quests"]["el_repartidor"]["progress"]  == 140


def test_evaluate_cazador_de_zombies_con_historial():
    """'cazador_zombies' debe desbloquearse si hubo zombies antes y ahora no hay."""
    conn = _make_db()
    ts   = 1_700_000_000

    # Primer ciclo: snapshot con zombies
    snap_con_z = _make_snap_dict(zombie_channels=2)
    snap_id_z  = _insert_snapshot(conn, zombie_channels=2)
    evaluate_game_state(conn, snap_id_z, ts, snap_con_z, [], [])

    # Segundo ciclo: sin zombies (cazador debe desbloquearse)
    snap_sin_z = _make_snap_dict(zombie_channels=0)
    snap_id_ok = _insert_snapshot(conn, ts=ts+3600, zombie_channels=0)
    result     = evaluate_game_state(conn, snap_id_ok, ts+3600, snap_sin_z, [], [])

    assert "cazador_zombies" in result["new_achievements"], \
        "El logro cazador_zombies debería desbloquearse en el segundo ciclo"


# =============================================================================
# PRUEBAS DE get_ui_gamification_payload()
# =============================================================================

def test_payload_estructura_completa():
    """get_ui_gamification_payload() debe retornar todos los campos esperados."""
    conn    = _make_db()
    snap    = _make_snap_dict(wallet_confirmed=1_000, fwd_count_cum=5)
    snap_id = _insert_snapshot(conn, wallet_confirmed=1_000, fwd_count_cum=5)
    evaluate_game_state(conn, snap_id, 1_700_000_000, snap, [], [])

    payload = get_ui_gamification_payload(conn)

    campos_requeridos = {"xp", "hp", "rank", "achievements", "records",
                         "quests", "unlocked_count", "total_achievements"}
    faltantes = campos_requeridos - set(payload.keys())
    assert not faltantes, f"Campos faltantes en el payload: {faltantes}"


def test_payload_rank_tiene_estructura_correcta():
    """El campo 'rank' del payload debe tener name, level, xp_current, xp_next_rank."""
    conn    = _make_db()
    snap    = _make_snap_dict()
    snap_id = _insert_snapshot(conn)
    evaluate_game_state(conn, snap_id, 1_700_000_000, snap, [], [])

    payload = get_ui_gamification_payload(conn)
    rank    = payload["rank"]

    assert "name"        in rank
    assert "level"       in rank
    assert "xp_current"  in rank
    assert "xp_next_rank" in rank


def test_payload_unlocked_count_correcto():
    """unlocked_count debe reflejar el número exacto de logros desbloqueados."""
    conn    = _make_db()
    snap    = _make_snap_dict(wallet_confirmed=5_000, fwd_count_cum=1)
    snap_id = _insert_snapshot(conn, wallet_confirmed=5_000, fwd_count_cum=1)
    result  = evaluate_game_state(conn, snap_id, 1_700_000_000, snap, [], [])

    payload        = get_ui_gamification_payload(conn)
    expected_count = len(result["new_achievements"])

    assert payload["unlocked_count"] == expected_count, \
        f"unlocked_count esperado {expected_count}, obtenido {payload['unlocked_count']}"


def test_payload_quests_tienen_estructura():
    """Cada misión en el payload debe tener los campos mínimos."""
    conn    = _make_db()
    snap    = _make_snap_dict()
    snap_id = _insert_snapshot(conn)
    evaluate_game_state(conn, snap_id, 1_700_000_000, snap, [], [])

    payload = get_ui_gamification_payload(conn)
    campos  = {"id", "name", "status", "progress", "target", "pct", "type"}

    for quest in payload["quests"]:
        faltantes = campos - set(quest.keys())
        assert not faltantes, f"A misión '{quest.get('id')}' le faltan: {faltantes}"


def test_payload_sin_evaluaciones_previas():
    """get_ui_gamification_payload() no debe fallar si no hay evaluaciones aún."""
    conn = _make_db()
    init_gamification_tables(conn)  # Solo inicializar, sin evaluate_game_state

    payload = get_ui_gamification_payload(conn)

    # Debe retornar valores por defecto sin lanzar excepciones
    assert payload["xp"] == 0
    assert payload["hp"] == 100
    assert payload["rank"]["level"] == 0


# =============================================================================
# PRUEBAS DE HELPERS PRIVADOS
# =============================================================================

def test_had_zombies_before_sin_historial():
    """Sin snapshots, _had_zombies_before debe retornar False."""
    conn = _make_db()
    assert _had_zombies_before(conn) is False


def test_had_zombies_before_con_historial():
    """Con un snapshot que tiene zombies, _had_zombies_before retorna True."""
    conn = _make_db()
    _insert_snapshot(conn, zombie_channels=2)
    assert _had_zombies_before(conn) is True


def test_get_total_net_msat_sin_datos():
    """Sin registros en daily_stats, _get_total_net_msat debe retornar 0."""
    conn = _make_db()
    assert _get_total_net_msat(conn) == 0


def test_get_total_net_msat_con_datos():
    """_get_total_net_msat debe sumar correctamente el histórico completo."""
    conn = _make_db()
    conn.execute(
        "INSERT INTO daily_stats (date, fees_earned_msat, fees_paid_msat) VALUES (?,?,?)",
        ("2026-05-01", 10_000_000, 2_000_000)
    )
    conn.execute(
        "INSERT INTO daily_stats (date, fees_earned_msat, fees_paid_msat) VALUES (?,?,?)",
        ("2026-05-02", 5_000_000, 1_000_000)
    )
    conn.commit()
    # Total neto = (10M - 2M) + (5M - 1M) = 8M + 4M = 12M msat
    assert _get_total_net_msat(conn) == 12_000_000


def test_evaluate_quests_xp_summed_to_total_xp():
    """El XP de las misiones completadas debe sumarse al XP total del estado de gamificación."""
    conn    = _make_db()
    snap    = _make_snap_dict()
    snap_id = _insert_snapshot(conn)
    # 7 días con 20 forwards cada uno = 140 transacciones (meta=100 → completado el_repartidor, recompensa 50 XP)
    daily   = _make_daily_7d(n=7, fwd=20, avg_channels=2.0)

    result = evaluate_game_state(conn, snap_id, 1_700_000_000, snap, [], daily)

    # Verificamos que 'el_repartidor' esté completada
    assert result["quests"]["el_repartidor"]["completed"] is True

    # El XP total del score debería ser 90 (50 por logro 'insomne' + 40 por la misión completada 'el_repartidor')
    assert result["score"]["xp"] == 90, f"Se esperaba 90 XP, se obtuvo {result['score']['xp']}"

    # Además, la tabla quests_history debe registrar la finalización
    history_count = conn.execute("SELECT COUNT(*) FROM quests_history").fetchone()[0]
    assert history_count == 1, f"Se esperaba 1 registro en quests_history, se obtuvo {history_count}"


# =============================================================================
# RUNNER MANUAL
# =============================================================================

def _run_all_tests():
    import traceback
    tests = [(name, obj) for name, obj in globals().items()
             if name.startswith("test_") and callable(obj)]

    passed, failed = 0, 0
    print(f"\n{'='*60}")
    print(f"  PRUEBAS DE INTEGRACIÓN — gamification/game_engine.py")
    print(f"{'='*60}\n")

    for name, fn in tests:
        try:
            fn()
            print(f"  ✅  {name}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌  {name} — {e}")
            failed += 1
        except Exception as e:
            print(f"  💥  {name} — ERROR INESPERADO: {e}")
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*60}")
    print(f"  Resultado: {passed} pasadas / {failed} fallidas de {len(tests)} pruebas.")
    print(f"{'='*60}\n")
    return failed == 0


if __name__ == "__main__":
    success = _run_all_tests()
    sys.exit(0 if success else 1)
