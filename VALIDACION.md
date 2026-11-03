# Validación de la actualización UI/UX

Repositorio: `CarlosVasquezz23/Analizador`. Cambios preparados en `dev/dashboard-deportivo` y pull request #1. La actualización del código fue autorizada por el propietario en la conversación; la incorporación a `main` se realiza solo después de aprobar las pruebas de la nueva revisión.

## Resultados locales

- `python -m pytest -q`: **40 pruebas aprobadas**.
- `python -m compileall -q analizador.py pro_ui.py ticket_ui.py finance_ui.py workspace_logic.py`: aprobado.
- `git diff --check`: aprobado.
- `python -m pip check`: sin dependencias incompatibles.
- Entorno: Python 3.12, Streamlit 1.66.1, Plotly 6.9.0, NumPy 2.3.5, pandas 2.2.3.

## Cobertura

Navegación por diez pantallas sin claves, cambios de tema, escaneo sin configuración, dashboard con eventos sintéticos, selección/guardado de boletos, aislamiento de sesiones, verificador, filtros de fecha y casas, normalización de mercados, Poisson, Monte Carlo, arbitraje completo, tratamiento de doble oportunidad y errores HTTP sin filtrar credenciales.

La segunda revisión agrega pruebas de accesos rápidos conservando el boleto, eliminación de selecciones, bloqueo de guardados duplicados y de cuotas no disponibles, filtros/ordenación del radar, retornos bruto y neto, detección de selecciones correlacionadas, liquidación del historial, cálculo de ROI e importación de respaldos antiguos/invalidación de datos incorrectos.

Los eventos sintéticos solo se usan en pruebas; no hay demostraciones presentadas como resultados reales dentro de la aplicación.

## Límites de esta validación

- El servidor Streamlit arranca. Chrome se descargó, pero el entorno impide abrir el socket necesario para iniciarlo (`Operation not permitted`). No se completó QA visual en navegador PC/móvil. Se implementaron reglas responsive y se verificó funcionalidad con AppTest, sin afirmar inspección visual completa.
- No hay credenciales de los cuatro proveedores en este entorno. Se conservan esquemas e IDs originales de AllSports/SportAPI y necesitan confirmación con el plan contratado.
- No se enviaron mensajes a Telegram. El OCR usa los idiomas español/inglés disponibles en Tesseract; no se ha medido su precisión con capturas reales del propietario.
- Bitácora y preferencias son por sesión. La exportación JSON es necesaria para conservar una copia; no se agregó una base de datos ni se promete almacenamiento permanente.

No se cambian Secrets, ajustes de hosting ni conexiones de Streamlit. Community Cloud puede redesplegar automáticamente cuando se incorpora el código a su rama conectada. El estado de publicación debe confirmarse en la aplicación del propietario.
