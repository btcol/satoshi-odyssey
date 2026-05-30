#!/usr/bin/env python3
"""
dashboard_core.py
======================
Lógica principal del dashboard unificado para gestión de canales Lightning Network.

Dependencias:
  pip install pandas plotly networkx
"""

import os
import sys
import csv
import json
import math
import queue
import shutil
import threading
import subprocess
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

# ── Intentar importar dependencias opcionales ─────────────────────────────────
try:
    import pandas as pd
    import plotly.graph_objects as go
    import networkx as nx
    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False

# Rutas relativas al directorio del script
BASE_DIR    = Path(__file__).parent.resolve()
SCRIPTS_DIR = BASE_DIR / "scripts"
DATA_DIR    = BASE_DIR / "data"
EXPORTS_DIR  = BASE_DIR / "exports"
BACKUPS_DIR  = BASE_DIR / "backups"

# =============================================================================
# CONFIGURACIÓN GLOBAL
# =============================================================================

# Carga variables de un archivo .env al entorno os.environ, ignorando lineas vacias o comentadas.
def load_env_file(env_path):
    if not env_path.exists():
        return
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip("'\"")
                    if key and key not in os.environ:
                        os.environ[key] = val
    except Exception as e:
        print(f"[WARN] No se pudo cargar {env_path}: {e}")

# Cargar .env desde el propio directorio del proyecto (lightning-dashboard/.env)
load_env_file(BASE_DIR / ".env")

# Red y binario lncli (sobreescribibles via variables de entorno)
NETWORK   = os.environ.get("NETWORK",   "testnet4")
LNCLI_BIN = os.environ.get("LNCLI_BIN", "lncli-debug")

# Archivo CSV generado por 01_scan_network.sh
CSV_FILE  = DATA_DIR / "lightning_network.csv"
# HTML de visualización 3D
HTML_FILE = EXPORTS_DIR / "lightning_network_red_3d.html"

# Parámetros de rebalanceo por defecto
DEFAULT_AMT_SATS      = 10000
DEFAULT_MAX_FEE_SATS  = 100
DEFAULT_MAX_FEE_PPM   = 1000
DEFAULT_CLTV_DELTA    = 40
TARGET_RATIO          = 50   # % de balance local objetivo
MIN_SHIFT_SATS        = 1000 # sats mínimos para sugerir rebalanceo

# Colores del tema oscuro
CLR_BG       = "#07070f"   # fondo principal  (igual que cockpit --bg)
CLR_PANEL    = "#0d0d1e"   # fondo de paneles  (igual que cockpit --panel)
CLR_ACCENT   = "#00dcff"   # cian eléctrico (acento)
CLR_ACCENT2  = "#7b2fff"   # violeta (acento secundario)
CLR_GOLD     = "#ffd700"   # dorado (mi nodo)
CLR_GREEN    = "#00ff88"   # verde (éxito / activo)
CLR_RED      = "#ff4466"   # rojo (error / deshabilitado)
CLR_YELLOW   = "#ffcc00"   # amarillo (advertencia)
CLR_TEXT     = "#d0d8f0"   # texto principal
CLR_SUBTEXT  = "#8888aa"   # texto secundario
CLR_BORDER   = "#1a1a35"   # bordes

# Fuente base (disponible en Linux/Windows/Mac)
_FONT_UI   = "DejaVu Sans"
_FONT_MONO = "DejaVu Sans Mono"


# =============================================================================
# UTILIDADES GENERALES
# =============================================================================

# Ejecuta un comando de lncli como subproceso, captura su salida JSON y maneja errores o timeouts.
def run_lncli(*args, timeout=30):
    """
    Ejecuta lncli-debug con los argumentos dados y devuelve el JSON parseado.
    Lanza RuntimeError si el comando falla o el timeout expira.
    """
    cmd = [LNCLI_BIN, f"-network={NETWORK}"] + list(args)
    try:
        result = subprocess.check_output(
            cmd, stderr=subprocess.STDOUT, timeout=timeout
        )
        return json.loads(result)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"lncli error: {e.output.decode(errors='replace')}")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Timeout ejecutando: {' '.join(cmd)}")


# Convierte un Short Channel ID (SCID) de formato 'bloque:transaccion:salida' a entero uint64.
def scid_to_uint64(scid: str) -> int:
    """
    Convierte un SCID en formato 'blockxTxIndexxVout' (ej: '129925x3x0')
    al entero uint64 que acepta payinvoice --outgoing_chan_id.
    """
    parts = scid.split("x")
    block, tx, vout = int(parts[0]), int(parts[1]), int(parts[2])
    return (block << 40) | (tx << 16) | vout


# Formatea un numero entero a string con separadores de miles y el sufijo 'sats'.
def fmt_sats(n) -> str:
    try:
        return f"{int(n):,} sats"
    except (ValueError, TypeError):
        return "? sats"


# Calcula la cantidad de dias transcurridos desde un timestamp (Unix) hasta el momento actual.
def days_since(ts) -> int:
    try:
        return int((datetime.now(timezone.utc).timestamp() - int(ts)) / 86400)
    except Exception:
        return 9999


# Obtiene la informacion basica del nodo (getinfo) usando lncli.
def get_node_info():
    try:
        return run_lncli("getinfo", timeout=10)
    except Exception:
        return None


# Devuelve la lista de canales abiertos activos e inactivos (listchannels) desde LND.
def get_channels():
    try:
        data = run_lncli("listchannels")
        return data.get("channels", [])
    except Exception:
        return []


# Obtiene el saldo total, confirmado y no confirmado de la billetera on-chain (walletbalance).
def get_wallet_balance():
    try:
        return run_lncli("walletbalance", timeout=10)
    except Exception:
        return {}


# Devuelve todos los canales (activos, inactivos, pendientes y en proceso de cierre) del nodo.
def get_all_channels():
    channels = []
    
    # Canales activos
    try:
        active = run_lncli("listchannels", timeout=15).get("channels", [])
        for ch in active:
            channels.append({
                "chan_point": ch.get("channel_point", ""),
                "pubkey": ch.get("remote_pubkey", ""),
                "alias": ch.get("peer_alias", ""),
                "local": int(ch.get("local_balance", 0)),
                "remote": int(ch.get("remote_balance", 0)),
                "status": "OPEN",
                "active": ch.get("active", False)
            })
    except Exception:
        pass
        
    # Canales pendientes
    try:
        pending = run_lncli("pendingchannels", timeout=15)
        
        for c in pending.get("pending_open_channels", []):
            ch = c.get("channel", {})
            channels.append({
                "chan_point": ch.get("channel_point", ""),
                "pubkey": ch.get("remote_node_pub", ""),
                "alias": "pendiente_abrir", 
                "local": int(ch.get("local_balance", 0)),
                "remote": int(ch.get("remote_balance", 0)),
                "status": "PENDING_OPEN",
                "active": False
            })
            
        for c in pending.get("pending_closing_channels", []):
            ch = c.get("channel", {})
            channels.append({
                "chan_point": ch.get("channel_point", ""),
                "pubkey": ch.get("remote_node_pub", ""),
                "alias": "pendiente_cerrar",
                "local": int(ch.get("local_balance", 0)),
                "remote": int(ch.get("remote_balance", 0)),
                "status": "PENDING_CLOSE",
                "active": False
            })
            
        for c in pending.get("pending_force_closing_channels", []):
            ch = c.get("channel", {})
            channels.append({
                "chan_point": ch.get("channel_point", ""),
                "pubkey": ch.get("remote_node_pub", ""),
                "alias": "force_close",
                "local": int(ch.get("local_balance", 0)),
                "remote": int(ch.get("remote_balance", 0)),
                "status": "FORCE_CLOSING",
                "active": False
            })
            
        for c in pending.get("waiting_close_channels", []):
            ch = c.get("channel", {})
            channels.append({
                "chan_point": ch.get("channel_point", ""),
                "pubkey": ch.get("remote_node_pub", ""),
                "alias": "esperando_cierre",
                "local": int(ch.get("local_balance", 0)),
                "remote": int(ch.get("remote_balance", 0)),
                "status": "WAITING_CLOSE",
                "active": False
            })
            
    except Exception:
        pass
        
    return channels


# Ejecuta el comando lncli closechannel de forma cooperativa o forzada (Force Close).
def execute_closechannel(chan_point: str, force: bool, log_callback) -> bool:
    log = log_callback
    log(f"[{datetime.now().strftime('%H:%M:%S')}] Petición de cierre para {chan_point} ...")
    
    if ":" not in chan_point:
        log("[ERROR] El identificador del canal debe contener ':' (txid:index)")
        return False
        
    txid, index = chan_point.split(":")
    
    cmd = [LNCLI_BIN, f"-network={NETWORK}", "closechannel", f"--funding_txid={txid}", f"--output_index={index}"]
    if force:
        cmd.append("--force")
        
    log(f"   Ejecutando: {' '.join(cmd)}")
    
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        for line in proc.stdout:
            log(f"   {line.rstrip()}")
        proc.wait(timeout=90)
        
        if proc.returncode == 0:
            log("[OK] ¡Comando closechannel enviado exitosamente!")
            return True
        else:
            log(f"[ERROR] closechannel falló con código {proc.returncode}")
            return False
    except subprocess.TimeoutExpired:
        proc.kill()
        log("[ERROR] Timeout ejecutando closechannel.")
        return False
    except Exception as e:
        log(f"[ERROR] Excepción ejecutando closechannel: {e}")
        return False


# =============================================================================
# LÓGICA DE REBALANCEO
# =============================================================================

# Analiza los canales y sugiere pares optimos para rebalanceo circular segun un ratio deseado.
def suggest_rebalances(channels: list, target_ratio=TARGET_RATIO,
                       min_shift=MIN_SHIFT_SATS, max_options=20) -> list:
    enriched = []
    for ch in channels:
        if not ch.get("active"):
            continue
        if len(ch.get("pending_htlcs", [])) > 0:
            continue
        cap   = int(ch.get("capacity", 0))
        local = int(ch.get("local_balance", 0))
        remote= int(ch.get("remote_balance", 0))
        scid  = ch.get("scid_str") or ch.get("chan_id", "")
        peer  = ch.get("peer_alias", "")
        pub   = ch.get("remote_pubkey", "")

        target_local = int((cap * target_ratio) / 100)
        delta = local - target_local
        sendable   = delta if delta > 0 else 0
        receivable = -delta if delta < 0 else 0

        enriched.append({
            "scid": scid, "peer": peer, "pub": pub,
            "cap": cap, "local": local, "remote": remote,
            "sendable": sendable, "receivable": receivable
        })

    suggestions = []
    for i, src in enumerate(enriched):
        if src["sendable"] < min_shift:
            continue
        for j, dst in enumerate(enriched):
            if i == j:
                continue
            if dst["receivable"] < min_shift:
                continue
            amount = min(src["sendable"], dst["receivable"])
            if amount < min_shift:
                continue
            suggestions.append({
                "score":      amount,
                "from_scid":  src["scid"],
                "to_scid":    dst["scid"],
                "from_peer":  src["peer"],
                "to_peer":    dst["peer"],
                "amount":     amount,
                "from_local": src["local"],
                "to_local":   dst["local"],
                "from_pub":   src["pub"],
                "to_pub":     dst["pub"],
            })

    suggestions.sort(key=lambda x: x["score"], reverse=True)
    return suggestions[:max_options]


# Simula y calcula la rentabilidad y costo esperado de un rebalanceo basandose en las tarifas (fees).
def fee_analysis(amt_sats: int, max_fee_sats: int, max_fee_ppm: int) -> dict:
    if amt_sats <= 0:
        return {}
    fee_estimado   = (max_fee_ppm / 1_000_000) * amt_sats
    fee_ppm_if_max = (max_fee_sats / amt_sats)  * 1_000_000
    monto_min      = (max_fee_sats / max_fee_ppm) * 1_000_000 if max_fee_ppm > 0 else 0
    return {
        "fee_estimado":   fee_estimado,
        "fee_ppm_if_max": fee_ppm_if_max,
        "monto_min":      monto_min,
        "ok":             fee_estimado <= max_fee_sats,
    }


# Genera un invoice y ejecuta un pago auto-enrutado (rebalanceo circular) entre dos canales especificos.
def execute_rebalance(from_scid: str, to_pub: str, amt_sats: int,
                      max_fee_sats: int, log_callback) -> bool:
    log = log_callback

    log(f"[1/3] Creando invoice de {amt_sats:,} sats en el nodo local...")
    try:
        inv = run_lncli(
            "addinvoice",
            f"--amt={amt_sats}",
            f"--memo=rebalance-{from_scid}",
            "--private",
            timeout=20
        )
    except RuntimeError as e:
        log(f"[ERROR] Error creando invoice: {e}")
        return False

    payment_request = inv.get("payment_request", "")
    payment_hash    = inv.get("r_hash") or inv.get("r_hash_str") or inv.get("payment_hash", "")
    log(f"   [OK] Invoice creada. Hash: {payment_hash[:20]}...")

    log(f"[2/3] Calculando chan_id uint64 para SCID {from_scid}...")
    try:
        from_chan_id = scid_to_uint64(from_scid)
        log(f"   [OK] chan_id uint64 = {from_chan_id}")
    except Exception as e:
        log(f"[ERROR] Error calculando chan_id: {e}")
        return False

    log(f"[3/3] Ejecutando payinvoice...")
    log(f"   outgoing_chan_id = {from_chan_id}")
    log(f"   last_hop         = {to_pub[:20]}...")
    log(f"   fee_limit        = {max_fee_sats} sats")

    cmd = [
        LNCLI_BIN, f"-network={NETWORK}",
        "payinvoice",
        "--allow_self_payment",
        f"--outgoing_chan_id={from_chan_id}",
        f"--last_hop={to_pub}",
        f"--fee_limit={max_fee_sats}",
        "--force",
        payment_request
    ]
    log(f"   CMD: {' '.join(cmd)}")

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        for line in proc.stdout:
            log(f"   {line.rstrip()}")
        proc.wait(timeout=120)
        if proc.returncode == 0:
            log("[OK] ¡Rebalanceo exitoso!")
            return True
        else:
            log(f"[ERROR] payinvoice terminó con código {proc.returncode}")
            return False
    except subprocess.TimeoutExpired:
        proc.kill()
        log("[ERROR] Timeout: el pago tardó más de 120 segundos.")
        return False
    except Exception as e:
        log(f"[ERROR] Error ejecutando payinvoice: {e}")
        return False


# Analiza el grafo publico (describegraph) y sugiere nodos candidatos optimos para abrir nuevos canales.
def get_channel_candidates(min_channels=2, max_days=60):
    """
    Lee la red desde el graph local (getnetworkinfo / describegraph) para
    encontrar nodos saludables con los que NO tenemos canal.
    Se usa el CSV pre-generado si existe para rapidez.
    """
    if not CSV_FILE.exists():
        return []
        
    # Mis canales actuales
    my_chans = get_channels()
    my_peers = set(ch.get("remote_pubkey") for ch in my_chans)
    
    my_info = get_node_info()
    my_pubkey = my_info.get("identity_pubkey", "") if my_info else ""
    
    nodes = {}
    import csv
    try:
        with open(CSV_FILE, newline='', encoding='utf-8') as f:
            reader = csv.reader(f)
            next(reader, None) # skip header
            now_ts = datetime.now(timezone.utc).timestamp()
            
            for row in reader:
                if len(row) < 16: continue
                n1, a1, n2, a2 = row[0], row[1], row[2], row[3]
                lu1, lu2 = int(row[10] or 0), int(row[11] or 0)
                ch1, ch2 = int(row[12] or 0), int(row[13] or 0)
                cap1, cap2 = int(row[14] or 0), int(row[15] or 0)
                
                if n1 not in nodes:
                    nodes[n1] = {"pubkey": n1, "alias": a1, "channels": ch1, "cap": cap1, "last_update": lu1}
                if n2 not in nodes:
                    nodes[n2] = {"pubkey": n2, "alias": a2, "channels": ch2, "cap": cap2, "last_update": lu2}
    except Exception:
        pass
        
    candidates = []
    now_ts = datetime.now(timezone.utc).timestamp()
    
    for pubkey, data in nodes.items():
        if pubkey == my_pubkey or pubkey in my_peers:
            continue
        if data["channels"] < min_channels:
            continue
            
        days_ago = (now_ts - data["last_update"]) / 86400 if data["last_update"] > 0 else 9999
        if days_ago > max_days:
            continue
            
        candidates.append({
            "pubkey": pubkey,
            "alias": data["alias"],
            "channels": data["channels"],
            "capacity": data["cap"],
            "days_ago": int(days_ago)
        })
        
    # Ordenar por capacidad descendente
    candidates.sort(key=lambda x: x["capacity"], reverse=True)
    return candidates[:100]


# Conecta este nodo con un peer remoto usando su URI (pubkey@host:port).
def execute_connect(uri: str, log_callback) -> bool:
    """
    Establece una conexión P2P con un nodo usando su URI completa (pubkey@host:port).
    Devuelve True si la conexión es exitosa o ya existía.
    """
    log = log_callback
    log(f"[connect] Intentando conectar a {uri} ...")
    cmd = [LNCLI_BIN, f"-network={NETWORK}", "connect", uri]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        out = (res.stdout + res.stderr).strip()
        if "already connected to peer" in out:
            log("   [OK] Ya estás conectado a este peer.")
            return True
        if res.returncode == 0:
            log("   [OK] Conexión P2P establecida.")
            return True
        log(f"   [ERROR] Fallo en connect (código {res.returncode}): {out[:200]}")
        return False
    except subprocess.TimeoutExpired:
        log("   [ERROR] Timeout esperando respuesta del nodo remoto.")
        return False
    except Exception as e:
        log(f"   [ERROR] Excepción en connect: {e}")
        return False


# Abre un nuevo canal on-chain hacia una pubkey especifica, permitiendo opcionalmente un push_amt.
def execute_openchannel(pubkey: str, amt_sats: int, log_callback,
                        host_uri: str = None, push_amt: int = 0) -> bool:
    """
    Abre un canal con el nodo indicado.
    - Si se pasa host_uri (pubkey@ip:port), usa ese para conectar directamente.
    - Si no, intenta obtener la dirección del grafo de la red.
    - push_amt: cantidad de sats a regalar al otro lado del canal.
    """
    log = log_callback

    # 1. Determinar URI de conexión: parámetro directo > grafo interno
    log(f"[1/3] Resolviendo dirección de conexión para {pubkey[:16]}...")
    host = None
    if host_uri:
        # URI proporcionada directamente (pubkey@ip:port)
        host = host_uri.split("@", 1)[1] if "@" in host_uri else host_uri
        log(f"   Usando URI externa: {host_uri}")
    else:
        try:
            info = run_lncli("getnodeinfo", f"--pub_key={pubkey}")
            addrs = info.get("node", {}).get("addresses", [])
            if addrs:
                host = addrs[0]["addr"]
                log(f"   Dirección del grafo: {host}")
            else:
                log("   [!] No se encontraron p2p_addresses en el grafo.")
        except Exception as e:
            log(f"   [!] Fallo al consultar el grafo: {e}")

    # 2. Conectar P2P
    if host:
        uri = host_uri if host_uri else f"{pubkey}@{host}"
        log(f"[2/3] Conectando a {uri} ...")
        cmd_conn = [LNCLI_BIN, f"-network={NETWORK}", "connect", uri]
        try:
            res = subprocess.run(cmd_conn, capture_output=True, text=True, timeout=20)
            out = res.stdout + res.stderr
            if "already connected to peer" in out:
                log("   Ya conectado.")
            elif res.returncode == 0:
                log("   [OK] Conexión P2P exitosa.")
            else:
                log(f"   [!] Fallo en connect: {out.strip()[:200]}")
        except subprocess.TimeoutExpired:
            log("   [!] Timeout en connect. Intentando openchannel de todas formas...")
        except Exception as e:
            log(f"   [!] Error en connect: {e}")
    else:
        log("[2/3] Sin dirección conocida. Confiando en la tabla de ruteo interna...")

    # 3. Ejecutar openchannel
    log(f"[3/3] Abriendo canal con un fondo local de {amt_sats:,} sats...")
    if push_amt > 0:
        log(f"   [!] Push Amount: REGALANDO {push_amt:,} sats al nodo remoto...")
        
    cmd_open = [
        LNCLI_BIN, f"-network={NETWORK}",
        "openchannel",
        f"--node_key={pubkey}",
        f"--local_amt={amt_sats}"
    ]
    if push_amt > 0:
        cmd_open.append(f"--push_amt={push_amt}")
    
    try:
        # Popen en block
        proc = subprocess.Popen(cmd_open, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in proc.stdout:
            log(f"   {line.rstrip()}")
        proc.wait(timeout=60)
        
        if proc.returncode == 0:
            log(f"[OK] ¡Comando openchannel enviado exitosamente!")
            return True
        else:
            log(f"[ERROR] openchannel falló con código {proc.returncode}")
            return False
    except Exception as e:
        log(f"[ERROR] Excepción ejecutando openchannel: {e}")
        return False


# =============================================================================
# LEER HISTORIAL SQLite PARA EL COCKPIT
# =============================================================================

import sqlite3 as _sqlite3

# \u2500\u2500 Ajuste de sys.path para importar el paquete de gamificaci\u00f3n \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
# El paquete 'gamification/' reside en el mismo directorio que dashboard_core.py.
# Si se ejecuta desde otro directorio, ajustamos sys.path.
import sys as _sys
import pathlib as _pathlib
_dashboard_core_dir = _pathlib.Path(__file__).resolve().parent  # satoshi-odyssey/
if str(_dashboard_core_dir) not in _sys.path:
    _sys.path.insert(0, str(_dashboard_core_dir))

# =============================================================================
# WALLET ON-CHAIN
# =============================================================================

# Reserva minima recomendada en sats para operacion segura del nodo
WALLET_MIN_RESERVE_SATS = 50_000
# Umbral de fragmentacion (numero de UTXOs que dispara advertencia)
UTXO_FRAGMENT_WARN = 10


# Devuelve el saldo detallado de la billetera on-chain, incluyendo saldos pendientes de cierres.
def get_wallet_balance_detail() -> dict:
    """
    Retorna balance on-chain detallado incluyendo:
    - confirmed / unconfirmed / total
    - reserved_balance_anchor_chan (reserva para anchor outputs)
    - pending_closing_sats (fondos en cierres de canal)
    - pending_open_sats
    """
    result = {
        "confirmed": 0, "unconfirmed": 0, "total": 0,
        "reserved_anchor": 0,
        "pending_closing_sats": 0,
        "pending_open_sats": 0,
        "error": None,
    }
    try:
        wb = run_lncli("walletbalance")
        result["confirmed"]       = int(wb.get("confirmed_balance", 0))
        result["unconfirmed"]     = int(wb.get("unconfirmed_balance", 0))
        result["total"]           = int(wb.get("total_balance", 0))
        result["reserved_anchor"] = int(wb.get("reserved_balance_anchor_chan", 0))
    except Exception as e:
        result["error"] = str(e)

    try:
        pc = run_lncli("pendingchannels")
        closing = pc.get("pending_force_closing_channels", []) + \
                  pc.get("waiting_close_channels", [])
        for ch in closing:
            result["pending_closing_sats"] += int(
                ch.get("channel", {}).get("local_balance", 0)
            )
        for ch in pc.get("pending_open_channels", []):
            result["pending_open_sats"] += int(
                ch.get("channel", {}).get("local_balance", 0)
            )
    except Exception:
        pass

    return result


# Devuelve la lista de UTXOs (Unspent Transaction Outputs) de la billetera del nodo.
def get_wallet_utxos(min_confs: int = 0) -> list:
    """
    Lista todos los UTXOs de la wallet on-chain via lncli listunspent.
    Compatible con el formato real de LND: outpoint como string "txid:index",
    address_type como entero, amount_sat como entero.
    """
    # Mapa de address_type (enum int) -> nombre legible
    ADDR_TYPE = {
        0: "p2wkh (bech32)",
        1: "np2wkh (anidado)",
        2: "hybrid np2wkh",
        3: "p2tr (taproot)",
        4: "p2tr (taproot)",
    }
    try:
        data = run_lncli("listunspent", f"--min_confs={min_confs}")
        utxos = data.get("utxos", [])
        result = []
        for u in utxos:
            # Outpoint puede ser string "txid:idx" o dict {txid_str, output_index}
            op = u.get("outpoint", "")
            if isinstance(op, str) and ":" in op:
                txid, idx = op.rsplit(":", 1)
                output_index = int(idx)
            elif isinstance(op, dict):
                txid = op.get("txid_str", "")
                output_index = int(op.get("output_index", 0))
            else:
                txid = str(op)
                output_index = 0

            addr_type_raw = u.get("address_type", 0)
            addr_type_str = ADDR_TYPE.get(int(addr_type_raw),
                                          f"tipo {addr_type_raw}")

            result.append({
                "txid":         txid,
                "output_index": output_index,
                "amount_sat":   int(u.get("amount_sat", 0)),
                "confirmations":int(u.get("confirmations", 0)),
                "address":      u.get("address", ""),
                "address_type": addr_type_str,
            })
        return result
    except Exception:
        return []



# Genera una nueva direccion on-chain (por defecto p2wkh) en la billetera de LND.
def get_new_address(addr_type: str = "p2wkh") -> str:
    """
    Genera una nueva direccion on-chain para recibir fondos.
    addr_type: 'p2wkh' (bech32/native segwit, recomendado),
               'np2wkh' (nested segwit), 'p2tr' (taproot, LND >= 0.15)
    """
    try:
        data = run_lncli("newaddress", addr_type)
        return data.get("address", "")
    except Exception:
        return ""


# Envia todos los fondos disponibles on-chain a una sola direccion para consolidar UTXOs.
def execute_consolidate_utxos(dest_addr: str, sat_per_vbyte: int,
                               log_callback) -> bool:
    """
    Consolida todos los UTXOs enviando todo el saldo a dest_addr.
    Usa --sweepall para incluir todos los UTXOs en una sola transaccion.
    ATENCION: esta operacion mueve TODOS los fondos disponibles.
    """
    log = log_callback
    log(f"[1/2] Preparando consolidacion hacia {dest_addr}...")
    log(f"   Fee rate: {sat_per_vbyte} sat/vbyte")
    cmd = [
        LNCLI_BIN, f"-network={NETWORK}",
        "sendcoins",
        f"--addr={dest_addr}",
        "--sweepall",
        f"--sat_per_vbyte={sat_per_vbyte}",
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        out = (res.stdout + res.stderr).strip()
        if res.returncode == 0:
            try:
                txid = json.loads(res.stdout).get("txid", "?")
                log(f"[2/2] OK - TXID: {txid}")
                log("   Los fondos llegaran en la proxima confirmacion del bloque.")
            except Exception:
                log(f"[2/2] OK: {out[:200]}")
            return True
        else:
            log(f"[2/2] Error ({res.returncode}): {out[:300]}")
            return False
    except subprocess.TimeoutExpired:
        log("   Timeout esperando respuesta de lncli.")
        return False
    except Exception as e:
        log(f"   Excepcion: {e}")
        return False


# Exporta el Static Channel Backup (SCB) actual del nodo a un archivo binario especificado.
def export_channel_backup(output_path: Path, log_callback) -> bool:
    """
    Exporta el Static Channel Backup (SCB) de todos los canales activos.
    output_path: ruta donde guardar el archivo .bin
    """
    log = log_callback
    output_path.parent.mkdir(parents=True, exist_ok=True)
    log(f"[SCB] Exportando backup de canales -> {output_path.name}")
    cmd = [
        LNCLI_BIN, f"-network={NETWORK}",
        "exportchanbackup", "--all",
        f"--output_file={output_path}",
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        out = (res.stdout + res.stderr).strip()
        if res.returncode == 0:
            size = output_path.stat().st_size if output_path.exists() else 0
            log(f"   OK - {output_path.name} ({size:,} bytes)")
            return True
        else:
            log(f"   Error: {out[:300]}")
            return False
    except Exception as e:
        log(f"   Excepcion: {e}")
        return False


# Devuelve la ruta por defecto donde LND guarda los respaldos automaticos de canales (channel.backup).
def get_scb_auto_path() -> Path:
    """Retorna la ruta del SCB automatico que mantiene LND."""
    lnd_dir = Path.home() / ".lnd" / "data" / "chain" / "bitcoin" / NETWORK
    return lnd_dir / "channel.backup"


# Lee y calcula metricas historicas de rentabilidad y salud del nodo desde la base de datos SQLite.
def read_history_stats(db_path: Path = None) -> dict:
    """
    Lee node_history.db y devuelve un dict con las métricas para el cockpit HUD.
    Incluye: snapshot más reciente, últimas 24h por hora y últimos 7 días.
    """
    if db_path is None:
        db_path = DATA_DIR / "node_history.db"

    empty = {
        "snap": {}, "hourly": [], "daily": [],
        "net_profit_7d_msat": 0,
        "uptime_pct_7d": 0.0,
        "zombies_list": [],
    }
    if not db_path.exists():
        return empty

    try:
        conn = _sqlite3.connect(db_path)
        conn.row_factory = _sqlite3.Row

        # Snapshot más reciente
        row = conn.execute(
            "SELECT * FROM snapshots ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        snap = dict(row) if row else {}

        zombies_list = []
        if snap and "id" in snap:
            z_rows = conn.execute(
                "SELECT peer_alias, chan_id, capacity FROM channel_snapshots WHERE snapshot_id = ? AND is_zombie = 1",
                (snap["id"],)
            ).fetchall()
            zombies_list = [dict(r) for r in z_rows]

        # Hourly últimas 24h
        cutoff_h = (int(datetime.now(timezone.utc).timestamp()) // 3600 - 24) * 3600
        hourly = [
            dict(r) for r in conn.execute(
                "SELECT * FROM hourly_stats WHERE hour_ts >= ? ORDER BY hour_ts ASC",
                (cutoff_h,)
            ).fetchall()
        ]

        # Daily últimos 7 días
        daily = [
            dict(r) for r in conn.execute(
                "SELECT * FROM daily_stats ORDER BY date DESC LIMIT 7"
            ).fetchall()
        ]
        daily.reverse()

        # Net profit acumulado 7 días
        net_7d = sum(d.get("net_profit_msat", 0) for d in daily)

        # Uptime: % snapshots con synced_to_chain=1 en las últimas 24h
        cutoff_ts = int(datetime.now(timezone.utc).timestamp()) - 86400
        total_snaps = conn.execute(
            "SELECT COUNT(*) FROM snapshots WHERE ts >= ?", (cutoff_ts,)
        ).fetchone()[0]
        synced_snaps = conn.execute(
            "SELECT COUNT(*) FROM snapshots WHERE ts >= ? AND synced_to_chain=1",
            (cutoff_ts,)
        ).fetchone()[0]
        uptime_pct = round(synced_snaps / max(total_snaps, 1) * 100, 1)

        conn.close()
        return {
            "snap": snap,
            "hourly": hourly,
            "daily": daily,
            "net_profit_7d_msat": net_7d,
            "uptime_pct_7d": uptime_pct,
            "zombies_list": zombies_list,
        }
    except Exception as e:
        print(f"[WARN] read_history_stats: {e}", file=sys.stderr)
        return empty


# Lee el estado de gamificacion del nodo: logros, records, XP calculado y puntuacion de salud.
def read_gamification_status(db_path: Path = None) -> dict:
    """
    Devuelve el estado completo del sistema de gamificacion delegando
    al motor modular gamification/game_engine.py.

    Retrocompatibilidad garantizada:
      Los campos 'health', 'rank' (string) y 'rank_level' se mantienen
      con los mismos nombres que usaba la implementacion anterior para
      no romper la interfaz web existente.

    Campos retornados (superset de la implementacion anterior):
      - achievements       : lista de logros con estado desbloqueado/bloqueado.
      - records            : records personales historicos.
      - xp                 : puntos de experiencia actuales.
      - health             : salud del nodo (0-100). Alias de 'hp'.
      - rank               : nombre del rango actual (string). Alias de rank.name.
      - rank_level         : nivel numerico del rango (0-4).
      - rank_info          : dict completo {name, level, xp_current, xp_next_rank}.
      - quests             : lista de misiones con progreso.
      - unlocked_count     : cantidad de logros desbloqueados.
      - total_achievements : total de logros en el catalogo.
    """
    from gamification.game_engine import get_ui_gamification_payload

    if db_path is None:
        db_path = DATA_DIR / "node_history.db"

    # Valores por defecto si la DB no existe aun o hay error
    empty = {
        "achievements":       [],
        "records":            {},
        "xp":                 0,
        "health":             100,     # alias de hp (retrocompatibilidad)
        "rank":               "Aprendiz de Satoshi",  # alias de rank.name
        "rank_level":         0,
        "rank_info":          {"name": "Aprendiz de Satoshi", "level": 0,
                               "xp_current": 0, "xp_next_rank": 50},
        "quests":             [],
        "unlocked_count":     0,
        "total_achievements": 0,
    }

    if not db_path.exists():
        return empty

    try:
        conn = _sqlite3.connect(db_path)
        conn.row_factory = _sqlite3.Row

        # Delegar al motor modular: una sola llamada que consolida todo
        payload = get_ui_gamification_payload(conn)
        conn.close()

        # Construir el dict de retorno con alias de retrocompatibilidad
        return {
            # Campos originales (retrocompatibles)
            "achievements":       payload["achievements"],
            "records":            payload["records"],
            "xp":                 payload["xp"],
            "health":             payload["hp"],          # alias: hp → health
            "rank":               payload["rank"]["name"],# alias: rank.name → rank
            "rank_level":         payload["rank"]["level"],
            # Campos nuevos (enriquecen la UI de gamificacion)
            "rank_info":          payload["rank"],
            "quests":             payload["quests"],
            "unlocked_count":     payload["unlocked_count"],
            "total_achievements": payload["total_achievements"],
        }

    except Exception as e:
        print(f"[WARN] read_gamification_status: {e}", file=_sys.stderr)
        return empty



# =============================================================================
# VISUALIZACIÓN 3D
# =============================================================================

# Procesa un CSV con el grafo de la red y genera un archivo HTML con la visualizacion 3D interactiva.
def generate_3d_html(csv_path: Path, html_path: Path,
                     my_pubkey: str = None, log_cb=print) -> bool:
    if not HAS_PLOTLY:
        log_cb("[ERROR] Faltan dependencias: pip install pandas plotly networkx")
        return False

    if not csv_path.exists():
        log_cb(f"[ERROR] CSV no encontrado: {csv_path}")
        return False

    log_cb(f"Cargando {csv_path.name}...")
    cols = [
        "source_pubkey","source_alias","target_pubkey","target_alias",
        "capacity_sats","fee_base_msat","fee_rate_ppm","max_htlc_sats",
        "cltv_delta","disabled",
        "source_last_update","target_last_update",
        "source_channels","target_channels",
        "source_total_cap","target_total_cap"
    ]
    df = pd.read_csv(csv_path, names=cols, header=0, dtype=str).fillna("")

    for c in ["capacity_sats","fee_base_msat","fee_rate_ppm","max_htlc_sats",
              "source_channels","target_channels","source_total_cap","target_total_cap"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int)
    df["disabled"] = df["disabled"].astype(str).str.strip() == "1"
    for c in ["source_last_update","target_last_update"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int)

    log_cb(f"  Edges en CSV: {len(df)}")
    NOW = datetime.now(timezone.utc).timestamp()
    MAX_NODES, EDGE_SAMPLE = 500, 5000

    G = nx.Graph()
    for _, r in df.iterrows():
        for side in ("source", "target"):
            pk  = r[f"{side}_pubkey"]
            al  = r[f"{side}_alias"] or pk[:10]
            lu  = r[f"{side}_last_update"]
            ch  = r[f"{side}_channels"]
            cap = r[f"{side}_total_cap"]
            d   = int((NOW - lu) / 86400) if lu > 0 else 9999
            if not G.has_node(pk):
                G.add_node(pk, alias=al, last_update=lu,
                           channels_gossip=ch, total_cap=cap, days_ago=d)

    seen = set()
    for _, r in df.iterrows():
        u, v = r["source_pubkey"], r["target_pubkey"]
        key = tuple(sorted([u, v]))
        if key not in seen:
            seen.add(key)
            G.add_edge(u, v, capacity=r["capacity_sats"],
                       fee_base=r["fee_base_msat"], fee_rate=r["fee_rate_ppm"],
                       max_htlc=r["max_htlc_sats"], disabled=r["disabled"])

    # Forzar la inclusión de canales vivos del usuario
    my_live_chans = get_channels()
    my_peers_to_keep = set()
    
    if my_pubkey:
        if not G.has_node(my_pubkey):
            my_info = get_node_info()
            my_alias = my_info.get("alias", "Mi Nodo") if my_info else "Mi Nodo"
            G.add_node(my_pubkey, alias=my_alias, last_update=NOW, channels_gossip=len(my_live_chans), total_cap=0, days_ago=0)
            
        for ch in my_live_chans:
            pub = ch.get("remote_pubkey")
            if not pub: continue
            my_peers_to_keep.add(pub)
            if not G.has_node(pub):
                al = ch.get("peer_alias") or pub[:10]
                G.add_node(pub, alias=al, last_update=NOW, channels_gossip=1, total_cap=int(ch.get("capacity",0)), days_ago=0)
            
            if not G.has_edge(my_pubkey, pub) and not G.has_edge(pub, my_pubkey):
                G.add_edge(my_pubkey, pub, capacity=int(ch.get("capacity",0)), fee_base=0, fee_rate=0, max_htlc=0, disabled=False)

    log_cb(f"  Nodos: {G.number_of_nodes()}  |  Edges: {G.number_of_edges()}")

    if G.number_of_nodes() > MAX_NODES:
        log_cb(f"  Reduciendo a {MAX_NODES} nodos más conectados...")
        top_pks = {n for n, _ in sorted(G.degree(), key=lambda x: x[1], reverse=True)[:MAX_NODES]}
        if my_pubkey:
            top_pks.add(my_pubkey)
            top_pks.update(my_peers_to_keep) # Proteger peers directos de ser eliminados
        G = G.subgraph(top_pks).copy()

    log_cb("  Calculando layout 3D...")
    pos3d = nx.spring_layout(G, dim=3, seed=42, k=0.7)

    node_pks = list(G.nodes())
    node_x = [pos3d[n][0] for n in node_pks]
    node_y = [pos3d[n][1] for n in node_pks]
    node_z = [pos3d[n][2] for n in node_pks]
    node_aliases  = [G.nodes[n].get("alias","") or n[:10] for n in node_pks]
    node_channels = [max(G.degree(n), G.nodes[n].get("channels_gossip",0)) for n in node_pks]
    node_total_cap= [G.nodes[n].get("total_cap",0) for n in node_pks]
    node_days     = [G.nodes[n].get("days_ago",9999) for n in node_pks]
    node_gossip   = [G.nodes[n].get("channels_gossip",0) for n in node_pks]

    def safe_log(x, base=2):
        return math.log(max(x, 1), base)
    node_sizes = [max(4, safe_log(c, 2) * 4) for c in node_channels]

    def days_to_hex(days):
        t = min(days / 30, 1.0)
        return f"rgb({int(80+170*t)},{int(230-180*t)},100)"
    node_colors = [days_to_hex(d) for d in node_days]

    node_text = []
    for i, pk in enumerate(node_pks):
        d = node_days[i]
        uptime = f"{d} días atrás" if d < 9999 else "desconocido"
        cap_s  = f"{node_total_cap[i]:,}" if node_total_cap[i] else "?"
        g      = node_gossip[i]
        extra  = f" (gossip: {g})" if g and g != node_channels[i] else ""
        node_text.append(
            f"<b>{node_aliases[i]}</b><br>"
            f"Pubkey: {pk[:20]}...<br>"
            f"Canales: {node_channels[i]}{extra}<br>"
            f"Cap. total: {cap_s} sats<br>"
            f"Últ. gossip: {uptime}"
        )

    edges = list(G.edges(data=True))
    if len(edges) > EDGE_SAMPLE:
        import random; random.seed(42)
        edges = random.sample(edges, EDGE_SAMPLE)

    groups = {"Grande (≥5M sat)":[],"Media (1M-5M sat)":[],"Pequeña (<1M sat)":[],"Deshabilitado":[]}
    for u, v, data in edges:
        if u not in pos3d or v not in pos3d: continue
        x0,y0,z0 = pos3d[u]; x1,y1,z1 = pos3d[v]
        seg = ([x0,x1,None],[y0,y1,None],[z0,z1,None])
        if data.get("disabled"):               groups["Deshabilitado"].append(seg)
        elif data.get("capacity",0) >= 5000000:groups["Grande (≥5M sat)"].append(seg)
        elif data.get("capacity",0) >= 1000000:groups["Media (1M-5M sat)"].append(seg)
        else:                                  groups["Pequeña (<1M sat)"].append(seg)

    clr = {"Grande (≥5M sat)":"rgba(255,215,0,0.6)","Media (1M-5M sat)":"rgba(100,180,255,0.5)",
           "Pequeña (<1M sat)":"rgba(100,255,100,0.35)","Deshabilitado":"rgba(255,60,60,0.4)"}

    log_cb("  Generando visualización 3D...")
    fig = go.Figure()

    for grp, segs in groups.items():
        if not segs: continue
        xs,ys,zs = [],[],[]
        for (x,y,z) in segs: xs+=x; ys+=y; zs+=z
        fig.add_trace(go.Scatter3d(x=xs,y=ys,z=zs,mode="lines",name=grp,
            line=dict(color=clr[grp],width=1),hoverinfo="skip",legendgroup=grp))

    my_idx = node_pks.index(my_pubkey) if my_pubkey and my_pubkey in node_pks else None
    other  = [i for i in range(len(node_pks)) if i != my_idx]

    fig.add_trace(go.Scatter3d(
        x=[node_x[i] for i in other], y=[node_y[i] for i in other],
        z=[node_z[i] for i in other], mode="markers+text", name="Nodos",
        marker=dict(size=[node_sizes[i] for i in other],
                    color=[node_colors[i] for i in other],
                    opacity=0.9, line=dict(width=0.5,color="rgba(255,255,255,0.3)")),
        text=[node_aliases[i] if node_channels[i]>5 else "" for i in other],
        textfont=dict(size=7,color="white"), textposition="top center",
        hovertext=[node_text[i] for i in other], hoverinfo="text"))

    my_annot = []
    if my_idx is not None:
        mx,my_,mz = node_x[my_idx],node_y[my_idx],node_z[my_idx]
        fig.add_trace(go.Scatter3d(
            x=[mx],y=[my_],z=[mz],mode="markers",name="⭐ Mi nodo",
            marker=dict(size=max(14,node_sizes[my_idx]*1.4),color="#FFD700",
                        opacity=1.0,symbol="diamond",line=dict(width=2,color="white")),
            hovertext=[node_text[my_idx]],hoverinfo="text"))
        my_annot = [dict(x=mx,y=my_,z=mz,text=f"<b>◀ {node_aliases[my_idx]}</b>",
            showarrow=True,arrowhead=2,arrowsize=1.5,arrowwidth=1.5,
            arrowcolor="#FFD700",ax=70,ay=-55,
            font=dict(size=11,color="#FFD700"),
            bgcolor="rgba(10,10,30,0.7)",bordercolor="#FFD700",borderwidth=1)]
        log_cb(f"  [*] Tu nodo '{node_aliases[my_idx]}' marcado en dorado.")

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    fig.update_layout(
        title=dict(text=f"<b>⚡ Red Lightning — Visualización 3D</b><br>"
                        f"<sub>{G.number_of_nodes()} nodos · {len(edges)} canales · {now_str}</sub>",
                   x=0.5,xanchor="center",font=dict(size=18,color="white")),
        scene=dict(
            xaxis=dict(showgrid=False,zeroline=False,showticklabels=False,
                       backgroundcolor="rgb(10,10,20)",title=""),
            yaxis=dict(showgrid=False,zeroline=False,showticklabels=False,
                       backgroundcolor="rgb(10,10,20)",title=""),
            zaxis=dict(showgrid=False,zeroline=False,showticklabels=False,
                       backgroundcolor="rgb(10,10,20)",title=""),
            bgcolor="rgb(10,10,20)", annotations=my_annot),
        paper_bgcolor="rgb(8,8,18)", font=dict(color="white"),
        legend=dict(bgcolor="rgba(20,20,40,0.8)",bordercolor="rgba(100,100,150,0.5)",
                    borderwidth=1,font=dict(size=11),
                    title=dict(text="<b>Canales por capacidad</b>")),
        margin=dict(l=0,r=0,t=80,b=0),
        hoverlabel=dict(bgcolor="rgba(20,20,50,0.95)",
                        bordercolor="rgba(100,150,255,0.8)",
                        font=dict(size=12,color="white")),
    )

    node_lookup_js = {}
    adj_lookup_js = {}
    for i, pk in enumerate(node_pks):
        al = node_aliases[i]
        entry = {"pk":pk,"alias":al,"x":node_x[i],"y":node_y[i],"z":node_z[i],
                 "ch":node_channels[i],"cap":node_total_cap[i],"days":node_days[i]}
        node_lookup_js[pk]       = entry
        node_lookup_js[pk[:20]]  = entry
        node_lookup_js[al.lower()]= entry
        adj_lookup_js[pk] = [n for n in G.neighbors(pk) if n in node_pks]
        
    nl_json = json.dumps(node_lookup_js, ensure_ascii=False)
    adj_json = json.dumps(adj_lookup_js, ensure_ascii=False)

    SEARCH_JS = f"""
(function() {{
  const NODE_LOOKUP = {nl_json};
  const ADJ_LOOKUP = {adj_json};
  let searchTraceIdx = null;
  let focusTraceIndices = [];
  let originalOpacities = {{}};

  // PANEL DE BÚSQUEDA
  const panel = document.createElement('div');
  panel.style.cssText = `position:fixed;top:16px;right:16px;z-index:9999;
    display:flex;flex-direction:column;gap:6px;font-family:monospace;`;
  const row = document.createElement('div');
  row.style.cssText = 'display:flex;gap:6px;align-items:center;';
  const input = document.createElement('input');
  input.type='text'; input.placeholder='🔍 alias o pubkey...';
  input.style.cssText=`background:rgba(10,10,30,0.92);border:1px solid rgba(0,220,255,0.55);
    border-radius:6px;color:#00dcff;font-size:13px;padding:6px 10px;width:220px;outline:none;
    box-shadow:0 0 8px rgba(0,220,255,0.25);`;
  const btn = document.createElement('button'); btn.textContent='Buscar';
  btn.style.cssText=`background:rgba(0,180,255,0.18);border:1px solid rgba(0,220,255,0.55);
    border-radius:6px;color:#00dcff;font-size:13px;padding:6px 12px;cursor:pointer;`;
  const clearBtn = document.createElement('button'); clearBtn.textContent='✕';
  clearBtn.style.cssText=`background:rgba(255,60,60,0.15);border:1px solid rgba(255,100,100,0.5);
    border-radius:6px;color:#ff6666;font-size:13px;padding:6px 10px;cursor:pointer;`;
  const status = document.createElement('div');
  status.style.cssText=`color:rgba(180,220,255,0.85);font-size:11px;padding:4px 8px;
    background:rgba(10,10,30,0.75);border-radius:4px;display:none;`;
  row.appendChild(input); row.appendChild(btn); row.appendChild(clearBtn);
  panel.appendChild(row); panel.appendChild(status);
  document.body.appendChild(panel);

  // PANEL DE LEYENDA Y CONTROLES DE CÁMARA
  const leg = document.createElement('div');
  leg.style.cssText = `position:fixed;bottom:20px;left:20px;z-index:9999;
    display:flex;flex-direction:column;gap:5px;font-family:sans-serif;font-size:12px;
    background:rgba(12,12,35,0.85);border:1px solid rgba(100,150,255,0.4);
    border-radius:8px;padding:12px;color:#ddd;box-shadow:0 8px 16px rgba(0,0,0,0.6);`;
  
  leg.innerHTML = `
    <div style="font-weight:bold;color:#fff;margin-bottom:4px;font-size:13px;">ℹ️ Leyenda de Nodos</div>
    <div><span style="font-size:14px;">◉</span> <b>Tamaño:</b> Proporcional al Nº de canales</div>
    <div><span style="font-size:14px;">🎨</span> <b>Color:</b> Último chisme (gossip)</div>
    <div style="display:flex;align-items:center;margin-top:4px;">
      <span style="background:#50e664;width:10px;height:10px;border-radius:50%;display:inline-block;margin-right:6px;"></span> Reciente (< 30 días)
    </div>
    <div style="display:flex;align-items:center;margin-top:2px;">
      <span style="background:#f03264;width:10px;height:10px;border-radius:50%;display:inline-block;margin-right:6px;"></span> Inactivo (> 30 días)
    </div>
    <hr style="border:0;border-top:1px solid rgba(255,255,255,0.2);margin:8px 0;width:100%;">
    <div style="display:flex;align-items:center;justify-content:space-between;">
      <label for="camSpeed" style="cursor:pointer;font-weight:bold;color:#00ffed;">↻ Auto-Giro:</label>
      <input type="range" id="camSpeed" min="0" max="50" value="2" style="width:80px;cursor:pointer;">
    </div>
    <div style="display:flex;align-items:center;justify-content:space-between;margin-top:5px;">
      <label for="autoReload" style="cursor:pointer;font-weight:bold;color:#ffcc00;">🔁 Auto-Refresco:</label>
      <div style="display:flex;align-items:center;gap:5px;">
        <input type="checkbox" id="autoReload" style="cursor:pointer;">
        <input type="number" id="reloadSecs" value="30" min="5" style="width:35px;background:#000;color:#ffcc00;border:1px solid #ffcc00;font-size:10px;text-align:center;">
      </div>
    </div>
  `;
  document.body.appendChild(leg);

  // FUNCIONALIDAD DE BÚSQUEDA
  function getGd() {{ return document.querySelector('.js-plotly-plot'); }}
  function removeSearchTrace() {{
    const gd=getGd(); if(!gd) return;
    let toDelete = [];
    if(searchTraceIdx !== null) {{ toDelete.push(searchTraceIdx); searchTraceIdx = null; }}
    if(focusTraceIndices.length > 0) {{ toDelete.push(...focusTraceIndices); focusTraceIndices = []; }}
    if(toDelete.length > 0) {{
      toDelete.sort((a,b) => b-a);
      Plotly.deleteTraces(gd, toDelete);
    }}
    if(Object.keys(originalOpacities).length > 0) {{
       const indices = Object.keys(originalOpacities).map(Number);
       const opacities = indices.map(i => originalOpacities[i]);
       Plotly.restyle(gd, {{'opacity': opacities}}, indices);
       originalOpacities = {{}};
    }}
  }}
  function doSearch() {{
    const gd=getGd(); if(!gd) return;
    const q=input.value.trim(); sessionStorage.setItem('savedSearchQuery', q);
    if(!q){{removeSearchTrace();status.style.display='none';return;}}
    const ql=q.toLowerCase();
    let found=NODE_LOOKUP[q]||NODE_LOOKUP[q.slice(0,20)]||NODE_LOOKUP[ql];
    if(!found) for(const [k,v] of Object.entries(NODE_LOOKUP))
      if(k.toLowerCase().includes(ql)&&v.alias){{found=v;break;}}
    removeSearchTrace();
    if(!found){{status.textContent='⚠ Nodo no encontrado.';status.style.color='#ff8888';
      status.style.display='block';return;}}
    const days_s=found.days<9999?found.days+' días':'desc.';
    const cap_s=found.cap?found.cap.toLocaleString()+' sats':'?';
    
    // ── Lógica de Ego-Network (Filtro 1 Salto) ──
    const neighbors = ADJ_LOOKUP[found.pk] || [];
    let n_x = [], n_y = [], n_z = [], n_hover = [], n_sizes = [], n_colors = [];
    let e_x = [], e_y = [], e_z = [];
    for (const npk of neighbors) {{
       const nb = NODE_LOOKUP[npk];
       if (!nb) continue;
       n_x.push(nb.x); n_y.push(nb.y); n_z.push(nb.z);
       n_hover.push('<b>' + nb.alias + '</b><br>' + nb.pk.slice(0,20) + '...<br>Canales: ' + nb.ch);
       n_sizes.push(Math.max(8, Math.log2(Math.max(nb.ch, 1)) * 4));
       let t = Math.min(nb.days / 30, 1.0);
       let r = Math.floor(80 + 170 * t);
       let g = Math.floor(230 - 180 * t);
       n_colors.push(`rgb(${{r}},${{g}},100)`);
       e_x.push(found.x, nb.x, null); e_y.push(found.y, nb.y, null); e_z.push(found.z, nb.z, null);
    }}
    
    const neighborNodesTrace = {{
        type: 'scatter3d', x: n_x, y: n_y, z: n_z, mode: 'markers+text', name: 'Vecinos (1 salto)',
        marker: {{ size: n_sizes, color: n_colors, opacity: 1.0, line: {{width: 1, color: 'rgba(255,255,255,0.8)'}}, symbol: 'circle' }},
        text: n_hover.map(h => h.split('<br>')[0].replace('<b>','').replace('</b>','')),
        textfont: {{size: 9, color: 'white'}}, textposition: 'top center', hovertext: n_hover, hoverinfo: 'text', showlegend: true
    }};
    
    const neighborEdgesTrace = {{
        type: 'scatter3d', x: e_x, y: e_y, z: e_z, mode: 'lines', name: 'Canales vecinos',
        line: {{color: 'rgba(0, 255, 237, 0.4)', width: 2}}, hoverinfo: 'skip', showlegend: true
    }};

    const indicesToDim = [];
    for (let i = 0; i < gd.data.length; i++) {{
        if (originalOpacities[i] === undefined) {{
            originalOpacities[i] = gd.data[i].opacity !== undefined ? gd.data[i].opacity : 1.0;
        }}
        indicesToDim.push(i);
    }}
    if (indicesToDim.length > 0) {{
        Plotly.restyle(gd, {{'opacity': 0.05}}, indicesToDim);
    }}

    const newTrace = {{type:'scatter3d',x:[found.x],y:[found.y],z:[found.z],
      mode:'markers',name:'🔍 Encontrado',
      marker:{{size:18,color:'#00ffed',opacity:1.0,symbol:'diamond',
               line:{{width:2,color:'white'}}}},
      hovertext:['<b>'+found.alias+'</b><br>Pubkey:'+found.pk.slice(0,20)+'...<br>'+
                 'Canales:'+found.ch+'<br>Cap:'+cap_s+'<br>Gossip:'+days_s],
      hoverinfo:'text',showlegend:true}};
      
    Plotly.addTraces(gd, [neighborEdgesTrace, neighborNodesTrace, newTrace]).then(f=>{{
      const len = gd.data.length; searchTraceIdx = len - 1; focusTraceIndices = [len - 3, len - 2];
    }});
    const cur=gd.layout.scene.annotations||[];
    const cleaned=cur.filter(a=>!a.text.startsWith('<b>🔍'));
    Plotly.relayout(gd,{{'scene.annotations':[...cleaned,{{x:found.x,y:found.y,z:found.z,
      text:'<b>🔍 '+found.alias+'</b>',showarrow:true,arrowhead:2,
      arrowcolor:'#00ffed',ax:-70,ay:55,
      font:{{size:12,color:'#00ffed'}},bgcolor:'rgba(0,30,30,0.8)',
      bordercolor:'#00ffed',borderwidth:1}}]}});
    status.innerHTML='✅ <b>'+found.alias+'</b> | ch:'+found.ch+' | cap:'+cap_s;
    status.style.color='#aaffee'; status.style.display='block';
  }}
  function doClear() {{
    input.value=''; removeSearchTrace(); sessionStorage.removeItem('savedSearchQuery');
    const gd=getGd(); if(gd){{
      const cur=gd.layout.scene.annotations||[];
      Plotly.relayout(gd,{{'scene.annotations':cur.filter(a=>!a.text.startsWith('<b>[SEARCH]'))}});
    }}
    status.style.display='none';
  }}
  btn.addEventListener('click',doSearch);
  clearBtn.addEventListener('click',doClear);
  input.addEventListener('keydown',e=>{{if(e.key==='Enter')doSearch();}});

  // AUTO-ROTACIÓN DE CÁMARA
  let isInteracting = false;
  let lastTime = performance.now();
  let rafId = null;

  document.addEventListener('mousedown', () => isInteracting = true);
  document.addEventListener('mouseup', () => isInteracting = false);
  document.addEventListener('touchstart', () => isInteracting = true);
  document.addEventListener('touchend', () => isInteracting = false);
  
  // Rueda del ratón congela la rotación un instante
  let wheelTimer;
  document.addEventListener('wheel', () => {{
    isInteracting = true;
    clearTimeout(wheelTimer);
    wheelTimer = setTimeout(() => isInteracting = false, 1500);
  }});

  function rotateCamera(now) {{
    rafId = requestAnimationFrame(rotateCamera);
    const dt = now - lastTime;
    lastTime = now;
    
    // Evitar saltos si minimizan la pestaña
    if (dt > 100) return;

    const gd = getGd();
    if (!gd || !gd.layout || !gd.layout.scene) return;

    const slider = document.getElementById('camSpeed');
    const speed = slider ? parseFloat(slider.value) : 0;

    // Pausa si el giro está en 0 o el usuario está interactuando (drag/zoom)
    if (speed === 0 || isInteracting) return;
    
    // Obtener cámara actual
    const cam = gd.layout.scene.camera;
    if (!cam || !cam.eye) return;
    
    const rSpeed = speed * -0.00003 * dt; // Dirección y factor escala

    const x = cam.eye.x;
    const y = cam.eye.y;
    const z = cam.eye.z;
    
    const r = Math.sqrt(x*x + y*y);
    if (r < 0.01) return; // evitar glitch en centro
    
    const currentAngle = Math.atan2(y, x);
    const newAngle = currentAngle + rSpeed;
    
    const nextX = r * Math.cos(newAngle);
    const nextY = r * Math.sin(newAngle);
    
    Plotly.relayout(gd, {{
      'scene.camera': {{
        eye: {{x: nextX, y: nextY, z: z}},
        up: cam.up || {{x:0, y:0, z:1}},
        center: cam.center || {{x:0, y:0, z:0}}
      }}
    }});
  }}
  
  // LÓGICA DE AUTO-REFRESCO (Persistente)
  const autoReloadCheck = document.getElementById('autoReload');
  const reloadSecsInput = document.getElementById('reloadSecs');
  
  autoReloadCheck.checked = localStorage.getItem('autoReload') === 'true';
  reloadSecsInput.value = localStorage.getItem('reloadSecs') || '30';

  let reloadTimer = null;
  const startReloadTimer = () => {{
    if (reloadTimer) clearTimeout(reloadTimer);
    if (autoReloadCheck.checked) {{
      const ms = Math.max(5, parseInt(reloadSecsInput.value)) * 1000;
      reloadTimer = setTimeout(() => {{ 
         console.log('Auto-refrescando gráfica...');
         const gd = getGd();
         if (gd && gd.layout && gd.layout.scene && gd.layout.scene.camera) {{
             sessionStorage.setItem('savedCamera', JSON.stringify(gd.layout.scene.camera));
         }}
         const slider = document.getElementById('camSpeed');
         if (slider) sessionStorage.setItem('savedCamSpeed', slider.value);
         location.reload(); 
      }}, ms);
    }}
  }};

  autoReloadCheck.onchange = () => {{
    localStorage.setItem('autoReload', autoReloadCheck.checked);
    startReloadTimer();
  }};
  reloadSecsInput.onchange = () => {{
    localStorage.setItem('reloadSecs', reloadSecsInput.value);
    if (autoReloadCheck.checked) startReloadTimer();
  }};

  // Eliminar flash blanco
  document.body.style.backgroundColor = 'rgb(10,10,20)';

  // Restaurar estado de cámara previo al refresco
  setTimeout(() => {{
      const gd = getGd();
      if (gd) {{
          const savedCamStr = sessionStorage.getItem('savedCamera');
          if (savedCamStr) {{
              try {{
                  Plotly.relayout(gd, {{'scene.camera': JSON.parse(savedCamStr)}});
              }} catch(e) {{}}
          }}
      }}
      const savedCamSpeed = sessionStorage.getItem('savedCamSpeed');
      if (savedCamSpeed) {{
          const slider = document.getElementById('camSpeed');
          if (slider) slider.value = savedCamSpeed;
      }}
      
      const savedSearchQuery = sessionStorage.getItem('savedSearchQuery');
      if (savedSearchQuery) {{
          input.value = savedSearchQuery;
          doSearch();
      }}
  }}, 100);

  // ── Listener para clics en nodos 3D ────────────────────────────────────────
  setTimeout(() => {{
     const gd = getGd();
     if (gd) {{
         gd.on('plotly_click', function(data) {{
             if (data.points && data.points.length > 0) {{
                 const pt = data.points[0];
                 let clickedPk = null;
                 for (const [pk, val] of Object.entries(NODE_LOOKUP)) {{
                     if (pk.length >= 66 && Math.abs(val.x - pt.x) < 0.0001 && Math.abs(val.y - pt.y) < 0.0001 && Math.abs(val.z - pt.z) < 0.0001) {{
                         clickedPk = val.pk;
                         break;
                     }}
                 }}
                 if (clickedPk) {{
                     input.value = clickedPk;
                     doSearch();
                 }}
             }}
         }});
     }}
  }}, 1500);

  // Iniciar loops
  startReloadTimer();
  requestAnimationFrame(rotateCamera);
}})();
"""
    html_path.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(str(html_path), include_plotlyjs="cdn", post_script=SEARCH_JS)
    log_cb(f"[OK] HTML guardado en: {html_path.name}")
    return True


# =============================================================================
# COCKPIT HTML — Red 3D + Paneles HUD de Instrumentos
# =============================================================================

COCKPIT_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>LN Lightning Cockpit HUD</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Orbitron:wght@400;700;900&display=swap" rel="stylesheet">
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  :root{
    --bg:#07070f;--panel:#0d0d1e;--cyan:#00dcff;--green:#00ff88;
    --gold:#ffd700;--red:#ff4466;--amber:#ffcc00;--violet:#7b2fff;
    --text:#e0e0f0;--sub:#8888aa;--border:rgba(0,220,255,0.35);
  }
  html,body{width:100%;height:100%;background:var(--bg);color:var(--text);overflow:hidden;
    font-family:'Share Tech Mono',monospace;}

  /* ── SCANLINE OVERLAY ── */
  body::after{content:'';position:fixed;inset:0;pointer-events:none;z-index:9990;
    background:repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(0,0,0,.07) 2px,rgba(0,0,0,.07) 4px);}

  /* ── GRID LAYOUT ── */
  #cockpit{display:grid;width:100vw;height:100vh;
    grid-template-areas:"top top top" "left center right" "bottom bottom bottom";
    grid-template-columns:230px 1fr 230px;
    grid-template-rows:58px 1fr 138px;}

  /* ── SHARED PANEL STYLE ── */
  .hud-panel{background:rgba(13,13,30,.92);border-color:var(--border);
    backdrop-filter:blur(6px);}

  /* ── TOP BAR ── */
  #hud-top{grid-area:top;border-bottom:1px solid var(--border);
    box-shadow:0 0 18px rgba(0,220,255,.25);display:flex;align-items:center;
    padding:0 16px;gap:10px;z-index:100;}
  #hud-top .logo{font-family:'Orbitron',sans-serif;font-weight:900;font-size:17px;
    color:var(--cyan);letter-spacing:2px;white-space:nowrap;margin-right:8px;
    text-shadow:0 0 12px var(--cyan);}
  .top-seg{display:flex;flex-direction:column;align-items:center;
    padding:0 10px;border-left:1px solid rgba(0,220,255,.2);min-width:90px;}
  .top-seg .lbl{font-size:12px;color:var(--sub);letter-spacing:1px;text-transform:uppercase;}
  .top-seg .val{font-size:17px;color:var(--cyan);font-weight:bold;white-space:nowrap;}
  #sync-dot{width:10px;height:10px;border-radius:50%;display:inline-block;margin-right:5px;
    box-shadow:0 0 8px currentColor;}
  .synced{color:var(--green)}.unsynced{color:var(--red)}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
  .pulse{animation:pulse 1.5s ease-in-out infinite}

  /* ── LEFT PANEL ── */
  #hud-left{grid-area:left;border-right:1px solid var(--border);
    box-shadow:0 0 20px rgba(0,220,255,.18) inset;padding:12px 10px;
    display:flex;flex-direction:column;gap:10px;overflow:hidden;}

  /* ── RIGHT PANEL ── */
  #hud-right{grid-area:right;border-left:1px solid var(--border);
    box-shadow:0 0 20px rgba(0,220,255,.18) inset;padding:12px 10px;
    display:flex;flex-direction:column;gap:10px;overflow:hidden;}

  /* ── CENTER ── */
  #hud-center{grid-area:center;position:relative;overflow:hidden;}
  #hud-center iframe{width:100%;height:100%;border:none;display:block;}

  /* ── BOTTOM BAR ── */
  #hud-bottom{grid-area:bottom;border-top:1px solid var(--border);
    box-shadow:0 0 18px rgba(0,220,255,.2);display:flex;align-items:stretch;
    padding:8px 12px;gap:12px;overflow:hidden;}

  /* ── INSTRUMENT CARD ── */
  .card{background:rgba(5,5,18,.7);border:1px solid rgba(0,220,255,.22);
    border-radius:6px;padding:8px 10px;}
  .card-title{font-size:12px;color:var(--sub);letter-spacing:1.5px;
    text-transform:uppercase;margin-bottom:6px;display:flex;align-items:center;gap:4px;}
  .card-val{font-size:26px;font-family:'Orbitron',sans-serif;color:var(--cyan);
    line-height:1.1;text-shadow:0 0 10px rgba(0,220,255,.5);}
  .card-val.green{color:var(--green);text-shadow:0 0 10px rgba(0,255,136,.5)}
  .card-val.amber{color:var(--amber);text-shadow:0 0 10px rgba(255,204,0,.5)}
  .card-val.red{color:var(--red);text-shadow:0 0 10px rgba(255,68,102,.5)}
  .card-sub{font-size:12px;color:var(--sub);margin-top:2px;}

  /* ── GAUGE SVG ── */
  .gauge-wrap{display:flex;justify-content:center;align-items:center;padding:4px 0;}

  /* ── BAR METER ── */
  .bar-meter{height:8px;background:rgba(255,255,255,.08);border-radius:4px;overflow:hidden;margin-top:4px;}
  .bar-fill{height:100%;border-radius:4px;transition:width .8s ease;}

  /* ── SPARKLINE ── */
  .spark-wrap{flex:1;min-width:0;display:flex;flex-direction:column;}
  .spark-title{font-size:12px;color:var(--sub);letter-spacing:1px;text-transform:uppercase;margin-bottom:4px;}
  .spark-wrap svg{width:100%;height:60px;}

  /* ── BOTTOM SEGMENTS ── */
  .bot-seg{flex:1;min-width:0;display:flex;flex-direction:column;gap:4px;
    border-right:1px solid rgba(0,220,255,.12);padding-right:10px;}
  .bot-seg:last-child{border-right:none;padding-right:0;}
  .bot-label{font-size:12px;color:var(--sub);letter-spacing:1px;text-transform:uppercase;}
  .bot-val{font-family:'Orbitron',sans-serif;font-size:22px;color:var(--cyan);}
  .bot-val.pos{color:var(--green)}.bot-val.neg{color:var(--red)}

  /* ── DIVIDER ── */
  .hdiv{height:1px;background:rgba(0,220,255,.15);margin:2px 0;}


  /* ── TOOLTIP (JS-driven, never clipped by overflow:hidden) ── */
  .tip{cursor:help;}
  #hud-floatip{
    position:fixed;z-index:99999;display:none;
    background:rgba(4,4,20,0.97);
    border:1px solid rgba(0,220,255,0.55);
    color:#d0d8f0;font-size:11.5px;line-height:1.6;
    padding:10px 14px;border-radius:8px;
    white-space:pre-line;max-width:260px;
    box-shadow:0 0 18px rgba(0,220,255,0.35);
    font-family:'Share Tech Mono',monospace;
    pointer-events:none;
  }

  /* ── CORNER DECO ── */
  .corner-tl,.corner-tr,.corner-bl,.corner-br{position:absolute;width:12px;height:12px;z-index:10;}
  .corner-tl{top:0;left:0;border-top:2px solid var(--cyan);border-left:2px solid var(--cyan);}
  .corner-tr{top:0;right:0;border-top:2px solid var(--cyan);border-right:2px solid var(--cyan);}
  .corner-bl{bottom:0;left:0;border-bottom:2px solid var(--cyan);border-left:2px solid var(--cyan);}
  .corner-br{bottom:0;right:0;border-bottom:2px solid var(--cyan);border-right:2px solid var(--cyan);}
</style>
</head>
<body>
<div id="cockpit">

  <!-- TOP BAR -->
  <div id="hud-top" class="hud-panel">
    <div class="logo">⚡ LN·COCKPIT</div>
    <div class="top-seg tip" data-tip="Alias del nodo anunciado al grafo Lightning.
Debe coincidir con lnd.conf.">
      <span class="lbl">Nodo</span>
      <span class="val" id="t-alias">—</span>
    </div>
    <div class="top-seg tip" data-tip="🟢 OK = sincronizado y operativo.
🔴 NO = offline o atrasado.
Ningún pago se enruta si NO está sincronizado.">
      <span class="lbl">Sync</span>
      <span class="val" id="t-sync"><span id="sync-dot"></span><span id="sync-txt">—</span></span>
    </div>
    <div class="top-seg tip" data-tip="Altura de bloque que conoce el nodo.
Debe estar al día con la red.
&gt;6 bloques de atraso = problema de sync.">
      <span class="lbl">Bloque</span>
      <span class="val" id="t-block">—</span>
    </div>
    <div class="top-seg tip" data-tip="Peers P2P conectados actualmente.
Óptimo: ≥3 peers activos.
&lt;2 = riesgo de aislamiento de la red.">
      <span class="lbl">Peers</span>
      <span class="val" id="t-peers">—</span>
    </div>
    <div class="top-seg tip" data-tip="Saldo on-chain confirmado.
Mantener reserva para abrir canales.
Demasiado on-chain = capital sin trabajar.">
      <span class="lbl">Wallet</span>
      <span class="val" id="t-wallet">—</span>
    </div>
    <div class="top-seg tip" data-tip="% de tiempo online en las últimas 24h.
100% = nodo siempre operativo.
&lt;95% = reinicios o problemas de red frecuentes.">
      <span class="lbl">Uptime 24h</span>
      <span class="val" id="t-uptime">—</span>
    </div>
    <div style="flex:1"></div>
    <div class="top-seg" style="border-left:none;">
      <span class="lbl">Hora</span>
      <span class="val" id="t-clock">—</span>
    </div>
  </div>

  <!-- LEFT PANEL: Canales & Liquidez -->
  <div id="hud-left" class="hud-panel">
    <!-- Logo -->
    <div style="text-align: center; margin-bottom: 5px;">
      <img src="../images/ln-cockpit-Small.png" alt="LN Cockpit Logo" style="width: 130px; height: auto; border-radius: 8px; box-shadow: 0 0 15px rgba(0, 220, 255, 0.15);">
    </div>
    
    <!-- Canales activos/inactivos -->
    <div class="card tip" data-tip="Ideal: ≥80% de canales activos.
Inactivos = peer desconectado.
Si persiste &gt;24h, considera cerrar el canal.">
      <div class="card-title">📡 Canales</div>
      <div class="card-val" id="l-ch-active">—</div>
      <div class="card-sub">activos / <span id="l-ch-total">—</span> total</div>
      <div class="bar-meter" style="margin-top:6px;">
        <div class="bar-fill" id="l-ch-bar" style="background:var(--green);width:0%"></div>
      </div>
    </div>
    <!-- Gauge liquidez -->
    <div class="card tip" data-tip="Óptimo: 40–60%.
&lt;20% = sin liquidez saliente (no puedes enviar).
&gt;80% = sin liquidez entrante (no puedes recibir).
Equilibrio = más enrutamiento posible.">
      <div class="card-title">💧 Liquidez Local</div>
      <div class="gauge-wrap">
        <svg width="110" height="70" viewBox="0 0 110 70">
          <path d="M5,60 A50,50 0 0,1 105,60" fill="none" stroke="rgba(255,255,255,.08)" stroke-width="10" stroke-linecap="round"/>
          <path id="gauge-arc" d="M5,60 A50,50 0 0,1 105,60" fill="none" stroke="var(--green)" stroke-width="10" stroke-linecap="round"
            stroke-dasharray="157" stroke-dashoffset="157"/>
          <text x="55" y="55" text-anchor="middle" fill="var(--cyan)" font-family="Orbitron,sans-serif" font-size="18" id="gauge-pct">—%</text>
        </svg>
      </div>
      <div class="card-sub" style="text-align:center">Local / (Local+Remote)</div>
    </div>
    <!-- Capacidad total -->
    <div class="card tip" data-tip="Sats totales bloqueados en canales.
A mayor capacidad, más atractivo como hub.
Mainnet: nodos relevantes &gt;10M sats.
Capital en on-chain = capital sin trabajar.">
      <div class="card-title">⚡ Capacidad Total</div>
      <div class="card-val" style="font-size:21px;" id="l-capacity">—</div>
      <div class="card-sub">sats en canales</div>
    </div>
    <!-- Zombies -->
    <div class="card tip" id="card-zombies" data-tip="Ideal: 0.
Canal activo sin actualizaciones en &gt;7 días.
Capital inmovilizado e improductivo.
Considera cerrar zombies persistentes.">
      <div class="card-title">🧟 Zombies</div>
      <div class="card-val" id="l-zombies">—</div>
      <div class="card-sub" id="l-zombie-cap">— sats inactivos</div>
    </div>
  </div>

  <!-- CENTER: iframe con la red 3D -->
  <div id="hud-center">
    <div class="corner-tl"></div><div class="corner-tr"></div>
    <div class="corner-bl"></div><div class="corner-br"></div>
    <iframe id="graph-frame" src="GRAPH_SRC_PLACEHOLDER" title="Red Lightning 3D"></iframe>
  </div>

  <!-- RIGHT PANEL: Routing & Rentabilidad -->
  <div id="hud-right" class="hud-panel">
    <!-- Fees ganadas acumulado -->
    <div class="card tip" data-tip="Msat ganados enrutando pagos de terceros.
Tendencia creciente = nodo utilizado como hub.
Mainnet: buenos nodos ganan 100k+ msat/mes.
Testnet4: cifras bajas son normales.">
      <div class="card-title">📈 Fees Ganadas (cum.)</div>
      <div class="card-val green" id="r-fees-earned">—</div>
      <div class="card-sub">msat enrutamiento</div>
    </div>
    <!-- Fees pagadas (rebalanceo) -->
    <div class="card tip" data-tip="Msat pagados en rebalanceos propios.
Debe ser MENOR que las fees ganadas.
Si supera lo ganado, estás subsidiando la red.
Revisa frecuencia y costo de rebalanceos.">
      <div class="card-title">📉 Fees Pagadas (cum.)</div>
      <div class="card-val amber" id="r-fees-paid">—</div>
      <div class="card-sub">msat rebalanceos</div>
    </div>
    <!-- Forwards -->
    <div class="card tip" data-tip="Pagos totales enrutados por tu nodo.
Vol: sats totales que pasaron por ti.
Más forwards = mejor posición en la red.
Forwards grandes = pagos relevantes enrutados.">
      <div class="card-title">⚡ Forwards (cum.)</div>
      <div class="card-val" id="r-fwd-count">—</div>
      <div class="card-sub">Vol: <span id="r-fwd-vol">—</span> sats</div>
    </div>
    <!-- Ratio rebalanceo/enrutamiento -->
    <div class="card tip" data-tip="fees_paid / fees_earned × 100.
&lt;50% Excelente — nodo muy rentable.
50–100% Aceptable — margen mejorable.
&gt;100% Pérdida — rebalanceos demasiado caros.">
      <div class="card-title">⚖️ Ratio Rebalan/Enrut.</div>
      <div class="card-val" id="r-ratio">—</div>
      <div class="card-sub">fees_paid / fees_earned</div>
    </div>
    <!-- Eficiencia de capital -->
    <div class="card tip" data-tip="sats_enrutados / capacidad_total.
Cuánto de tu capital está 'trabajando'.
Mainnet: &gt;1% mensual es aceptable.
Si es 0% en mainnet: revisar fees y conectividad.">
      <div class="card-title">🎯 Efic. Capital</div>
      <div class="card-val" id="r-efficiency">—</div>
      <div class="card-sub">sats_enrutados / capacidad</div>
    </div>
  </div>

  <!-- BOTTOM BAR -->
  <div id="hud-bottom" class="hud-panel">
    <!-- Mini chart fees 7 días -->
    <div class="spark-wrap tip" data-tip="Barras diarias: verde=ganadas, amarillo=pagadas.
Barras verdes más altas = nodo rentable ese día.
Días vacíos = sin actividad de enrutamiento.">
      <div class="spark-title">📊 Fees Ganadas vs Pagadas — 7 días</div>
      <svg id="spark-daily" viewBox="0 0 300 55" preserveAspectRatio="none">
        <text x="150" y="30" fill="rgba(136,136,170,.5)" text-anchor="middle" font-size="10" font-family="monospace">Sin datos</text>
      </svg>
    </div>
    <!-- Mini chart forwards 24h -->
    <div class="spark-wrap tip" data-tip="Pagos enrutados por hora en las últimas 24h.
Picos = alta actividad de enrutamiento.
Plano en 0 = sin tráfico (normal en testnet4).
Tendencia creciente = ganando relevancia en la red.">
      <div class="spark-title">⚡ Forwards por hora — 24h</div>
      <svg id="spark-hourly" viewBox="0 0 300 55" preserveAspectRatio="none">
        <text x="150" y="30" fill="rgba(136,136,170,.5)" text-anchor="middle" font-size="10" font-family="monospace">Sin datos</text>
      </svg>
    </div>
    <!-- Net profit 7 días -->
    <div class="bot-seg tip" data-tip="fees_earned - fees_paid en 7 días.
+ Verde = nodo rentable.
- Rojo = gastas más en rebalanceos de lo que ganas.
Objetivo: siempre positivo.">
      <div class="bot-label">💰 Net Profit 7d</div>
      <div class="bot-val" style="font-size:26px;" id="b-net-profit">—</div>
      <div class="card-sub" id="b-net-sub">fees_earned - fees_paid</div>
    </div>
    <!-- Snapshot ts -->
    <div class="bot-seg tip" data-tip="Timestamp del último registro en node_history.db.
Debe ser reciente (min = intervalo configurado).
Muy desactualizado = colector no está corriendo.">
      <div class="bot-label">🕒 Último Snapshot</div>
      <div class="bot-val" style="font-size:16px;" id="b-snap-ts">—</div>
      <div class="card-sub">Datos en tiempo real</div>
    </div>
  </div>

</div><!-- #cockpit -->

<script>
(function(){
  const S = __STATS_JSON__;

  // ── helpers ──
  const $ = id => document.getElementById(id);
  const fmtSat = n => n >= 1e6 ? (n/1e6).toFixed(2)+'M' : n >= 1e3 ? (n/1e3).toFixed(1)+'k' : String(n);
  const fmtMsat = n => n >= 1e9 ? (n/1e9).toFixed(2)+'M sat' : n >= 1e6 ? (n/1e6).toFixed(1)+'k sat' : (n/1000).toFixed(0)+' sat';

  // ── clock ──
  function updateClock(){
    $('t-clock').textContent = new Date().toLocaleTimeString('es',{hour12:false});
  }
  setInterval(updateClock, 1000); updateClock();

  // ── populate from snapshot ──
  const snap = S.snap || {};
  if(snap.alias) $('t-alias').textContent = snap.alias;
  if(snap.block_height) $('t-block').textContent = Number(snap.block_height).toLocaleString();
  
  if(snap.num_peers !== undefined) {
    const p = snap.num_peers;
    const pEl = $('t-peers');
    pEl.textContent = p;
    if (p >= 3) { pEl.style.color = 'var(--green)'; pEl.style.textShadow = '0 0 8px var(--green)'; }
    else if (p === 2) { pEl.style.color = 'var(--amber)'; pEl.style.textShadow = '0 0 8px var(--amber)'; }
    else { pEl.style.color = 'var(--red)'; pEl.style.textShadow = '0 0 8px var(--red)'; }
  }

  if(snap.wallet_confirmed !== undefined)
    $('t-wallet').textContent = fmtSat(snap.wallet_confirmed)+' sat';

  const up = S.uptime_pct_7d || 0;
  const upEl = $('t-uptime');
  upEl.textContent = up + '%';
  if (up >= 95) { upEl.style.color = 'var(--green)'; upEl.style.textShadow = '0 0 8px var(--green)'; }
  else if (up >= 80) { upEl.style.color = 'var(--amber)'; upEl.style.textShadow = '0 0 8px var(--amber)'; }
  else { upEl.style.color = 'var(--red)'; upEl.style.textShadow = '0 0 8px var(--red)'; }

  // sync dot
  const synced = snap.synced_to_chain === 1;
  const dot = $('sync-dot');
  dot.className = synced ? 'synced' : 'unsynced pulse';
  $('sync-txt').textContent = synced ? 'OK' : 'NO';

  // channels
  const chA = snap.channels_active || 0;
  const chT = snap.channels_total || 0;
  const chEl = $('l-ch-active');
  chEl.textContent = chA;
  $('l-ch-total').textContent = chT;
  const pct = chT > 0 ? (chA/chT*100) : 0;
  
  let cColor = 'var(--green)';
  if (pct < 40) cColor = 'var(--red)';
  else if (pct < 80) cColor = 'var(--amber)';
  
  $('l-ch-bar').style.width = pct + '%';
  $('l-ch-bar').style.background = cColor;
  chEl.style.color = cColor;
  chEl.style.textShadow = `0 0 10px ${cColor}`;

  // gauge liquidez
  const liq = snap.liquidity_ratio || 0;
  const liqEl = $('gauge-pct');
  liqEl.textContent = liq.toFixed(1) + '%';
  const arc = document.getElementById('gauge-arc');
  const offset = 157 - (liq/100)*157;
  arc.setAttribute('stroke-dashoffset', offset);
  const gc = (liq > 85 || liq < 15) ? 'var(--red)' : (liq > 70 || liq < 30) ? 'var(--amber)' : 'var(--green)';
  arc.setAttribute('stroke', gc);
  liqEl.setAttribute('fill', gc);

  // capacity
  $('l-capacity').textContent = fmtSat(snap.capacity_total || 0);

  // zombies
  const z = snap.zombie_channels || 0;
  const zEl = $('l-zombies');
  zEl.textContent = z;
  zEl.className = 'card-val ' + (z > 0 ? 'red' : 'green');
  $('l-zombie-cap').textContent = fmtSat(snap.inactive_capital_sat || 0) + ' sats inactivos';

  if (S.zombies_list && S.zombies_list.length > 0) {
    let zTip = "🧟 ZOMBIES DETECTADOS:\\n";
    S.zombies_list.forEach(zb => {
      const alias = zb.peer_alias || 'Desconocido';
      zTip += `- ${alias} (${fmtSat(zb.capacity)} sats)\\n`;
    });
    zTip += "\\nConsidera cerrar estos canales.";
    const cardZ = $('card-zombies');
    if (cardZ) cardZ.setAttribute('data-tip', zTip);
  }

  // right panel
  $('r-fees-earned').textContent = fmtMsat(snap.fwd_fees_cum_msat || 0);
  $('r-fees-paid').textContent = fmtMsat(snap.payments_fees_cum_msat || 0);
  $('r-fwd-count').textContent = (snap.fwd_count_cum || 0).toLocaleString();
  $('r-fwd-vol').textContent = fmtSat(snap.fwd_amt_cum_sat || 0);

  const earned = snap.fwd_fees_cum_msat || 1;
  const paid   = snap.payments_fees_cum_msat || 0;
  const ratio  = earned > 0 ? (paid/earned*100).toFixed(1) : '0.0';
  const rEl = $('r-ratio');
  rEl.textContent = ratio + '%';
  rEl.className = 'card-val ' + (parseFloat(ratio) > 100 ? 'red' : parseFloat(ratio) > 50 ? 'amber' : 'green');

  const eff = ((snap.capital_efficiency || 0) * 100).toFixed(3);
  $('r-efficiency').textContent = eff + '%';

  // bottom
  const net7 = S.net_profit_7d_msat || 0;
  const netEl = $('b-net-profit');
  netEl.textContent = (net7 >= 0 ? '+' : '') + fmtMsat(net7);
  netEl.className = 'bot-val ' + (net7 >= 0 ? 'pos' : 'neg');

  if(snap.ts){
    const d = new Date(snap.ts * 1000);
    $('b-snap-ts').textContent = d.toLocaleString('es',{hour12:false});
  }

  // ── sparkline helpers ──
  function makeSparkBars(svgId, data, keyEarned, keyPaid, colorE, colorP){
    const svg = $(svgId);
    if(!data || data.length === 0) return;
    svg.innerHTML = '';
    const W=300, H=55, PAD=4, n=data.length;
    const bw = (W - PAD*(n+1)) / n;
    const maxV = Math.max(...data.map(d=>(d[keyEarned]||0)+(d[keyPaid]||0)), 1);
    data.forEach((d,i)=>{
      const e = d[keyEarned]||0, p = d[keyPaid]||0;
      const xb = PAD + i*(bw+PAD);
      const he = (e/maxV)*(H-8);
      const hp = (p/maxV)*(H-8);
      // earned bar
      if(he > 0){
        const r = document.createElementNS('http://www.w3.org/2000/svg','rect');
        r.setAttribute('x',xb); r.setAttribute('y',H-8-he);
        r.setAttribute('width',bw/2); r.setAttribute('height',he);
        r.setAttribute('fill',colorE); r.setAttribute('rx','1');
        svg.appendChild(r);
      }
      // paid bar
      if(hp > 0){
        const r = document.createElementNS('http://www.w3.org/2000/svg','rect');
        r.setAttribute('x',xb+bw/2); r.setAttribute('y',H-8-hp);
        r.setAttribute('width',bw/2); r.setAttribute('height',hp);
        r.setAttribute('fill',colorP); r.setAttribute('rx','1');
        svg.appendChild(r);
      }
    });
    // legend
    const lg1 = document.createElementNS('http://www.w3.org/2000/svg','rect');
    lg1.setAttribute('x',2);lg1.setAttribute('y',1);lg1.setAttribute('width',6);lg1.setAttribute('height',4);lg1.setAttribute('fill',colorE);
    const lg2 = document.createElementNS('http://www.w3.org/2000/svg','rect');
    lg2.setAttribute('x',38);lg2.setAttribute('y',1);lg2.setAttribute('width',6);lg2.setAttribute('height',4);lg2.setAttribute('fill',colorP);
    const t1 = document.createElementNS('http://www.w3.org/2000/svg','text');
    t1.setAttribute('x',10);t1.setAttribute('y',6);t1.setAttribute('fill','rgba(136,136,170,.8)');t1.setAttribute('font-size','5');t1.textContent='Ganadas';
    const t2 = document.createElementNS('http://www.w3.org/2000/svg','text');
    t2.setAttribute('x',46);t2.setAttribute('y',6);t2.setAttribute('fill','rgba(136,136,170,.8)');t2.setAttribute('font-size','5');t2.textContent='Pagadas';
    svg.appendChild(lg1);svg.appendChild(t1);svg.appendChild(lg2);svg.appendChild(t2);
  }

  function makeSparkLine(svgId, data, key, color){
    const svg = $(svgId);
    if(!data || data.length === 0) return;
    svg.innerHTML = '';
    const W=300, H=55, PAD=6;
    const vals = data.map(d=>d[key]||0);
    const maxV = Math.max(...vals, 1);
    const pts = vals.map((v,i)=>{
      const x = PAD + (i/(vals.length-1||1))*(W-2*PAD);
      const y = H-PAD - (v/maxV)*(H-2*PAD);
      return `${x},${y}`;
    });
    const area = document.createElementNS('http://www.w3.org/2000/svg','polyline');
    area.setAttribute('points', pts.join(' '));
    area.setAttribute('fill','none');
    area.setAttribute('stroke',color);
    area.setAttribute('stroke-width','1.5');
    area.setAttribute('stroke-linejoin','round');
    svg.appendChild(area);
    // dot at last point
    const last = pts[pts.length-1].split(',');
    const dot = document.createElementNS('http://www.w3.org/2000/svg','circle');
    dot.setAttribute('cx',last[0]);dot.setAttribute('cy',last[1]);dot.setAttribute('r','3');
    dot.setAttribute('fill',color);
    svg.appendChild(dot);
  }

  makeSparkBars('spark-daily', S.daily, 'fees_earned_msat', 'fees_paid_msat', '#00ff88', '#ffcc00');
  makeSparkLine('spark-hourly', S.hourly, 'fwd_count_delta', '#00dcff');

  // ── AUTO-RELOAD: recarga la página cada 5 minutos para mostrar stats frescos ──
  (function(){
    var RELOAD_SECS = 300; // 5 minutos, sincronizado con el colector
    var remaining = RELOAD_SECS;

    // Crear indicador en el top bar
    var indEl = document.createElement('div');
    indEl.className = 'top-seg';
    indEl.style.cssText = 'border-left:1px solid rgba(0,220,255,.2);min-width:72px;';
    indEl.innerHTML = '<span class="lbl">REFRESCO</span><span class="val" id="reload-cd" style="font-size:13px;color:var(--sub);">5:00</span>';
    document.getElementById('hud-top').appendChild(indEl);

    var cdEl = document.getElementById('reload-cd');

    setInterval(function(){
      remaining--;
      if(remaining <= 0){
        location.reload();
        return;
      }
      var m = Math.floor(remaining / 60);
      var s = remaining % 60;
      cdEl.textContent = m + ':' + (s < 10 ? '0' : '') + s;
      // Últimos 30s: cambiar color para advertir
      if(remaining <= 30){
        cdEl.style.color = 'var(--amber)';
      }
    }, 1000);
  })();

})();
</script>

<div id="hud-floatip"></div>
<script>
(function(){
  const tip = document.getElementById('hud-floatip');
  if(!tip) return;
  document.querySelectorAll('[data-tip]').forEach(el => {
    el.addEventListener('mouseenter', () => {
      tip.textContent = el.getAttribute('data-tip');
      tip.style.display = 'block';
    });
    el.addEventListener('mousemove', e => {
      let x = e.clientX + 16, y = e.clientY - 12;
      const tw = 270, th = tip.offsetHeight || 120;
      if (x + tw > window.innerWidth)  x = e.clientX - tw - 10;
      if (y + th > window.innerHeight) y = e.clientY - th - 10;
      tip.style.left = x + 'px';
      tip.style.top  = y + 'px';
    });
    el.addEventListener('mouseleave', () => {
      tip.style.display = 'none';
    });
  });
})();
</script>
</body>
</html>
"""


# Genera el panel HTML del Cockpit HUD inyectando metricas y la visualizacion 3D de la red.
def generate_cockpit_html(csv_path: Path = None, cockpit_path: Path = None,
                          my_pubkey: str = None, log_cb=print,
                          skip_graph: bool = False) -> bool:
    """
    Genera el HTML cockpit: red 3D en iframe + paneles HUD con stats de node_history.db.

    Parámetros:
      skip_graph  -- Si True, omite regenerar el grafo 3D (solo actualiza stats).
                     Útil para refrescos automáticos en background sin costo computacional.
    """
    if csv_path is None:
        csv_path = CSV_FILE
    if cockpit_path is None:
        cockpit_path = EXPORTS_DIR / "lightning_cockpit.html"

    graph_html_path = EXPORTS_DIR / "lightning_cockpit_graph.html"

    # 1. Generar (o reutilizar) el grafo 3D
    if skip_graph:
        if not graph_html_path.exists():
            log_cb("[!]  No existe el grafo 3D previo; generando por primera vez...")
            skip_graph = False  # forzar generación inicial
        else:
            log_cb("[REBALANCE]  Reutilizando grafo 3D existente (solo actualizando stats)...")
    if not skip_graph:
        log_cb("[TOOL] Generando visualización 3D base...")
        ok = generate_3d_html(csv_path, graph_html_path, my_pubkey, log_cb)
        if not ok:
            return False

    # 2. Leer estadísticas históricas
    if not skip_graph:
        log_cb("[CHART] Leyendo node_history.db...")
    stats = read_history_stats()

    # 3. Serializar stats como JSON
    stats_json = json.dumps(stats, ensure_ascii=False, default=str)

    # 4. Construir el HTML cockpit
    graph_rel = graph_html_path.name
    html = COCKPIT_HTML.replace(
        "GRAPH_SRC_PLACEHOLDER", graph_rel
    ).replace(
        "__STATS_JSON__", stats_json
    )

    cockpit_path.parent.mkdir(parents=True, exist_ok=True)
    cockpit_path.write_text(html, encoding="utf-8")
    if not skip_graph:
        log_cb(f"[OK] Cockpit guardado: {cockpit_path.name}")
    return True

