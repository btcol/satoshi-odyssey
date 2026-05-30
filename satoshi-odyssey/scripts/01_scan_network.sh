#!/usr/bin/env bash
# =============================================================================
# 01_scan_network.sh
# -----------------------------------------------------------------------------
# Escanea la red Lightning consultando:
#   - Tus canales abiertos (listchannels)
#   - El graph público de la red (describegraph / gossips)
#
# Para cada peer con el que tienes canal:
#   • Muestra alias, pubkey, última actualización gossip, nº canales, cap. total
#   • Muestra sus vecinos (nodos a 2 saltos) con estado del canal peer↔vecino
#
# Al finalizar exporta un CSV con toda la red para la visualización 3D.
#
# VARIABLES DE ENTORNO (opcionales — tienen valores por defecto):
#   NETWORK    → red LND (default: testnet4)
#   LNCLI_BIN  → binario lncli (default: lncli-debug)
#   CSV_OUT    → ruta del archivo CSV de salida (default: ../data/lightning_network.csv)
#
# USO:
#   sh scripts/01_scan_network.sh
#   CSV_OUT=/ruta/custom.csv sh scripts/01_scan_network.sh
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
# CSV_OUT apunta a ../data/ relativo al directorio lightning-dashboard
CSV_OUT="${CSV_OUT:-${DASHBOARD_DIR}/data/lightning_network.csv}"
export CSV_OUT
NOW=$(date +%s)   # timestamp actual en segundos (para calcular "hace N días")

# ── Función: verificar dependencias ──────────────────────────────────────────
# Comprueba que un comando esté disponible en PATH antes de continuar.
need_cmd() {
  command -v "$1" > /dev/null 2>&1 || {
    echo "Error: falta el comando '$1'" >&2
    exit 1
  }
}
need_cmd "$LNCLI_BIN"
need_cmd jq

# ── Función: timestamp → fecha legible + "hace N días" ───────────────────────
# Convierte un timestamp Unix en una cadena legible como "2024-01-15 10:30 (hace 5 días)".
# Si el timestamp es 0 o vacío, devuelve "nunca".
fmt_date() {
  local ts="$1"
  if [[ "$ts" == "0" || -z "$ts" ]]; then
    echo "nunca"
    return
  fi
  local fecha diff days
  fecha=$(date -d "@${ts}" '+%Y-%m-%d %H:%M' 2>/dev/null || date -r "${ts}" '+%Y-%m-%d %H:%M')
  diff=$(( NOW - ts ))
  days=$(( diff / 86400 ))
  echo "${fecha} (hace ${days} días)"
}

# ── Función: sats → formato legible ──────────────────────────────────────────
# Convierte un número de satoshis en texto con separadores de miles.
# Ejemplo: 1000000 → "1,000,000 sats"
fmt_sats() {
  local n="$1"
  [[ -z "$n" || "$n" == "null" ]] && echo "?" && return
  printf "%'d sats" "$n"
}

# =============================================================================
# SECCIÓN 1: Obtener canales propios y graph de la red
# =============================================================================
echo "Obteniendo canales abiertos..."
# listchannels: devuelve todos los canales activos e inactivos del nodo
channels_json=$($LNCLI_BIN -network="$NETWORK" listchannels)

# Extraer las pubkeys de todos los peers con canal abierto (sin duplicados)
my_peers=$(echo "$channels_json" | jq -r '.channels[].remote_pubkey' | sort -u)
MY_PUBKEY=$($LNCLI_BIN -network="$NETWORK" getinfo | jq -r '.identity_pubkey // empty')

if [[ -z "$my_peers" ]]; then
  echo "No tienes canales abiertos." >&2
  exit 1
fi

echo "Obteniendo graph de la red (puede tardar unos segundos)..."
# describegraph: devuelve todos los nodos y canales conocidos por el nodo
# (basado en los gossips recibidos de la red)
graph_json=$($LNCLI_BIN -network="$NETWORK" describegraph)

echo
echo "================================================================"
echo " ESCANEO DE RED - PROFUNDIDAD DINÁMICA (MAX_HOPS=${MAX_HOPS:-2})"
echo "================================================================"

tmp_channels=$(mktemp)
tmp_graph=$(mktemp)
echo "$channels_json" > "$tmp_channels"
echo "$graph_json" > "$tmp_graph"

# Info básica para debug
total_nodes=$(echo "$graph_json" | jq '.nodes | length')
total_edges=$(echo "$graph_json" | jq '.edges | length')
echo "Graph total en memoria: $total_nodes nodos y $total_edges canales."

python3 -c "
import os, sys, json, time
from datetime import datetime

def fmt_date(ts):
    if not ts: return 'nunca'
    diff = int(time.time() - ts)
    days = diff // 86400
    dt_str = datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M')
    return f'{dt_str} (hace {days} d)'

def fmt_sats(n):
    if n is None or str(n) == 'null': return '0'
    return f'{int(n):,} sats'

try:
    with open('$tmp_channels') as f:
        channels_data = json.load(f)
    with open('$tmp_graph') as f:
        graph_data = json.load(f)
except Exception as e:
    print(f'Error al procesar JSON en Python: {e}', file=sys.stderr)
    sys.exit(1)

max_hops = int(os.environ.get('MAX_HOPS', '2'))
my_pubkey = '$MY_PUBKEY'

my_peers = set()
for ch in channels_data.get('channels', []):
    pk = ch.get('remote_pubkey')
    if pk: my_peers.add(pk)

nodes = {}
for n in graph_data.get('nodes', []):
    nodes[n['pub_key']] = n

adj = {}
for e in graph_data.get('edges', []):
    n1 = e.get('node1_pub')
    n2 = e.get('node2_pub')
    if not n1 or not n2: continue
    if n1 not in adj: adj[n1] = []
    if n2 not in adj: adj[n2] = []
    adj[n1].append(e)
    adj[n2].append(e)

node_cap = {}
node_chan_count = {}
for pub, edges in adj.items():
    node_chan_count[pub] = len(edges)
    node_cap[pub] = sum(int(e.get('capacity', 0)) for e in edges)

print(f'\n--- [ SALTO 1: TUS PEERS ({len(my_peers)} nodos) ] ---')
for p in my_peers:
    al = nodes.get(p, {}).get('alias', 'sin_alias')
    lu = nodes.get(p, {}).get('last_update', 0)
    print(f'▸ PEER: {al} | {p}')
    print(f'  Última gossip: {fmt_date(lu)} | Canales: {node_chan_count.get(p, 0)} | Cap: {fmt_sats(node_cap.get(p, 0))}')

visited = set(my_peers)
if my_pubkey: visited.add(my_pubkey) # Excluirme de ser descubierto en saltos
current_layer = set(my_peers)
candidates = set()

for hop in range(2, max_hops + 1):
    next_layer = set()
    for p in current_layer:
        for e in adj.get(p, []):
            np = e['node2_pub'] if e['node1_pub'] == p else e['node1_pub']
            if np not in visited:
                visited.add(np)
                next_layer.add(np)
    
    print(f'\n--- [ SALTO {hop}: ({len(next_layer)} nodos descubiertos) ] ---')
    if not next_layer:
        print('  (No hay más nodos conectados)')
        break
        
    candidates.update(next_layer)
    count = 0
    for np in next_layer:
        if count >= 300:
            print(f'  ... y {len(next_layer) - 300} nodos más en este salto omitidos por presentación.')
            break
        
        nd = nodes.get(np, {})
        al = nd.get('alias', 'sin_alias')
        addrs = nd.get('addresses', [])
        addr = addrs[0].get('addr', '?') if addrs else '?'
        lu = nd.get('last_update', 0)
        c_chan = node_chan_count.get(np, 0)
        c_cap = node_cap.get(np, 0)
        
        print(f'  ▸ vecino: {al} | {np} | uri: {np}@{addr}')
        print(f'    Última gossip: {fmt_date(lu)} | Canales: {c_chan} | Cap: {fmt_sats(c_cap)}')
        count += 1
        
    current_layer = next_layer

print('\n================================================================')
print(' RESUMEN: Candidatos para nuevo canal descubiertos en este rango')
print('================================================================\n')
if not candidates:
    print('  No se encontraron candidatos adicionales en esta profundidad.')
else:
    print('  %-28s | %-6s | %-12s | %s' % ('ALIAS', 'CANS', 'CAP.TOTAL', 'CONNECT URI'))
    print('  ' + '-' * 130)
    cand_list = list(candidates)
    cand_list.sort(key=lambda x: node_cap.get(x, 0), reverse=True)
    count = 0
    for cand in cand_list:
        if count >= 100:
            print(f'  ... y {len(cand_list)-100} candidatos más.')
            break
        nd = nodes.get(cand, {})
        al = nd.get('alias', 'sin_alias')
        addrs = nd.get('addresses', [])
        addr = addrs[0].get('addr', '?') if addrs else '?'
        c_chan = node_chan_count.get(cand, 0)
        c_cap = node_cap.get(cand, 0)
        print('  %-28s | %-6s | %-12s | %s@%s' % (al[:28], c_chan, c_cap, cand, addr))
        count += 1
"

rm -f "$tmp_channels" "$tmp_graph"

# =============================================================================
# SECCIÓN 4: Exportar CSV de la red completa
# =============================================================================
echo
echo "================================================================"
echo " EXPORTANDO RED AL CSV: $CSV_OUT"
echo "================================================================"

# Cabecera del CSV con todos los campos necesarios para la visualización 3D
printf '%s\n' \
  "source_pubkey,source_alias,target_pubkey,target_alias,capacity_sats,fee_base_msat,fee_rate_ppm,max_htlc_sats,cltv_delta,disabled,source_last_update,target_last_update,source_channels,target_channels,source_total_cap,target_total_cap" \
  > "$CSV_OUT"

# Extraer todos los edges del graph (no solo los de mis peers) con sus datos
echo "$graph_json" | jq -r '
  .edges[] |
  [
    .node1_pub,
    .node2_pub,
    (.capacity // "0"),
    (.node1_policy.fee_base_msat // "0"),
    (.node1_policy.fee_rate_milli_msat // "0"),
    (if .node1_policy.max_htlc_msat then (.node1_policy.max_htlc_msat | tonumber / 1000 | floor | tostring) else "0" end),
    (.node1_policy.time_lock_delta // "0"),
    (if (.node1_policy.disabled == true or .node2_policy.disabled == true) then "1" else "0" end)
  ] | @csv
' > /tmp/ln_edges_raw.csv

# Construir lookup de nodos: alias, last_update, número de canales y capacidad total
node_lookup=$(echo "$graph_json" | jq -c '
  [.nodes[] | {
    key: .pub_key,
    value: {
      alias: (.alias // ""),
      last_update: (.last_update // 0),
      channels: 0,
      total_cap: 0
    }
  }] | from_entries
')

# Acumular canales y capacidad total por nodo recorriendo todos los edges
echo "$graph_json" | jq -r --argjson lookup "$node_lookup" '
  reduce .edges[] as $e (
    $lookup;
    . as $lk |
    ($e.node1_pub) as $n1 |
    ($e.node2_pub) as $n2 |
    ($e.capacity | tonumber) as $cap |
    (if $lk[$n1] then .[$n1].channels += 1 | .[$n1].total_cap += $cap else . end) |
    (if $lk[$n2] then .[$n2].channels += 1 | .[$n2].total_cap += $cap else . end)
  ) |
  to_entries[] |
  [.key, .value.alias, (.value.last_update | tostring), (.value.channels | tostring), (.value.total_cap | tostring)] |
  @csv
' > /tmp/ln_node_lookup.csv

# Unir edges con datos de nodos usando Python (JOIN en memoria)
python3 - <<PYEOF
import csv, os, sys

# Leer lookup de nodos (pubkey → alias, last_update, channels, total_cap)
nodes = {}
with open('/tmp/ln_node_lookup.csv', newline='', encoding='utf-8') as f:
    for row in csv.reader(f):
        if len(row) >= 5:
            pubkey, alias, last_update, channels, total_cap = row[0], row[1], row[2], row[3], row[4]
            nodes[pubkey] = {
                'alias':      alias,
                'last_update':last_update,
                'channels':   channels,
                'total_cap':  total_cap
            }

csv_out = os.environ.get('CSV_OUT', '../data/lightning_network.csv')

with open(csv_out, 'a', newline='', encoding='utf-8') as out_file:
    writer = csv.writer(out_file)
    with open('/tmp/ln_edges_raw.csv', newline='', encoding='utf-8') as f:
        for row in csv.reader(f):
            if len(row) < 8:
                continue
            n1, n2, cap, fee_base, fee_rate, max_htlc, cltv, disabled = row
            info1 = nodes.get(n1, {'alias':'', 'last_update':'0', 'channels':'0', 'total_cap':'0'})
            info2 = nodes.get(n2, {'alias':'', 'last_update':'0', 'channels':'0', 'total_cap':'0'})
            writer.writerow([
                n1, info1['alias'],
                n2, info2['alias'],
                cap, fee_base, fee_rate, max_htlc, cltv, disabled,
                info1['last_update'], info2['last_update'],
                info1['channels'], info2['channels'],
                info1['total_cap'], info2['total_cap']
            ])

print(f"CSV exportado correctamente a: {csv_out}")
PYEOF

rm -f /tmp/ln_edges_raw.csv /tmp/ln_node_lookup.csv

echo
echo "✅ Escaneo completado. CSV guardado en: $CSV_OUT"
echo "   Abre el dashboard y usa 'Abrir Visualización 3D' para ver la red."
