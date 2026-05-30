#!/usr/bin/env python3
"""
app.py — Lightning Web Dashboard
Servidor Flask que expone la lógica de dashboard_core.py como REST API + SSE.
Puerto: 9630 | Auth: HTTP Basic (WEB_USER / WEB_PASS en .env raíz)
"""

import os
import sys
import json
import queue
import threading
import subprocess
from functools import wraps
from datetime import datetime
from pathlib import Path
from flask import Flask, request, Response, jsonify, send_from_directory

# ── Rutas ─────────────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent.resolve()
STATIC_DIR  = BASE_DIR / "static"

# ── Cargar .env raíz antes de importar core ────────────────────────────────────
def _load_env(path):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip(); v = v.strip().strip("'\"")
        if k and k not in os.environ:
            os.environ[k] = v

_load_env(BASE_DIR / ".env")

# ── Importar core ──────────────────────────────────────────────────────────────
# dashboard_core.py está copiado en este mismo directorio.
# BASE_DIR apunta a satoshi-odyssey/, por lo que data/, exports/, backups/ son propios.
import dashboard_core as core

# ── Credenciales ────────────────────────────────────────────────────────────────
WEB_USER = os.environ.get("WEB_USER", "admin")
WEB_PASS = os.environ.get("WEB_PASS", "lightning")
WEB_PORT = int(os.environ.get("WEB_PORT", "9630"))
WEB_HOST = os.environ.get("WEB_HOST", "0.0.0.0")

# ── Flask App ──────────────────────────────────────────────────────────────────
app = Flask(__name__, static_folder=str(STATIC_DIR))

# Decorador que protege las rutas requiriendo autenticacion HTTP Basica.
def requires_auth(f):
    @wraps(f)
    # Funcion interna (closure) del decorador de autenticacion.
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or auth.username != WEB_USER or auth.password != WEB_PASS:
            return Response(
                "Acceso denegado. Autenticación requerida.",
                401,
                {"WWW-Authenticate": 'Basic realm="Lightning Dashboard"'}
            )
        return f(*args, **kwargs)
    return decorated


# ── Utilidad SSE ───────────────────────────────────────────────────────────────
def sse_stream(generator_fn, *args, **kwargs):
    """Ejecuta generator_fn(log_cb) en un thread y hace stream SSE de cada línea."""
    q = queue.Queue()
    done_sentinel = "__DONE__"

    # Callback interno para inyectar mensajes en la cola del stream SSE.
    def log_cb(msg):
        q.put(msg)

    # Hilo en segundo plano que ejecuta la tarea asincrona para el SSE.
    def worker():
        try:
            generator_fn(log_cb, *args, **kwargs)
        except Exception as e:
            q.put(f"[ERROR] {e}")
        finally:
            q.put(done_sentinel)

    threading.Thread(target=worker, daemon=True).start()

    # Yields (emite) progresivamente los mensajes desde la cola para el stream SSE.
    def event_generator():
        while True:
            msg = q.get()
            if msg == done_sentinel:
                yield "data: __END__\n\n"
                break
            yield f"data: {msg}\n\n"

    return Response(event_generator(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# Ejecuta un script bash y emite su salida en vivo a traves de un stream SSE.
def sse_subprocess(script_path, env_extra=None):
    """Ejecuta un script .sh y hace stream de su stdout por SSE."""
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)

    # Yields (emite) progresivamente los mensajes desde la cola para el stream SSE.
    def event_generator():
        try:
            proc = subprocess.Popen(
                ["bash", str(script_path)],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, env=env
            )
            for line in proc.stdout:
                yield f"data: {line.rstrip()}\n\n"
            proc.wait()
            yield f"data: [FIN] Proceso terminado (código {proc.returncode})\n\n"
        except Exception as e:
            yield f"data: [ERROR] {e}\n\n"
        finally:
            yield "data: __END__\n\n"

    return Response(event_generator(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# =============================================================================
# RUTAS ESTÁTICAS
# =============================================================================

@app.route("/")
@requires_auth
# Sirve la pagina web principal (SPA) del frontend en HTML.
def index():
    return send_from_directory(STATIC_DIR, "index.html")

@app.route("/static/<path:filename>")
@requires_auth
# Sirve los archivos estaticos auxiliares (CSS, JS) ubicados en el directorio static.
def static_files(filename):
    return send_from_directory(STATIC_DIR, filename)

@app.route("/exports/<path:filename>")
@requires_auth
# Sirve los archivos exportados generados (como el html 3D del cockpit).
def serve_exports(filename):
    return send_from_directory(str(core.EXPORTS_DIR), filename)

@app.route("/images/<path:filename>")
@requires_auth
# Sirve las imagenes y recursos graficos locales para el frontend.
def serve_images(filename):
    return send_from_directory(str(BASE_DIR / "images"), filename)


# =============================================================================
# API — NODO
# =============================================================================

@app.route("/api/node/info")
@requires_auth
# Endpoint: Devuelve la informacion tecnica y publica del nodo desde getinfo.
def api_node_info():
    data = core.get_node_info()
    return jsonify(data or {})

@app.route("/api/node/metrics")
@requires_auth
# Endpoint: Lee y devuelve las ultimas metricas consolidadas desde la base de datos.
def api_node_metrics():
    return jsonify(core.read_history_stats())

@app.route("/api/gamification/status")
@requires_auth
# Endpoint: Devuelve el estado del sistema de gamificacion: XP, rango, salud, logros y records.
def api_gamification_status():
    return jsonify(core.read_gamification_status())


# =============================================================================
# API — RED
# =============================================================================

@app.route("/api/network/scan")
@requires_auth
# Endpoint: Dispara el escaneo de red profundo devolviendo el progreso via SSE.
def api_network_scan():
    max_hops = request.args.get("hops", "2")
    script = core.SCRIPTS_DIR / "01_scan_network.sh"
    return sse_subprocess(script, {"MAX_HOPS": max_hops})

@app.route("/api/network/stats-snapshot")
@requires_auth
# Endpoint: Ejecuta el script colector de estadisticas y devuelve el estado de exito.
def api_network_stats_snapshot():
    script = core.SCRIPTS_DIR / "04_collect_stats.sh"
    return sse_subprocess(script)

@app.route("/api/network/generate-cockpit")
@requires_auth
# Endpoint: Fuerza la regeneracion del HTML interactivo del cockpit 3D.
def api_network_generate_cockpit():
    my_pubkey = request.args.get("pubkey", None)

    # Hilo interno de ejecucion asincrona para no colgar o bloquear peticiones HTTP de red.
    def run(log_cb):
        cockpit_path = core.EXPORTS_DIR / "lightning_cockpit.html"
        ok = core.generate_cockpit_html(
            csv_path=core.CSV_FILE,
            cockpit_path=cockpit_path,
            my_pubkey=my_pubkey,
            log_cb=log_cb,
        )
        if ok:
            log_cb(f"[OK] Cockpit listo: /exports/lightning_cockpit.html")
        else:
            log_cb("[ERROR] No se pudo generar el cockpit.")

    return sse_stream(run)


# =============================================================================
# API — WALLET ON-CHAIN
# =============================================================================

@app.route("/api/wallet/balance")
@requires_auth
# Endpoint: Devuelve el saldo on-chain desglosado (confirmado y pendiente).
def api_wallet_balance():
    return jsonify(core.get_wallet_balance_detail())

@app.route("/api/wallet/utxos")
@requires_auth
# Endpoint: Devuelve la lista detallada de UTXOs disponibles en el nodo.
def api_wallet_utxos():
    return jsonify(core.get_wallet_utxos(min_confs=0))

@app.route("/api/wallet/newaddress", methods=["POST"])
@requires_auth
# Endpoint: Crea y devuelve una nueva direccion de la wallet (por defecto p2wkh).
def api_wallet_newaddress():
    addr_type = request.json.get("type", "p2wkh")
    addr = core.get_new_address(addr_type)
    return jsonify({"address": addr})

@app.route("/api/wallet/scb-status")
@requires_auth
# Endpoint: Retorna el estado y la fecha del ultimo Static Channel Backup (SCB).
def api_wallet_scb_status():
    import time as _t
    auto_path = core.get_scb_auto_path()
    backups = sorted(core.BACKUPS_DIR.glob("channel_backup_*.bin")) if core.BACKUPS_DIR.exists() else []
    auto_info = {}
    if auto_path.exists():
        age_h = (_t.time() - auto_path.stat().st_mtime) / 3600
        auto_info = {"path": str(auto_path), "age_hours": round(age_h, 1)}
    manual_info = {}
    if backups:
        last = backups[-1]
        age_h = (_t.time() - last.stat().st_mtime) / 3600
        manual_info = {"name": last.name, "age_hours": round(age_h, 1)}
    return jsonify({"auto": auto_info, "manual": manual_info})

@app.route("/api/wallet/scb-export", methods=["POST"])
@requires_auth
# Endpoint: Fuerza una exportacion explicita del Static Channel Backup.
def api_wallet_scb_export():
    fname = f"channel_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.bin"
    out_path = core.BACKUPS_DIR / fname
    log_lines = []
    ok = core.export_channel_backup(out_path, lambda m: log_lines.append(m))
    return jsonify({"ok": ok, "file": fname, "log": log_lines})

@app.route("/api/wallet/scb-download/<filename>", methods=["GET"])
@requires_auth
# Endpoint: Permite descargar un archivo de backup SCB específico.
def api_wallet_scb_download(filename):
    import os
    safe_name = os.path.basename(filename)
    file_path = core.BACKUPS_DIR / safe_name
    if not file_path.exists():
        return jsonify({"ok": False, "error": "Archivo no encontrado"}), 404
    return send_from_directory(core.BACKUPS_DIR, safe_name, as_attachment=True)

@app.route("/api/wallet/consolidate", methods=["POST"])
@requires_auth
# Endpoint: Inicia la consolidacion de UTXOs hacia una direccion devolviendo logs via SSE.
def api_wallet_consolidate():
    data = request.json
    dest = data.get("dest_addr", "")
    fee  = int(data.get("sat_per_vbyte", 2))
    if not dest:
        return jsonify({"ok": False, "error": "Dirección de destino requerida"}), 400

    # Hilo interno de ejecucion asincrona para no colgar o bloquear peticiones HTTP de red.
    def run(log_cb):
        core.execute_consolidate_utxos(dest, fee, log_cb)

    return sse_stream(run)


# =============================================================================
# API — CANALES
# =============================================================================

@app.route("/api/channels/list")
@requires_auth
# Endpoint: Devuelve la lista estructurada de canales abiertos y activos.
def api_channels_list():
    return jsonify(core.get_channels())

@app.route("/api/channels/all")
@requires_auth
# Endpoint: Devuelve todos los canales (abiertos, cerrados, pendientes) para administracion.
def api_channels_all():
    return jsonify(core.get_all_channels())

@app.route("/api/channels/candidates")
@requires_auth
# Endpoint: Devuelve sugerencias de prospectos estables para abrir nuevos canales.
def api_channels_candidates():
    min_c = int(request.args.get("min_channels", 2))
    max_d = int(request.args.get("max_days", 30))
    return jsonify(core.get_channel_candidates(min_channels=min_c, max_days=max_d))

@app.route("/api/channels/suggestions")
@requires_auth
# Endpoint: Devuelve canales sugeridos y aptos para aplicar un rebalanceo circular.
def api_channels_suggestions():
    channels = core.get_channels()
    target   = int(request.args.get("target_ratio", core.TARGET_RATIO))
    sugs = core.suggest_rebalances(channels, target_ratio=target)
    return jsonify(sugs)

@app.route("/api/channels/connect", methods=["POST"])
@requires_auth
# Endpoint: Ordena al nodo conectarse (lncli connect) con un peer remoto.
def api_channels_connect():
    uri = request.json.get("uri", "")
    if not uri:
        return jsonify({"ok": False, "error": "URI requerida"}), 400
    log_lines = []
    ok = core.execute_connect(uri, lambda m: log_lines.append(m))
    return jsonify({"ok": ok, "log": log_lines})

@app.route("/api/channels/open", methods=["POST"])
@requires_auth
# Endpoint: Abre un canal hacia un peer con logs transmitidos por SSE.
def api_channels_open():
    data     = request.json
    pubkey   = data.get("pubkey", "")
    amt      = int(data.get("amt_sats", 0))
    push_amt = int(data.get("push_amt", 0))
    host_uri = data.get("host_uri", None)
    if not pubkey or amt <= 0:
        return jsonify({"ok": False, "error": "Pubkey y monto requeridos"}), 400

    # Hilo interno de ejecucion asincrona para no colgar o bloquear peticiones HTTP de red.
    def run(log_cb):
        core.execute_openchannel(pubkey, amt, log_cb, host_uri=host_uri, push_amt=push_amt)

    return sse_stream(run)

@app.route("/api/channels/close", methods=["POST"])
@requires_auth
# Endpoint: Inicia el cierre (cooperativo o force) de un canal usando SSE.
def api_channels_close():
    data      = request.json
    chan_point = data.get("chan_point", "")
    force     = bool(data.get("force", False))
    if not chan_point:
        return jsonify({"ok": False, "error": "chan_point requerido"}), 400

    # Hilo interno de ejecucion asincrona para no colgar o bloquear peticiones HTTP de red.
    def run(log_cb):
        core.execute_closechannel(chan_point, force, log_cb)

    return sse_stream(run)


# =============================================================================
# API — REBALANCEO
# =============================================================================

@app.route("/api/rebalance/simulate", methods=["POST"])
@requires_auth
# Endpoint: Corre un dry-run del rebalanceo para calcular fees sin mover fondos.
def api_rebalance_simulate():
    data    = request.json
    amt     = int(data.get("amt_sats", 0))
    max_fee = int(data.get("max_fee_sats", core.DEFAULT_MAX_FEE_SATS))
    max_ppm = int(data.get("max_fee_ppm", core.DEFAULT_MAX_FEE_PPM))
    return jsonify(core.fee_analysis(amt, max_fee, max_ppm))

@app.route("/api/rebalance/execute", methods=["POST"])
@requires_auth
# Endpoint: Ejecuta el pago auto-enrutado de rebalanceo usando SSE para los logs.
def api_rebalance_execute():
    data     = request.json
    from_scid = data.get("from_scid", "")
    to_pub    = data.get("to_pub", "")
    amt       = int(data.get("amt_sats", 0))
    max_fee   = int(data.get("max_fee_sats", core.DEFAULT_MAX_FEE_SATS))
    if not from_scid or not to_pub or amt <= 0:
        return jsonify({"ok": False, "error": "from_scid, to_pub y amt_sats requeridos"}), 400

    # Hilo interno de ejecucion asincrona para no colgar o bloquear peticiones HTTP de red.
    def run(log_cb):
        core.execute_rebalance(from_scid, to_pub, amt, max_fee, log_cb)

    return sse_stream(run)


# ── Estado del autopiloto (en memoria por sesión de servidor) ──────────────
# _autopilot_active: True mientras el bucle del bot está corriendo en el thread.
# _autopilot_lock:   Mutex para leer/escribir _autopilot_active de forma segura.
_autopilot_active = False
_autopilot_lock   = threading.Lock()

@app.route("/api/rebalance/autopilot/status")
@requires_auth
def api_autopilot_status():
    """Devuelve si el piloto automático está activo en este momento.
    El frontend lo consulta al iniciar para sincronizar el estado visual."""
    return jsonify({"active": _autopilot_active})

@app.route("/api/rebalance/autopilot/stop", methods=["POST"])
@requires_auth
def api_autopilot_stop():
    """Envía la señal de parada al thread del piloto y espera hasta 3s
    a que el flag se limpie, garantizando que el estado sea False al responder."""
    global _autopilot_active
    with _autopilot_lock:
        _autopilot_active = False
    # Esperar hasta 3 segundos a que el thread limpie su propio estado
    import time as _t
    for _ in range(30):
        with _autopilot_lock:
            if not _autopilot_active:
                break
        _t.sleep(0.1)
    return jsonify({"ok": True, "active": False})

@app.route("/api/rebalance/autopilot", methods=["POST"])
@requires_auth
def api_rebalance_autopilot():
    """
    Piloto automático experimental de rebalanceo (SSE).
    Ejecuta ciclos periódicos: calcula sugerencias, elige una aleatoriamente
    entre las top-10 y ejecuta el rebalanceo con reintentos escalando el fee.

    IMPORTANTE: Este endpoint bloquea la conexión HTTP mientras el bot corre.
    El cliente puede detenerlo cerrando la conexión o enviando POST /stop.
    Si el cliente recarga la página, Flask detecta el cierre del generador SSE
    (GeneratorExit) y el finally garantiza que _autopilot_active vuelva a False.
    """
    import random, time as _time

    global _autopilot_active

    data        = request.json or {}
    amt         = int(data.get("amt_sats",    1000))
    base_fee    = int(data.get("max_fee_sats", 1))
    target      = int(data.get("target_ratio", core.TARGET_RATIO))
    interval_s  = max(60, int(data.get("interval_secs", 300)))  # mín. 1 min
    max_retries = 15

    with _autopilot_lock:
        if _autopilot_active:
            return jsonify({"ok": False, "error": "Autopiloto ya está activo"}), 409
        _autopilot_active = True

    # Cola compartida entre el thread del bot y el generador SSE.
    # El generador SSE vive en el contexto de la petición HTTP (hilo de Flask).
    q = queue.Queue()
    DONE = object()  # Sentinel único para señalar fin de stream

    def log_cb(msg):
        """Encola un mensaje de log para enviarlo al cliente por SSE."""
        q.put(msg)

    def worker():
        """Thread del bot: ejecuta el bucle de rebalanceo y maneja paradas seguras."""
        global _autopilot_active
        cycle = 0
        try:
            log_cb(f"[BOT] Piloto Automático ACTIVADO — amt={amt:,} sats | fee_base={base_fee} | intervalo={interval_s//60} min")
            while True:
                with _autopilot_lock:
                    if not _autopilot_active:
                        log_cb("[BOT] Piloto detenido por el usuario.")
                        break

                cycle += 1
                now_str = datetime.now().strftime("%H:%M:%S")
                log_cb(f"\n[BOT] ── Ciclo #{cycle} ({now_str}) ─────────────")

                channels = core.get_channels()
                if not channels:
                    log_cb("[BOT] [!] No hay canales activos. Reintentando en el próximo ciclo.")
                else:
                    sugs = core.suggest_rebalances(channels, target_ratio=target)
                    if not sugs:
                        log_cb("[BOT] [!] No hay rutas de rebalanceo viables ahora.")
                    else:
                        top = sugs[:10]
                        chosen = random.choice(top)
                        fscid = chosen["from_scid"]
                        tpub  = chosen["to_pub"]
                        log_cb(f"[BOT] Selección aleatoria: FROM {fscid} → TO {tpub[:15]}...")

                        current_fee = base_fee
                        for attempt in range(1, max_retries + 1):
                            with _autopilot_lock:
                                if not _autopilot_active:
                                    log_cb("[BOT] Detenido durante ejecución.")
                                    return
                            log_cb(f"[BOT] [Intento {attempt}/{max_retries}] {amt:,} sats | fee máx: {current_fee} sats")
                            ok = core.execute_rebalance(fscid, tpub, amt, current_fee, log_cb)
                            if ok:
                                log_cb(f"[BOT] [OK] Rebalanceo exitoso con fee={current_fee} sats.")
                                break
                            else:
                                if attempt < max_retries:
                                    current_fee += 1
                                    log_cb(f"[BOT] Falló. Incrementando fee a {current_fee} sats...")
                                else:
                                    log_cb(f"[BOT] [!] {max_retries} intentos agotados. Se pasa al siguiente ciclo.")

                # Espera entre ciclos verificando stop cada 5 seg
                log_cb(f"[BOT] Próximo ciclo en {interval_s//60} min {interval_s%60:02d} seg...")
                waited = 0
                while waited < interval_s:
                    _time.sleep(5)
                    waited += 5
                    with _autopilot_lock:
                        if not _autopilot_active:
                            log_cb("[BOT] Piloto detenido durante espera.")
                            return
        finally:
            # Garantizar limpieza del flag aunque el thread muera por cualquier causa
            with _autopilot_lock:
                _autopilot_active = False
            log_cb("[BOT] Piloto Automático DESACTIVADO.")
            q.put(DONE)  # Notificar al generador SSE que termine

    threading.Thread(target=worker, daemon=True).start()

    def event_generator():
        """Generador SSE: retransmite los mensajes del thread al navegador.
        Si el cliente cierra la conexión (recarga, navega fuera), Python lanza
        GeneratorExit aqui, lo que detiene el flujo. El thread detecta
        _autopilot_active=False en su próxima iteración y termina limpiamente."""
        global _autopilot_active
        try:
            while True:
                msg = q.get()
                if msg is DONE:
                    yield "data: __END__\n\n"
                    break
                yield f"data: {msg}\n\n"
        except GeneratorExit:
            # El cliente se fue (recargó página, cerró pestaña, etc.)
            # Marcar el flag para que el thread termine en su próximo chequeo.
            with _autopilot_lock:
                _autopilot_active = False

    return Response(
        event_generator(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    print(f"⚡ Lightning Web Dashboard arrancando en http://{WEB_HOST}:{WEB_PORT}")
    print(f"   Usuario: {WEB_USER} | Red: {core.NETWORK}")
    print(f"   Ctrl+C para detener.")
    app.run(host=WEB_HOST, port=WEB_PORT, debug=False, threaded=True)
