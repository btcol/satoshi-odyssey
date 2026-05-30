#!/usr/bin/env bash
# =============================================================================
# 02_list_peers.sh
# -----------------------------------------------------------------------------
# Lista todos los peers (nodos) con los que tu nodo Lightning tiene conexión
# activa, indicando si existe un canal abierto con cada uno.
#
# Para cada peer muestra:
#   - Estado: CON_CANAL (hay canal abierto) o SIN_CANAL (solo conectado)
#   - Pubkey del peer
#   - Dirección P2P (IP:puerto o .onion)
#
# VARIABLES DE ENTORNO (opcionales):
#   NETWORK    → red LND (default: testnet4)
#   LNCLI_BIN  → binario lncli (default: lncli-debug)
#
# USO:
#   sh scripts/02_list_peers.sh
# =============================================================================

set -euo pipefail

# ── Configuración ─────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DASHBOARD_DIR="$(dirname "$SCRIPT_DIR")"
ROOT_DIR="$DASHBOARD_DIR"

if [ -f "$ROOT_DIR/.env" ]; then
    source "$ROOT_DIR/.env"
fi

NETWORK="${NETWORK:-testnet4}"
LNCLI_BIN="${LNCLI_BIN:-lncli-debug}"

# ── Verificar dependencias ────────────────────────────────────────────────────
need_cmd() {
  command -v "$1" > /dev/null 2>&1 || {
    echo "Error: falta el comando '$1'" >&2
    exit 1
  }
}
need_cmd "$LNCLI_BIN"
need_cmd jq

echo "=== Todos los peers con su estado ==="

# Obtener las pubkeys de los canales abiertos para comparar con los peers
# Esto permite marcar qué peers tienen canal vs. solo están conectados
chan_pubkeys=$(${LNCLI_BIN} -network="${NETWORK}" listchannels | jq -r '[.channels[].remote_pubkey]')

# Listar todos los peers conectados actualmente y clasificar cada uno:
#   - Si su pubkey está en chan_pubkeys → CON_CANAL
#   - Si no está                        → SIN_CANAL
# Salida formateada: ESTADO | PUBKEY | DIRECCIÓN
${LNCLI_BIN} -network="${NETWORK}" listpeers | jq -r --argjson chans "$chan_pubkeys" \
  '.peers[] | [
    if (.pub_key as $p | $chans | index($p) != null) then "CON_CANAL" else "SIN_CANAL" end,
    .pub_key,
    .address
  ] | @tsv' | awk -F'\t' '{printf "%-12s | %s | %s\n", $1, $2, $3}'

echo
echo "Leyenda:"
echo "  CON_CANAL  → peer con canal abierto (capital comprometido)"
echo "  SIN_CANAL  → peer solo conectado, sin canal abierto"
