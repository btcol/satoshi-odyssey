"""
tests/test_quests.py
=====================
Pruebas unitarias para el módulo gamification/quests.py.

Cómo ejecutar:
  Desde la carpeta satoshi-odyssey/:
    python3 -m pytest tests/test_quests.py -v
  O directamente:
    python3 tests/test_quests.py

Cobertura:
  - Catálogo de misiones (estructura, unicidad, tipos válidos).
  - Funciones helpers internas (_count_balanced_channels, etc.).
  - evaluate_all_quests() por misión concreta.
  - evaluate_single_quest() incluyendo ID inexistente.
  - Porcentaje de completitud y lógica de techo (no supera 100%).
  - get_current_week_id() con formato esperado.
"""

import sys
import os
import re

# ── Ajuste de path para importar el paquete gamification ─────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from gamification.quests import (
    ALL_QUESTS,
    QUESTS_BY_ID,
    WEEKLY_QUESTS,
    MILESTONE_QUESTS,
    QUESTS_SCHEMA,
    get_all_quests,
    get_quest_by_id,
    get_current_week_id,
    evaluate_all_quests,
    evaluate_single_quest,
    _count_balanced_channels,
    _count_weekly_transactions,
    _count_consecutive_days_with_5plus_channels,
)


# =============================================================================
# DATOS DE PRUEBA (Fixtures simulados reutilizables)
# =============================================================================

def _snap(channels_active=2, wallet_sats=10_000):
    """Snapshot de nodo configurable con valores por defecto."""
    return {"channels_active": channels_active, "wallet_confirmed": wallet_sats}


def _canal(active=1, local_ratio=50.0, capacity=500_000):
    """Canal simulado configurable."""
    return {"active": active, "local_ratio": local_ratio, "capacity": capacity}


def _dia(fwd_count_delta=0, avg_active_channels=2.0):
    """Registro diario simulado configurable."""
    return {"fwd_count_delta": fwd_count_delta, "avg_active_channels": avg_active_channels}


def _semana_activa(fwd_por_dia=20, canales_por_dia=3.0):
    """7 días de historial con actividad configurable."""
    return [_dia(fwd_count_delta=fwd_por_dia, avg_active_channels=canales_por_dia)
            for _ in range(7)]


# =============================================================================
# PRUEBAS DEL CATÁLOGO
# =============================================================================

def test_catalogo_no_vacio():
    """El catálogo de misiones debe tener al menos una misión."""
    assert len(get_all_quests()) > 0, "El catálogo de misiones está vacío"


def test_catalogo_campos_obligatorios():
    """Cada misión debe tener todos los campos requeridos."""
    campos = {"id", "name", "description", "emoji", "type", "target", "unit", "xp_reward"}
    for quest in get_all_quests():
        faltantes = campos - set(quest.keys())
        assert not faltantes, f"A la misión '{quest.get('id','?')}' le faltan: {faltantes}"


def test_catalogo_ids_unicos():
    """No debe haber dos misiones con el mismo ID."""
    ids = [q["id"] for q in get_all_quests()]
    assert len(ids) == len(set(ids)), "Hay IDs de misiones duplicados"


def test_catalogo_tipos_validos():
    """El campo 'type' de cada misión debe ser 'milestone' o 'weekly'."""
    tipos_validos = {"milestone", "weekly"}
    for quest in get_all_quests():
        assert quest["type"] in tipos_validos, \
            f"Misión '{quest['id']}' tiene tipo inválido: '{quest['type']}'"


def test_catalogo_targets_positivos():
    """El target de cada misión debe ser un número positivo."""
    for quest in get_all_quests():
        assert quest["target"] > 0, \
            f"Misión '{quest['id']}' tiene target no positivo: {quest['target']}"


def test_catalogo_xp_reward_positivo():
    """La recompensa XP de cada misión debe ser positiva."""
    for quest in get_all_quests():
        assert quest["xp_reward"] > 0, \
            f"Misión '{quest['id']}' tiene xp_reward no positivo: {quest['xp_reward']}"


def test_subconjuntos_weekly_y_milestone():
    """WEEKLY_QUESTS y MILESTONE_QUESTS deben sumar el total del catálogo."""
    total = len(ALL_QUESTS)
    assert len(WEEKLY_QUESTS) + len(MILESTONE_QUESTS) == total, \
        "Los subconjuntos weekly+milestone no cubren todo el catálogo"


def test_buscar_quest_existente():
    """get_quest_by_id debe retornar la misión correcta."""
    quest = get_quest_by_id("el_repartidor")
    assert quest is not None
    assert quest["id"] == "el_repartidor"
    assert quest["type"] == "weekly"


def test_buscar_quest_inexistente():
    """get_quest_by_id debe retornar None si el ID no existe."""
    assert get_quest_by_id("mision_fantasma_999") is None


def test_catalogo_retorna_copia():
    """get_all_quests() no debe retornar la lista interna directamente."""
    lista = get_all_quests()
    lista.clear()
    assert len(ALL_QUESTS) > 0, "get_all_quests() devolvió la lista interna"


def test_schema_sql_contiene_tabla():
    """QUESTS_SCHEMA debe contener la definición de la tabla quests_progress."""
    assert "quests_progress" in QUESTS_SCHEMA
    assert "CREATE TABLE IF NOT EXISTS" in QUESTS_SCHEMA


# =============================================================================
# PRUEBAS DE FUNCIONES HELPERS INTERNAS
# =============================================================================

def test_count_balanced_sin_canales():
    """Sin canales, el conteo de balanceados debe ser 0."""
    assert _count_balanced_channels([]) == 0


def test_count_balanced_todos_en_rango():
    """3 canales activos con ratio 50% => 3 balanceados."""
    canales = [_canal(active=1, local_ratio=50.0)] * 3
    assert _count_balanced_channels(canales) == 3


def test_count_balanced_borde_inferior():
    """Canal con ratio exactamente 45.0% debe contar (borde inclusivo)."""
    canales = [_canal(active=1, local_ratio=45.0)]
    assert _count_balanced_channels(canales) == 1


def test_count_balanced_borde_superior():
    """Canal con ratio exactamente 55.0% debe contar (borde inclusivo)."""
    canales = [_canal(active=1, local_ratio=55.0)]
    assert _count_balanced_channels(canales) == 1


def test_count_balanced_fuera_del_rango():
    """Canal con ratio 44.9% no debe contar (por debajo del borde)."""
    canales = [_canal(active=1, local_ratio=44.9)]
    assert _count_balanced_channels(canales) == 0


def test_count_balanced_excluye_inactivos():
    """Canales inactivos (active=0) no deben contar aunque su ratio esté en rango."""
    canales = [
        _canal(active=1, local_ratio=50.0),   # Activo → cuenta
        _canal(active=0, local_ratio=50.0),   # Inactivo → no cuenta
    ]
    assert _count_balanced_channels(canales) == 1


def test_count_weekly_sin_historial():
    """Sin días de historial, el conteo semanal debe ser 0."""
    assert _count_weekly_transactions([]) == 0


def test_count_weekly_suma_correcta():
    """Suma de fwd_count_delta de 7 días = total semanal."""
    dias = [_dia(fwd_count_delta=15) for _ in range(7)]  # 7 × 15 = 105
    assert _count_weekly_transactions(dias) == 105


def test_count_weekly_con_dias_vacios():
    """Días sin actividad (delta=0) contribuyen 0 sin causar errores."""
    dias = [_dia(fwd_count_delta=0)] * 3 + [_dia(fwd_count_delta=10)] * 4
    assert _count_weekly_transactions(dias) == 40  # 4 × 10


def test_consecutive_days_5plus_sin_historial():
    """Sin historial la racha de días con 5+ canales debe ser 0."""
    assert _count_consecutive_days_with_5plus_channels([]) == 0


def test_consecutive_days_5plus_racha_completa():
    """7 días con 5+ canales => racha de 7."""
    dias = [_dia(avg_active_channels=6.0)] * 7
    assert _count_consecutive_days_with_5plus_channels(dias) == 7


def test_consecutive_days_5plus_racha_parcial():
    """Solo los últimos 3 días tienen 5+ canales; los anteriores no."""
    dias = [
        _dia(avg_active_channels=3.0),  # día 1 (más antiguo) - rompe la racha
        _dia(avg_active_channels=2.0),  # día 2
        _dia(avg_active_channels=4.0),  # día 3 - también rompe
        _dia(avg_active_channels=5.0),  # día 4 ✓
        _dia(avg_active_channels=6.0),  # día 5 ✓
        _dia(avg_active_channels=7.0),  # día 6 ✓ (más reciente)
    ]
    assert _count_consecutive_days_with_5plus_channels(dias) == 3


def test_consecutive_days_5plus_sin_racha():
    """El día más reciente no cumple la condición: racha = 0."""
    dias = [
        _dia(avg_active_channels=6.0),  # día 1 ✓ (pero no el más reciente)
        _dia(avg_active_channels=4.0),  # día 2 ✗ (más reciente - rompe racha)
    ]
    assert _count_consecutive_days_with_5plus_channels(dias) == 0


# =============================================================================
# PRUEBAS DE evaluate_all_quests()
# =============================================================================

def test_evaluate_retorna_todas_las_misiones():
    """evaluate_all_quests() debe retornar un resultado para cada misión del catálogo."""
    resultados = evaluate_all_quests(_snap(), [], [])
    ids_catalogo = {q["id"] for q in ALL_QUESTS}
    ids_resultado = set(resultados.keys())
    assert ids_catalogo == ids_resultado, \
        f"Misiones faltantes en resultado: {ids_catalogo - ids_resultado}"


def test_evaluate_estructura_resultado():
    """Cada resultado debe tener los campos estándar esperados."""
    resultados = evaluate_all_quests(_snap(), [], [])
    campos = {"progress", "target", "completed", "type", "pct", "name", "emoji", "unit", "xp_reward"}
    for quest_id, info in resultados.items():
        faltantes = campos - set(info.keys())
        assert not faltantes, f"Al resultado de '{quest_id}' le faltan: {faltantes}"


def test_el_equilibrador_sin_canales():
    """El Equilibrador con 0 canales balanceados: progress=0, no completado."""
    canales = [_canal(active=1, local_ratio=20.0)] * 2  # Fuera del rango 45-55%
    resultado = evaluate_all_quests(_snap(), canales, [])
    eq = resultado["el_equilibrador"]
    assert eq["progress"] == 0
    assert eq["completed"] is False


def test_el_equilibrador_con_3_canales_balanceados():
    """El Equilibrador con 3 canales balanceados: completado."""
    canales = [_canal(active=1, local_ratio=50.0)] * 3
    resultado = evaluate_all_quests(_snap(), canales, [])
    eq = resultado["el_equilibrador"]
    assert eq["progress"] == 3
    assert eq["completed"] is True


def test_el_equilibrador_con_solo_2():
    """El Equilibrador con solo 2 canales balanceados (meta=3): NO completado."""
    canales = [
        _canal(active=1, local_ratio=50.0),  # ✓
        _canal(active=1, local_ratio=50.0),  # ✓
        _canal(active=1, local_ratio=20.0),  # ✗ desbalanceado
    ]
    resultado = evaluate_all_quests(_snap(), canales, [])
    eq = resultado["el_equilibrador"]
    assert eq["progress"] == 2
    assert eq["completed"] is False


def test_el_repartidor_con_100_transacciones():
    """El Repartidor se completa con exactamente 100 transacciones en la semana."""
    # 100 transacciones exactas en 7 días: 7 días × ~14 + 1 día con 2 = más simple así:
    dias = [_dia(fwd_count_delta=15)] * 6 + [_dia(fwd_count_delta=10)]  # 6×15 + 10 = 100
    resultado = evaluate_all_quests(_snap(), [], dias)
    rep = resultado["el_repartidor"]
    assert rep["progress"] == 100
    assert rep["completed"] is True


def test_el_repartidor_no_completado_con_99():
    """El Repartidor NO se completa con 99 transacciones."""
    dias = [_dia(fwd_count_delta=14)] * 7  # 14 × 7 = 98 < 100
    resultado = evaluate_all_quests(_snap(), [], dias)
    rep = resultado["el_repartidor"]
    assert rep["progress"] == 98
    assert rep["completed"] is False


def test_el_gerente_meta_500():
    """El Gerente requiere 500 transacciones. Con 490: no completado."""
    dias = [_dia(fwd_count_delta=70)] * 7  # 70 × 7 = 490 < 500
    resultado = evaluate_all_quests(_snap(), [], dias)
    assert resultado["el_gerente"]["completed"] is False
    assert resultado["el_gerente"]["progress"] == 490


def test_el_mayorista_meta_1000():
    """El Mayorista requiere 1000 transacciones semanales."""
    dias = [_dia(fwd_count_delta=200)] * 5  # 200 × 5 = 1000
    resultado = evaluate_all_quests(_snap(), [], dias)
    mayorista = resultado["el_mayorista"]
    assert mayorista["progress"] == 1000
    assert mayorista["completed"] is True


def test_hub_premium_con_racha_de_3_dias():
    """Hub Premium: 3 días consecutivos con 5+ canales activos => completado."""
    dias = [_dia(avg_active_channels=5.0)] * 3
    resultado = evaluate_all_quests(_snap(), [], dias)
    hp = resultado["hub_premium"]
    assert hp["progress"] == 3
    assert hp["completed"] is True


def test_hub_premium_racha_rota():
    """Hub Premium: racha rota el día más reciente => progress=0, no completado."""
    dias = [
        _dia(avg_active_channels=5.0),  # día 1 ✓
        _dia(avg_active_channels=5.0),  # día 2 ✓
        _dia(avg_active_channels=4.0),  # día 3 ✗ (el más reciente rompe la racha)
    ]
    resultado = evaluate_all_quests(_snap(), [], dias)
    hp = resultado["hub_premium"]
    assert hp["progress"] == 0
    assert hp["completed"] is False


def test_porcentaje_completitud_calculo():
    """El campo 'pct' debe ser correcto: progress/target × 100, techo en 100."""
    dias = [_dia(fwd_count_delta=25)] * 7  # 175 transacciones
    resultado = evaluate_all_quests(_snap(), [], dias)
    rep = resultado["el_repartidor"]  # target = 100
    # 175/100 × 100 = 175%, pero con techo en 100%
    assert rep["pct"] == 100.0, f"Se esperaba 100.0 (techo), se obtuvo {rep['pct']}"


def test_porcentaje_completitud_parcial():
    """El campo 'pct' debe reflejar el progreso parcial correctamente."""
    dias = [_dia(fwd_count_delta=10)] * 7  # 70 transacciones de 100 = 70%
    resultado = evaluate_all_quests(_snap(), [], dias)
    rep = resultado["el_repartidor"]
    assert rep["pct"] == 70.0, f"Se esperaba 70.0%, se obtuvo {rep['pct']}"


def test_diplomatico_y_francotirador_devuelven_0():
    """El Diplomático y Francotirador devuelven progress=0 (son gestionados por el motor)."""
    resultado = evaluate_all_quests(_snap(), [], [])
    assert resultado["el_diplomatico"]["progress"] == 0
    assert resultado["francotirador_de_fees"]["progress"] == 0


# =============================================================================
# PRUEBAS DE evaluate_single_quest()
# =============================================================================

def test_single_quest_resultado_correcto():
    """evaluate_single_quest retorna el mismo resultado que evaluate_all_quests para esa misión."""
    snap = _snap()
    canales = [_canal(active=1, local_ratio=50.0)] * 3
    single = evaluate_single_quest("el_equilibrador", snap, canales, [])
    all_r   = evaluate_all_quests(snap, canales, [])
    assert single == all_r["el_equilibrador"]


def test_single_quest_id_inexistente():
    """evaluate_single_quest retorna None para un ID que no existe en el catálogo."""
    resultado = evaluate_single_quest("mision_inexistente", _snap(), [], [])
    assert resultado is None


# =============================================================================
# PRUEBA DE get_current_week_id()
# =============================================================================

def test_week_id_formato_correcto():
    """get_current_week_id() debe retornar un string con formato YYYY-WNN."""
    week_id = get_current_week_id()
    # Formato: 4 dígitos de año, guión, W seguido de 2 dígitos de semana
    assert re.match(r"^\d{4}-W\d{2}$", week_id), \
        f"Formato inesperado para week_id: '{week_id}'"


# =============================================================================
# RUNNER MANUAL (sin pytest)
# =============================================================================

def _run_all_tests():
    """Ejecuta todas las pruebas manualmente e imprime el resultado."""
    import traceback

    tests = [(name, obj) for name, obj in globals().items()
             if name.startswith("test_") and callable(obj)]

    passed, failed = 0, 0

    print(f"\n{'='*60}")
    print(f"  PRUEBAS UNITARIAS — gamification/quests.py")
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
