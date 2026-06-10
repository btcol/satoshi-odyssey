"""
tests/test_achievements.py
===========================
Pruebas unitarias para el módulo gamification/achievements.py.

Cómo ejecutar:
  Desde la carpeta satoshi-odyssey/:
    python3 -m pytest tests/test_achievements.py -v
  O directamente:
    python3 tests/test_achievements.py

Principio de diseño:
  Cada prueba construye su propio snapshot/canales simulados y verifica
  que evaluate_achievements() retorne exactamente los IDs esperados.
  No se usa base de datos en ningún momento.
"""

import sys
import os

# ── Ajuste de path para importar el paquete gamification ─────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from gamification.achievements import (
    ALL_ACHIEVEMENTS,
    ACHIEVEMENTS_BY_ID,
    get_all_achievements,
    get_achievement_by_id,
    evaluate_achievements,
)


# =============================================================================
# DATOS DE PRUEBA (Fixtures simulados reutilizables)
# =============================================================================

def _snap_vacio():
    """Snapshot de un nodo nuevo, sin actividad alguna."""
    return {
        "fwd_count_cum":     0,
        "fwd_fees_cum_msat": 0,
        "channels_active":   0,
        "wallet_confirmed":  0,
        "zombie_channels":   0,
    }


def _snap_activo(
    fwd_count=50,
    fees_msat=0,
    channels_active=2,
    wallet_sats=10_000,
    zombies=0,
):
    """Snapshot de un nodo con actividad básica configurable."""
    return {
        "fwd_count_cum":     fwd_count,
        "fwd_fees_cum_msat": fees_msat,
        "channels_active":   channels_active,
        "wallet_confirmed":  wallet_sats,
        "zombie_channels":   zombies,
    }


def _canal(capacity=500_000, local_ratio=50.0, active=1, is_zombie=0):
    """Genera un registro de canal simulado con valores configurables."""
    return {
        "active":      active,
        "local_ratio": local_ratio,
        "capacity":    capacity,
        "is_zombie":   is_zombie,
    }


def _dia(disconnections=0, fees_earned_msat=0, fees_paid_msat=0):
    """Genera un registro diario simulado."""
    return {
        "disconnections":   disconnections,
        "fees_earned_msat": fees_earned_msat,
        "fees_paid_msat":   fees_paid_msat,
    }


def _semana_perfecta():
    """7 días seguidos sin desconexiones ni costos relevantes."""
    return [_dia() for _ in range(7)]


# =============================================================================
# PRUEBAS DEL CATÁLOGO (get_all_achievements / get_achievement_by_id)
# =============================================================================

def test_catalogo_tiene_logros():
    """El catálogo debe tener al menos un logro definido."""
    logros = get_all_achievements()
    assert len(logros) > 0, "El catálogo de logros está vacío"


def test_catalogo_tiene_campos_requeridos():
    """Cada logro del catálogo debe tener los campos obligatorios."""
    campos_requeridos = {"id", "name", "description", "emoji", "hint"}
    for logro in get_all_achievements():
        faltantes = campos_requeridos - set(logro.keys())
        assert not faltantes, f"Al logro '{logro.get('id','?')}' le faltan: {faltantes}"


def test_catalogo_ids_son_unicos():
    """No debe haber dos logros con el mismo ID."""
    ids = [a["id"] for a in get_all_achievements()]
    assert len(ids) == len(set(ids)), "Hay IDs de logros duplicados en el catálogo"


def test_buscar_logro_existente():
    """get_achievement_by_id debe retornar el logro correcto."""
    logro = get_achievement_by_id("primera_sangre")
    assert logro is not None
    assert logro["id"] == "primera_sangre"
    assert logro["emoji"] == "⚡"


def test_buscar_logro_inexistente():
    """get_achievement_by_id debe retornar None si el ID no existe."""
    resultado = get_achievement_by_id("logro_que_no_existe_99")
    assert resultado is None


def test_catalogo_retorna_copia():
    """get_all_achievements() no debe retornar la lista interna directamente."""
    lista1 = get_all_achievements()
    lista2 = get_all_achievements()
    lista1.clear()  # Mutar la copia
    assert len(lista2) > 0, "get_all_achievements() devolvió la lista interna (no es copia)"


# =============================================================================
# PRUEBAS DE evaluate_achievements() — Nodo sin actividad
# =============================================================================

def test_nodo_nuevo_sin_logros():
    """Un nodo sin actividad no debe desbloquear ningún logro."""
    nuevos = evaluate_achievements(
        snap=_snap_vacio(),
        chan_rows=[],
        daily_7d=[],
        already_unlocked=set(),
    )
    assert nuevos == [], f"Se esperaba lista vacía, se obtuvo: {nuevos}"


# =============================================================================
# PRUEBAS DE evaluate_achievements() — Logro por logro
# =============================================================================

def test_bautismo_de_fuego_con_sats():
    """Bautismo de Fuego: se desbloquea al tener al menos 1 sat confirmado."""
    snap = _snap_vacio()
    snap["wallet_confirmed"] = 1
    nuevos = evaluate_achievements(snap, [], [], set())
    assert "bautismo_de_fuego" in nuevos


def test_bautismo_de_fuego_sin_sats():
    """Bautismo de Fuego: NO se desbloquea si la wallet está vacía."""
    nuevos = evaluate_achievements(_snap_vacio(), [], [], set())
    assert "bautismo_de_fuego" not in nuevos


def test_primera_sangre_con_forward():
    """Primera Sangre: se desbloquea con al menos 1 forward enrutado."""
    snap = _snap_vacio()
    snap["fwd_count_cum"] = 1
    nuevos = evaluate_achievements(snap, [], [], set())
    assert "primera_sangre" in nuevos


def test_primera_sangre_sin_forward():
    """Primera Sangre: NO se desbloquea con 0 forwards."""
    nuevos = evaluate_achievements(_snap_vacio(), [], [], set())
    assert "primera_sangre" not in nuevos


def test_enrutador_activo_con_100_forwards():
    """Enrutador Activo: se desbloquea con exactamente 100 forwards."""
    snap = _snap_vacio()
    snap["fwd_count_cum"] = 100
    nuevos = evaluate_achievements(snap, [], [], set())
    assert "enrutador_activo" in nuevos


def test_enrutador_activo_no_con_99():
    """Enrutador Activo: NOT se desbloquea con 99 forwards."""
    snap = _snap_vacio()
    snap["fwd_count_cum"] = 99
    nuevos = evaluate_achievements(snap, [], [], set())
    assert "enrutador_activo" not in nuevos


def test_hub_de_la_red_con_5_canales():
    """Hub de la Red: se desbloquea con exactamente 5 canales activos."""
    snap = _snap_activo(channels_active=5)
    nuevos = evaluate_achievements(snap, [], [], set())
    assert "hub_de_la_red" in nuevos


def test_hub_de_la_red_no_con_4_canales():
    """Hub de la Red: NOT se desbloquea con 4 canales."""
    snap = _snap_activo(channels_active=4)
    nuevos = evaluate_achievements(snap, [], [], set())
    assert "hub_de_la_red" not in nuevos


def test_rayo_plateado_todos_balanceados_y_capacidad_suficiente():
    """Rayo Plateado: se desbloquea si todos los canales tienen >= 1M sats y ratio 40-60%."""
    canales = [_canal(capacity=1_000_000, local_ratio=50.0)] * 3
    snap = _snap_activo(channels_active=3)
    nuevos = evaluate_achievements(snap, canales, [], set())
    assert "rayo_plateado" in nuevos


def test_rayo_plateado_falla_si_un_canal_tiene_poca_capacidad():
    """Rayo Plateado: falla si uno de los canales tiene menos de 1M sats."""
    canales = [
        _canal(capacity=1_000_000, local_ratio=50.0),
        _canal(capacity=999_999,   local_ratio=50.0),  # Justo por debajo del umbral
    ]
    snap = _snap_activo(channels_active=2)
    nuevos = evaluate_achievements(snap, canales, [], set())
    assert "rayo_plateado" not in nuevos


def test_rayo_plateado_falla_si_ratio_desbalanceado():
    """Rayo Plateado: falla si un canal está desbalanceado (ratio fuera de 40-60%)."""
    canales = [
        _canal(capacity=2_000_000, local_ratio=50.0),
        _canal(capacity=2_000_000, local_ratio=39.9),  # Justo por debajo del 40%
    ]
    snap = _snap_activo(channels_active=2)
    nuevos = evaluate_achievements(snap, canales, [], set())
    assert "rayo_plateado" not in nuevos


def test_rayo_plateado_sin_canales_activos():
    """Rayo Plateado: NOT se desbloquea si no hay canales activos."""
    snap = _snap_activo(channels_active=0)
    nuevos = evaluate_achievements(snap, [], [], set())
    assert "rayo_plateado" not in nuevos


def test_rayo_dorado_capacidad_5M():
    """Rayo Dorado: se desbloquea si todos los canales tienen >= 5M sats y ratio 40-60%."""
    canales = [_canal(capacity=5_000_000, local_ratio=50.0)] * 2
    snap = _snap_activo(channels_active=2)
    nuevos = evaluate_achievements(snap, canales, [], set())
    assert "rayo_dorado" in nuevos


def test_rayo_dorado_no_con_capacidad_1M():
    """Rayo Dorado: NOT se desbloquea con 1M sats (requiere 5M)."""
    canales = [_canal(capacity=1_000_000, local_ratio=50.0)] * 2
    snap = _snap_activo(channels_active=2)
    nuevos = evaluate_achievements(snap, canales, [], set())
    assert "rayo_dorado" not in nuevos


def test_manos_de_diamante_con_100k_sats():
    """Manos de Diamante: se desbloquea con exactamente 100,000 sats en fees (en msat)."""
    snap = _snap_activo(fees_msat=100_000 * 1000)
    nuevos = evaluate_achievements(snap, [], [], set())
    assert "manos_de_diamante" in nuevos


def test_manos_de_diamante_no_con_99k():
    """Manos de Diamante: NOT se desbloquea con 99,999 sats en fees."""
    snap = _snap_activo(fees_msat=(100_000 * 1000) - 1)
    nuevos = evaluate_achievements(snap, [], [], set())
    assert "manos_de_diamante" not in nuevos


def test_ahorrador_con_ganancia_neta_suficiente():
    """El Ahorrador: se desbloquea con 50,000 sats de ganancia neta total."""
    # 10 días × (6000 earned - 1000 paid) = 10 × 5,000,000 msat = 50,000,000 msat = 50k sats
    historial = [_dia(fees_earned_msat=6_000_000, fees_paid_msat=1_000_000) for _ in range(10)]
    nuevos = evaluate_achievements(_snap_vacio(), [], historial, set())
    assert "ahorrador" in nuevos


def test_ahorrador_no_con_ganancia_insuficiente():
    """El Ahorrador: NOT se desbloquea si la ganancia neta es menor a 50k sats."""
    # 1 día con 10,000 sats de ganancia
    historial = [_dia(fees_earned_msat=10_000_000, fees_paid_msat=0)]
    nuevos = evaluate_achievements(_snap_vacio(), [], historial, set())
    assert "ahorrador" not in nuevos


def test_cazador_de_zombies_con_historial_de_zombies():
    """Cazador de Zombies: se desbloquea si hubo zombies en el pasado y ahora no hay."""
    snap = _snap_activo(zombies=0)
    nuevos = evaluate_achievements(snap, [], [], set(), had_zombies_before=True)
    assert "cazador_zombies" in nuevos


def test_cazador_de_zombies_sin_historial():
    """Cazador de Zombies: NOT se desbloquea si nunca hubo zombies."""
    snap = _snap_activo(zombies=0)
    nuevos = evaluate_achievements(snap, [], [], set(), had_zombies_before=False)
    assert "cazador_zombies" not in nuevos


def test_cazador_de_zombies_todavia_hay_zombies():
    """Cazador de Zombies: NOT se desbloquea si aún hay zombies activos."""
    snap = _snap_activo(zombies=2)
    nuevos = evaluate_achievements(snap, [], [], set(), had_zombies_before=True)
    assert "cazador_zombies" not in nuevos


def test_insomne_con_7_dias_perfectos():
    """Insomne: se desbloquea con exactamente 7 días sin desconexiones."""
    nuevos = evaluate_achievements(_snap_vacio(), [], _semana_perfecta(), set())
    assert "insomne" in nuevos


def test_insomne_no_con_menos_de_7_dias():
    """Insomne: NOT se desbloquea si hay menos de 7 días de historial."""
    historial_incompleto = [_dia() for _ in range(6)]  # Solo 6 días
    nuevos = evaluate_achievements(_snap_vacio(), [], historial_incompleto, set())
    assert "insomne" not in nuevos


def test_insomne_no_si_hubo_desconexion():
    """Insomne: NOT se desbloquea si algún día tuvo desconexiones."""
    semana_con_falla = _semana_perfecta()
    semana_con_falla[3]["disconnections"] = 1  # Falla el día 4
    nuevos = evaluate_achievements(_snap_vacio(), [], semana_con_falla, set())
    assert "insomne" not in nuevos


# =============================================================================
# PRUEBAS DE already_unlocked (no re-desbloquear)
# =============================================================================

def test_no_re_desbloquea_logro_ya_desbloqueado():
    """Un logro ya desbloqueado NO debe aparecer de nuevo en la lista de retorno."""
    snap = _snap_activo(fwd_count=1, wallet_sats=100)
    # 'primera_sangre' y 'bautismo_de_fuego' ya estaban desbloqueados
    ya_desbloqueados = {"primera_sangre", "bautismo_de_fuego"}
    nuevos = evaluate_achievements(snap, [], [], ya_desbloqueados)
    assert "primera_sangre"   not in nuevos
    assert "bautismo_de_fuego" not in nuevos


def test_desbloquea_solo_los_nuevos():
    """Solo debe retornar logros nuevos, aunque las condiciones de otros ya estén cumplidas."""
    snap = _snap_activo(fwd_count=150, channels_active=6, wallet_sats=5_000)
    ya_desbloqueados = {"bautismo_de_fuego", "primera_sangre"}
    nuevos = evaluate_achievements(snap, [], [], ya_desbloqueados)
    # 'enrutador_activo' (150 >= 100) y 'hub_de_la_red' (6 >= 5) deben aparecer
    assert "enrutador_activo" in nuevos
    assert "hub_de_la_red"    in nuevos
    # Los ya desbloqueados no deben aparecer
    assert "bautismo_de_fuego" not in nuevos
    assert "primera_sangre"    not in nuevos


def test_retorno_es_lista_vacia_cuando_todos_ya_desbloqueados():
    """Si todos los logros aplicables ya están desbloqueados, retornar lista vacía."""
    snap = _snap_activo(fwd_count=1, wallet_sats=1)
    ya_desbloqueados = {"bautismo_de_fuego", "primera_sangre"}
    nuevos = evaluate_achievements(snap, [], [], ya_desbloqueados)
    assert nuevos == []


def test_escudo_protector_con_towers():
    """Escudo Protector: se desbloquea al conectar al menos una torre."""
    nuevos = evaluate_achievements(_snap_vacio(), [], [], set(), towers_count=1)
    assert "escudo_protector" in nuevos


def test_escudo_protector_sin_towers():
    """Escudo Protector: no se desbloquea con 0 torres."""
    nuevos = evaluate_achievements(_snap_vacio(), [], [], set(), towers_count=0)
    assert "escudo_protector" not in nuevos


# =============================================================================
# RUNNER MANUAL (sin pytest)
# =============================================================================

def _run_all_tests():
    """Ejecuta todas las pruebas manualmente e imprime el resultado."""
    import traceback

    # Recopilar todas las funciones que empiezan con "test_"
    tests = [(name, obj) for name, obj in globals().items()
             if name.startswith("test_") and callable(obj)]

    passed, failed, errors = 0, 0, []

    print(f"\n{'='*60}")
    print(f"  PRUEBAS UNITARIAS — gamification/achievements.py")
    print(f"{'='*60}\n")

    for name, fn in tests:
        try:
            fn()
            print(f"  ✅  {name}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌  {name} — {e}")
            failed += 1
            errors.append((name, str(e)))
        except Exception as e:
            print(f"  💥  {name} — ERROR INESPERADO: {e}")
            traceback.print_exc()
            failed += 1
            errors.append((name, str(e)))

    print(f"\n{'='*60}")
    print(f"  Resultado: {passed} pasadas / {failed} fallidas de {len(tests)} pruebas.")
    print(f"{'='*60}\n")
    return failed == 0


if __name__ == "__main__":
    success = _run_all_tests()
    sys.exit(0 if success else 1)
