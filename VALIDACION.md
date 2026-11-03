# Validación de la rama de desarrollo

Base revisada: `CarlosVasquezz23/Analizador`, `main`, commit `d23b61deccef89c91579ab4fed40b19f856d8814`.

## Resultados locales

- `python -m pytest -q`: **27 pruebas aprobadas**.
- `python -m compileall -q analizador.py analytics.py api_client.py pro_ui.py football_details.py`: aprobado.
- `git diff --check`: aprobado.
- `python -m pip check`: sin dependencias incompatibles.
- Instalación desde `requirements.txt`: completada. Entorno: Python 3.12, Streamlit 1.66.1, Plotly 6.9.0, NumPy 2.3.5, pandas 2.2.3.

Las pruebas cubren navegación por las diez pantallas sin claves, tema visual, escaneo sin configuración, dashboard con eventos de prueba, selección y guardado de boletos, bitácoras aisladas por sesión, verificador con cuotas inválidas, normalización de mercados, filtros de fechas/casas, probabilidades Poisson, Monte Carlo reproducible, arbitraje completo y rechazo de doble oportunidad como arbitraje. También verifican timeouts HTTP sin filtración de credenciales, tratamiento de 429 y conservación de resultados válidos cuando otro proveedor responde con un esquema incorrecto.

Los eventos usados en pruebas son sintéticos y no se introducen como resultados de la aplicación.

## Pendiente antes de aprobar la incorporación

- Verificar contratos y resultados reales con credenciales de los cuatro proveedores. No hay secretos de producción en este entorno. Los adaptadores de AllSports y SportAPI y sus IDs provienen de la base original y necesitan confirmación.
- Validar OCR con capturas reales y Tesseract instalado. AppTest no valida la precisión del reconocimiento de imágenes.
- Revisar el aspecto en navegador de escritorio y móvil. El servidor Streamlit arrancó, pero no se pudieron obtener capturas: la descarga de Chromium para Playwright falló con un archivo truncado. La adaptación CSS está implementada; no se presenta como validación visual completada.
- Verificar entrega a Telegram únicamente con un chat de pruebas autorizado.
- Aprobar el cambio de persistencia: la bitácora y las preferencias son por sesión; se debe descargar el respaldo JSON antes de cerrar. No se agregó una base de datos ni se garantiza almacenamiento permanente.

No se fusionó la rama, no se activó auto-merge y no se publicó en Streamlit. La aprobación debe ocurrir antes de fusionar: Community Cloud puede desplegar automáticamente cuando cambia su rama conectada.
