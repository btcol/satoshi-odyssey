#!/bin/bash
# Este script inicia el servidor Bitcoin/LND/LNbits en testnet4

############################3

# ── Cargar variables de entorno ──
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/.env" ]; then
    source "$SCRIPT_DIR/.env"
fi

NETWORK="${NETWORK:-testnet4}"
LNCLI_BIN="${LNCLI_BIN:-lncli-debug}"
BITCOIN_CLI_BIN="${BITCOIN_CLI_BIN:-bitcoin-cli}"

# Inicializa el nodo de bitcoin
echo "Inicializando bitcoind ..."
screen -dmS bitcoind bash -c "bitcoind -${NETWORK}; exec bash"

echo "Esperando a que bitcoind se sincronice al 100%..."
while ! ${BITCOIN_CLI_BIN} -${NETWORK} getblockchaininfo 2>/dev/null | grep -q '"initialblockdownload": false'; do
    sleep 5
done
sleep 2

echo "Inicializando lnd ..."
screen -dmS lnd bash -c "lnd --bitcoin.${NETWORK}; exec bash"

# Esperar a que lnd se inicie
echo "Esperando a que LND abra el puerto RPC..."
while ${LNCLI_BIN} --network=${NETWORK} state 2>&1 | grep -q "connection refused"; do
    sleep 3
done
sleep 2 # Margen de seguridad extra

# Desbloquear la wallet existente
echo "Desbloqueando la wallet existente ..."
echo "ClaveWallet1234" | ${LNCLI_BIN} --network=${NETWORK} unlock --stdin


# Inicializa el servicio LNbits
echo "Inicializando LNbits ..."
screen -dmS lnbits bash -c 'cd lnbits && cv run lnbits; exec bash'

# Espacio reservado para la parte de caddy
# Aca debe ir la activacion del proxy inverso

# Servidor en linea
echo "Servicio Bitcoin/LND/LNbits en linea !!!"
