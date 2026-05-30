"""
gamification/__init__.py
========================
Paquete de gamificación del Lightning Web Dashboard.

Este paquete contiene la lógica del sistema de juego desacoplada del
código de administración técnica del nodo. Está diseñado para ser
importado y usado por otros módulos sin introducir dependencias circulares.

Módulos incluidos:
  - scoring.py    : Cálculo de XP, HP (Salud) y Rangos del operador.
  - achievements.py : Definición y evaluación de Logros desbloqueables.
  - quests.py     : Definición y seguimiento de Misiones activas.
  - game_engine.py : Orquestador principal que coordina todos los módulos.
"""

# Versión del sistema de gamificación — incrementar en cada cambio de contrato de datos.
GAMIFICATION_VERSION = "0.1.0"
