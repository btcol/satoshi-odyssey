/* ============================================================
   Lightning Web Dashboard — app.js
   Lógica frontend: tabs, API calls, SSE streams, tablas
   ============================================================ */

'use strict';

// ── Utilidades generales ───────────────────────────────────────────────────

const $ = id => document.getElementById(id);
const fmtSats = n => { n = parseInt(n)||0; return n>=1e6?(n/1e6).toFixed(2)+'M':n>=1e3?(n/1e3).toFixed(1)+'k':String(n); };
const fmtMsat = n => { n = parseInt(n)||0; return fmtSats(Math.round(n/1000)); };

// Copiar texto al portapapeles de forma robusta en entornos seguros (HTTPS/localhost) y no seguros (HTTP local)
function copyTextToClipboard(text) {
  if (navigator.clipboard && window.isSecureContext) {
    return navigator.clipboard.writeText(text);
  } else {
    const textArea = document.createElement("textarea");
    textArea.value = text;
    textArea.style.position = "fixed";
    textArea.style.top = "0";
    textArea.style.left = "0";
    textArea.style.width = "2em";
    textArea.style.height = "2em";
    textArea.style.padding = "0";
    textArea.style.border = "none";
    textArea.style.outline = "none";
    textArea.style.boxShadow = "none";
    textArea.style.background = "transparent";
    document.body.appendChild(textArea);
    textArea.focus();
    textArea.select();
    try {
      const successful = document.execCommand('copy');
      document.body.removeChild(textArea);
      if (successful) return Promise.resolve();
      return Promise.reject(new Error('Fallback copy failed'));
    } catch (err) {
      document.body.removeChild(textArea);
      return Promise.reject(err);
    }
  }
}


/**
 * Convierte los emojis de texto de un elemento DOM en imágenes SVG usando Twemoji.
 * Garantiza renderizado correcto en Linux / sistemas sin fuentes emoji instaladas.
 * Si Twemoji no está disponible (sin internet), no hace nada (degradación elegante).
 *
 * @param {Element|null} el - Elemento raíz a procesar. Si es null, procesa document.body.
 */
function twemojiParse(el) {
  if (typeof twemoji === 'undefined') return; // Twemoji no cargó (sin conexión)
  twemoji.parse(el || document.body, {
    folder: 'svg',
    ext: '.svg',
    // Estilo inline para que los SVG no rompan el layout
    callback: (icon, opts) =>
      `${opts.base}${opts.size}/${icon}${opts.ext}`,
  });
}


// Muestra una pequeña notificacion emergente (toast) en la esquina de la pantalla.
function toast(msg, type='info') {
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.textContent = msg;
  $('toast-container').appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

// Añade una nueva linea de texto al final de un contenedor de logs especifico.
function logAppend(boxId, msg, cls='') {
  const box = $(boxId);
  if (!box) return;
  const line = document.createElement('span');
  if (cls) line.className = cls;
  line.textContent = msg + '\n';
  box.appendChild(line);
  box.scrollTop = box.scrollHeight;
}

// Limpia todo el contenido de texto dentro de una caja de logs.
function logClear(boxId) {
  const box = $(boxId); if (box) box.innerHTML = '';
}

// Wrapper asincrono para fetch que maneja errores HTTP y parsea automaticamente JSON.
async function apiFetch(url, opts={}) {
  try {
    const r = await fetch(url, opts);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  } catch(e) {
    toast('Error: ' + e.message, 'err');
    return null;
  }
}

// Establece una conexion Server-Sent Events (SSE) y canaliza los mensajes a un logbox.
function sseConnect(url, logBoxId, onEnd) {
  const es = new EventSource(url);
  es.onmessage = e => {
    if (e.data === '__END__') { es.close(); if(onEnd) onEnd(); return; }
    const cls = e.data.includes('[ERROR]')||e.data.includes('[!]') ? 'log-err'
              : e.data.includes('[OK]') ? 'log-ok' : '';
    logAppend(logBoxId, e.data, cls);
  };
  es.onerror = () => { es.close(); logAppend(logBoxId,'[SSE desconectado]','log-warn'); if(onEnd) onEnd(); };
  return es;
}

// ── Navegación de pestañas ─────────────────────────────────────────────────

let cockpitGenerated = false;

document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    $('tab-' + btn.dataset.tab).classList.add('active');
    // Al entrar en cockpit, generar automáticamente si no se ha generado aún
    if (btn.dataset.tab === 'cockpit' && !cockpitGenerated) generateCockpit();
    // Al entrar en rebalanceo, sincronizar el estado del autopiloto con el servidor
    // (resuelve el desfase visual tras recargar la página con el bot activo)
    if (btn.dataset.tab === 'rebalance') syncBotStatus();
    // Al entrar en watchtower, cargar su estado
    if (btn.dataset.tab === 'watchtower') loadWatchtowerStatus();
    // Al entrar en aventura, cargar su estado
    if (btn.dataset.tab === 'aventura') loadGameStatus();
  });
});

// Solicita al backend la regeneracion del mapa 3D y lo carga en el iframe.
function generateCockpit() {
  logAppend('log-network', '[Cockpit] Generando HUD...');
  fetch(`/api/network/generate-cockpit?pubkey=${myPubkey||''}`)
    .then(r => {
      const reader = r.body.getReader(); const dec = new TextDecoder();
      function read() { reader.read().then(({done, value}) => {
        if (done) return;
        dec.decode(value).split('\n').forEach(l => {
          const m = l.replace(/^data: /,'').trim();
          if (!m) return;
          if (m === '__END__') {
            // Mostrar iframe, ocultar placeholder
            $('cockpit-placeholder').style.display = 'none';
            $('cockpit-iframe').style.display = 'block';
            $('cockpit-iframe').src = `/exports/lightning_cockpit.html?t=${Date.now()}`;
            cockpitGenerated = true;
            logAppend('log-network', '[Cockpit] HUD listo.', 'log-ok');
            return;
          }
          const cls = m.includes('[ERROR]') ? 'log-err' : m.includes('[OK]') ? 'log-ok' : '';
          logAppend('log-network', m, cls);
        });
        read();
      }); }
      read();
    }).catch(e => logAppend('log-network', '[Cockpit ERROR] '+e, 'log-err'));
}

// ── Estado global del nodo ─────────────────────────────────────────────────

let myPubkey = null;

// Llama al endpoint de info del nodo para verificar si LND esta online y poblar datos basicos.
async function detectNode() {
  const info = await apiFetch('/api/node/info');
  const el = $('node-status');
  if (info && info.identity_pubkey) {
    myPubkey = info.identity_pubkey;
    el.textContent = `[*] ${info.alias}  |  ${myPubkey.slice(0,16)}...  |  Altura: ${info.block_height}  |  Red: ${info.chains?.[0]?.network||'?'}`;
    el.className = 'online';
    // versión desde git (aproximada: usamos la fecha de buildtime del servidor)
    $('btcol-version').textContent = `Red: ${info.chains?.[0]?.network||'?'} | Alias: ${info.alias}`;
  } else {
    el.textContent = 'Nodo no detectado. ¿Está LND en ejecución?';
    el.className = 'offline';
  }
}

// ── TAB: Red & Cockpit ────────────────────────────────────────────────────

// Trae las estadisticas historicas y de salud del nodo para actualizar la grilla superior.
async function loadMetrics() {
  const s = await apiFetch('/api/node/metrics');
  if (!s) return;
  const snap = s.snap || {};
  const chA = snap.channels_active ?? '—', chT = snap.channels_total ?? '?';
  const liq  = snap.liquidity_ratio != null ? snap.liquidity_ratio.toFixed(1)+'%' : '—';
  const net7 = s.net_profit_7d_msat || 0;

  $('m-channels').textContent = `${chA} / ${chT}`;
  $('m-liq').textContent      = liq;
  $('m-cap').textContent      = fmtSats(snap.capacity_total || 0) + ' sats';
  $('m-earned').textContent   = fmtMsat(snap.fwd_fees_cum_msat || 0) + ' sat';
  $('m-paid').textContent     = fmtMsat(snap.payments_fees_cum_msat || 0) + ' sat';
  $('m-zombies').textContent  = snap.zombie_channels ?? '—';
  $('m-uptime').textContent   = (s.uptime_pct_7d || 0).toFixed(1) + '%';

  const netEl = $('m-net');
  netEl.textContent  = (net7 >= 0 ? '+' : '') + fmtMsat(Math.abs(net7)) + ' sat';
  netEl.className    = 'metric-val ' + (net7 >= 0 ? 'green' : 'red');
  const zmb = parseInt(snap.zombie_channels || 0);
  $('m-zombies').className = 'metric-val ' + (zmb > 0 ? 'red' : 'green');
}

$('btn-refresh-metrics').addEventListener('click', loadMetrics);

// ── Auto-escaneo ────────────────────────────────────────────
let autoScanTimer   = null;
let autoScanCountdown = 0;
let autoScanTicker  = null;

// Dispara el barrido de red profundo y maneja su conexion de streaming (SSE).
function runAutoScan() {
  if ($('btn-scan').disabled) return; // ya hay un escaneo en curso
  const hops = $('hops-select').value;
  $('btn-scan').disabled = true;
  logClear('log-network');
  logAppend('log-network', '[AUTO] Escaneo automático iniciado...');
  const es = new EventSource(`/api/network/scan?hops=${hops}`);
  es.onmessage = e => {
    if (e.data === '__END__') { 
      es.close(); 
      $('btn-scan').disabled = false; 
      logAppend('log-network', '[AUTO] Registrando snapshot de métricas...', 'log-info');
      // Registrar snapshot en la DB histórica
      sseConnect('/api/network/stats-snapshot', 'log-network', () => loadMetrics());
      return; 
    }
    const cls = e.data.includes('[ERROR]') ? 'log-err' : e.data.includes('[OK]') ? 'log-ok' : '';
    logAppend('log-network', e.data, cls);
  };
  es.onerror = () => { es.close(); $('btn-scan').disabled = false; };
}

// Inicia el temporizador (interval) que ejecuta el auto-escaneo ciclicamente.
function startAutoScan() {
  const secs = parseInt($('auto-scan-interval').value);
  autoScanCountdown = secs;
  if (autoScanTicker) clearInterval(autoScanTicker);
  autoScanTicker = setInterval(() => {
    autoScanCountdown--;
    const m = Math.floor(autoScanCountdown / 60), s = autoScanCountdown % 60;
    $('auto-scan-next').textContent = `Próximo: ${m}:${s.toString().padStart(2,'0')}`;
    if (autoScanCountdown <= 0) {
      autoScanCountdown = secs;
      runAutoScan();
    }
  }, 1000);
  $('auto-scan-next').textContent = `Próximo: ${Math.floor(secs/60)}:00`;
}

// Detiene el temporizador de auto-escaneo y limpia el indicador de proximo ciclo.
function stopAutoScan() {
  if (autoScanTicker) { clearInterval(autoScanTicker); autoScanTicker = null; }
  $('auto-scan-next').textContent = '';
}

$('auto-scan-toggle').addEventListener('change', e => {
  if (e.target.checked) {
    toast('Auto-escaneo activado', 'info');
    startAutoScan();
  } else {
    toast('Auto-escaneo desactivado', 'info');
    stopAutoScan();
  }
});

// Reiniciar timer si cambia el intervalo con el toggle activo
$('auto-scan-interval').addEventListener('change', () => {
  if ($('auto-scan-toggle').checked) startAutoScan();
});

$('btn-scan').addEventListener('click', () => {
  logClear('log-network');
  const hops = $('hops-select').value;
  $('btn-scan').disabled = true;
  sseConnect(`/api/network/scan?hops=${hops}`, 'log-network', () => {
    $('btn-scan').disabled = false;
    logAppend('log-network', '[INFO] Registrando snapshot de métricas...', 'log-info');
    sseConnect('/api/network/stats-snapshot', 'log-network', () => {
      loadMetrics();
      toast('Escaneo y Snapshot completados', 'ok');
    });
  });
});

// Cockpit: generación automática al entrar al tab (ver navegación de tabs arriba)
// El log del proceso va a log-network en la pestaña Red.

// ── TAB: Wallet ───────────────────────────────────────────────────────────

// Obtiene y muestra los saldos confirmados y pendientes de la wallet on-chain.
async function loadWalletBalance() {
  const d = await apiFetch('/api/wallet/balance');
  if (!d) return;
  $('w-conf').textContent   = (d.confirmed   || 0).toLocaleString() + ' sats';
  $('w-unconf').textContent = (d.unconfirmed || 0).toLocaleString() + ' sats';
  $('w-anchor').textContent = (d.reserved_anchor || 0).toLocaleString() + ' sats';
  const warn = d.confirmed < 50000 ? '[!] Saldo bajo — mantener >= 50,000 sats' : '';
  $('w-warn').textContent = warn;
}

// Solicita la lista de UTXOs disponibles y reconstruye la tabla HTML de la UI.
async function loadUTXOs() {
  const utxos = await apiFetch('/api/wallet/utxos');
  if (!utxos) return;
  const tbody = $('utxo-tbody');
  tbody.innerHTML = '';
  let total = 0;
  utxos.forEach(u => {
    total += u.amount_sat;
    const tr = document.createElement('tr');
    const txid = u.txid.length > 28 ? u.txid.slice(0,14)+'...'+u.txid.slice(-8) : u.txid;
    const confs = u.confirmations > 0 ? u.confirmations : 'mempool';
    const cls = u.confirmations > 0 ? 'td-green' : 'td-amber';
    tr.innerHTML = `<td class="mono">${txid}</td><td>${u.output_index}</td><td class="td-green">${u.amount_sat.toLocaleString()}</td><td class="${cls}">${confs}</td><td class="td-sub">${u.address_type}</td>`;
    tbody.appendChild(tr);
  });
  const warn = utxos.length >= 10 ? ' — [!] Alta fragmentación' : '';
  $('utxo-summary').textContent = `${utxos.length} UTXOs | ${total.toLocaleString()} sats${warn}`;
}

// Pide los metadatos del Static Channel Backup para mostrar su antiguedad.
async function loadSCBStatus() {
  const s = await apiFetch('/api/wallet/scb-status');
  if (!s) return;
  $('scb-auto').textContent   = s.auto?.path   ? `${s.auto.path} (hace ${s.auto.age_hours}h)` : 'No encontrado';
  $('scb-manual').textContent = s.manual?.name ? `${s.manual.name} (hace ${s.manual.age_hours}h)` : 'Ninguno';
}

// Agrupa y ejecuta todas las funciones de refresco de la pestaña Wallet On-chain.
function walletRefreshAll() { loadWalletBalance(); loadUTXOs(); loadSCBStatus(); }

$('btn-wallet-refresh').addEventListener('click', walletRefreshAll);

$('btn-gen-addr').addEventListener('click', async () => {
  const type = $('addr-type').value;
  const d = await apiFetch('/api/wallet/newaddress', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({type}) });
  if (d?.address) { $('new-addr').value = d.address; toast('Dirección generada', 'ok'); }
});

$('btn-copy-addr').addEventListener('click', () => {
  const a = $('new-addr').value;
  if (a) {
    copyTextToClipboard(a)
      .then(() => toast('Copiado', 'ok'))
      .catch(err => {
        console.error('Error al copiar la dirección:', err);
        toast('Error al copiar automáticamente', 'err');
      });
  }
});

$('btn-scb-export').addEventListener('click', async () => {
  const d = await apiFetch('/api/wallet/scb-export', { method:'POST', headers:{'Content-Type':'application/json'}, body:'{}' });
  if (d) {
    if (d.ok) {
      toast('SCB exportado: ' + d.file, 'ok');
      loadSCBStatus();
      // Crear un enlace temporal para forzar la descarga en el navegador del cliente
      const link = document.createElement('a');
      link.href = `/api/wallet/scb-download/${encodeURIComponent(d.file)}`;
      link.download = d.file;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      toast('Copia descargada en tu máquina personal', 'ok');
    } else {
      toast('Error exportando SCB', 'err');
    }
  }
});

$('btn-consolidate').addEventListener('click', () => {
  const dest = $('new-addr').value.trim();
  const fee  = parseInt($('cons-fee').value) || 2;
  if (!dest) { toast('Genera una dirección primero', 'err'); return; }
  if (!confirm(`Consolidar TODOS los UTXOs hacia:\n${dest}\nFee: ${fee} sat/vbyte\n\n¿Continuar?`)) return;
  logClear('log-wallet');
  sseConnect(`/api/wallet/consolidate`, 'log-wallet', () => { walletRefreshAll(); toast('Consolidación enviada','ok'); });
  // consolidate usa POST+SSE: workaround con fetch+ReadableStream
  fetch('/api/wallet/consolidate', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({dest_addr: dest, sat_per_vbyte: fee})
  }).then(r => {
    const reader = r.body.getReader(); const dec = new TextDecoder();
    function read() { reader.read().then(({done,value}) => {
      if(done) { walletRefreshAll(); return; }
      dec.decode(value).split('\n').forEach(l => { const m = l.replace(/^data: /,'').trim(); if(m && m!=='__END__') logAppend('log-wallet', m); });
      read();
    }); }
    read();
  }).catch(e => logAppend('log-wallet','[ERROR] '+e,'log-err'));
});

// ── TAB: Rebalanceo ───────────────────────────────────────────────────────

let currentSugs = [];

$('ratio-slider').addEventListener('input', e => {
  const v = e.target.value;
  $('ratio-display').textContent = `${v}/${100-v}`;
  localStorage.setItem('reb_ratio', v);
});

['bot-amt', 'bot-fee', 'bot-interval'].forEach(id => {
  $(id).addEventListener('input', e => localStorage.setItem('reb_' + id, e.target.value));
});

$('btn-calc-sugs').addEventListener('click', async () => {
  const ratio = $('ratio-slider').value;
  const sugs = await apiFetch(`/api/channels/suggestions?target_ratio=${ratio}`);
  if (!sugs) return;
  currentSugs = sugs;
  const tbody = $('sug-tbody');
  tbody.innerHTML = '';
  if (!sugs.length) { tbody.innerHTML = '<tr><td colspan="5" class="td-sub">Sin sugerencias. Verifica que haya canales activos.</td></tr>'; return; }
  sugs.forEach((s, i) => {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td class="td-green">${s.amount.toLocaleString()}</td><td class="mono">${s.from_scid}</td><td class="mono">${s.to_scid}</td><td class="td-alias">${s.from_peer}</td><td class="td-alias">${s.to_peer}</td>`;
    tr.addEventListener('click', () => {
      $('reb-from-scid').value = s.from_scid;
      $('reb-to-scid').value   = s.to_scid;
      $('reb-to-pub').value    = s.to_pub;
      $('reb-amt').value       = s.amount;
      tbody.querySelectorAll('tr').forEach(r => r.classList.remove('selected'));
      tr.classList.add('selected');
    });
    tbody.appendChild(tr);
  });
});

$('btn-simulate').addEventListener('click', async () => {
  const body = { amt_sats: parseInt($('reb-amt').value)||0, max_fee_sats: parseInt($('reb-fee').value)||100, max_fee_ppm: parseInt($('reb-ppm').value)||1000 };
  const res = await apiFetch('/api/rebalance/simulate', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body) });
  if (!res) return;
  logAppend('log-rebalance', `--- Análisis de Rentabilidad ---`);
  logAppend('log-rebalance', `Fee esperado @ ${body.max_fee_ppm} ppm: ${res.fee_estimado?.toFixed(1)} sats`);
  logAppend('log-rebalance', `PPM si pagas el máximo: ${res.fee_ppm_if_max?.toFixed(0)} ppm`);
  logAppend('log-rebalance', res.ok ? '[OK] Fee dentro del límite.' : '[!] Fee supera el límite.', res.ok?'log-ok':'log-warn');
});

// Realiza una peticion POST via Fetch pero consumiendo la respuesta como un stream SSE (ReadableStream).
function ssePost(endpoint, payload, logBoxId, onEnd) {
  fetch(endpoint, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) })
    .then(r => {
      const reader = r.body.getReader(); const dec = new TextDecoder();
      function read() { reader.read().then(({done, value}) => {
        if(done) { if(onEnd) onEnd(); return; }
        dec.decode(value).split('\n').forEach(l => {
          const m = l.replace(/^data: /,'').trim();
          if (!m || m === '__END__') { if(m==='__END__' && onEnd) onEnd(); return; }
          const cls = m.includes('[ERROR]')||m.includes('[!]') ? 'log-err' : m.includes('[OK]') ? 'log-ok' : '';
          logAppend(logBoxId, m, cls);
        });
        read();
      }); }
      read();
    }).catch(e => { logAppend(logBoxId,'[ERROR] '+e,'log-err'); if(onEnd) onEnd(); });
}

$('btn-exec-reb').addEventListener('click', () => {
  const payload = { from_scid: $('reb-from-scid').value.trim(), to_pub: $('reb-to-pub').value.trim(), amt_sats: parseInt($('reb-amt').value)||0, max_fee_sats: parseInt($('reb-fee').value)||100 };
  if (!payload.from_scid || !payload.to_pub || payload.amt_sats <= 0) { toast('Faltan datos en el formulario','err'); return; }
  logClear('log-rebalance');
  $('btn-exec-reb').disabled = true;
  ssePost('/api/rebalance/execute', payload, 'log-rebalance', () => { $('btn-exec-reb').disabled = false; });
});

$('btn-clear-reb-log').addEventListener('click', () => logClear('log-rebalance'));

// ── Piloto Automático Experimental ─────────────────────────
let botES = null;  // EventSource activo del autopiloto

// Actualiza el color y texto de estado visual del Piloto Automatico de rebalanceo.
function setBotStatus(active) {
  const val = $('bot-status-val');
  val.textContent = active ? 'ACTIVO' : 'INACTIVO';
  val.style.color = active ? 'var(--green)' : 'var(--subtext)';
  $('bot-toggle').checked = active;
}

/**
 * Consulta el backend para saber si el autopiloto está corriendo y
 * sincroniza el estado visual del toggle + label.
 *
 * Se llama al iniciar la app y cada vez que el usuario entra al tab
 * Rebalanceo. Resuelve el desfase visual tras recargar la página:
 * el bot puede seguir corriendo en el servidor aunque el UI diga INACTIVO.
 *
 * Si el bot está activo en el servidor, inicia un polling para detectar
 * cuándo se detiene y actualizar el UI automáticamente.
 */
async function syncBotStatus() {
  const r = await apiFetch('/api/rebalance/autopilot/status');
  if (!r) return;  // Error de red, no cambiar estado
  setBotStatus(r.active);
  if (r.active) {
    // El bot sigue corriendo en el servidor tras una recarga de página.
    // No podemos reconectar el stream SSE POST original, pero sí
    // monitorear el estado con polling y actualizar el UI cuando pare.
    logAppend('log-bot', '[BOT] Piloto activo en servidor (reconectado tras recarga).', 'log-info');
    toast('Piloto automático activo en servidor', 'info');
    startBotStatusPolling();
  }
}

/**
 * Variable global para el intervalo de polling del estado del bot.
 * Se limpia cuando el bot se detiene para no dejar timers huérfanos.
 */
let _botPollTimer = null;

/**
 * Inicia un polling cada 10 segundos para verificar si el bot sigue activo.
 * Cuando el backend reporta inactive, limpia el timer y actualiza el UI.
 * Se usa cuando la conexión SSE se perdió (recarga de página) pero el bot
 * sigue corriendo en el thread del servidor.
 */
function startBotStatusPolling() {
  // Limpiar timer previo si existía
  if (_botPollTimer) clearInterval(_botPollTimer);
  _botPollTimer = setInterval(async () => {
    const r = await apiFetch('/api/rebalance/autopilot/status');
    if (!r || !r.active) {
      // El bot terminó (stop manual, error, o el servidor se reinició)
      clearInterval(_botPollTimer);
      _botPollTimer = null;
      setBotStatus(false);
      logAppend('log-bot', '[BOT] Piloto Automático DESACTIVADO.', 'log-warn');
      toast('Piloto automático detenido', 'info');
    }
  }, 10_000);  // Verificar cada 10 seg
}

$('bot-toggle').addEventListener('change', async e => {
  if (e.target.checked) {
    // Activar
    const amt      = parseInt($('bot-amt').value)      || 1000;
    const fee      = parseInt($('bot-fee').value)      || 1;
    const interval = (parseInt($('bot-interval').value) || 5) * 60;
    const target   = parseInt($('ratio-slider').value) || 50;

    logClear('log-bot');
    logAppend('log-bot', '[BOT] Iniciando piloto automático...');
    setBotStatus(true);
    toast('Piloto automático ACTIVADO', 'info');

    // Abrir SSE vía fetch (POST) — el backend mantiene la conexión abierta
    try {
      const resp = await fetch('/api/rebalance/autopilot', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({amt_sats: amt, max_fee_sats: fee,
                              target_ratio: target, interval_secs: interval})
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        logAppend('log-bot', '[BOT ERROR] ' + (err.error || resp.status), 'log-err');
        setBotStatus(false);
        return;
      }
      const reader = resp.body.getReader();
      const dec = new TextDecoder();
      function readBot() {
        reader.read().then(({done, value}) => {
          if (done) { setBotStatus(false); return; }
          dec.decode(value).split('\n').forEach(l => {
            const m = l.replace(/^data: /, '').trim();
            if (!m || m === '__END__') { if (m === '__END__') setBotStatus(false); return; }
            const cls = m.includes('[OK]') ? 'log-ok' : m.includes('[ERROR]') || m.includes('[!]') ? 'log-err' : '';
            logAppend('log-bot', m, cls);
          });
          readBot();
        });
      }
      readBot();
    } catch(err) {
      logAppend('log-bot', '[BOT ERROR] ' + err, 'log-err');
      setBotStatus(false);
    }
  } else {
    // Detener manualmente: limpiar el polling timer si estaba activo
    if (_botPollTimer) { clearInterval(_botPollTimer); _botPollTimer = null; }
    logAppend('log-bot', '[BOT] Enviando señal de parada...');
    fetch('/api/rebalance/autopilot/stop', {method: 'POST'})
      .then(() => { setBotStatus(false); toast('Piloto automático DETENIDO', 'info'); });
  }
});

$('btn-clear-bot-log').addEventListener('click', () => logClear('log-bot'));

// ── TAB: Apertura Canales ─────────────────────────────────────────────────

let currentCands = [];

// Actualiza especificamente el campo de saldo enfocado en la pestaña de Apertura de Canales.
async function loadOpenWallet() {
  const d = await apiFetch('/api/wallet/balance');
  if (d) $('open-wallet-bal').textContent = (d.confirmed||0).toLocaleString() + ' sats confirmados';
}

$('btn-refresh-open-wallet').addEventListener('click', loadOpenWallet);

$('open-push').addEventListener('input', e => {
  $('push-warn').style.display = parseInt(e.target.value) > 0 ? '' : 'none';
});

$('btn-connect-only').addEventListener('click', async () => {
  const uri = $('ext-uri').value.trim();
  if (!uri || !uri.includes('@')) { toast('URI inválida (pubkey@ip:port)','err'); return; }
  const d = await apiFetch('/api/channels/connect', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({uri}) });
  if (d) {
    toast(d.ok ? 'Conectado' : 'Error: '+d.log?.slice(-1)?.[0], d.ok?'ok':'err');
    if (d.ok) $('open-pubkey').value = uri.split('@')[0];
  }
});

$('btn-connect-open').addEventListener('click', () => {
  const uri = $('ext-uri').value.trim();
  if (!uri || !uri.includes('@')) { toast('URI inválida','err'); return; }
  const push = parseInt($('open-push').value)||0;
  if (push > 0 && !confirm(`ATENCIÓN: Regalarás ${push.toLocaleString()} sats al nodo remoto.\n¿Continuar?`)) return;
  const payload = { pubkey: uri.split('@')[0], amt_sats: parseInt($('open-amt').value)||0, push_amt: push, host_uri: uri };
  logClear('log-open');
  $('btn-connect-open').disabled = true;
  ssePost('/api/channels/open', payload, 'log-open', () => { $('btn-connect-open').disabled = false; loadOpenWallet(); });
});

$('btn-scan-cands').addEventListener('click', async () => {
  const minC = parseInt($('cand-min-ch').value)||2;
  const maxD = parseInt($('cand-max-days').value)||30;
  const cands = await apiFetch(`/api/channels/candidates?min_channels=${minC}&max_days=${maxD}`);
  if (!cands) return;
  currentCands = cands;
  const tbody = $('cand-tbody');
  tbody.innerHTML = '';
  if (!cands.length) { tbody.innerHTML = '<tr><td colspan="5" class="td-sub">Sin candidatos. Ejecuta "Escanear Red" en la pestaña Red & Cockpit primero.</td></tr>'; return; }
  cands.forEach(c => {
    const tr = document.createElement('tr');
    const daysStr = c.days_ago < 9999 ? `hace ${c.days_ago}d` : 'nunca';
    tr.innerHTML = `<td class="td-alias">${c.alias}</td><td class="mono">${c.pubkey.slice(0,20)}...</td><td>${c.channels}</td><td class="td-green">${c.capacity.toLocaleString()}</td><td class="td-sub">${daysStr}</td>`;
    tr.addEventListener('click', () => { $('open-pubkey').value = c.pubkey; tbody.querySelectorAll('tr').forEach(r=>r.classList.remove('selected')); tr.classList.add('selected'); });
    tbody.appendChild(tr);
  });
});

$('btn-open-channel').addEventListener('click', () => {
  const pubkey = $('open-pubkey').value.trim();
  const amt    = parseInt($('open-amt').value)||0;
  const push   = parseInt($('open-push').value)||0;
  if (!pubkey || amt <= 0) { toast('Pubkey y monto requeridos','err'); return; }
  if (push > 0 && !confirm(`ATENCIÓN: Regalarás ${push.toLocaleString()} sats.\n¿Continuar?`)) return;
  logClear('log-open');
  $('btn-open-channel').disabled = true;
  ssePost('/api/channels/open', {pubkey, amt_sats: amt, push_amt: push}, 'log-open', () => { $('btn-open-channel').disabled = false; loadOpenWallet(); });
});

$('btn-clear-open-log').addEventListener('click', () => logClear('log-open'));

// ── TAB: Cierre Canales ───────────────────────────────────────────────────

let closeChanList = [];

// Solicita el listado completo de canales (activos e inactivos) para la pestaña de Cierre.
async function loadAllChannels() {
  const chans = await apiFetch('/api/channels/all');
  if (!chans) return;
  closeChanList = chans;
  const tbody = $('close-tbody');
  tbody.innerHTML = '';
  chans.forEach(c => {
    const tr = document.createElement('tr');
    const st = c.status === 'OPEN' ? '<span class="badge badge-open">OPEN</span>'
             : c.status === 'PENDING_OPEN' ? '<span class="badge badge-pending">PENDING</span>'
             : '<span class="badge badge-close">'+c.status+'</span>';
    tr.innerHTML = `<td class="td-alias">${c.alias||'—'}</td><td class="mono">${c.pubkey.slice(0,16)}...</td><td>${st}</td><td class="td-green">${c.local.toLocaleString()}</td><td class="td-sub">${c.remote.toLocaleString()}</td><td class="mono" style="font-size:11px;">${c.chan_point}</td>`;
    tr.addEventListener('click', () => { $('close-chanpoint').value = c.chan_point; tbody.querySelectorAll('tr').forEach(r=>r.classList.remove('selected')); tr.classList.add('selected'); });
    tbody.appendChild(tr);
  });
}

$('btn-refresh-close').addEventListener('click', loadAllChannels);

$('btn-close-channel').addEventListener('click', () => {
  const chanpoint = $('close-chanpoint').value.trim();
  const force     = $('close-force').checked;
  if (!chanpoint) { toast('Selecciona un canal primero','err'); return; }
  if (force && !confirm('ATENCIÓN: Force Close bloqueará fondos temporalmente.\n¿Estás seguro?')) return;
  logClear('log-close');
  $('btn-close-channel').disabled = true;
  ssePost('/api/channels/close', {chan_point: chanpoint, force}, 'log-close', () => { $('btn-close-channel').disabled = false; loadAllChannels(); });
});

$('btn-clear-close-log').addEventListener('click', () => logClear('log-close'));

// ── Inicialización ────────────────────────────────────────────────────────

(async function init() {
  // Restaurar configuración de rebalanceo desde localStorage
  if (localStorage.getItem('reb_ratio')) $('ratio-slider').value = localStorage.getItem('reb_ratio');
  // Forzar sincronización visual (útil cuando el navegador restaura el input por sí solo)
  const ratioV = $('ratio-slider').value;
  $('ratio-display').textContent = `${ratioV}/${100-ratioV}`;

  ['bot-amt', 'bot-fee', 'bot-interval'].forEach(id => {
    if (localStorage.getItem('reb_' + id)) $(id).value = localStorage.getItem('reb_' + id);
  });

  await detectNode();
  await loadMetrics();
  walletRefreshAll();
  loadOpenWallet();
  loadAllChannels();
  // Auto-refresh métricas cada 5 min
  setInterval(loadMetrics, 300_000);
  // Cargar gamificación al inicio (puede no tener datos aún, es OK)
  loadGameStatus();
  // Sincronizar estado del autopiloto con el servidor (resuelve desfase tras recarga)
  syncBotStatus();
  // Procesar todos los emojis del DOM inicial (sidebar, encabezados, etc.)
  // Se llama en un timeout corto para asegurar que Twemoji ya terminó de cargar.
  setTimeout(() => twemojiParse(document.body), 200);
})();


// ══════════════════════════════════════════════════════════
// MÓDULO DE GAMIFICACIÓN — Pestaña Aventura
// ══════════════════════════════════════════════════════════

// Caché de logros previos para detectar nuevos desbloqueos en cada refresh
let _prevUnlockedIds = new Set();

// Avatares según nivel de rango (índice 0-4)
const RANK_AVATARS = ['⚡', '🌩️', '🔀', '🌊', '🌟'];

/**
 * Formatea un timestamp Unix como fecha local legible.
 * Retorna una cadena vacía si el timestamp no es válido.
 */
function fmtTs(ts) {
  if (!ts) return '';
  try {
    return new Date(ts * 1000).toLocaleDateString('es', {
      day:'2-digit', month:'short', year:'numeric'
    });
  } catch { return ''; }
}

/**
 * Muestra un toast especial de "¡Logro Desbloqueado!"
 * con el emoji y nombre del trofeo obtenido.
 */
function toastAchievement(emoji, name) {
  const el = document.createElement('div');
  el.className = 'toast toast-achievement';
  el.innerHTML = `🏆 ¡Logro desbloqueado!<br><strong>${emoji} ${name}</strong>`;
  $('toast-container').appendChild(el);
  setTimeout(() => el.remove(), 5000);
}

/**
 * Calcula el porcentaje de XP absoluto hacia el próximo rango.
 * Si xp_next_rank es null (rango máximo), retorna 100%.
 * Retorna un número entre 0 y 100.
 */
function calcXpPct(xp, xpNext) {
  if (!xpNext) return 100;          // Rango máximo: barra llena
  if (xpNext <= 0) return 0;
  return Math.min(100, Math.round((xp / xpNext) * 100));
}

/**
 * Renderiza las tarjetas de misiones en el contenedor del panel.
 * Separa las misiones por estado: activas y completadas (sin reclamar).
 */
function renderQuests(quests) {
  const container = $('game-quests-container');
  if (!quests || !quests.length) {
    container.innerHTML = '<p class="text-sub" style="padding:16px;">Sin misiones disponibles aún. ¡Activa tu nodo para comenzar!</p>';
    return;
  }

  container.innerHTML = '';
  // Ordenar: activas primero, luego completadas
  const sorted = [...quests].sort((a, b) => {
    if (a.status === 'completed' && b.status !== 'completed') return 1;
    if (a.status !== 'completed' && b.status === 'completed') return -1;
    return b.pct - a.pct;   // mayor progreso primero
  });

  sorted.forEach(q => {
    const isCompleted = q.status === 'completed' || q.status === 'claimed';
    const typeBadge   = q.type === 'weekly'
      ? '<span class="game-quest-type-badge badge-weekly">Semanal</span>'
      : '<span class="game-quest-type-badge badge-milestone">Hito</span>';
    const weekInfo = q.type === 'weekly' && q.period_week
      ? `<span class="game-quest-pct text-sub" style="font-size:10px;margin-left:auto;">Semana ${q.period_week}</span>` : '';

    const card = document.createElement('div');
    card.className = `game-quest-card${isCompleted ? ' quest-completed' : ''}`;
    card.innerHTML = `
      <div class="game-quest-header">
        <span class="game-quest-emoji">${q.emoji || '🎯'}</span>
        <span class="game-quest-name">${q.name}</span>
      </div>
      <div class="game-quest-meta">
        ${typeBadge}
        <span class="game-quest-xp">+${q.xp_reward || 0} XP</span>
        ${weekInfo}
      </div>
      <div class="game-quest-desc">${q.description}</div>
      <div class="game-quest-progress-row">
        <div class="game-quest-bar-track">
          <div class="game-quest-bar-fill" style="width:${q.pct || 0}%"></div>
        </div>
        <span class="game-quest-pct">${q.pct || 0}%</span>
      </div>
      <div class="text-sub" style="font-size:10px;">
        ${isCompleted ? '✅ Completada' : `${q.progress || 0} / ${q.target || 0} ${q.unit || ''}`}
      </div>
    `;
    container.appendChild(card);
  });

  // Contador de misiones activas en la pill de stats
  const activeCnt = quests.filter(q => q.status === 'active').length;
  $('game-stat-quests').textContent = activeCnt;
  // Reprocesar emojis del contenedor recién renderizado
  twemojiParse($('game-quests-container'));
}

/**
 * Renderiza la vitrina de trofeos en el contenedor del panel.
 * Los desbloqueados aparecen primero, con fecha de obtención.
 * Los bloqueados aparecen en gris con "Pendiente".
 */
function renderAchievements(achievements, prevIds) {
  const container = $('game-achievements-container');
  if (!achievements || !achievements.length) {
    container.innerHTML = '<p class="text-sub" style="padding:16px;">Sin datos de trofeos aún.</p>';
    return;
  }

  container.innerHTML = '';

  // Ordenar: desbloqueados primero (por fecha desc), luego bloqueados
  const sorted = [...achievements].sort((a, b) => {
    if (a.unlocked_at && !b.unlocked_at) return -1;
    if (!a.unlocked_at && b.unlocked_at) return 1;
    if (a.unlocked_at && b.unlocked_at) return b.unlocked_at - a.unlocked_at;
    return 0;
  });

  sorted.forEach(ach => {
    const unlocked = !!ach.unlocked_at;
    const card = document.createElement('div');
    card.className = `game-achievement-card ${unlocked ? 'unlocked' : 'locked'}`;

    card.innerHTML = `
      <div class="game-achievement-emoji">${ach.emoji || '🏆'}</div>
      <div class="game-achievement-name">${ach.name}</div>
      <div class="game-achievement-desc">${ach.description || ''}</div>
      ${unlocked
        ? `<div class="game-achievement-date">🗓 ${fmtTs(ach.unlocked_at)}</div>`
        : `<div class="game-achievement-locked-label">🔒 Pendiente</div>`
      }
    `;
    container.appendChild(card);

    // Detectar logros nuevamente desbloqueados y mostrar toast
    if (unlocked && !prevIds.has(ach.id)) {
      toastAchievement(ach.emoji || '🏆', ach.name);
    }
  });
  // Reprocesar emojis del contenedor recién renderizado
  twemojiParse($('game-achievements-container'));
}

/**
 * Carga el estado de gamificación desde la API y actualiza toda la UI del panel.
 * Es seguro llamar repetidamente (idempotente en la UI).
 *
 * Endpoint: GET /api/gamification/status
 */
async function loadGameStatus() {
  const data = await apiFetch('/api/gamification/status');
  if (!data) return;

  const now = new Date().toLocaleTimeString('es');
  const lastUpd = $('game-last-update');
  if (lastUpd) lastUpd.textContent = `Actualizado: ${now}`;

  // ── Perfil: XP ────────────────────────────────────────────
  const xp      = data.xp || 0;
  const rank    = data.rank_info || {};
  const xpNext  = rank.xp_next_rank;
  const xpPct   = calcXpPct(xp, xpNext);

  const xpBar   = $('game-xp-bar');
  const xpText  = $('game-xp-text');
  const xpHint  = $('game-xp-hint');

  if (xpBar)  xpBar.style.width = xpPct + '%';
  if (xpText) xpText.textContent = xp.toLocaleString() + ' XP';
  if (xpHint) xpHint.textContent = xpNext
    ? `${xp.toLocaleString()} / ${xpNext.toLocaleString()} XP para el próximo rango`
    : '🌟 ¡Rango máximo alcanzado!';

  // ── Perfil: HP ────────────────────────────────────────────
  // La barra HP usa un gradiente rojo→amarillo→verde (bg-size:300%):
  //   0% HP  → posición 100% (rojo)
  //   100% HP → posición 0% (verde)
  const hp    = data.health ?? 100;
  const hpPct = Math.max(0, Math.min(100, hp));
  const hpPos = 100 - hpPct;   // inversión para gradiente rojo→verde

  const hpBar  = $('game-hp-bar');
  const hpText = $('game-hp-text');
  const hpHint = $('game-hp-hint');

  if (hpBar) {
    hpBar.style.width              = hpPct + '%';
    hpBar.style.backgroundPosition = hpPos + '% 50%';
  }
  if (hpText) hpText.textContent = hpPct + '%';
  if (hpHint) {
    if      (hpPct >= 80) hpHint.textContent = '💪 Excelente — nodo en óptimas condiciones';
    else if (hpPct >= 50) hpHint.textContent = '⚠️ Moderado — revisa canales y liquidez';
    else                  hpHint.textContent = '🚨 Crítico — zombies, desbalance o desconexiones detectadas';
  }

  // ── Perfil: Rango y Avatar ────────────────────────────────
  const level = rank.level ?? data.rank_level ?? 0;
  const rankAvatar = $('game-avatar');
  const rankBadge  = $('game-rank-badge');
  const rankLevel  = $('game-rank-level');

  if (rankAvatar) rankAvatar.textContent = RANK_AVATARS[level] || '⚡';
  if (rankBadge)  rankBadge.textContent  = rank.name || data.rank || 'Aprendiz de Satoshi';
  if (rankLevel)  rankLevel.textContent  = `Nivel ${level}`;

  // ── Pills de stats rápidas ────────────────────────────────
  const statAch = $('game-stat-achievements');
  if (statAch) {
    statAch.textContent = `${data.unlocked_count || 0} / ${data.total_achievements || 0}`;
  }

  // ── Misiones ──────────────────────────────────────────────
  renderQuests(data.quests || []);

  // ── Trofeos ───────────────────────────────────────────────
  renderAchievements(data.achievements || [], _prevUnlockedIds);

  // Actualizar caché de IDs desbloqueados para el próximo refresh
  _prevUnlockedIds = new Set(
    (data.achievements || []).filter(a => a.unlocked_at).map(a => a.id)
  );
}

// Botón de actualizar en la pestaña Aventura
$('btn-refresh-game').addEventListener('click', loadGameStatus);

// Auto-refresh de gamificación cada 5 minutos (mismo ciclo que métricas)
setInterval(loadGameStatus, 300_000);


// ══════════════════════════════════════════════════════════
// MÓDULO DE WATCHTOWER (Torres de Vigilancia)
// ══════════════════════════════════════════════════════════

/**
 * Carga el estado consolidado de watchtowers y actualiza la UI.
 */
async function loadWatchtowerStatus() {
  const data = await apiFetch('/api/watchtower/status');
  if (!data) return;

  // Manejo de error si los módulos de LND no están configurados/activos
  // data.wtclient_active es false solo cuando el cliente reportó error de configuración
  // data.error solo se propaga cuando el cliente NO está activo
  if (data.error && !data.wtclient_active) {
    $('wt-config-guide').style.display = 'block';
    $('wt-active-content').style.display = 'none';
    return;
  }

  $('wt-config-guide').style.display = 'none';
  $('wt-active-content').style.display = 'block';

  // 1. Cliente: Estado & Métricas
  // active viene directamente del backend (client.get("active"))
  const isClientActive = !!data.wtclient_active;

  $('wt-c-status').innerHTML = isClientActive 
    ? `<span style="color:var(--green)">⚡ Activo</span>` 
    : `<span style="color:var(--sub)">Inactivo</span>`;

  const numTowers = (data.towers || []).length;
  $('wt-c-count').textContent = numTowers;

  // Calcular backups y sesiones totales
  let totalBackups = 0;
  let activeSessions = 0;
  let exhaustedSessions = 0;

  (data.towers || []).forEach(t => {
    (t.sessions || []).forEach(s => {
      totalBackups += (s.num_backups || 0);
      if (s.exhausted) {
        exhaustedSessions++;
      } else {
        activeSessions++;
      }
    });
  });

  $('wt-c-backups').textContent = totalBackups;
  $('wt-c-sessions').textContent = `${activeSessions} / ${exhaustedSessions}`;

  // 2. Servidor: Estado & URI
  // active=true y pubkey presente indican que el servidor está habilitado
  const serverInfo = data.server_info || {};
  const isServerActive = serverInfo.active === true && !!serverInfo.pubkey;

  if (isServerActive) {
    $('wt-s-status').innerHTML = `<span style="color:var(--green)">📡 Escuchando</span>`;
    $('wt-s-details').style.display = 'block';
    $('wt-s-inactive-msg').style.display = 'none';
    $('wt-s-listen').textContent = serverInfo.listening_addresses && serverInfo.listening_addresses.length
      ? serverInfo.listening_addresses.join(', ') : '—';

    // Preferir las URIs completas si están disponibles, o construir una
    const uris = serverInfo.uris || [];
    const uriDisplay = uris.length ? uris.join('\n') : `${serverInfo.pubkey}@${serverInfo.listening_addresses && serverInfo.listening_addresses[0] ? serverInfo.listening_addresses[0] : 'localhost:9911'}`;
    $('wt-s-uri').value = uriDisplay;
  } else {
    $('wt-s-status').innerHTML = `<span style="color:var(--sub)">Inactivo</span>`;
    $('wt-s-details').style.display = 'none';
    $('wt-s-inactive-msg').style.display = 'block';
  }

  // 3. Tabla de Torres Conectadas
  const tbody = $('wt-towers-tbody');
  tbody.innerHTML = '';

  if (data.towers && data.towers.length > 0) {
    data.towers.forEach(t => {
      const pubkey = t.pubkey || '—';
      const addresses = t.addresses ? t.addresses.join(', ') : '—';
      const numSessions = t.sessions ? t.sessions.length : 0;
      
      // Sumar backups de esta torre
      let okBackups = 0;
      let pendingBackups = 0;
      (t.sessions || []).forEach(s => {
        okBackups += (s.num_backups || 0);
        pendingBackups += (s.num_pending_backups || 0);
      });

      const backupsStr = `${okBackups} OK / ${pendingBackups} Pend`;
      const sweepFeeLimit = t.sweep_fee_limit_sat_per_vbyte ? `${t.sweep_fee_limit_sat_per_vbyte} sat/vB` : '—';

      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td class="mono" style="font-size:11px;" title="${pubkey}">${pubkey.substring(0, 16)}...${pubkey.substring(pubkey.length - 8)}</td>
        <td style="font-size:12px;">${addresses}</td>
        <td>${numSessions}</td>
        <td>${backupsStr}</td>
        <td>${sweepFeeLimit}</td>
        <td>
          <button class="btn btn-danger btn-sm" onclick="removeTower('${pubkey}')">❌ Eliminar</button>
        </td>
      `;
      tbody.appendChild(tr);
    });
  } else {
    tbody.innerHTML = `
      <tr>
        <td colspan="6" class="text-sub" style="text-align:center; padding:16px;">No hay torres de vigilancia configuradas.</td>
      </tr>
    `;
  }

  // 4. Estadísticas del Cliente Globales
  const stats = data.stats || {};
  $('wt-stat-active').textContent = stats.num_tasks_active || 0;
  $('wt-stat-pending').textContent = stats.num_tasks_pending || 0;
  
  const policy = data.policy || {};
  $('wt-stat-policy-max').textContent = policy.max_updates || 0;
  $('wt-stat-policy-fee').textContent = policy.sweep_fee_rate_sat_per_vbyte ? `${policy.sweep_fee_rate_sat_per_vbyte} sat/vB` : '0 sat/vB';

  // Twemoji parse
  twemojiParse($('tab-watchtower'));
}

/**
 * Registra una nueva torre de vigilancia.
 */
async function addTower() {
  const uriInput = $('wt-uri-input');
  const uri = uriInput.value.trim();
  if (!uri) return;

  const btn = $('wt-btn-add');
  btn.disabled = true;
  btn.textContent = 'Conectando...';

  try {
    const res = await apiFetch('/api/watchtower/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ uri }),
    });
    if (res && res.ok) {
      toast('🛡️ Torre agregada con éxito', 'ok');
      uriInput.value = '';
      await loadWatchtowerStatus();
      // Recargar gamificación por si desbloqueó el logro escudo_protector
      await loadGameStatus();
    } else {
      toast('❌ Error al agregar torre: ' + (res ? res.error : 'Respuesta vacía'), 'err');
    }
  } catch (err) {
    toast('❌ Error al conectar con el servidor', 'err');
  } finally {
    btn.disabled = false;
    btn.textContent = '🛡️ Conectar';
  }
}

/**
 * Elimina una torre de vigilancia.
 */
async function removeTower(pubkey) {
  if (!confirm(`¿Estás seguro de que deseas desconectar la torre ${pubkey}?`)) {
    return;
  }

  try {
    const res = await apiFetch('/api/watchtower/remove', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pubkey }),
    });
    if (res && res.ok) {
      toast('🗑️ Torre eliminada', 'ok');
      await loadWatchtowerStatus();
      await loadGameStatus();
    } else {
      toast('❌ Error al eliminar torre: ' + (res ? res.error : 'Respuesta vacía'), 'err');
    }
  } catch (err) {
    toast('❌ Error al conectar con el servidor', 'err');
  }
}

// Exportar funciones globales para callbacks inline de HTML
window.removeTower = removeTower;
window.loadWatchtowerStatus = loadWatchtowerStatus;

// Configurar Event Listeners para Watchtower
$('wt-add-form').addEventListener('submit', (e) => {
  e.preventDefault();
  addTower();
});

$('wt-btn-copy-uri').addEventListener('click', () => {
  copyTextToClipboard($('wt-s-uri').value)
    .then(() => toast('📋 URI copiada al portapapeles', 'ok'))
    .catch(() => toast('❌ No se pudo copiar', 'err'));
});
