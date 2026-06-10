"""
gamification/game_engine.py
============================
Motor principal del sistema de gamificación.

Responsabilidad:
  Orquestar los módulos puros (scoring, achievements, quests) conectándolos
  con la base de datos SQLite del nodo. Es el único módulo del paquete que
  lee y escribe en la DB.

Flujo de trabajo por ciclo de colección:
  1. collect_stats.py termina de insertar un snapshot en la DB.
  2. Llama a evaluate_game_state(conn, snap_id, ts, snap, chan_rows, daily_7d).
  3. El motor:
     a. Inicializa las tablas propias del juego si no existen.
     b. Consulta la DB para obtener contexto histórico (logros ya desbloqueados,
        historial de zombies, ganancia neta total).
     c. Delega el cálculo puro a achievements, scoring y quests.
     d. Persiste los cambios (logros, estado de gamificación, progreso de misiones).
  4. app.py sirve /api/gamification/status llamando a get_ui_gamification_payload(conn).

Diseño:
  - Toda la lógica de evaluación reside en los módulos puros (sin DB).
  - Este módulo solo: lee contexto de DB, invoca módulos puros, persiste resultados.
  - Esto mantiene el motor liviano y fácil de probar con DB en memoria (:memory:).

Uso típico (desde collect_stats.py):
  >>> from gamification.game_engine import evaluate_game_state, init_gamification_tables
  >>> init_gamification_tables(conn)  # Una vez al inicio
  >>> result = evaluate_game_state(conn, snap_id, ts, snap_dict, chan_rows, daily_7d)
"""

import sqlite3
import sys
from datetime import datetime, timezone

# Importar los tres módulos puros del paquete de gamificación
from gamification.scoring      import get_full_score_summary
from gamification.achievements import get_all_achievements, evaluate_achievements
from gamification.quests       import (
    get_all_quests, evaluate_all_quests,
    get_current_week_id, QUESTS_SCHEMA,
    QUESTS_BY_ID,
)


# =============================================================================
# SCHEMA ADICIONAL: tabla de estado histórico de gamificación
# =============================================================================

# Almacena un snapshot de XP/HP/rango en cada ciclo de evaluación.
# Permite trazar la evolución del operador en el tiempo.
#
# NOTA: Las tablas achievements y records se definen aquí también con
# CREATE TABLE IF NOT EXISTS para que init_gamification_tables() sea
# completamente autocontenido. En producción, collect_stats.py las crea
# con el mismo schema; la cláusula IF NOT EXISTS garantiza idempotencia.
GAMIFICATION_STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS achievements (
    id          TEXT    PRIMARY KEY,
    name        TEXT    NOT NULL,
    description TEXT    DEFAULT '',
    emoji       TEXT    DEFAULT '🏆',
    unlocked_at INTEGER DEFAULT NULL,
    snapshot_id INTEGER DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS records (
    key         TEXT PRIMARY KEY,
    value       REAL DEFAULT 0,
    achieved_at INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS gamification_state (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           INTEGER NOT NULL,
    xp           INTEGER DEFAULT 0,
    hp           INTEGER DEFAULT 0,
    rank_name    TEXT    DEFAULT 'Aprendiz de Satoshi',
    rank_level   INTEGER DEFAULT 0,
    xp_next_rank INTEGER DEFAULT NULL
);
CREATE INDEX IF NOT EXISTS idx_gstate_ts ON gamification_state(ts);

CREATE TABLE IF NOT EXISTS quests_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    quest_id     TEXT NOT NULL,
    completed_at INTEGER NOT NULL,
    xp_reward    INTEGER NOT NULL,
    period_week  TEXT
);
"""


# =============================================================================
# INICIALIZACIÓN DE TABLAS
# =============================================================================

def init_gamification_tables(conn: sqlite3.Connection) -> None:
    """
    Crea las tablas del sistema de gamificación si no existen y
    siembra los catálogos de logros y misiones en la DB.

    Es idempotente: puede llamarse múltiples veces sin efectos secundarios.
    Debe invocarse una vez al inicio de collect_stats.py antes de cualquier
    evaluación.

    Parámetros:
      conn: Conexión activa a la base de datos SQLite node_history.db.
    """
    # Crear tabla de progreso de misiones (definida en quests.py)
    conn.executescript(QUESTS_SCHEMA)

    # Crear tabla de estado histórico de gamificación
    conn.executescript(GAMIFICATION_STATE_SCHEMA)

    # Sembrar catálogo de logros: insertar los que no existan (respeta los ya desbloqueados)
    _seed_achievements(conn)

    # Sembrar catálogo de misiones: insertar las que no existan
    _seed_quests(conn)

    # Auto-rellenar historial para misiones ya completadas anteriormente
    count_history = conn.execute("SELECT COUNT(*) FROM quests_history").fetchone()[0]
    if count_history == 0:
        completed_rows = conn.execute(
            "SELECT id, completed_at, period_week FROM quests_progress WHERE status IN ('completed', 'claimed')"
        ).fetchall()
        for r in completed_rows:
            quest_id, comp_at, p_week = r[0], r[1], r[2]
            quest_def = QUESTS_BY_ID.get(quest_id)
            if quest_def:
                if not comp_at:
                    comp_at = int(datetime.now(timezone.utc).timestamp())
                conn.execute(
                    """INSERT INTO quests_history (quest_id, completed_at, xp_reward, period_week)
                       VALUES (?, ?, ?, ?)""",
                    (quest_id, comp_at, quest_def.get("xp_reward", 0), p_week)
                )

    conn.commit()


# =============================================================================
# HELPERS PRIVADOS — CONSULTAS A LA DB
# =============================================================================

def _seed_achievements(conn: sqlite3.Connection) -> None:
    """
    Inserta en la tabla 'achievements' los logros del catálogo que aún no existen.
    Usa INSERT OR IGNORE para no sobreescribir los ya desbloqueados.
    """
    for ach in get_all_achievements():
        conn.execute(
            "INSERT OR IGNORE INTO achievements (id, name, description, emoji) VALUES (?,?,?,?)",
            (ach["id"], ach["name"], ach["description"], ach["emoji"])
        )


def _seed_quests(conn: sqlite3.Connection) -> None:
    """
    Inserta en la tabla 'quests_progress' las misiones del catálogo que aún no existen.
    El campo 'started_at' se inicializa con el timestamp actual de Unix.
    """
    now_ts = int(datetime.now(timezone.utc).timestamp())
    for quest in get_all_quests():
        conn.execute(
            """INSERT OR IGNORE INTO quests_progress
               (id, status, progress, target, started_at, period_week)
               VALUES (?, 'active', 0, ?, ?, ?)""",
            (quest["id"], quest["target"], now_ts, get_current_week_id())
        )


def _get_unlocked_achievement_ids(conn: sqlite3.Connection) -> set:
    """
    Retorna el conjunto de IDs de logros ya desbloqueados en la DB.
    Se usa para evitar re-evaluar logros que ya fueron otorgados.

    Retorna:
      set[str]: IDs con unlocked_at IS NOT NULL.
    """
    rows = conn.execute(
        "SELECT id FROM achievements WHERE unlocked_at IS NOT NULL"
    ).fetchall()
    return {r[0] for r in rows}


def _had_zombies_before(conn: sqlite3.Connection) -> bool:
    """
    Verifica si algún snapshot histórico anterior registró canales zombie.
    Se usa para evaluar el logro 'cazador_zombies'.

    Retorna:
      bool: True si existe al menos un snapshot con zombie_channels > 0.
    """
    count = conn.execute(
        "SELECT COUNT(*) FROM snapshots WHERE zombie_channels > 0"
    ).fetchone()[0]
    return count > 0


def _get_total_net_msat(conn: sqlite3.Connection) -> int:
    """
    Consulta la ganancia neta acumulada de TODA la historia en la DB.
    Se usa para evaluar el logro 'ahorrador' con precisión histórica completa.

    Retorna:
      int: Suma de (fees_earned_msat - fees_paid_msat) de toda la tabla daily_stats.
    """
    result = conn.execute(
        "SELECT COALESCE(SUM(fees_earned_msat - fees_paid_msat), 0) FROM daily_stats"
    ).fetchone()[0]
    return int(result)


def _unlock_achievement(
    conn: sqlite3.Connection,
    ach_id: str,
    snap_id: int,
    ts: int
) -> None:
    """
    Desbloquea un logro en la DB registrando el timestamp y el snap_id que lo detonó.
    Solo actualiza si el logro aún no estaba desbloqueado (unlocked_at IS NULL).

    Parámetros:
      conn:    Conexión activa a SQLite.
      ach_id:  ID del logro a desbloquear.
      snap_id: ID del snapshot que disparó el desbloqueo.
      ts:      Timestamp Unix del momento del desbloqueo.
    """
    conn.execute(
        """UPDATE achievements
           SET unlocked_at = ?, snapshot_id = ?
           WHERE id = ? AND unlocked_at IS NULL""",
        (ts, snap_id, ach_id)
    )


def _update_quests_progress(
    conn: sqlite3.Connection,
    quest_progress: dict,
    ts: int
) -> None:
    """
    Actualiza el progreso de todas las misiones en la DB.

    Para misiones semanales (weekly): verifica si la semana ISO actual
    es diferente a la registrada en la DB; si cambió, reinicia el progreso.

    Para misiones de hito (milestone): solo actualiza si el nuevo progreso
    es mayor que el actual o si el estado cambia a 'completed'.

    Parámetros:
      conn:           Conexión activa a SQLite.
      quest_progress: Dict {quest_id → info} retornado por evaluate_all_quests().
      ts:             Timestamp Unix de este ciclo de evaluación.
    """
    current_week = get_current_week_id()

    for quest_id, info in quest_progress.items():
        quest_def  = QUESTS_BY_ID.get(quest_id)
        if not quest_def:
            continue  # ID desconocido en el catálogo, saltear silenciosamente

        quest_type = quest_def["type"]

        # Leer estado actual de la DB para esta misión
        row = conn.execute(
            "SELECT status, progress, period_week FROM quests_progress WHERE id = ?",
            (quest_id,)
        ).fetchone()

        if not row:
            # La misión no existe en la DB (no debería ocurrir tras seed, pero se maneja)
            continue

        db_status, db_progress, db_week = row[0], row[1], row[2]

        # ── Reset semanal ─────────────────────────────────────────────────────
        # Si la misión es semanal y el período cambió, reiniciar el progreso.
        if quest_type == "weekly" and db_week != current_week:
            conn.execute(
                """UPDATE quests_progress
                   SET progress = 0, status = 'active', started_at = ?, period_week = ?
                   WHERE id = ?""",
                (ts, current_week, quest_id)
            )
            db_progress = 0
            db_status   = "active"

        # ── Determinar el nuevo progreso ──────────────────────────────────────
        new_progress  = info["progress"]
        new_completed = info["completed"]

        # Para misiones ya completadas y reclamadas, no actualizar el progreso.
        # Para misiones de hito ya completadas, no "descompletar" aunque el
        # progreso baje (el estado se mantiene).
        if db_status == "claimed":
            continue  # El usuario ya vio la notificación; no tocar

        if quest_type == "milestone" and db_status == "completed":
            continue  # Hito ya completado; preservar estado

        # Calcular nuevo status
        new_status = "completed" if new_completed else "active"

        # Actualizar solo si hay cambio real (evitar escrituras innecesarias)
        if new_progress != db_progress or new_status != db_status:
            completed_at = ts if new_completed and db_status != "completed" else None
            if completed_at:
                conn.execute(
                    """UPDATE quests_progress
                       SET progress = ?, status = ?, completed_at = ?
                       WHERE id = ?""",
                    (new_progress, new_status, completed_at, quest_id)
                )
                conn.execute(
                    """INSERT INTO quests_history (quest_id, completed_at, xp_reward, period_week)
                       VALUES (?, ?, ?, ?)""",
                    (quest_id, completed_at, quest_def.get("xp_reward", 0), db_week or current_week)
                )
            else:
                conn.execute(
                    "UPDATE quests_progress SET progress = ?, status = ? WHERE id = ?",
                    (new_progress, new_status, quest_id)
                )


def _update_records(
    conn: sqlite3.Connection,
    snap: dict,
    daily_7d: list,
    ts: int,
) -> None:
    """
    Actualiza los récords personales del operador en la tabla 'records'.

    Un récord se actualiza solo si el nuevo valor supera el valor actual guardado.
    Esto permite rastrear los máximos históricos del nodo de forma automática.

    Récords gestionados:
      - max_daily_fwd_sat   : Mayor volumen enrutado en un solo día (en sats).
      - max_daily_fees_msat : Mayor comisión ganada en un solo día (en msat).
      - max_capacity_sat    : Mayor capacidad total de canales alcanzada.
      - max_active_channels : Mayor número de canales activos simultáneos.

    Parámetros:
      conn    : Conexión activa a SQLite.
      snap    : Snapshot actual del nodo.
      daily_7d: Últimos 7 días de daily_stats (el primero = día más antiguo,
                el último = día más reciente). Se usa solo el último día si existe.
      ts      : Timestamp Unix para registrar cuándo se logró el récord.
    """
    def _set_if_greater(key: str, new_value: float) -> None:
        """Actualiza el récord 'key' solo si new_value supera el valor actual en DB."""
        if new_value <= 0:
            return  # No guardar valores neutros/vacíos como récords
        row = conn.execute("SELECT value FROM records WHERE key = ?", (key,)).fetchone()
        current = row[0] if row else 0
        if new_value > current:
            conn.execute(
                "INSERT OR REPLACE INTO records (key, value, achieved_at) VALUES (?,?,?)",
                (key, new_value, ts)
            )

    # Récord de volumen diario: usar el último día del historial si disponible
    if daily_7d:
        last_day = daily_7d[-1]  # El registro más reciente
        _set_if_greater("max_daily_fwd_sat",   last_day.get("fwd_amt_delta_sat", 0))
        _set_if_greater("max_daily_fees_msat",  last_day.get("fees_earned_msat", 0))

    # Récords de estado del nodo: usar el snapshot actual
    _set_if_greater("max_capacity_sat",    snap.get("capacity_total", 0))
    _set_if_greater("max_active_channels", snap.get("channels_active", 0))


# =============================================================================
# FUNCIÓN PRINCIPAL DE EVALUACIÓN
# =============================================================================

def evaluate_game_state(
    conn: sqlite3.Connection,
    snap_id: int,
    ts: int,
    snap: dict,
    chan_rows: list,
    daily_7d: list,
) -> dict:
    """
    Punto de entrada principal del motor de gamificación.

    Evalúa logros, calcula XP/HP/rango y actualiza el progreso de misiones
    para el snapshot actual. Persiste todos los cambios en la DB y retorna
    un resumen de los cambios producidos en este ciclo.

    Llamar al final de cada ciclo de collect_stats.collect() después
    de haber commiteado el snapshot principal.

    Parámetros:
      conn     : Conexión activa a SQLite (con el snapshot ya insertado).
      snap_id  : ID del snapshot recién insertado (último autoincrement).
      ts       : Timestamp Unix de este ciclo.
      snap     : Dict con las métricas del snapshot actual. Campos clave:
                   fwd_count_cum, fwd_fees_cum_msat, channels_active,
                   wallet_confirmed, zombie_channels, liquidity_ratio.
      chan_rows : Lista de dicts de channel_snapshots del snapshot actual.
      daily_7d  : Últimos 7 registros de daily_stats (para scoring y quests).

    Retorna:
      dict con:
        - "new_achievements" (list[str]): IDs de logros desbloqueados en este ciclo.
        - "score" (dict):  {xp, hp, rank} calculados por scoring.py.
        - "quests" (dict): Progreso actual de todas las misiones.
    """
    # ── 1. Asegurar que las tablas del juego existen ──────────────────────────
    init_gamification_tables(conn)

    # ── 2. Obtener contexto histórico de la DB ────────────────────────────────
    already_unlocked    = _get_unlocked_achievement_ids(conn)
    had_zombies         = _had_zombies_before(conn)
    net_profit_total    = _get_total_net_msat(conn)

    # ── 3. Evaluar logros nuevos (delegado al módulo puro) ────────────────────
    new_unlocked = evaluate_achievements(
        snap                  = snap,
        chan_rows             = chan_rows,
        daily_7d              = daily_7d,
        already_unlocked      = already_unlocked,
        had_zombies_before    = had_zombies,
        net_profit_total_msat = net_profit_total,
        towers_count          = snap.get("towers_count", 0),
    )

    # ── 4. Persistir logros nuevos en la DB ───────────────────────────────────
    for ach_id in new_unlocked:
        _unlock_achievement(conn, ach_id, snap_id, ts)
        print(f"  [GAME] 🏆 Logro desbloqueado: {ach_id}", file=sys.stderr)

    # ── 5. Actualizar récords personales en la DB ──────────────────────────────
    _update_records(conn, snap, daily_7d, ts)

    # ── 6. Evaluar progreso de misiones y persistir ───────────────────────────
    # Hacemos esto antes de calcular el score final para incluir las misiones
    # que se acaban de completar en este ciclo.
    quest_progress = evaluate_all_quests(snap, chan_rows, daily_7d)
    _update_quests_progress(conn, quest_progress, ts)

    # ── 7. Calcular XP de misiones completadas/reclamadas del historial ───────
    completed_quests_xp = 0
    hist_rows = conn.execute("SELECT SUM(xp_reward) FROM quests_history").fetchone()[0]
    if hist_rows is not None:
        completed_quests_xp = int(hist_rows)

    # ── 8. Calcular XP, HP y Rango (delegado al módulo puro) ─────────────────
    # Contar logros desbloqueados (incluyendo los recién otorgados)
    total_unlocked = len(already_unlocked) + len(new_unlocked)
    fees_sats      = snap.get("fwd_fees_cum_msat", 0) // 1000
    total_disc_7d  = sum(d.get("disconnections", 0) for d in daily_7d)

    score = get_full_score_summary(
        fwd_count             = snap.get("fwd_count_cum", 0),
        fees_sats             = fees_sats,
        unlocked_achievements = total_unlocked,
        zombies               = snap.get("zombie_channels", 0),
        liquidity_ratio       = snap.get("liquidity_ratio", 50.0),
        daily_disconnections  = total_disc_7d,
        history_days          = len(daily_7d),
        completed_quests_xp   = completed_quests_xp,
    )

    # ── 9. Persistir estado de gamificación en la DB ──────────────────────────
    conn.execute(
        """INSERT INTO gamification_state (ts, xp, hp, rank_name, rank_level, xp_next_rank)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (ts, score["xp"], score["hp"],
         score["rank"]["name"], score["rank"]["level"],
         score["rank"]["xp_next_rank"])
    )

    conn.commit()

    return {
        "new_achievements": new_unlocked,
        "score":            score,
        "quests":           quest_progress,
    }


# =============================================================================
# LECTURA DEL PAYLOAD PARA LA API (dashboard_core.py / app.py)
# =============================================================================

def get_ui_gamification_payload(conn: sqlite3.Connection) -> dict:
    """
    Genera el dict completo de gamificación para la API del frontend.

    Lee el estado más reciente de todas las tablas de gamificación y
    lo consolida en un único dict con la estructura esperada por el
    endpoint /api/gamification/status y por dashboard_core.read_gamification_status().

    Parámetros:
      conn: Conexión activa a SQLite (modo lectura es suficiente).

    Retorna:
      dict con:
        - "xp"                 (int): Puntos de experiencia actuales.
        - "hp"                 (int): Salud del nodo actual (0-100).
        - "rank"               (dict): {name, level, xp_current, xp_next_rank}
        - "achievements"       (list): Logros con estado desbloqueado/bloqueado.
        - "records"            (dict): Records personales históricos.
        - "quests"             (list): Estado de todas las misiones.
        - "unlocked_count"     (int): Número de logros desbloqueados.
        - "total_achievements" (int): Total de logros en el catálogo.
    """
    conn.row_factory = sqlite3.Row

    # ── Estado de XP/HP/rango más reciente ───────────────────────────────────
    state_row = conn.execute(
        "SELECT * FROM gamification_state ORDER BY ts DESC LIMIT 1"
    ).fetchone()

    if state_row:
        xp           = state_row["xp"]
        hp           = state_row["hp"]
        rank_name    = state_row["rank_name"]
        rank_level   = state_row["rank_level"]
        xp_next_rank = state_row["xp_next_rank"]
    else:
        # La tabla existe pero aún no hay evaluaciones registradas
        xp, hp, rank_name, rank_level, xp_next_rank = 0, 100, "Aprendiz de Satoshi", 0, 50

    # ── Logros con estado de desbloqueo ──────────────────────────────────────
    ach_rows = conn.execute(
        "SELECT id, name, description, emoji, unlocked_at FROM achievements ORDER BY unlocked_at ASC"
    ).fetchall()
    achievements  = [dict(r) for r in ach_rows]
    unlocked_count = sum(1 for a in achievements if a["unlocked_at"] is not None)

    # ── Records personales ────────────────────────────────────────────────────
    rec_rows = conn.execute("SELECT key, value, achieved_at FROM records").fetchall()
    records  = {r["key"]: {"value": r["value"], "achieved_at": r["achieved_at"]}
                for r in rec_rows}

    # ── Progreso de misiones: combinar DB con metadatos del catálogo ──────────
    quest_rows = conn.execute("SELECT * FROM quests_progress").fetchall()
    quests = []
    for row in quest_rows:
        quest_id  = row["id"]
        quest_def = QUESTS_BY_ID.get(quest_id, {})
        quests.append({
            "id":          quest_id,
            "name":        quest_def.get("name", quest_id),
            "description": quest_def.get("description", ""),
            "emoji":       quest_def.get("emoji", "🎯"),
            "type":        quest_def.get("type", "milestone"),
            "status":      row["status"],
            "progress":    row["progress"],
            "target":      row["target"],
            "xp_reward":   quest_def.get("xp_reward", 0),
            "unit":        quest_def.get("unit", ""),
            "pct":         round(min(row["progress"] / max(row["target"], 1) * 100, 100.0), 1),
            "completed_at":row["completed_at"],
            "period_week": row["period_week"],
        })

    return {
        "xp":                 xp,
        "hp":                 hp,
        "rank": {
            "name":        rank_name,
            "level":       rank_level,
            "xp_current":  xp,
            "xp_next_rank":xp_next_rank,
        },
        "achievements":       achievements,
        "records":            records,
        "quests":             quests,
        "unlocked_count":     unlocked_count,
        "total_achievements": len(achievements),
    }
