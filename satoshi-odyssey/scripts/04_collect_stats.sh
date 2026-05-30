#!/usr/bin/env bash
# =============================================================================
# 04_collect_stats.sh
# -----------------------------------------------------------------------------
# Recolector de estadísticas históricas del nodo Lightning Network.
# Guarda métricas en una base de datos SQLite local para análisis de rendimiento.
#
# Se invoca automáticamente en cada ciclo de auto-escaneo del dashboard,
# o manualmente para un snapshot puntual.
#
# VARIABLES DE ENTORNO (opcionales — tienen valores por defecto):
#   NETWORK    → red LND (default: testnet4)
#   LNCLI_BIN  → binario lncli (default: lncli-debug)
#
# USO:
#   bash scripts/04_collect_stats.sh
#   NETWORK=mainnet bash scripts/04_collect_stats.sh
# =============================================================================

set -euo pipefail

# ── Rutas ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DASHBOARD_DIR="$(dirname "$SCRIPT_DIR")"
COLLECTOR="$SCRIPT_DIR/collect_stats.py"
ROOT_DIR="$DASHBOARD_DIR"

if [ -f "$ROOT_DIR/.env" ]; then
    source "$ROOT_DIR/.env"
fi

# ── Configuración ─────────────────────────────────────────────────────────────
export NETWORK="${NETWORK:-testnet4}"
export LNCLI_BIN="${LNCLI_BIN:-lncli-debug}"

# ── Verificar dependencias ─────────────────────────────────────────────────────
need_cmd() {
  command -v "$1" > /dev/null 2>&1 || {
    echo "[04_collect_stats] ERROR: falta el comando '$1'" >&2
    exit 1
  }
}
need_cmd python3
need_cmd "$LNCLI_BIN"

# Verificar que el script Python existe
if [[ ! -f "$COLLECTOR" ]]; then
  echo "[04_collect_stats] ERROR: No se encontró $COLLECTOR" >&2
  exit 1
fi

# ── Ejecutar el colector Python ────────────────────────────────────────────────
echo "[$(date '+%H:%M:%S')] Iniciando recolección de estadísticas..."
python3 "$COLLECTOR"
EXIT_CODE=$?

if [[ $EXIT_CODE -eq 0 ]]; then
  echo "[$(date '+%H:%M:%S')] ✅ Estadísticas guardadas correctamente."
else
  echo "[$(date '+%H:%M:%S')] ⚠️  El colector terminó con código $EXIT_CODE." >&2
fi

exit $EXIT_CODE
