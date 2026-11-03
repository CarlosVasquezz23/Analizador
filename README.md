# Analizador · Sports Workspace

Dashboard deportivo para Streamlit Community Cloud. La entrada sigue siendo `analizador.py`; no cambia el despliegue existente hasta fusionar esta rama y autorizar su incorporación.

## Ejecutar localmente

```bash
python -m pip install -r requirements-dev.txt
python -m streamlit run analizador.py
python -m pytest -q
```

Para OCR instala Tesseract y los idiomas español e inglés. Community Cloud utiliza el archivo `packages.txt` existente. No se necesita EasyOCR, OpenCV ni una GPU.

## Credenciales

Configura solo los proveedores contratados en **Settings → Secrets** de Streamlit. Nunca subas claves al repositorio:

```toml
ODDS_API_KEY = "tu-clave"
AF_API_KEY = "tu-clave"
ALLSPORTS_API_KEY = "tu-clave"
SPORTAPI_KEY = "tu-clave"
TG_TOKEN = "token-opcional"
TG_CHAT_ID = "chat-opcional"
```

La aplicación arranca sin secretos y explica qué falta configurar. Telegram solo se invoca al pulsar Enviar resumen; el destinatario corresponde al chat configurado por el propietario del despliegue.

## Cambios

- Dashboard con métricas de cobertura, gráficos Plotly, comparador y descarga CSV.
- Navegación única, búsqueda de equipos, interfaz con modo claro/oscuro y reglas adaptables para móvil. El menú ☰ permanece accesible.
- Radar, boleto, modelo Poisson, Monte Carlo, Kelly, bitácora y proveedores existentes conservados; módulos vacíos sustituidos por vistas útiles de cobertura, oportunidades y resumen descargable.
- Consulta explícita de próximos eventos, alineaciones y lesiones vía API-Football.
- OCR real con revisión del texto; se eliminó el boleto fijo que se mostraba al subir cualquier foto. Sin cuotas válidas no hay resultados inventados.
- Validación de cuotas, totales 2.5 explícitos, nombres reconocidos, fechas y filtro estricto de casas. Mercados incompletos y doble oportunidad no generan señales falsas de arbitraje o EV.
- Poisson con cola más amplia, varianza correcta de retorno en Sharpe y semilla fija en Monte Carlo para evitar cambios aleatorios al navegar.
- Caché con límites y vencimiento, cuatro workers, conexiones con timeout y reintentos acotados para errores 502/503/504. No se fuerza la limpieza global de caché en cada escaneo ni se reintenta 429.
- Avisos de errores sin revelar URLs con credenciales. Aislamiento de respuestas malformadas por proveedor y evento.
- API-Football resuelve la temporada vigente y consulta hasta tres páginas de cuotas por liga. The Odds API usa el endpoint general solo para h2h y totals; BTTS y doble oportunidad dependen de otros proveedores.
- Dependencias reducidas a las utilizadas y pruebas automáticas con pytest/AppTest.

## Datos y límites que se deben revisar antes de fusionar

**Historial:** el almacenamiento compartido en JSON del servidor fue sustituido por estado de sesión para que los visitantes no compartan bitácora ni configuración. Los registros se conservan al navegar, pero pueden perderse al cerrar o reiniciar la sesión. Exporta la bitácora a JSON y restáurala desde la pantalla financiera. Los respaldos del formato anterior son compatibles. Para persistencia duradera multiusuario hace falta una base de datos con autenticación; no se presenta el disco efímero de Community Cloud como respaldo permanente.

**Probabilidades:** el EV del radar compara contra el consenso de cuotas, no contra un modelo deportivo validado. El verificador usa una aproximación del 95 % de la probabilidad implícita. Poisson usa medias manuales, no datos H2H históricos. Las combinadas asumen independencia; se advierte si contienen selecciones del mismo evento. Mercados incompletos o superpuestos no tienen consenso calculable y se indican como no disponibles.

**Proveedores:** se conservan los identificadores de ligas y esquemas de AllSports/SportAPI de la versión original. Su disponibilidad, suscripción y correspondencia deben verificarse con las credenciales reales. Los eventos de proveedores diferentes conservan sus identificadores; no se hace unión aproximada de equipos que pueda mezclar partidos. Una clave configurada no significa conexión verificada. Las cuotas pueden estar cacheadas hasta tres minutos; no se afirma sincronización en vivo.

**Validación:** ver `VALIDACION.md`. No se han usado claves de producción ni enviado mensajes a Telegram. La revisión visual real en escritorio/móvil y los contratos de los cuatro proveedores quedan pendientes antes de fusionar.

## Referencias de API

- https://the-odds-api.com/liveapi/guides/v4/
- https://the-odds-api.com/sports-odds-data/betting-markets.html
- https://www.api-football.com/documentation_v3

## Incorporación y reversión

Revisar la pull request y los límites anteriores. Probar la rama localmente con credenciales de pruebas antes de autorizar la fusión a `main`. No cambiar la rama conectada a la aplicación publicada sin aprobación. Si la aplicación sigue `main`, la fusión puede actualizar Streamlit automáticamente: la aprobación de fusión debe contemplar ese despliegue. Para revertir, usar un revert del commit de fusión en GitHub; no reescribir el historial de producción.
