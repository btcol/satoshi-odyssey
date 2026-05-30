"""
gamification/quests.py
=======================
Módulo de Misiones (Quests / Desafíos) del sistema de gamificación.

Responsabilidades:
  - Declarar el catálogo estático de todas las misiones disponibles,
    incluyendo su tipo (semanal o hito único), meta numérica y descripción.
  - Exponer funciones puras que calculan el progreso actual de cada misión
    dado el estado del nodo (snapshot, canales, historial diario).
  - Definir el esquema SQL de la tabla 'quests_progress' que el motor
    (game_engine.py) usará para persistir el estado de las misiones en SQLite.

Diferencias con achievements.py:
  - Los logros (achievements) son binarios: bloqueados o desbloqueados para siempre.
  - Las misiones (quests) tienen PROGRESO numérico: pueden estar activas,
    completadas y volver a activarse si son de tipo semanal.

Tipos de misiones:
  - "milestone": Se completan una sola vez cuando se alcanza la meta acumulada.
                 No se reinician. Ejemplo: "lograr que 3 canales estén balanceados".
  - "weekly":    Se reinician cada 7 días. El progreso se mide sobre el
                 historial de la semana actual. Ejemplo: "100 transacciones en 7 días".

Diseño:
  - Este módulo es 100% puro: no lee ni escribe en la base de datos.
  - La función evaluate_all_quests() retorna el progreso calculado desde cero
    en base a los datos que recibe como parámetros.
  - Las escrituras en DB las realiza game_engine.py.

Uso típico:
  >>> from gamification.quests import evaluate_all_quests
  >>> progreso = evaluate_all_quests(snap, canales, historial_7d)
  >>> # progreso es un dict: {quest_id: {"progress": int, "target": int, "completed": bool}}
"""

from datetime import datetime, timezone


# =============================================================================
# ESQUEMA SQL DE LA TABLA DE PROGRESO
# =============================================================================

# Este string SQL es usado por game_engine.py para crear/migrar la tabla
# en la base de datos node_history.db durante la inicialización.
QUESTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS quests_progress (
    id              TEXT    PRIMARY KEY,   -- ID de la misión (ej. 'el_repartidor')
    status          TEXT    DEFAULT 'active',
    -- Estados posibles:
    --   'active'    : en progreso, aún no completada
    --   'completed' : condición alcanzada, pendiente de notificación al usuario
    --   'claimed'   : el usuario ya vio la notificación del logro
    progress        INTEGER DEFAULT 0,     -- Progreso actual hacia la meta
    target          INTEGER NOT NULL,      -- Valor objetivo para completar la misión
    started_at      INTEGER DEFAULT 0,     -- Timestamp de inicio del período actual
    completed_at    INTEGER DEFAULT NULL,  -- Timestamp en que se completó (NULL si no)
    period_week     TEXT    DEFAULT NULL   -- Semana ISO (ej. '2026-W21') para misiones semanales
);
"""


# =============================================================================
# CATÁLOGO DE MISIONES
# =============================================================================

# Cada misión es un dict con los siguientes campos:
#   id          (str):  Identificador único. Clave en la tabla quests_progress.
#   name        (str):  Nombre visible en la interfaz del juego.
#   description (str):  Descripción de cómo completar la misión.
#   emoji       (str):  Icono visual de la misión.
#   type        (str):  'milestone' (única) o 'weekly' (se reinicia cada semana).
#   target      (int):  Valor numérico de la meta a alcanzar.
#   unit        (str):  Unidad de medida del progreso (para mostrar en la UI).
#   xp_reward   (int):  XP otorgada al completar la misión.

ALL_QUESTS: list[dict] = [
    # ── Misiones de Hito (Milestone) ─────────────────────────────────────────
    # Se completan una sola vez; su condición se evalúa sobre el estado actual del nodo.
    {
        "id":          "el_equilibrador",
        "name":        "El Equilibrador",
        "description": (
            "Logra que al menos 3 de tus canales activos tengan un ratio de liquidez "
            "entre 45% y 55% de forma simultánea."
        ),
        "emoji":       "⚖️",
        "type":        "milestone",
        "target":      3,       # 3 canales en el rango equilibrado
        "unit":        "canales balanceados",
        "xp_reward":   75,
    },
    {
        "id":          "el_diplomatico",
        "name":        "El Diplomático",
        "description": (
            "Abre un canal con un nodo que aparezca en la lista de candidatos sugeridos "
            "de la red (peer con alta capacidad y canales estables)."
        ),
        "emoji":       "🤝",
        "type":        "milestone",
        "target":      1,       # Al menos 1 canal abierto con un candidato sugerido
        "unit":        "canal con candidato",
        "xp_reward":   60,
        # Nota: la detección de "candidato sugerido" requiere datos externos que
        # game_engine.py proveerá al momento de evaluar; aquí se define la meta.
    },
    {
        "id":          "francotirador_de_fees",
        "name":        "Francotirador de Fees",
        "description": (
            "Consolida tus UTXOs on-chain pagando una tarifa menor a 5 sats/vbyte. "
            "Se registra cuando la wallet reduce su fragmentación (UTXOs < 3 después de consolidar)."
        ),
        "emoji":       "🎯",
        "type":        "milestone",
        "target":      1,       # Al menos 1 consolidación exitosa con fee bajo
        "unit":        "consolidaciones económicas",
        "xp_reward":   50,
    },
    {
        "id":          "hub_premium",
        "name":        "Hub Premium",
        "description": "Mantén 5 o más canales activos de forma simultánea durante 3 días seguidos.",
        "emoji":       "🌐",
        "type":        "milestone",
        "target":      3,       # 3 días consecutivos con 5+ canales activos
        "unit":        "días con 5+ canales activos",
        "xp_reward":   100,
    },

    # ── Misiones Semanales (Weekly) ───────────────────────────────────────────
    # Se reinician cada 7 días (lunes a domingo).
    # El progreso se acumula sobre fwd_count_delta del historial de la semana.
    {
        "id":          "el_repartidor",
        "name":        "El Repartidor",
        "description": "Genera un flujo de más de 100 transacciones en una semana (incluye rebalanceos).",
        "emoji":       "📦",
        "type":        "weekly",
        "target":      100,
        "unit":        "transacciones",
        "xp_reward":   40,
    },
    {
        "id":          "el_gerente",
        "name":        "El Gerente de Sucursal",
        "description": "Genera un flujo de más de 500 transacciones en una semana (incluye rebalanceos).",
        "emoji":       "🏢",
        "type":        "weekly",
        "target":      500,
        "unit":        "transacciones",
        "xp_reward":   80,
    },
    {
        "id":          "el_mayorista",
        "name":        "El Mayorista",
        "description": "Genera un flujo de más de 1000 transacciones en una semana (incluye rebalanceos).",
        "emoji":       "🏭",
        "type":        "weekly",
        "target":      1000,
        "unit":        "transacciones",
        "xp_reward":   150,
    },
]

# Índice rápido por ID para lookups O(1)
QUESTS_BY_ID: dict[str, dict] = {q["id"]: q for q in ALL_QUESTS}

# Subconjuntos por tipo para filtrado eficiente
WEEKLY_QUESTS:    list[dict] = [q for q in ALL_QUESTS if q["type"] == "weekly"]
MILESTONE_QUESTS: list[dict] = [q for q in ALL_QUESTS if q["type"] == "milestone"]


# =============================================================================
# FUNCIONES DE CONSULTA DEL CATÁLOGO
# =============================================================================

def get_all_quests() -> list[dict]:
    """
    Devuelve la lista completa de misiones del catálogo.

    Retorna:
      list[dict]: Copia de ALL_QUESTS. Cada elemento contiene:
                  id, name, description, emoji, type, target, unit, xp_reward.
    """
    return list(ALL_QUESTS)


def get_quest_by_id(quest_id: str) -> dict | None:
    """
    Busca y retorna una misión por su ID único.

    Parámetros:
      quest_id (str): ID de la misión a buscar (ej. 'el_repartidor').

    Retorna:
      dict: La misión encontrada, o None si el ID no existe en el catálogo.
    """
    return QUESTS_BY_ID.get(quest_id)


def get_current_week_id() -> str:
    """
    Retorna el identificador de la semana ISO actual en formato 'YYYY-WWW'.
    Se usa como clave para saber si una misión semanal debe reiniciarse.

    Ejemplo:
      '2026-W21'  (año 2026, semana número 21)
    """
    now = datetime.now(timezone.utc)
    # isocalendar() retorna (año_ISO, semana_ISO, día_semana)
    iso_year, iso_week, _ = now.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


# =============================================================================
# FUNCIONES DE EVALUACIÓN DE PROGRESO (Puras — sin efectos secundarios)
# =============================================================================

def _count_balanced_channels(chan_rows: list, balance_min: float = 45.0, balance_max: float = 55.0) -> int:
    """
    Cuenta cuántos canales activos tienen un ratio de liquidez dentro
    del rango especificado (considerado 'balanceado').

    Parámetros:
      chan_rows (list[dict]): Lista de registros de canales del snapshot actual.
      balance_min (float): Porcentaje mínimo de liquidez local para considerar balanceado.
      balance_max (float): Porcentaje máximo de liquidez local para considerar balanceado.

    Retorna:
      int: Número de canales activos dentro del rango.
    """
    return sum(
        1 for c in chan_rows
        if c.get("active") == 1
        and balance_min <= c.get("local_ratio", 0.0) <= balance_max
    )


def _count_weekly_transactions(daily_7d: list) -> int:
    """
    Suma las transacciones enrutadas (forwards) de los últimos 7 días de historial.
    Incluye rebalanceos ya que fwd_count_delta los registra también.

    Parámetros:
      daily_7d (list[dict]): Últimos registros de daily_stats (máx. 7 días).
                             Cada elemento debe tener la clave 'fwd_count_delta'.

    Retorna:
      int: Total de transacciones enrutadas en los últimos 7 días.
    """
    return sum(d.get("fwd_count_delta", 0) for d in daily_7d)


def _count_consecutive_days_with_5plus_channels(daily_7d: list) -> int:
    """
    Cuenta la racha máxima de días consecutivos donde el promedio
    de canales activos fue >= 5, empezando desde el día más reciente.

    Parámetros:
      daily_7d (list[dict]): Registros de daily_stats ordenados del más antiguo al más reciente.
                             Cada elemento debe tener 'avg_active_channels'.

    Retorna:
      int: Cantidad de días consecutivos (desde hoy hacia atrás) con 5+ canales activos.
    """
    count = 0
    # Recorrer desde el día más reciente hacia atrás
    for day in reversed(daily_7d):
        if day.get("avg_active_channels", 0) >= 5:
            count += 1
        else:
            break  # La racha se rompe al primer día sin la condición
    return count


def evaluate_all_quests(
    snap: dict,
    chan_rows: list,
    daily_7d: list,
) -> dict:
    """
    Evalúa todas las misiones del catálogo y retorna su progreso actual.

    Esta función NO consulta ni escribe en la base de datos. Solo calcula
    el progreso en base a los datos que recibe como parámetros.

    Parámetros:
      snap (dict): Snapshot actual del nodo. Campos usados:
        - "channels_active" (int): Número de canales activos en este momento.
        - "wallet_confirmed" (int): Saldo confirmado on-chain en sats.
          (No se usa directamente aquí; disponible para extensiones futuras.)

      chan_rows (list[dict]): Lista de canales del snapshot actual. Campos por canal:
        - "active"      (int): 1 si está activo, 0 si no.
        - "local_ratio" (float): Porcentaje de liquidez local (0-100).

      daily_7d (list[dict]): Últimos 7 registros de daily_stats. Campos por día:
        - "fwd_count_delta"      (int): Forwards nuevos ese día.
        - "avg_active_channels"  (float): Promedio de canales activos ese día.

    Retorna:
      dict: Mapa de {quest_id (str) -> info (dict)} con la evaluación de cada misión.
            Cada 'info' contiene:
              - "progress"  (int):  Valor actual de progreso.
              - "target"    (int):  Meta a alcanzar.
              - "completed" (bool): True si progress >= target.
              - "type"      (str):  Tipo de misión ('milestone' o 'weekly').
              - "pct"       (float): Porcentaje de completitud (0.0 a 100.0).

    Ejemplo:
      >>> snap = {"channels_active": 3}
      >>> canales = [{"active": 1, "local_ratio": 50.0}] * 3
      >>> evaluate_all_quests(snap, canales, [])
      {"el_equilibrador": {"progress": 3, "target": 3, "completed": True, ...}, ...}
    """
    results = {}

    # ── Cálculos compartidos (evitan repetir la misma lógica en cada misión) ──
    balanced_channels   = _count_balanced_channels(chan_rows)
    weekly_transactions = _count_weekly_transactions(daily_7d)
    consecutive_days_5  = _count_consecutive_days_with_5plus_channels(daily_7d)

    # ── Helper interno para construir el dict de resultado de una misión ─────
    def _build_result(quest_id: str, progress: int) -> dict:
        """Construye el dict estandarizado de resultado para una misión."""
        quest = QUESTS_BY_ID[quest_id]
        target = quest["target"]
        completed = progress >= target
        pct = round(min(progress / target * 100, 100.0), 1) if target > 0 else 0.0
        return {
            "progress":  progress,
            "target":    target,
            "completed": completed,
            "type":      quest["type"],
            "pct":       pct,
            "name":      quest["name"],
            "emoji":     quest["emoji"],
            "unit":      quest["unit"],
            "xp_reward": quest["xp_reward"],
        }

    # ── Misión: El Equilibrador ───────────────────────────────────────────────
    # Progreso = cuántos canales activos tienen ratio entre 45% y 55%
    results["el_equilibrador"] = _build_result("el_equilibrador", balanced_channels)

    # ── Misión: El Diplomático ────────────────────────────────────────────────
    # Esta misión requiere un dato externo (si el canal fue abierto desde sugerencias).
    # Su progreso será inyectado por game_engine.py consultando la DB.
    # Aquí devolvemos 0 como valor neutral; el motor sobreescribirá si corresponde.
    results["el_diplomatico"] = _build_result("el_diplomatico", 0)

    # ── Misión: Francotirador de Fees ─────────────────────────────────────────
    # Similar al Diplomático: requiere datos del evento de consolidación de UTXOs.
    # game_engine.py detecta este evento y actualiza el progreso en la DB.
    # Aquí devolvemos 0 como valor neutral.
    results["francotirador_de_fees"] = _build_result("francotirador_de_fees", 0)

    # ── Misión: Hub Premium ───────────────────────────────────────────────────
    # Progreso = días consecutivos con >= 5 canales activos (racha desde hoy)
    results["hub_premium"] = _build_result("hub_premium", consecutive_days_5)

    # ── Misiones Semanales ────────────────────────────────────────────────────
    # Progreso = suma de fwd_count_delta en los últimos 7 días
    results["el_repartidor"] = _build_result("el_repartidor", weekly_transactions)
    results["el_gerente"]    = _build_result("el_gerente",    weekly_transactions)
    results["el_mayorista"]  = _build_result("el_mayorista",  weekly_transactions)

    return results


def evaluate_single_quest(
    quest_id: str,
    snap: dict,
    chan_rows: list,
    daily_7d: list,
) -> dict | None:
    """
    Evalúa el progreso de una misión específica por su ID.

    Es un atajo para cuando solo se necesita evaluar una misión concreta
    sin calcular todo el catálogo.

    Parámetros:
      quest_id (str): ID de la misión a evaluar.
      snap, chan_rows, daily_7d: Igual que en evaluate_all_quests().

    Retorna:
      dict: Resultado de la evaluación de esa misión (misma estructura que en evaluate_all_quests).
      None: Si el quest_id no existe en el catálogo.
    """
    if quest_id not in QUESTS_BY_ID:
        return None
    # Reutilizar la función principal y extraer solo lo que se necesita
    all_results = evaluate_all_quests(snap, chan_rows, daily_7d)
    return all_results.get(quest_id)
