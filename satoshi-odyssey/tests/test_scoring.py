"""
tests/test_scoring.py
=====================
Pruebas unitarias para el módulo gamification/scoring.py.

Cómo ejecutar:
  Desde la carpeta satoshi-odyssey/:
    python3 -m pytest tests/test_scoring.py -v
  O directamente sin pytest:
    python3 tests/test_scoring.py

Principio de diseño:
  Cada función de prueba es independiente y no depende de base de datos
  ni de lncli. Solo inyecta datos simulados directamente a las funciones.
"""

import sys
import os

# ── Ajuste de path para importar el paquete gamification ─────────────────────
# Permite ejecutar este script tanto desde la raíz del proyecto como desde /tests
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from gamification.scoring import (
    calculate_xp,
    calculate_hp,
    get_rank,
    get_full_score_summary,
    RANK_TABLE,
    XP_PER_FORWARD,
    XP_PER_1K_FEES_SATS,
    XP_PER_ACHIEVEMENT,
)


# =============================================================================
# PRUEBAS DE calculate_xp()
# =============================================================================

def test_xp_cero_cuando_nodo_nuevo():
    """Un nodo sin actividad debe tener XP = 0."""
    xp = calculate_xp(fwd_count=0, fees_sats=0, unlocked_achievements=0)
    assert xp == 0, f"Esperado 0, obtenido {xp}"


def test_xp_solo_por_forwards():
    """XP por enrutamientos = fwd_count × XP_PER_FORWARD."""
    xp = calculate_xp(fwd_count=100, fees_sats=0, unlocked_achievements=0)
    expected = 100 * XP_PER_FORWARD
    assert xp == expected, f"Esperado {expected}, obtenido {xp}"


def test_xp_solo_por_fees():
    """XP por fees: 3500 sats => 3 bloques de 1000 => 3 × 10 = 30 XP."""
    xp = calculate_xp(fwd_count=0, fees_sats=3500, unlocked_achievements=0)
    expected = 3 * XP_PER_1K_FEES_SATS
    assert xp == expected, f"Esperado {expected}, obtenido {xp}"


def test_xp_fees_exactos_en_multiplo():
    """5000 sats en fees => 5 bloques de 1000 => 5 × 10 = 50 XP."""
    xp = calculate_xp(fwd_count=0, fees_sats=5000, unlocked_achievements=0)
    expected = 5 * XP_PER_1K_FEES_SATS
    assert xp == expected, f"Esperado {expected}, obtenido {xp}"


def test_xp_por_logros():
    """XP por logros: 3 logros × 50 = 150 XP."""
    xp = calculate_xp(fwd_count=0, fees_sats=0, unlocked_achievements=3)
    expected = 3 * XP_PER_ACHIEVEMENT
    assert xp == expected, f"Esperado {expected}, obtenido {xp}"


def test_xp_combinado():
    """XP total = forwards + fees + logros combinados correctamente."""
    xp = calculate_xp(fwd_count=150, fees_sats=3500, unlocked_achievements=2)
    expected = (150 * XP_PER_FORWARD) + (3 * XP_PER_1K_FEES_SATS) + (2 * XP_PER_ACHIEVEMENT)
    assert xp == expected, f"Esperado {expected}, obtenido {xp}"


def test_xp_con_misiones():
    """XP con misiones completadas debe sumar el completed_quests_xp de forma correcta."""
    xp = calculate_xp(fwd_count=150, fees_sats=3500, unlocked_achievements=2, completed_quests_xp=75)
    expected = (150 * XP_PER_FORWARD) + (3 * XP_PER_1K_FEES_SATS) + (2 * XP_PER_ACHIEVEMENT) + 75
    assert xp == expected, f"Esperado {expected}, obtenido {xp}"


def test_xp_nunca_negativo():
    """La XP nunca debe devolver valores negativos."""
    xp = calculate_xp(fwd_count=-5, fees_sats=-100, unlocked_achievements=-1)
    assert xp >= 0, f"XP no puede ser negativa, obtenido {xp}"


# =============================================================================
# PRUEBAS DE calculate_hp()
# =============================================================================

def test_hp_nodo_perfecto():
    """Sin zombies, liquidez ideal (50%), sin desconexiones => HP máximo o cercano al máximo."""
    hp = calculate_hp(zombies=0, liquidity_ratio=50.0, daily_disconnections=0, history_days=7)
    assert hp == 100, f"Esperado 100, obtenido {hp}"


def test_hp_penalizacion_zombie():
    """1 zombie => HP = 100 - 10 = 90."""
    hp = calculate_hp(zombies=1, liquidity_ratio=50.0, daily_disconnections=0)
    assert hp == 90, f"Esperado 90, obtenido {hp}"


def test_hp_penalizacion_maximo_zombies():
    """Muchos zombies no pueden bajar HP más de HP_MAX_ZOMBIE_PENALTY (40)."""
    hp = calculate_hp(zombies=100, liquidity_ratio=50.0, daily_disconnections=0)
    assert hp == 60, f"Esperado 60 (100 - 40 max zombie), obtenido {hp}"


def test_hp_penalizacion_liquidez_severa_baja():
    """Liquidez < 20% es penalización severa: HP = 100 - 20 = 80."""
    hp = calculate_hp(zombies=0, liquidity_ratio=10.0, daily_disconnections=0)
    assert hp == 80, f"Esperado 80, obtenido {hp}"


def test_hp_penalizacion_liquidez_severa_alta():
    """Liquidez > 80% también es penalización severa: HP = 100 - 20 = 80."""
    hp = calculate_hp(zombies=0, liquidity_ratio=95.0, daily_disconnections=0)
    assert hp == 80, f"Esperado 80, obtenido {hp}"


def test_hp_penalizacion_liquidez_moderada():
    """Liquidez entre 25% y 30% es penalización moderada: HP = 100 - 10 = 90."""
    hp = calculate_hp(zombies=0, liquidity_ratio=25.0, daily_disconnections=0)
    assert hp == 90, f"Esperado 90, obtenido {hp}"


def test_hp_liquidez_exactamente_en_borde_moderado():
    """
    Liquidez exactamente en 20.0% cae en la penalización MODERADA (no severa),
    porque la condición severa es '< 20.0' (exclusivo).
    HP = 100 - 10 (moderado) = 90.
    """
    hp = calculate_hp(zombies=0, liquidity_ratio=20.0, daily_disconnections=0)
    assert hp == 90, f"Esperado 90 (borde moderado, no severo), obtenido {hp}"


def test_hp_liquidez_estrictamente_severa():
    """Liquidez por debajo de 20% (ej: 19.9) sí aplica penalización severa: HP = 80."""
    hp = calculate_hp(zombies=0, liquidity_ratio=19.9, daily_disconnections=0)
    assert hp == 80, f"Esperado 80 (severo: < 20.0), obtenido {hp}"


def test_hp_liquidez_dentro_de_rango_optimo():
    """Liquidez en 50% es el rango ideal: sin penalización."""
    hp = calculate_hp(zombies=0, liquidity_ratio=50.0, daily_disconnections=0, history_days=0)
    # Sin historial suficiente (< 3 días) no aplica el bonus de uptime
    assert hp == 100, f"Esperado 100, obtenido {hp}"


def test_hp_penalizacion_desconexiones():
    """2 días con desconexiones => HP = 100 - (2 × 5) = 90."""
    hp = calculate_hp(zombies=0, liquidity_ratio=50.0, daily_disconnections=2)
    assert hp == 90, f"Esperado 90, obtenido {hp}"


def test_hp_maximo_penalizacion_desconexiones():
    """Muchas desconexiones no bajan más de 20 puntos de HP."""
    hp = calculate_hp(zombies=0, liquidity_ratio=50.0, daily_disconnections=10)
    assert hp == 80, f"Esperado 80 (100 - 20 max desconexion), obtenido {hp}"


def test_hp_bonus_uptime_perfecto():
    """Sin desconexiones y con 3+ días de historial => bonus de +5 HP."""
    # Nodo perfecto + bonus => sigue siendo 100 (no puede superar 100)
    hp = calculate_hp(zombies=0, liquidity_ratio=50.0, daily_disconnections=0, history_days=5)
    assert hp == 100, f"Esperado 100 (bonus pero techo=100), obtenido {hp}"


def test_hp_nunca_negativo():
    """HP no puede ser negativo bajo ninguna combinación de penalizaciones."""
    hp = calculate_hp(zombies=100, liquidity_ratio=5.0, daily_disconnections=100)
    assert hp >= 0, f"HP no puede ser negativa, obtenido {hp}"


def test_hp_nunca_supera_100():
    """HP no puede superar 100 bajo ninguna circunstancia."""
    hp = calculate_hp(zombies=0, liquidity_ratio=50.0, daily_disconnections=0, history_days=100)
    assert hp <= 100, f"HP no puede superar 100, obtenido {hp}"


# =============================================================================
# PRUEBAS DE get_rank()
# =============================================================================

def test_rango_inicial_sin_xp():
    """Con 0 XP el operador es 'Aprendiz de Satoshi'."""
    rank = get_rank(0)
    assert rank["name"] == "Aprendiz de Satoshi"
    assert rank["level"] == 0
    assert rank["xp_current"] == 0
    assert rank["xp_next_rank"] == 50  # siguiente umbral en la tabla


def test_rango_novato():
    """Con 50+ XP el operador alcanza 'Novato del Rayo'."""
    rank = get_rank(50)
    assert rank["name"] == "Novato del Rayo"
    assert rank["level"] == 1


def test_rango_enrutador():
    """Con 200+ XP el operador alcanza 'Enrutador Activo'."""
    rank = get_rank(200)
    assert rank["name"] == "Enrutador Activo"
    assert rank["level"] == 2


def test_rango_maestro():
    """Con 500+ XP el operador alcanza 'Maestro de Liquidez'."""
    rank = get_rank(500)
    assert rank["name"] == "Maestro de Liquidez"
    assert rank["level"] == 3


def test_rango_hub_maximo():
    """Con 1000+ XP el operador alcanza el rango máximo 'Hub de la Red'."""
    rank = get_rank(1000)
    assert rank["name"] == "Hub de la Red"
    assert rank["level"] == 4
    assert rank["xp_next_rank"] is None  # No hay rango superior


def test_rango_exactamente_en_umbral():
    """XP exactamente en el umbral debe conceder el rango correspondiente."""
    for threshold, name, level in RANK_TABLE:
        rank = get_rank(threshold)
        assert rank["name"] == name, \
            f"Con XP={threshold} se esperaba '{name}', se obtuvo '{rank['name']}'"


def test_rango_justo_debajo_del_umbral():
    """XP justo por debajo de un umbral NO debe conceder ese rango."""
    rank = get_rank(49)
    assert rank["name"] == "Aprendiz de Satoshi", \
        f"Con XP=49 debería ser 'Aprendiz', se obtuvo '{rank['name']}'"


# =============================================================================
# PRUEBAS DE get_full_score_summary()
# =============================================================================

def test_resumen_completo_nodo_nuevo():
    """Un nodo sin actividad debe retornar XP=0, HP alto, rango inicial."""
    summary = get_full_score_summary(
        fwd_count=0, fees_sats=0, unlocked_achievements=0,
        zombies=0, liquidity_ratio=50.0, daily_disconnections=0
    )
    assert "xp"   in summary
    assert "hp"   in summary
    assert "rank" in summary
    assert summary["xp"] == 0
    assert summary["hp"] == 100
    assert summary["rank"]["level"] == 0


def test_resumen_completo_nodo_avanzado():
    """Un nodo activo debe tener XP > 0, HP calculado y rango superior."""
    summary = get_full_score_summary(
        fwd_count=300, fees_sats=8000, unlocked_achievements=4,
        zombies=0, liquidity_ratio=55.0, daily_disconnections=0,
        history_days=7
    )
    assert summary["xp"] > 0
    assert summary["hp"] > 50
    assert summary["rank"]["level"] > 0


# =============================================================================
# RUNNER MANUAL (sin pytest)
# =============================================================================

def _run_all_tests():
    """Ejecuta todas las pruebas manualmente e imprime el resultado."""
    import traceback

    # Recopilar todas las funciones que empiezan con "test_"
    tests = [(name, obj) for name, obj in globals().items()
             if name.startswith("test_") and callable(obj)]

    passed = 0
    failed = 0
    errors = []

    print(f"\n{'='*60}")
    print(f"  PRUEBAS UNITARIAS — gamification/scoring.py")
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
