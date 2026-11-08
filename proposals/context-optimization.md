# Plan de optimización de contexto y caché

Este documento reúne el diseño acordado para optimizar el contexto de Sigil sin
perseguir el porcentaje de cache hits como objetivo aislado. Describe el estado
actual, las mediciones exploratorias, el trabajo pendiente y las pruebas que
deben cumplirse antes de activar la política por defecto.

**Estado: correcciones de la auditoría implementadas y verificadas localmente.** La calibración y la calidad con tareas reales siguen pendientes. No hay
evidencia de ahorro facturado ni de calidad equivalente en un proveedor real.

La revisión detectó que la primera implementación no cumplía el diseño: usaba una
ventana móvil por turno, sintetizaba eventos periódicos y varias políticas LOD
perdían campos reales. Esos comportamientos no se consideran decisiones aceptadas.
Las regresiones ahora usan modelos reales, agentes, SQLite y adaptadores con
transportes simulados. La suite existente, por sí sola, no validaba los requisitos.

## Lo que quedó implementado

Las siguientes piezas implementan el recorrido revisado.

| Pieza | Dónde | Qué hace |
| --- | --- | --- |
| Reloj de steps | `context/compaction/clock.py` | Step = una respuesta del modelo; se deriva del transcript y por eso sobrevive a reanudar, rollback y fork |
| Evento | `context/compaction/events.py` | Evento inmutable y fronteras versionadas: step, motivo, objetivos no cumplidos y contadores sin contenido |
| Reglas | `context/compaction/policy.py` | Techos y objetivos inclusivos; la edad determina elegibilidad, no aplicación inmediata |
| Coordinador | `context/compaction/coordinator.py`, `projection.py` | Histéresis entre turnos, LOD por tandas y ajuste de presupuesto compartido; el hook bloquea requests que no entran |
| LOD | `tooling/lod.py` | Enum, funciones puras por lado y política genérica para tools ajenas |
| Declaraciones | `tooling/lod_policies.py` | Input y output de cada tool propia; el build falla si falta una |
| Estrategias | `context/strategy/windowed.py`, `compacting.py` | Usan el coordinador y reportan los eventos en `ViewResult` |
| Debug | `context/meter/debug*.py`, `ui/widgets/debug_view.py` | Tabla por turno: contexto usado, caché, y los eventos de ese turno |
| Skills | `skills/tools.py`, `skills/loaded.py`, `skills/rendering.py` | Las lecturas devuelven una referencia; el texto viaja por el bloque dinámico; los binarios no se envían |
| Persistencia | `context/capability.py`, metadata `sigil_context` | Versión 2, política efectiva y fronteras en la transacción del transcript; anclaje por parte para rollback, recuperación antes de fusionar envelopes y deduplicación por request |
| Constantes | `context/compaction/constants.py`, `tooling/lod_constants.py` | Techos, objetivos, edades y tamaños semánticos/genéricos con unidades explícitas |
| Simulador | `context/compaction/simulator.py` | Lectura consistente de SQLite sin escrituras, acumulados por request y escenarios hipotéticos de precios |

Los modos que retenían todo el reasoning o nada de él (`off`, `all`) se
eliminaron, junto con `SIGIL_CONTEXT_REASONING` y
`SIGIL_CONTEXT_KEEP_THINKING`. La ventana de steps es la única política.

## Verificación de las correcciones

La suite completa reúne 1.976 pruebas. Pasaron en lotes secuenciales y ejecuciones
focalizadas, incluyendo contratos de las 23 tools propias, tamaños genéricos
agregados, histéresis, persistencia, fallos transaccionales, rollback, concurrencia,
streaming y recursos parciales tras varias reanudaciones. Los adaptadores de
OpenAI Chat, Responses y Anthropic se verificaron con transportes simulados.

La cobertura de ramas y líneas del conjunto focalizado es 93%. `ruff check`,
`ruff format --check` del código Python y `git diff --check` pasan. No hay un
comprobador de tipos configurado. Los tests existentes de renderizado y mensajería
PAS mostraron sensibilidad a carga y pasaron aislados, sin modificar su código.
No se ejecutaron requests contra proveedores reales ni se midió ahorro facturado.

## Lo que falta antes de confiar en los números

La verificación local no reemplaza la calibración con tareas y proveedores reales.

- Medir costo por tarea con sesiones reales y precios del proveedor.
- Calibrar los techos y los cortes de edad con el simulador, comparando el
  acumulado de todos los requests y no el ahorro de un evento aislado.
- Ampliar la prueba local de recursos compactados y múltiples reanudaciones con
  sesiones representativas reales; la regresión sintética ya cubre ese recorrido.
- Evaluar calidad en tareas largas: qué decide peor el agente cuando el detalle
  de una tool ya bajó de nivel.

## Objetivo y criterio de éxito

Minimizar el costo y la latencia por tarea completada, manteniendo calidad,
continuidad, seguridad y espacio suficiente en la ventana del modelo. Un cache
hit es una señal útil, pero no justifica conservar contexto irrelevante de forma
indefinida.

El criterio principal de comparación es el costo total por tarea, incluyendo
input normal, lecturas y escrituras de caché, output, resúmenes, relecturas y
reintentos. Comparar también latencia, calidad, uso máximo de ventana, cambios
de prefijo y cantidad de veces que el agente debe recuperar información.

Una aproximación útil es comparar el costo acumulado de conservar contexto con
el costo de reducirlo: conservarlo implica reenviarlo en requests posteriores;
reducirlo puede provocar una nueva escritura o un miss y, en algunos casos,
obligar a recuperar información. El precio de cada categoría y las reglas de
caché dependen del proveedor.

## Alcance y decisiones acordadas

- Mantener el historial persistido intacto y construir una vista del modelo.
- Introducir eventos de compactación coordinados de mantenimiento de contexto. No confundir
  esta operación con la compactación que crea un resumen de conversación.
- Definir para cada componente un objetivo operacional `x`, un techo `y` y,
  aparte, límites duros de seguridad. Si uno alcanza su techo, un evento de compactación
  aplica en paquete las reducciones elegibles de todos los componentes.
- Usar steps del agente como reloj, continuos entre turnos. Un step debe
  definirse y contarse igual en runtime, replay, debug y calibración; varias
  tools paralelas no deben contarse como varios steps. Distinguir el reloj del
  número de respuestas que contienen reasoning.
- Conservar reasoning reciente entre turnos, incluso si el usuario responde una
  pregunta. Propuesta inicial: techo de 20 steps con reasoning y objetivo de
  10; techo de 10 % de ventana y objetivo de 5 %, cuando el tamaño sea medible.
  Dispara cualquiera de los techos; tras el corte deben cumplirse ambos objetivos
  aplicables. Si la ventana está bajo presión, la seguridad prevalece.
- Crear tres niveles de detalle para tools: completo, LOD 1, LOD 2 y LOD 3.
  Para tools externas o sin política propia, usar como objetivos genéricos
  2.000, 500 y 100 caracteres. Para tools propias, definir políticas semánticas
  específicas para argumentos y resultados, no imponer esos tamaños.
- Mantener instrucciones de skills y recursos de texto ya leídos en la sección
  de esa skill. Releerlos dinámicamente del disco al reconstruir el prompt; no
  guardar snapshots. La salida de scripts sigue en el historial normal.
- Mostrar analíticas claras en debug sin exponer el contenido de la conversación.
  Los bloques dinámicos del prompt deben conservar su resaltado de color.

## Baseline histórico anterior a las correcciones

Esta sección describe el punto de partida, no el comportamiento vigente.
El árbol tiene cambios previos sin commit que deben preservarse. No asumir que
están revisados o terminados simplemente porque existen.

### Contexto y caché

`WindowedStrategy` en `src/engine/context/strategy/windowed.py` todavía usa una
ventana móvil de reasoning del turno en curso. El default es `recent`, con 10
steps y un tope estimado del 5 % de la ventana. Los resultados de tools de turnos
viejos se truncan según las constantes actuales: umbral de 4.000 caracteres y
un máximo de 2.000 caracteres en el resultado truncado, incluido el aviso.

`ContextViewCapability` proyecta el historial sin modificar el log original.
`project()` se usa para debug y debe seguir siendo de solo lectura. La
estrategia `CompactingStrategy` ya existe y tiene semántica propia; el nuevo
coordinador no debe romperla ni llamarse implícitamente compactación con resumen.

El debug existente separa lecturas y escrituras de caché, muestra tablas y no
muestra el contenido de la conversación. `OpenRouterBuilder` tiene activados
breakpoints de caché para modelos `anthropic/`; ese cambio se probó con un
transporte simulado, no con un proveedor real.

### Skills

En `src/engine/skills/loaded.py` y `src/engine/skills/rendering.py` hay una
implementación parcial que deriva lecturas de recursos del transcript y los
relee desde disco al renderizar el bloque de skills. Hay una prueba sencilla de
cambio del archivo y rollback. **No considerar esta parte terminada**: los
resultados originales siguen protegidos en el historial y pueden duplicar el
contenido; faltan pruebas de binarios, lecturas parciales, fallos, compactación,
reanudación y aislamiento por ejecución.

El catálogo automático de skills permanece ordenado por nombre y `list_skills`
existe. No se acordó eliminar el catálogo: su costo debe medirse contra el costo
extra y la confiabilidad de descubrir skills solo mediante una tool.

## Evidencia recogida y límites de las mediciones

### Sesión de referencia

Se consultó SQLite en modo de solo lectura para una captura de esta sesión hasta
la secuencia 115. Había 56 respuestas medidas, 12.872.654 tokens de input y
5.521.664 tokens leídos de caché: **42,89 %** agregado. El modelo registrado
era `gpt-6.1-terra` y el proveedor `openai-chatgpt`. Los contadores de escritura
figuraban en cero; eso no prueba que el proveedor no haya escrito caché.

En una reconstrucción local del historial serializado con el adaptador OpenAI
Responses, se compararon 55 transiciones. Con la política `recent` simulada, 31
eran append-only, 22 tenían como primera diferencia un bloque de reasoning y 2
un resultado de tool. En las transiciones agrupadas por esa clasificación, las
lecturas reales agregadas fueron 51,5 %, 36,8 % y 0 %, respectivamente. Son
correlaciones, no una prueba de causalidad.

El request histórico completo no se guarda: faltan schemas de tools históricos,
parámetros efectivos y, en algunos casos, ventana. Por eso esa reconstrucción no
prueba coincidencia byte a byte ni predice hits reales.

### Reasoning cifrado

En la sesión de referencia se observaron bloques `ThinkingPart` con contenido
textual vacío y firma cifrada. El adaptador puede serializar esos bloques aunque
la heurística actual estime cero tokens a partir de `content`. No interpretar
ese cero como costo cero ni convertir directamente bytes cifrados a tokens
facturados sin evidencia. El límite de cantidad debe seguir funcionando cuando
el tamaño sea desconocido; debug debe distinguir desconocido de cero.

### Simulación de LOD y reasoning visible

Se revisaron siete ejecuciones guardadas de 38 a 522 respuestas. Para la
simulación inicial de la ejecución de 522 respuestas, un intervalo fijo de 10,
15 o 20 steps produjo, respectivamente, 51, 34 o 26 requests con cambios en
resultados anteriores. No son cache misses medidos.

Después se simuló reasoning visible de esa ejecución: 163 bloques y 262.762
caracteres. En eventos de compactación periódicos, conservando los últimos 10 steps con
reasoning y aplicando LOD de tools aproximado, se obtuvieron estos promedios:

| Intervalo | Eventos de compactación | Historial antes | Historial después | Reducción tools | Reducción reasoning |
| --- | ---: | ---: | ---: | ---: | ---: |
| 10 steps | 52 | ~110.772 | ~104.486 | ~5.110 | ~1.177 |
| 15 steps | 34 | ~113.928 | ~104.350 | ~7.778 | ~1.800 |
| 20 steps | 26 | ~116.450 | ~103.878 | ~10.219 | ~2.353 |

Las cifras son tokens estimados del historial. Son promedios de eventos de compactación
ocurridos en posiciones diferentes; no incluyen schemas ni instrucciones fijas,
ni miden el área total de todos los requests. La simulación usó intervalos
fijos, no el coordinador de disparadores combinados acordado.

Con 1.000 tokens sintéticos por respuesta que tenía reasoning, el intervalo 20
dio ~126.183 tokens antes y ~110.118 después, con ~5.846 de reducción media de
reasoning. Es un escenario artificial, no una medición de tokens cifrados.

### Argumentos y resultados de tools

En la ejecución de 522 respuestas, los argumentos sumaron ~142.373 tokens
estimados y los resultados ~284.134. Simulando el reenvío del historial completo
sin poda, sus acumulados fueron ~38,4 millones y ~88,7 millones de tokens,
respectivamente. No son tokens facturados y no descuentan lecturas de caché.

En la sesión de referencia hubo una llamada anómala con argumentos de ~40.456
tokens estimados. Es evidencia de que los argumentos también necesitan política
LOD y de que conviene prevenir cargas excepcionales en el origen; no demuestra
que los argumentos superen los resultados en todas las sesiones.

## Diseño del coordinador

### Modelo de estado y eventos

Definir un evento de compactación versionado por ejecución con identificadores estables de
step y fronteras de reasoning y de cada LOD. Registrar motivo, política efectiva,
valores anteriores y posteriores, y objetivos que no pudieron alcanzarse. No
persistir contenido sensible ni hashes reversibles. Aplicar el mismo evento dos
veces debe ser idempotente.

Decidir con pruebas la integración exacta con SQLite, transacciones y ciclo de
vida del agente. Una ejecución hija debe tener estado propio. Reanudar debe
reconstruir la misma vista; un rollback debe descartar o invalidar eventos de compactación
posteriores al punto restaurado. Una rama alternativa no debe heredar decisiones
de una rama descartada. La lectura de debug no debe escribir eventos.

Contemplar compatibilidad de sesiones anteriores, migraciones, fallos parciales,
concurrencia y cambios de configuración o modelo. No recalcular silenciosamente
una frontera anterior con parámetros nuevos. Documentar la versión de política y
la regla de migración o reinicio de estado.

### Disparadores y coordinación

1. Calcular el estado de cada componente sobre la vista elegible, sin mutar el
   transcript.
2. Si cualquier componente alcanza su techo operacional, abrir un evento de compactación
   normal y llevar cada componente por encima de su objetivo hacia ese objetivo.
3. Si la presión del presupuesto global exige una acción anticipada, abrir un
   evento de compactación de presupuesto sin esperar a los techos normales.
4. Si el request no entra en la ventana, aplicar la ruta de emergencia. Si no
   alcanza, escalar a la compactación existente o informar el error; nunca
   prometer que los mínimos operacionales vencen al límite físico.
5. Registrar cuando una sección protegida o un bloque indivisible impida cumplir
   un objetivo. Evitar bucles de eventos de compactación que no hacen progreso.

Entre eventos de compactación el historial anterior no debe cambiar por estas políticas;
solo se agregan nuevos mensajes. Esto favorece el prefijo, pero otras causas
como herramientas, instrucciones, imágenes, TTL o routing pueden invalidarlo.

### Reasoning

Definir un step con reasoning como una respuesta elegible que contiene uno o más
bloques `ThinkingPart`, y probar respuestas con varios bloques. La ventana de
10–20 debe extenderse entre turnos, sin recuperar bloques ya descartados por
una compactación previa.

Disparar al alcanzar 20 steps elegibles o el 10 % medible de la ventana. Al
evento de compactación, quitar los más antiguos hasta quedar en 10 o menos y en 5 % o menos,
cuando sea calculable. Especificar en tests si el disparo es inclusivo y evitar
ambigüedades fuera de límite. Aplicar la ventana única (sin modos que retengan todo o
nada) y las reglas de compatibilidad de firmas; no reenviar a un proveedor un bloque
que no pueda aceptar.

Para reasoning cifrado o ventana desconocida, contabilizar la cantidad y marcar
el tamaño como desconocido o parcial. Investigar primero qué reporta cada
adaptador. No presentar los porcentajes como garantías si faltan datos. El
presupuesto general y la ruta de emergencia siguen aplicando.

### LOD de tools

Las siguientes distancias absolutas desde el step actual son una hipótesis de
calibración, no defaults validados:

| Frontera | Edad del call (steps) | Tamaño genérico (chars) |
| --- | ---: | ---: |
| Completo | 0–24 | sin tocar |
| LOD 1 | 25–49 | 2.000 |
| LOD 2 | 50–74 | 500 |
| LOD 3 | 75 o más | 100 |

Las edades son fijas y no dependen de los eventos de compactación: son dos
calendarios independientes. Los tamaños aplican solo a tools sin política propia;
cada tool del proyecto declara la suya.

Una tool puede pasar directamente al nivel que le corresponda al reanudar. Los
niveles deben ser monótonos dentro de una rama y construirse desde el contenido
original, no desde otro resumen. Conservar el vínculo entre llamada y resultado,
IDs, orden histórico y estructura que exija el proveedor. El evento de compactación no debe
introducir nuevos mensajes en medio de intercambios de tools ni romper llamadas
paralelas, reintentos o resultados multimodales.

Definir un registro de políticas deterministas y locales para input y output de
cada tool propia. Priorizar `run`, `read_file` y búsqueda según mediciones:

- `run`: preservar comando identificable, estado de salida, timeout y errores;
  truncar salida cuando sea apropiado sin presentar datos parciales como completos.
- `read_file`: preservar ruta y rango; seleccionar una representación que permita
  releer el archivo, sin inventar conclusiones.
- Búsqueda: preservar consulta, alcance, rutas y líneas antes de fragmentos extensos.
- Skills: aplicar el tratamiento de la sección siguiente; salida de scripts no
  se convierte en instrucciones y necesita política propia.

Para tools externas o sin política registrada, usar los objetivos genéricos de
2.000/500/100 caracteres. Preservar nombre, ID y vínculo; los argumentos deben
seguir siendo válidos para el adaptador. Si no puede transformarse de forma
segura un argumento, binario o resultado estructurado, conservarlo y registrar
por qué. Las bandas genéricas son objetivos; no ocultar errores o metadatos
indispensables solo para cumplir una longitud.

## Instrucciones y recursos de skills

Al ejecutar `load_skill`, mantener `SKILL.md` en el bloque dinámico de esa skill.
Al leer con éxito un recurso de texto, recordar por ejecución skill, ruta y límite
realmente solicitado. En cada reconstrucción, releerlo del disco bajo esa skill;
una lectura repetida actualiza o amplía el registro sin duplicados. Definir con
pruebas el tratamiento de lecturas parciales y repetidas con límites distintos.

El usuario acepta que una modificación posterior del archivo cambie el prompt y
pueda causar un miss. Si el archivo desaparece o deja de poder leerse, mostrar
una indicación explícita. No fingir que la versión anterior sigue disponible.
Evitar que texto procedente de un recurso se eleve a una autoridad superior a la
que corresponde; etiquetar claramente su origen.

Los binarios no deben inyectarse como base64 indiscriminadamente. Conservar su
identidad y tratarlos según el formato y capacidades del modelo; no afirmar que
el contenido binario permanece disponible en instrucciones si no es así.

Solo cuando esté garantizado que las instrucciones y el recurso leído se
reconstruyen en la ejecución correcta, reducir el resultado histórico de
`load_skill` o `read_skill_resource` a una confirmación breve. Preservar nombre,
ruta, éxito y hechos necesarios para reconstrucción. Una llamada fallida no
activa una skill ni un recurso. La compactación y rollback no deben perder ni
resucitar recursos; contemplar recursos cuyo `SKILL.md` ya no esté instalado.
La salida de `run_skill_script` permanece en el historial normal.

## Analíticas y debug

Extender la vista TUI y `/debug --text` sin mostrar el contenido del prompt:

- Evento de compactación último y anterior: step, motivo, versión y si fue operacional,
  presupuesto o emergencia.
- Bandas: valor actual, objetivo, techo y estado por componente; indicar si el
  dato es medido, estimado, parcial o desconocido.
- Reasoning: steps conservados y descartados, presupuesto conocido, presencia
  de firmas cuyo tamaño no puede estimarse.
- Tools: cantidad de intercambios por LOD y reducción estimada de argumentos y
  resultados por separado; excepciones y reducciones no aplicadas.
- Skills: costo estimado por instrucciones y recursos leídos, recursos parciales,
  faltantes o binarios, sin mostrar su contenido. Mantener el resaltado cálido de
  bloques dinámicos y etiquetas textuales accesibles.
- Caché y costo: lecturas, escrituras e input normal separados; costo reportado
  frente a estimado y cobertura parcial. No presentar una escritura como hit ni
  interpretar cero ausente como ausencia probada de caché.

El debug debe distinguir métricas observadas del request anterior de una
proyección del siguiente. Debe poder abrirse repetidamente sin cambiar la sesión.

## Calibración y experimento

Construir un simulador reproducible, de solo lectura sobre una copia o snapshot
consistente de SQLite. No registrar contenido de conversaciones en resultados ni
leer secretos. Agrupar por ejecución y respetar turnos, orden, llamadas
paralelas, herramientas protegidas, proveedor y rollback. Comparar el sistema
actual, eventos de compactación coordinados y configuraciones alternativas.

Para cada request estimar la suma acumulada de contexto enviado, no solo el
antes/después de eventos de compactación. Separar instrucciones, schemas, mensajes,
reasoning visible, reasoning de tamaño desconocido, argumentos y resultados.
Informar cantidad de eventos de compactación, primer cambio de prefijo y sus limitaciones.
Ejecutar también escenarios sintéticos con razonamiento de tamaño estable y
outliers de tools; no convertir firmas cifradas en tokens facturados inventados.

Comparar escenarios de precios y caché claramente etiquetados, incluyendo
escrituras, hits parciales, TTL y routing cuando corresponda. El tamaño de prompt
no basta para inferir facturación. No publicar porcentajes de ahorro real sin
requests y costos observados en el proveedor.

Validar con tareas representativas cortas y largas: éxito y calidad de la tarea,
relecturas, errores, latencia, tokens y costo por tarea. No considerar una
configuración ganadora solo por reducir el hit rate o aumentar la compresión.
Elegir defaults después de esa comparación; documentar muestras y sesgos.

## Plan de implementación y criterios de aceptación

1. **Contratos y baseline.** Definir step, identificadores, bandas, política
   versionada, estructura de evento y simulador. Capturar tests de comportamiento
   actual y preservar los cambios existentes. Criterio: replay determinista y
   comparación reproducible de sesiones sin modificar la base.
2. **Estado de ejecución y recuperación.** Integrar eventos de compactación con persistencia,
   reanudación, rollback, ramas y aislamiento de subagentes. Criterio: misma vista
   tras reinicio, sin eventos de compactación futuros tras rollback, debug sin escrituras.
3. **Coordinador y reasoning.** Implementar disparadores duales 10–20 y 5–10 %,
   continuidad entre turnos, firmas compatibles y emergencia. Criterio: poda
   por tandas, idempotencia, datos desconocidos explícitos y ningún bucle.
4. **LOD de tools.** Implementar registro de políticas, fallback y políticas de
   tools propias para argumentos y resultados. Criterio: resultados monotónicos,
   invariantes del proveedor y transcript original intactos.
5. **Skills.** Completar la lectura dinámica protegida y reducir duplicados solo
   cuando sea seguro. Criterio: recursos de proyecto/globales, éxito/fallo,
   binarios, límites, actualizaciones de disco, rollback y compactación probados.
6. **Debug.** Integrar tablas y estados operacionales. Criterio: información
   accionable, color dinámico conservado y ningún contenido privado mostrado.
7. **Calibración.** Ejecutar simulaciones y tareas reales cuando existan medios
   autorizados. Criterio: resultados reproducibles y separación entre datos
   reales, estimaciones y supuestos; ajustar o justificar defaults.
8. **Verificación final.** Ejecutar tests focalizados y suite completa factible,
   lint, formato, tipos configurados, revisión de diff y pruebas de serialización
   con adaptadores reales usando transportes simulados. Documentar cualquier
   fallo preexistente, herramienta ausente o comprobación no ejecutada.

## Matriz mínima de pruebas

- Bandas: debajo, exactamente en y por encima de cada techo; cantidad y tamaño
  disparan por separado; corte satisface ambos objetivos cuando es posible.
- Histéresis: ningún cambio antiguo entre eventos de compactación; varias acciones en uno;
  saltos grandes; ausencia de progreso; límites duros y ventana desconocida.
- Reasoning: continuidad entre turnos; sin reasoning; varios bloques por step;
  firma cifrada sin tamaño conocido; cambio de proveedor; ventana en cero y ventana
  mayor que el turno.
- Tools: varias llamadas paralelas, errores, timeout, argumentos JSON, MCP,
  binarios, resultados cortos y largos, pasos sin tools y orden de llamadas.
- Skills: `SKILL.md`, texto, binario, lectura parcial, éxito/fallo, recursos
  borrados o cambiados, múltiples lecturas, reanudación y rollback por agente.
- Persistencia: reinicio, dos agentes simultáneos, rollback, rama alternativa,
  falla transaccional, sesión antigua y cambio de versión de política.
- Debug: no escribe ni muestra contenido; métricas incompletas y estimadas son
  legibles; resaltado dinámico y representación en terminales estrechas.

## Fuentes externas consultadas

- [Pi: construcción del system prompt](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/system-prompt.ts).
- [Pi: skills](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/skills.md).
- [Pi: compactación](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/compaction.md).
- [OpenAI: prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching).
- [Anthropic: prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching).

Estas fuentes orientan el diseño, pero las reglas exactas dependen de la versión
del adaptador y del proveedor; verificar documentación vigente al implementar.
