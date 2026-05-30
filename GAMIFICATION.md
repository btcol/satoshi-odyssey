# Lightning Node Gamification: De Herramienta a Juego

Este documento recoge la lluvia de ideas para transformar el proyecto en una experiencia gamificada y divertida llamada Satoshi Odyssey, donde el usuario aprende a gestionar su nodo Lightning Network superando retos y consiguiendo objetivos visuales.

## 0. El Rito de Iniciación: El Despertar del Nodo (Fase Testnet4)

En lugar de un aburrido tutorial de configuración, el inicio del juego se convierte en la primera misión oficial del jugador, aprovechando la infraestructura de Testnet4:

- **Misión "Bautismo de Fuego"**: El juego arranca con el nodo completamente "a oscuras", apagado y en nivel cero. La interfaz le presenta al usuario su dirección de depósito on-chain y un mapa de portales al exterior (enlaces a faucets de Testnet4). El objetivo del jugador es realizar el viaje hacia estos portales para reclamar sus primeros recursos.

- **La Espera de la Forja (Confirmación de Bloques)**: Una vez solicitados los fondos en el faucet, la red real tardará unos minutos en procesar la transacción. El juego transforma esta espera técnica en una barra de progreso mística que representa la "forja o canalización de energía" hacia el núcleo del nodo.

- **¡Nodo Energizado!**: En el instante en que los fondos impactan la cartera, el nodo "despierta" con una gran animación visual y sonora. El cockpit cobra vida, se restaura la energía general, el usuario recibe su primer gran botín de XP y se desbloquea oficialmente el mapa de canales y misiones principales.


## 1. Sistema de Puntuación (Score) y Rango del Nodo
En lugar de mostrar solo métricas financieras, se traducen a "Puntos de Experiencia (XP)":
- **XP por Enrutamiento:** Puntos por cada satoshi ganado en comisiones. Multiplicadores si el enrutamiento pasó por canales recientemente rebalanceados.
- **Rangos (Niveles):** El usuario evoluciona: *“Aprendiz de Satoshi”* -> *“Novato del Rayo”* -> *“Enrutador Activo”* -> *“Maestro de Liquidez”* -> *“Hub de la Red”*. 
- **Salud del Nodo (HP - Health Points):** Una barra (0 al 100%). Baja si hay "Zombies" (>30 días inactivos), si la liquidez está vaciada hacia un lado, o si los UTXOs on-chain están fragmentados.

## 2. Misiones y Desafíos (Quests)
Un panel de "Misiones Activas" para fomentar el aprendizaje práctico:
- **"El Equilibrador":** Lograr que 3 canales tengan un ratio perfecto (50/50).
- **"Cazador de Zombies":** Identificar y cerrar un canal inactivo (Force o Coop).
- **"El Diplomático":** Abrir un canal usando sugerencias de la red (peer con alta capacidad).
- **"Francotirador de Fees":** Consolidar UTXOs pagando una tarifa muy baja (ej. < 5 sats/vbyte).
- **"El repartidor":** Genere un flujo de mas de 100 transacciones en una semana (se valen rebalanceos de canales).
- **"El gerente de sucursal":** Genere un flujo de mas de 500 transacciones en una semana (se valen rebalanceos de canales).
- **"El mayorista":** Genere un flujo de mas de 1000 transacciones en una semana (se valen rebalanceos de canales).
-**Insomne:** 100% de Uptime del nodo en los últimos 7 días.

## 3. Logros Desbloqueables (Badges / Trofeos)
Vitrina visual de medallas:
- 🏆 **Bautismo de Fuego:** Financiar el nodo por primera vez con fondos on-chain.
- 🏆 **Primer relámpago:** Enrutaste tu primera transacción Lightning.

- 🏆 **Rayo de Cobre:** Todos los canales en el Cockpit 3D están de color rojo o verde, o almenos uno está en este color (menos de 1M sats de liquidez en cada canal, o algun canal es zombie o está caido).
- 🏆 **Rayo Plateado:** Todos los canales en el Cockpit 3D están de color azul (+ 1M de sats de liquidez en cada canal).
- 🏆 **Rayo Dorado:** Todos los canales en el Cockpit 3D están de color amarillo (+ 5M de sats de liquidez en cada canal).

- 🏆 **Manos de Diamante:** Acumulaste 100,000 sats en comisiones ganadas.

- 🏆 **Tinterillo:** Tiene almenos 2 canales y menos de 6 canales en total.
- 🏆 **Tramitador:** Tiene almenos 6 canales y menos de 10 canales en total.
- 🏆 **El Contacto:** Tiene almenos 10 canales y menos de 15 canales en total.
- 🏆 **El Intermediador:** Tiene almenos 15 canales y menos de 20 canales en total.
- 🏆 **El Patron:** Tiene almenos 20 canales en total.


## 4. Feedbacks Visuales y "Micro-Recompensas"
- **Confeti / Animaciones:** Al finalizar un rebalanceo exitoso o ganar un logro.
- **Grafo 3D Vivo:** Nodos que "palpitan" cuando ganan muchas comisiones o parpadean en rojo pidiendo ayuda si están muy desbalanceados.
- **Sonidos:** Pequeños efectos sonoros (tipo moneda) cuando el stream SSE notifica una ganancia de ruteo.

## 5. Tablas de Récords (Leaderboard Local)
- **Comisiones vs Mes Pasado:** Gráfico comparativo para superarse a sí mismo.
- **Récords Personales:** "Mayor pago enrutado: X sats", "Día más rentable", etc.

## 6. El Clima de la Red (Eventos Aleatorios)

El estado cambiante de la mempool real de Testnet4 se transforma en "climatología" dentro del juego, obligando al usuario a adaptar su estrategia:

- **Tormenta de Fees (Alta Congestión):** Las tarifas de la red principal de prueba suben drásticamente. El juego entra en "Modo Tormenta". Las misiones de apertura/cierre de canales dan el doble de XP si se logran optimizar, pero el HP del nodo peligra si ocurre un imprevisto.

- **Cripto-Invierno (Mercado Lateral):** El tráfico global de transacciones baja. Aparece una misión especial temporizada: "Sobrevive al invierno reduciendo tus tarifas a la mitad para atraer el poco tráfico disponible".

- **Día de Descuentos (Cielo Despejado):** Tarifas de la red extremadamente bajas. El juego avisa que es el momento ideal para realizar tareas de mantenimiento pesadas con bonificaciones de experiencia.

## 7. Defensa de Torres (Mecánicas de Riesgo y Seguridad)

Transformar las alertas y errores reales del sistema en situaciones de tensión e interacción jugable:

- **Alerta de Ataque de Sondeo (Probing):** Si el sistema detecta que alguien intenta "adivinar" la liquidez interna de tus canales de forma sospechosa, la interfaz visual enciende luces de alarma. La acción defensiva del jugador consiste en ajustar temporalmente las tarifas de ese canal para "despistar" al atacante.

- **Misión de Rescate del Canal:** Cuando un canal entra en un estado de desconexión o cierre forzado en Testnet4, la interfaz lo muestra como un temporizador de cuenta atrás. El usuario debe vigilar visualmente el rescate de los fondos hasta que vuelvan a salvo a su base segura.

## 8. Feedbacks Visuales y "Micro-Recompensas"

- **Confeti / Animaciones:** Al finalizar un rebalanceo exitoso o ganar un logro.

- **Grafo 3D Vivo:** Nodos que "palpitan" cuando ganan muchas comisiones o parpadean en rojo pidiendo ayuda si están muy desbalanceados.

- **Sonidos:** Pequeños efectos sonoros (tipo moneda de arcade) cuando el sistema notifica una ganancia de ruteo en tiempo real.

