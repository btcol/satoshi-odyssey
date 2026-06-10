"""
gamification/achievements.py
=============================
Módulo de Logros (Achievements / Trofeos) del sistema de gamificación.

Responsabilidades:
  - Declarar el catálogo estático de todos los logros disponibles en el juego
    (nombre, descripción, emoji, criterio de desbloqueo).
  - Exponer funciones puras que evalúan qué logros deben desbloquearse
    dado el estado actual del nodo (snapshot, canales, historial diario).

Diseño:
  - Este módulo NO accede a la base de datos. Recibe los datos que necesita
    como parámetros y devuelve una lista de IDs de logros a desbloquear.
  - Las escrituras en DB las realiza game_engine.py, quien invoca este módulo.
  - Esto permite pruebas unitarias completamente aisladas (sin fixtures de DB).

Uso típico:
  >>> from gamification.achievements import get_all_achievements, evaluate_achievements
  >>> nuevos = evaluate_achievements(snap, canales, historial_7d, ya_desbloqueados)
  >>> # nuevos es una lista de IDs (str) a desbloquear en la DB
"""


# =============================================================================
# CATÁLOGO DE LOGROS
# =============================================================================

# Cada logro es un dict con los siguientes campos:
#   id          (str): Identificador único. Se usa como clave en la tabla achievements de SQLite.
#   name        (str): Nombre visible en la interfaz del juego.
#   description (str): Explicación de cómo obtener el logro.
#   emoji       (str): Icono visual que representa el trofeo.
#   hint        (str): Pista opcional para el jugador sin revelar la condición exacta.

ALL_ACHIEVEMENTS: list[dict] = [
    {
        "id":          "bautismo_de_fuego",
        "name":        "Bautismo de Fuego",
        "description": "Fondea tu nodo por primera vez con sats on-chain confirmados.",
        "emoji":       "🔥",
        "hint":        "Consigue tus primeros sats en el nodo usando un faucet de Testnet4.",
    },
    {
        "id":          "primera_sangre",
        "name":        "Primera Sangre",
        "description": "Enrutaste tu primera transacción Lightning por el nodo.",
        "emoji":       "⚡",
        "hint":        "Abre un canal y espera a que alguien enrute un pago a través de ti.",
    },
    {
        "id":          "enrutador_activo",
        "name":        "Enrutador Activo",
        "description": "Enrutaste más de 100 transacciones en total.",
        "emoji":       "🔀",
        "hint":        "Mantén tus canales balanceados y con buenas tarifas para atraer tráfico.",
    },
    {
        "id":          "hub_de_la_red",
        "name":        "Hub de la Red",
        "description": "Tu nodo tiene 5 o más canales activos de forma simultánea.",
        "emoji":       "🕸️",
        "hint":        "Conecta con más peers y abre canales hacia nodos con alta capacidad.",
    },
    {
        "id":          "rayo_plateado",
        "name":        "Rayo Plateado",
        "description": (
            "Todos tus canales activos tienen al menos 1,000,000 sats de capacidad "
            "y un ratio de liquidez entre 40% y 60%."
        ),
        "emoji":       "🥈",
        "hint":        "Mantén todos tus canales bien balanceados y con buena capacidad.",
    },
    {
        "id":          "rayo_dorado",
        "name":        "Rayo Dorado",
        "description": (
            "Todos tus canales activos tienen al menos 5,000,000 sats de capacidad "
            "y un ratio de liquidez entre 40% y 60%."
        ),
        "emoji":       "🌟",
        "hint":        "Amplía la capacidad de tus canales y mantén el balance perfecto.",
    },
    {
        "id":          "manos_de_diamante",
        "name":        "Manos de Diamante",
        "description": "Acumulaste 100,000 sats en comisiones de enrutamiento.",
        "emoji":       "💎",
        "hint":        "Las comisiones se acumulan poco a poco. ¡Paciencia y canales activos!",
    },
    {
        "id":          "ahorrador",
        "name":        "El Ahorrador",
        "description": "Tu ganancia neta acumulada (fees ganadas - fees pagadas) supera los 50,000 sats.",
        "emoji":       "💰",
        "hint":        "Minimiza los costos de rebalanceo y maximiza las comisiones ganadas.",
    },
    {
        "id":          "cazador_zombies",
        "name":        "Cazador de Zombies",
        "description": (
            "Detectaste y eliminaste canales zombie (inactivos). "
            "El nodo tenía zombies en el pasado, y ahora no tiene ninguno."
        ),
        "emoji":       "🧟",
        "hint":        "Revisa el panel de canales y cierra los que llevan mucho tiempo sin actividad.",
    },
    {
        "id":          "insomne",
        "name":        "Insomne",
        "description": "Tu nodo mantuvo 100% de uptime (sincronizado) durante 7 días completos.",
        "emoji":       "🌙",
        "hint":        "Asegúrate de que tu nodo esté siempre encendido y sincronizado a la cadena.",
    },
    {
        "id":          "escudo_protector",
        "name":        "Escudo Protector",
        "description": "Conectaste una torre de vigilancia (watchtower) activa para salvaguardar tu nodo.",
        "emoji":       "🛡️",
        "hint":        "Busca una torre de vigilancia pública o privada y conéctala a tu nodo.",
    },
]

# Índice rápido por ID para lookups O(1) desde el motor del juego
ACHIEVEMENTS_BY_ID: dict[str, dict] = {a["id"]: a for a in ALL_ACHIEVEMENTS}


# =============================================================================
# FUNCIONES DE CONSULTA DEL CATÁLOGO
# =============================================================================

def get_all_achievements() -> list[dict]:
    """
    Devuelve la lista completa de logros del catálogo.

    Retorna:
      list[dict]: Copia de la lista ALL_ACHIEVEMENTS con todos los logros definidos.
                  Cada elemento contiene: id, name, description, emoji, hint.
    """
    # Retornar copia para evitar mutación accidental del catálogo global
    return list(ALL_ACHIEVEMENTS)


def get_achievement_by_id(achievement_id: str) -> dict | None:
    """
    Busca y retorna un logro por su identificador único.

    Parámetros:
      achievement_id (str): El ID del logro a buscar (ej. 'primera_sangre').

    Retorna:
      dict: El logro encontrado, o None si el ID no existe en el catálogo.
    """
    return ACHIEVEMENTS_BY_ID.get(achievement_id)


# =============================================================================
# FUNCIONES DE EVALUACIÓN (Puras — sin efectos secundarios ni I/O)
# =============================================================================

def evaluate_achievements(
    snap: dict,
    chan_rows: list,
    daily_7d: list,
    already_unlocked: set,
    had_zombies_before: bool = False,
    net_profit_total_msat: int | None = None,
    towers_count: int = 0,
) -> list[str]:
    """
    Evalúa el estado actual del nodo y devuelve los IDs de los logros
    que deben desbloquearse en este ciclo de evaluación.

    Solo retorna los logros que AÚN NO están desbloqueados (es decir,
    cuyo ID no esté en 'already_unlocked').

    Parámetros:
      snap (dict): Snapshot actual del nodo. Campos usados:
        - "fwd_count_cum"     (int): Total acumulado de forwards enrutados.
        - "fwd_fees_cum_msat" (int): Total acumulado de fees en msat.
        - "channels_active"   (int): Número de canales activos.
        - "wallet_confirmed"  (int): Saldo confirmado on-chain en sats.
        - "zombie_channels"   (int): Canales zombie detectados en este snapshot.

      chan_rows (list[dict]): Lista de canales del snapshot actual.
      daily_7d (list[dict]): Últimos 7 registros de daily_stats.
      already_unlocked (set[str]): IDs de logros ya desbloqueados (no se reprocesarán).
      had_zombies_before (bool): True si algún snapshot previo tuvo zombie_channels > 0.
      net_profit_total_msat (int | None): Total histórico de ganancia neta en msat.
        Si se provee, se usa para 'ahorrador' con precisión histórica completa.
        Si es None, se suma desde daily_7d (puede subestimar si es historia parcial).

    Retorna:
      list[str]: IDs de logros que deben desbloquearse ahora. Lista vacía si ninguno aplica.
    """
    to_unlock = []

    # Función auxiliar interna: agrega el logro si no está ya desbloqueado.
    def _check(achievement_id: str, condition: bool):
        """Evalúa una condición y encola el logro si aplica y no está desbloqueado."""
        if condition and achievement_id not in already_unlocked:
            to_unlock.append(achievement_id)

    # ── Datos derivados para simplificar las evaluaciones ────────────────────
    fwd_count       = snap.get("fwd_count_cum", 0)
    fees_cum_msat   = snap.get("fwd_fees_cum_msat", 0)
    channels_active = snap.get("channels_active", 0)
    wallet_sats     = snap.get("wallet_confirmed", 0)
    zombie_now      = snap.get("zombie_channels", 0)

    # Canales activos del snapshot actual
    active_chans = [c for c in chan_rows if c.get("active") == 1]

    # Ganancia neta acumulada:
    #   Si el motor inyecta el total histórico real (net_profit_total_msat),
    #   se usa ese valor para mayor precisión. Si no, se suma desde daily_7d
    #   (puede subestimar si daily_7d < historial completo).
    if net_profit_total_msat is not None:
        total_net_msat = net_profit_total_msat
    else:
        total_net_msat = sum(d.get("fees_earned_msat", 0) - d.get("fees_paid_msat", 0)
                             for d in daily_7d)

    # ── Logro: Bautismo de Fuego ─────────────────────────────────────────────
    # Condición: el nodo tiene al menos 1 sat confirmado en la wallet on-chain.
    _check("bautismo_de_fuego", wallet_sats >= 1)

    # ── Logro: Primera Sangre ────────────────────────────────────────────────
    # Condición: al menos 1 pago fue enrutado en toda la historia del nodo.
    _check("primera_sangre", fwd_count >= 1)

    # ── Logro: Enrutador Activo ──────────────────────────────────────────────
    # Condición: más de 100 pagos enrutados de forma acumulada.
    _check("enrutador_activo", fwd_count >= 100)

    # ── Logro: Hub de la Red ─────────────────────────────────────────────────
    # Condición: 5 o más canales activos al mismo tiempo en este snapshot.
    _check("hub_de_la_red", channels_active >= 5)

    # ── Logro: Rayo Plateado ─────────────────────────────────────────────────
    # Condición: todos los canales activos tienen:
    #   - Capacidad >= 1,000,000 sats
    #   - Ratio de liquidez entre 40% y 60%
    if active_chans:
        rayo_plateado_ok = all(
            c.get("capacity", 0) >= 1_000_000 and 40.0 <= c.get("local_ratio", 0) <= 60.0
            for c in active_chans
        )
    else:
        rayo_plateado_ok = False  # Sin canales activos, el logro no aplica
    _check("rayo_plateado", rayo_plateado_ok)

    # ── Logro: Rayo Dorado ───────────────────────────────────────────────────
    # Condición: todos los canales activos tienen:
    #   - Capacidad >= 5,000,000 sats
    #   - Ratio de liquidez entre 40% y 60%
    if active_chans:
        rayo_dorado_ok = all(
            c.get("capacity", 0) >= 5_000_000 and 40.0 <= c.get("local_ratio", 0) <= 60.0
            for c in active_chans
        )
    else:
        rayo_dorado_ok = False
    _check("rayo_dorado", rayo_dorado_ok)

    # ── Logro: Manos de Diamante ─────────────────────────────────────────────
    # Condición: fees acumuladas >= 100,000 sats (convertido a msat para comparar).
    _check("manos_de_diamante", fees_cum_msat >= 100_000 * 1000)

    # ── Logro: El Ahorrador ──────────────────────────────────────────────────
    # Condición: ganancia neta acumulada >= 50,000 sats (en msat).
    # NOTA: game_engine.py puede pasar el total histórico de daily_stats
    # para mayor precisión; este cálculo usa solo lo que se recibe en daily_7d.
    _check("ahorrador", total_net_msat >= 50_000 * 1000)

    # ── Logro: Cazador de Zombies ────────────────────────────────────────────
    # Condición: el nodo tuvo zombies en el pasado (had_zombies_before=True)
    # y actualmente no tiene ninguno (zombie_now == 0).
    _check("cazador_zombies", had_zombies_before and zombie_now == 0)

    # ── Logro: Insomne ───────────────────────────────────────────────────────
    # Condición: los últimos 7 días registrados no tienen ninguna desconexión.
    if len(daily_7d) >= 7:
        insomne_ok = all(d.get("disconnections", 1) == 0 for d in daily_7d)
    else:
        # Sin 7 días completos de historial, el logro no puede verificarse.
        insomne_ok = False
    _check("insomne", insomne_ok)

    # ── Logro: Escudo Protector ──────────────────────────────────────────────
    # Condición: al menos una watchtower conectada.
    _check("escudo_protector", towers_count >= 1)

    return to_unlock
