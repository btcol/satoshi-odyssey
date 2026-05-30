"""
gamification/scoring.py
========================
Módulo de cálculo de puntuación del sistema de gamificación.

Responsabilidades:
  - Calcular los Puntos de Experiencia (XP) del operador del nodo.
  - Calcular la Salud del Nodo (HP) como porcentaje de 0 a 100.
  - Determinar el Rango actual del operador según su XP acumulado.

Diseño:
  - Este módulo es 100% puro: no lee ni escribe en la base de datos.
  - Todas las funciones reciben los datos que necesitan como parámetros.
  - Esto facilita las pruebas unitarias (unit tests) sin necesidad de
    crear fixtures de base de datos, simplemente inyectando datos simulados.

Uso típico:
  >>> from gamification.scoring import calculate_xp, calculate_hp, get_rank
  >>> xp = calculate_xp(fwd_count=50, fees_sats=2000, unlocked_achievements=3)
  >>> hp = calculate_hp(zombies=1, liquidity_ratio=72.0, daily_disconnections=0)
  >>> rank = get_rank(xp)
"""


# =============================================================================
# CONSTANTES DE CONFIGURACIÓN
# =============================================================================

# Tabla de Rangos: lista de tuplas (xp_minimo, nombre_del_rango, nivel_numerico).
# El operador alcanza el rango cuando su XP acumulado es >= al umbral.
# Orden: de mayor a menor para facilitar la búsqueda (break en el primero que aplique).
RANK_TABLE = [
    (1000, "Hub de la Red",         4),
    (500,  "Maestro de Liquidez",   3),
    (200,  "Enrutador Activo",      2),
    (50,   "Novato del Rayo",       1),
    (0,    "Aprendiz de Satoshi",   0),
]

# Multiplicador de XP por cada logro desbloqueado.
XP_PER_ACHIEVEMENT = 50

# XP ganada por cada transacción de enrutamiento (forward) completada.
XP_PER_FORWARD = 1

# XP ganada por cada 1000 satoshis en comisiones acumuladas.
# Ej: 5000 sats en fees => 5 × 10 = 50 XP adicionales.
XP_PER_1K_FEES_SATS = 10

# Penalización de HP por cada canal zombie detectado (máximo aplicable: 40 puntos).
HP_PENALTY_PER_ZOMBIE = 10
HP_MAX_ZOMBIE_PENALTY = 40

# Penalización de HP cuando la liquidez está muy desbalanceada (< 20% o > 80%).
HP_PENALTY_IMBALANCE_SEVERE = 20

# Penalización de HP cuando la liquidez está moderadamente desbalanceada (< 30% o > 70%).
HP_PENALTY_IMBALANCE_MODERATE = 10

# Penalización de HP por cada día con desconexiones registradas (máximo aplicable: 20 puntos).
HP_PENALTY_PER_DISCONNECTION_DAY = 5
HP_MAX_DISCONNECTION_PENALTY = 20

# Bonificación de HP si el nodo no tuvo desconexiones y tiene al menos 3 días de historial.
HP_BONUS_PERFECT_UPTIME = 5


# =============================================================================
# FUNCIONES DE CÁLCULO (Puras — sin efectos secundarios ni I/O)
# =============================================================================

def calculate_xp(
    fwd_count: int,
    fees_sats: int,
    unlocked_achievements: int,
    completed_quests_xp: int = 0,
) -> int:
    """
    Calcula los Puntos de Experiencia (XP) del operador.

    Fórmula:
      XP = (enrutamientos × XP_POR_FORWARD)
         + (fees_sats // 1000) × XP_POR_1K_FEES_SATS
         + (logros_desbloqueados × XP_POR_LOGRO)
         + XP_DE_MISIONES_COMPLETADAS

    Parámetros:
      fwd_count (int): Número total acumulado de pagos enrutados por el nodo.
      fees_sats (int): Total acumulado de comisiones ganadas, en satoshis.
      unlocked_achievements (int): Número de logros desbloqueados hasta ahora.
      completed_quests_xp (int): XP total acumulada por misiones completadas.

    Retorna:
      int: Puntos de experiencia totales. Siempre >= 0.

    Ejemplo:
      >>> calculate_xp(fwd_count=150, fees_sats=3500, unlocked_achievements=2, completed_quests_xp=75)
      325  # 150 + 35 + 100 + 75
    """
    # Puntos base por enrutamientos realizados
    xp_from_forwards = fwd_count * XP_PER_FORWARD

    # Puntos por comisiones: 10 XP cada 1000 sats ganados
    xp_from_fees = (fees_sats // 1000) * XP_PER_1K_FEES_SATS

    # Puntos por logros desbloqueados en la vitrina
    xp_from_achievements = unlocked_achievements * XP_PER_ACHIEVEMENT

    total_xp = xp_from_forwards + xp_from_fees + xp_from_achievements + completed_quests_xp
    return max(0, total_xp)


def calculate_hp(
    zombies: int,
    liquidity_ratio: float,
    daily_disconnections: int,
    history_days: int = 0,
) -> int:
    """
    Calcula la Salud del Nodo (HP) como un porcentaje de 0 a 100.

    La salud empieza en 100% y se reduce por factores de mala gestión:
      - Canales zombie (inactivos pero abiertos).
      - Liquidez severamente desbalanceada (< 20% o > 80% local).
      - Días con desconexiones registradas en los últimos 7 días.
    Puede recibir un pequeño bonus si el uptime fue perfecto.

    Parámetros:
      zombies (int): Número de canales zombies actualmente detectados.
      liquidity_ratio (float): Porcentaje de liquidez local sobre total (0-100).
      daily_disconnections (int): Suma de días con desconexiones en los últimos 7 días.
      history_days (int): Número de días de historial disponible (para activar el bonus).

    Retorna:
      int: Salud del nodo de 0 a 100.

    Ejemplo:
      >>> calculate_hp(zombies=2, liquidity_ratio=75.0, daily_disconnections=1)
      55  # 100 - 20 (zombies) - 10 (balance moderado) - 5 (desconexión) - bonus N/A
    """
    hp = 100

    # ── Penalización por canales zombie ──────────────────────────────────────
    # Cada zombie resta HP_PENALTY_PER_ZOMBIE puntos, con techo en HP_MAX_ZOMBIE_PENALTY.
    zombie_penalty = min(HP_MAX_ZOMBIE_PENALTY, zombies * HP_PENALTY_PER_ZOMBIE)
    hp -= zombie_penalty

    # ── Penalización por desbalance de liquidez ───────────────────────────────
    # Rango saludable: entre 20% (exclusivo) y 80% (exclusivo) de liquidez local.
    # Nota: los valores exactos 20.0 y 80.0 caen en el rango moderado (no severo).
    if liquidity_ratio < 20.0 or liquidity_ratio > 80.0:
        # Desbalance severo: fuera del rango de operación eficiente
        hp -= HP_PENALTY_IMBALANCE_SEVERE
    elif liquidity_ratio <= 30.0 or liquidity_ratio >= 70.0:
        # Desbalance moderado: 20-30% o 70-80% inclusive en los bordes
        hp -= HP_PENALTY_IMBALANCE_MODERATE

    # ── Penalización por desconexiones ────────────────────────────────────────
    # Cada día con al menos una desconexión penaliza, con techo.
    disconnection_penalty = min(HP_MAX_DISCONNECTION_PENALTY,
                                daily_disconnections * HP_PENALTY_PER_DISCONNECTION_DAY)
    hp -= disconnection_penalty

    # ── Bonus por uptime perfecto ─────────────────────────────────────────────
    # Solo aplica si no hubo ninguna desconexión y hay suficiente historial.
    if daily_disconnections == 0 and history_days >= 3:
        hp = min(100, hp + HP_BONUS_PERFECT_UPTIME)

    # Asegurar que HP nunca sea negativo ni supere 100
    return max(0, min(100, hp))


def get_rank(xp: int) -> dict:
    """
    Determina el Rango del operador según sus Puntos de Experiencia (XP).

    Recorre la tabla RANK_TABLE de mayor a menor umbral y retorna
    el primer rango cuyo umbral mínimo sea <= al XP actual.

    Parámetros:
      xp (int): Puntos de Experiencia totales del operador.

    Retorna:
      dict con:
        - "name" (str): Nombre del rango actual.
        - "level" (int): Nivel numérico del rango (0=más bajo, 4=más alto).
        - "xp_current" (int): XP actual del operador.
        - "xp_next_rank" (int | None): XP requerida para el siguiente rango.
          None si ya está en el rango máximo.

    Ejemplo:
      >>> get_rank(250)
      {"name": "Enrutador Activo", "level": 2, "xp_current": 250, "xp_next_rank": 500}
    """
    current_name  = RANK_TABLE[-1][1]   # Valor por defecto: rango base
    current_level = RANK_TABLE[-1][2]

    # Buscar el rango más alto que el operador ya ha alcanzado
    for (threshold, name, level) in RANK_TABLE:
        if xp >= threshold:
            current_name  = name
            current_level = level
            break  # RANK_TABLE está ordenada de mayor a menor, primer match es el correcto

    # Calcular XP requerida para el siguiente rango
    xp_next_rank = None
    # Recorrer la tabla de MENOR a MAYOR umbral (reversed) para encontrar
    # el primer umbral que supera el XP actual: ese es el siguiente rango.
    for (threshold, name, level) in reversed(RANK_TABLE):
        if threshold > xp:
            xp_next_rank = threshold
            break  # El primero encontrado es el umbral inmediatamente superior

    return {
        "name":          current_name,
        "level":         current_level,
        "xp_current":    xp,
        "xp_next_rank":  xp_next_rank,
    }


def get_full_score_summary(
    fwd_count: int,
    fees_sats: int,
    unlocked_achievements: int,
    zombies: int,
    liquidity_ratio: float,
    daily_disconnections: int,
    history_days: int = 0,
    completed_quests_xp: int = 0,
) -> dict:
    """
    Función de conveniencia que calcula y devuelve un resumen completo
    del estado de puntuación del operador en una sola llamada.

    Es el punto de entrada principal para el motor de juego (game_engine.py)
    y también el método más fácil de usar en pruebas unitarias ya que encapsula
    todos los cálculos en un único dict serializable.

    Parámetros: (combinación de los parámetros de calculate_xp y calculate_hp)
      Ver documentación individual de cada función para detalles.

    Retorna:
      dict con:
        - "xp"    (int): Puntos de experiencia totales.
        - "hp"    (int): Salud del nodo de 0 a 100.
        - "rank"  (dict): Información del rango actual (ver get_rank()).
    """
    xp = calculate_xp(fwd_count, fees_sats, unlocked_achievements, completed_quests_xp)
    hp = calculate_hp(zombies, liquidity_ratio, daily_disconnections, history_days)
    rank = get_rank(xp)

    return {
        "xp":   xp,
        "hp":   hp,
        "rank": rank,
    }
