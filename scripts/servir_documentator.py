#!/usr/bin/env python3
"""
Sirve en local la documentacion generada por la skill documentar-objeto.

    python servir_documentator.py
    python servir_documentator.py --org MI-SANDBOX --abrir vivo/usuarios

Cuatro cosas, en el mismo servidor:

- Los HTML de `documentator/` (indice y una pagina por objeto), tal cual estan
  en disco.
- El boton «Actualizar» de cada pagina de objeto (/api/actualizar): lanza Claude
  Code sin interfaz (`claude -p`) con la skill para ese objeto y el org elegido,
  y la pagina se recarga cuando termina. Las actualizaciones van de una en una,
  porque todas reescriben el indice y el manifiesto.
- Las paginas en vivo (/vivo/usuarios, /vivo/licencias, /vivo/flows), que
  consultan el org en cada carga y no escriben nada a disco: muestran datos de
  usuarios del org, y un fichero con ellos acabaria versionado.
- La pagina de Omni-Channel (/vivo/omnichannel), que es la excepcion: recogerla
  tarda decenas de segundos, asi que enseña lo ultimo guardado en
  documentator/_vivo/ y solo «Actualizar» vuelve a consultar el org.

La sesion con el org se toma prestada de la CLI `sf` (ver org.py). La unica
escritura en el org es la asignacion de permisos de la gestion de usuarios
(/api/permisos), por POST y con confirmacion en la pagina.

Solo usa la libreria estandar de Python: no hay nada que instalar.

Autor: vsaez@nubestic.com
"""

import argparse
import collections
import concurrent.futures
import datetime
import http.server
import json
import mimetypes
import pathlib
import queue
import re
import shutil
import subprocess
import threading
import traceback
import urllib.parse
import webbrowser

import org as modulo_org
import secciones
import sf_doc_generator

BASE = pathlib.Path(__file__).resolve().parent
PLANTILLA = BASE / "plantilla"
SKILL = BASE.parent / "SKILL.md"

PUERTO_POR_DEFECTO = 8788

# Una actualizacion es una sesion entera de la skill: con muchas formulas y flows
# nuevos tarda minutos, pero mas de esto es que se ha quedado colgada.
TIMEOUT_ACTUALIZACION = 45 * 60

SUFIJOS_TECNICOS = ("__mdt", "__e", "__b", "__x", "__xo", "__ChangeEvent", "__Share", "__History", "__Feed")

NOMBRE_OBJETO = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,79}$")

PIE_VIVO = (
    "Los datos se consultan al org en cada carga y en cada cambio de org; no se guardan en disco. La hora "
    "de la última consulta aparece en la cabecera."
)
PIE_GUARDADO = (
    "Los datos se guardan en documentator/_vivo/ cada vez que se consultan, y la página enseña lo último "
    "guardado de cada org: «Actualizar» vuelve a consultar el org. La fecha de la consulta aparece en la cabecera."
)

# Lo unico que puede hacer la sesion lanzada por el boton. Va en modo dontAsk, asi
# que lo que no este aqui se deniega sin preguntar: no hay nadie para contestar.
# Es lo que necesita la skill —leer el repo, consultar el org, bajar los flows a
# documentator/ y ejecutar el generador, que borra el mismo sus temporales— y nada mas.
HERRAMIENTAS = [
    "Read", "Glob", "Grep",
    "Write(./documentator/**)", "Edit(./documentator/**)",
]
for _shell in ("Bash", "PowerShell"):
    HERRAMIENTAS += [
        _shell + "(sf sobject describe *)",
        _shell + "(sf data query *)",
        _shell + "(sf api request rest *)",
        _shell + "(sf project retrieve start *--target-metadata-dir documentator/.tmp_flows*)",
        _shell + "(python *sf_doc_generator.py*)",
        _shell + "(py *sf_doc_generator.py*)",
    ]
# `sf api request rest` admite cualquier verbo: solo se deja el GET por defecto.
DENEGADAS = [
    "Bash(sf api request rest *--method*)", "Bash(sf api request rest * -X *)",
    "PowerShell(sf api request rest *--method*)", "PowerShell(sf api request rest * -X *)",
]

PROMPT = (
    "Actualización automática de documentación, lanzada desde el botón «Actualizar» de la "
    "página del objeto o desde «Documentar objeto» del índice. Lee {skill} y sigue sus pasos para documentar el objeto {objeto} en el "
    "org {org}, en el modo no interactivo que describe. Es lo único que hay que hacer: no "
    "preguntes ni pidas confirmación, no compares con el org, no abras tarjetas ni sigas el "
    "ciclo de trabajo del proyecto, no hagas commits y no escribas fuera de documentator/. "
    "Termina con una frase que diga qué se ha actualizado o por qué ha fallado."
)


def pagina_vivo(pagina):
    """
    Esqueleto de una pagina en vivo con CSS y JS embebidos. Se lee del disco en
    cada peticion, para que tocar la plantilla y refrescar baste.
    """
    return (
        (PLANTILLA / "vivo.html").read_text(encoding="utf-8")
        .replace("__TITULO__", pagina["titulo"])
        .replace("__PAGINA__", pagina["id"])
        .replace("__PIE__", PIE_GUARDADO if pagina.get("guardar") else PIE_VIVO)
        .replace("__GUARDADA__", "1" if pagina.get("guardar") else "")
        .replace("/*__CSS__*/", (PLANTILLA / "vivo.css").read_text(encoding="utf-8"))
        .replace("/*__JS__*/", (PLANTILLA / "vivo.js").read_text(encoding="utf-8"))
    )


def recoger_datos(alias, pagina):
    """Lanza las consultas de las secciones de una pagina y devuelve su payload."""
    conexion = modulo_org.Org(alias)

    # La sesion se resuelve antes de repartir el trabajo: si no, los hilos
    # entrarian a la vez a pedir el token y cada uno lanzaria su propia CLI.
    url = conexion.url_instancia()

    modulos = pagina["modulos"]
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(modulos)) as ejecutor:
        pendientes = [ejecutor.submit(modulo.recoger, conexion) for modulo in modulos]
        resultados = [pendiente.result() for pendiente in pendientes]

    return {
        "org": alias,
        "urlInstancia": url,
        "esProduccion": conexion.es_produccion(),
        "pagina": pagina["id"],
        "consultado": datetime.datetime.now().strftime("%H:%M:%S"),
        # Lo que se guarda en disco se lee otro dia: ahi la hora sola no basta.
        "consultadoEl": datetime.datetime.now().isoformat(timespec="seconds"),
        "secciones": resultados,
    }


class Actualizaciones:
    """
    Cola de actualizaciones de objetos, atendida por un solo hilo.

    El estado de cada objeto se guarda en memoria mientras viva el servidor: es lo
    que consulta la pagina para saber cuando recargarse.
    """

    def __init__(self, raiz):
        self.raiz = raiz
        self.cola = queue.Queue()
        self.estado = collections.OrderedDict()
        self.cerrojo = threading.Lock()
        threading.Thread(target=self._atender, daemon=True).start()

    def pedir(self, objeto, alias):
        with self.cerrojo:
            actual = self.estado.get(objeto)
            if actual and actual["estado"] in ("en-cola", "actualizando"):
                return actual
            self.estado[objeto] = {
                "objeto": objeto, "org": alias, "estado": "en-cola",
                "pedido": _ahora(), "inicio": "", "fin": "", "mensaje": "",
                "pasos": PASOS, "paso": -1, "accion": "",
            }
            self.cola.put(objeto)
            return dict(self.estado[objeto])

    def consultar(self, objeto):
        with self.cerrojo:
            actual = self.estado.get(objeto)
            return dict(actual) if actual else {"objeto": objeto, "estado": "ninguna"}

    def en_curso(self):
        """Las que están en cola o en marcha, para que el índice las retome al abrirse."""
        with self.cerrojo:
            return [dict(e) for e in self.estado.values() if e["estado"] in ("en-cola", "actualizando")]

    def _marcar(self, objeto, **campos):
        with self.cerrojo:
            self.estado[objeto].update(campos)

    def _atender(self):
        while True:
            objeto = self.cola.get()
            alias = self.estado[objeto]["org"]
            self._marcar(objeto, estado="actualizando", inicio=_ahora())
            print("  [{}] {} · ACTUALIZAR {} (claude -p)".format(_ahora(), alias, objeto))
            try:
                correcto, mensaje = self._lanzar(objeto, alias)
            except Exception as error:
                traceback.print_exc()
                correcto, mensaje = False, str(error)
            self._marcar(objeto, estado="hecho" if correcto else "error", fin=_ahora(), mensaje=mensaje)
            print("      {} · {}".format("hecho" if correcto else "ERROR", mensaje[:300]))

    def _lanzar(self, objeto, alias):
        claude = shutil.which("claude") or shutil.which("claude.exe") or shutil.which("claude.cmd")
        if not claude:
            return False, "No se encuentra Claude Code (`claude`) en el PATH."

        html = self.raiz / "documentator" / objeto / (objeto + ".html")
        antes = html.stat().st_mtime if html.exists() else None

        comando = [
            claude, "-p", PROMPT.format(skill=SKILL.as_posix(), objeto=objeto, org=alias),
            "--permission-mode", "dontAsk",
            # Sin los ajustes de usuario ni los locales: sus permisos amplios (p. ej.
            # cualquier `sf project`) valdrian tambien aqui. Los del proyecto traen
            # los hooks que vigilan las escrituras en el org.
            "--setting-sources", "project",
            "--allowedTools", *HERRAMIENTAS,
            "--disallowedTools", *DENEGADAS,
            # stream-json: un evento por línea mientras trabaja, para ir contando
            # en qué paso va. En modo -p exige --verbose.
            "--output-format", "stream-json", "--verbose",
            "--no-session-persistence",
        ]
        proceso = subprocess.Popen(
            comando, cwd=str(self.raiz), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
        )
        vigia = threading.Timer(TIMEOUT_ACTUALIZACION, proceso.kill)
        vigia.start()
        # stderr se vacía aparte: si se llena su tubería, el proceso se bloquea.
        errores = []
        threading.Thread(target=lambda: errores.append(proceso.stderr.read()), daemon=True).start()

        respuesta = None
        try:
            for linea in proceso.stdout:
                try:
                    evento = json.loads(linea)
                except json.JSONDecodeError:
                    continue
                if evento.get("type") == "result":
                    respuesta = evento
                for paso, accion in _avances(evento):
                    with self.cerrojo:
                        actual = self.estado[objeto]
                        actual["accion"] = accion
                        actual["paso"] = max(actual["paso"], paso)
            proceso.wait()
        finally:
            vigia.cancel()

        if respuesta is not None:
            mensaje = (respuesta.get("result") or "").strip()
            fallo = respuesta.get("is_error") or proceso.returncode != 0
        else:
            mensaje = ("".join(errores) or "La sesión terminó sin respuesta (¿tiempo agotado?).").strip()[-800:]
            fallo = True

        # La sesion puede acabar «bien» sin haber regenerado nada (p. ej. si le
        # denegaron el generador): lo que cuenta es que el HTML sea nuevo.
        regenerado = html.exists() and html.stat().st_mtime != antes
        if not fallo and not regenerado:
            return False, "La sesión terminó sin regenerar la página. " + mensaje
        return not fallo, mensaje or "Sin mensaje de la sesión."


def _ahora():
    return datetime.datetime.now().strftime("%H:%M:%S")


# Los pasos de la skill, en orden, para la barra de progreso de la página.
PASOS = ["Fórmulas", "Flows", "Triggers", "Revisar", "Página"]


def _paso_de(herramienta, entrada):
    """Índice en PASOS de lo que está haciendo una llamada a herramienta, o -1."""
    texto = json.dumps(entrada, ensure_ascii=False)
    if "sf_doc_generator.py" in texto:
        return 0 if "--formulas" in texto else 4
    if "_review_" in texto:
        return 3
    if "_triggers_" in texto or re.search(r"triggers/|\.trigger\b|TriggerDispatcher|Handler", texto):
        return 2
    if re.search(r"FlowDefinitionView|_flow_|tmp_flows|tooling/sobjects/Flow", texto):
        return 1
    if "_formula_cache" in texto or "_explanations_" in texto:
        return 0
    return -1


def _accion_de(herramienta, entrada):
    """Una línea legible de lo que hace la llamada."""
    if herramienta in ("Bash", "PowerShell"):
        comando = " ".join((entrada.get("command") or "").split())
        consulta = re.search(r"FROM\s+(\w+)", comando)
        if "sf_doc_generator.py" in comando:
            return "Lee las fórmulas del objeto" if "--formulas" in comando else "Genera la página"
        if "retrieve start" in comando:
            return "Descarga los flows que hay que describir"
        if consulta:
            return "Consulta " + consulta.group(1)
        return comando[:90]
    ruta = entrada.get("file_path") or entrada.get("pattern") or entrada.get("path") or ""
    nombre = ruta.replace("\\", "/").rsplit("/", 1)[-1]
    verbo = {"Read": "Lee", "Write": "Escribe", "Edit": "Edita", "Glob": "Busca", "Grep": "Busca"}.get(herramienta, herramienta)
    return (verbo + " " + nombre).strip()


def _avances(evento):
    """(paso, acción) de cada llamada a herramienta de un evento de stream-json."""
    if evento.get("type") != "assistant":
        return []
    avances = []
    for bloque in (evento.get("message") or {}).get("content") or []:
        if bloque.get("type") == "tool_use":
            entrada = bloque.get("input") or {}
            avances.append((_paso_de(bloque.get("name"), entrada), _accion_de(bloque.get("name"), entrada)))
    return avances


class Manejador(http.server.BaseHTTPRequestHandler):
    # Inyectados por main() antes de arrancar.
    org_por_defecto = ""
    raiz = pathlib.Path.cwd()
    actualizaciones = None

    @property
    def documentator(self):
        return self.raiz / "documentator"

    def _seguro(self):
        """
        Las rutas que escriben exigen la cabecera X-Documentator y JSON. Un formulario o un
        fetch sin cabeceras de cualquier web abierta en el navegador puede hacer
        POST a localhost, pero no con una cabecera propia sin pasar antes por CORS,
        que este servidor no concede.
        """
        return self.headers.get("X-Documentator") == "1" and "application/json" in (self.headers.get("Content-Type") or "")

    def do_POST(self):
        partes = urllib.parse.urlparse(self.path)
        try:
            if partes.path not in ("/api/permisos", "/api/actualizar"):
                return self.send_error(404)
            if not self._seguro():
                return self._responder_json({"error": "Petición no admitida."}, codigo=403)

            longitud = int(self.headers.get("Content-Length") or 0)
            cuerpo = json.loads(self.rfile.read(longitud).decode("utf-8") or "{}")
            alias = cuerpo.get("org") or self.org_por_defecto

            if partes.path == "/api/actualizar":
                return self._responder_json(self._actualizar(cuerpo.get("objeto") or "", alias))

            print("  [{}] {} · ESCRITURA: {} {} {} a {} usuarios".format(
                _ahora(), alias, cuerpo.get("accion"),
                len(cuerpo.get("permisos") or []), cuerpo.get("tipo"), len(cuerpo.get("usuarios") or [])
            ))
            resultado = secciones.usuarios.cambiar_permisos(modulo_org.Org(alias), cuerpo)
            print("      hechas {} · sin cambio {} · errores {}".format(
                resultado["hechas"], resultado["sinCambio"], len(resultado["errores"])
            ))
            return self._responder_json(resultado)
        except Exception as error:
            traceback.print_exc()
            return self._responder_json({"error": str(error)}, codigo=500)

    def do_GET(self):
        partes = urllib.parse.urlparse(self.path)
        consulta = urllib.parse.parse_qs(partes.query)

        try:
            if partes.path.startswith("/vivo/"):
                pagina = secciones.pagina(partes.path[len("/vivo/"):].strip("/"))
                if not pagina:
                    return self.send_error(404)
                return self._responder(pagina_vivo(pagina).encode("utf-8"), "text/html; charset=utf-8", 200)
            if partes.path == "/api/config":
                return self._responder_json(self._config())
            if partes.path == "/api/datos":
                return self._responder_json(self._datos(consulta))
            if partes.path == "/api/detalle":
                return self._responder_json(self._detalle(consulta))
            if partes.path == "/api/actualizacion":
                objeto = (consulta.get("objeto") or [""])[0]
                return self._responder_json(self.actualizaciones.consultar(objeto))
            if partes.path == "/api/actualizaciones":
                return self._responder_json(self.actualizaciones.en_curso())
            if partes.path == "/api/objetos":
                return self._responder_json(self._objetos(consulta))
            return self._fichero(partes.path)
        except Exception as error:
            traceback.print_exc()
            return self._responder_json({"error": str(error)}, codigo=500)

    def _fichero(self, ruta):
        """Un fichero de documentator/, sin salir de la carpeta."""
        relativa = urllib.parse.unquote(ruta).lstrip("/") or "index.html"
        destino = (self.documentator / relativa).resolve()
        if self.documentator.resolve() not in destino.parents or not destino.is_file():
            return self.send_error(404)
        tipo = mimetypes.guess_type(destino.name)[0] or "application/octet-stream"
        if tipo.startswith("text/") or tipo == "application/json":
            tipo += "; charset=utf-8"
        return self._responder(destino.read_bytes(), tipo, 200)

    def _config(self):
        return {"org": self.org_por_defecto, "orgs": modulo_org.alias_disponibles()}

    def _datos(self, consulta):
        alias = (consulta.get("org") or [self.org_por_defecto])[0]
        pagina = secciones.pagina((consulta.get("pagina") or [""])[0])
        if not pagina:
            raise ValueError("Página desconocida.")
        if not pagina.get("guardar"):
            print("  [{}] {} · {}".format(_ahora(), alias, pagina["id"]))
            return recoger_datos(alias, pagina)

        # El alias va en el nombre del fichero: solo los que la CLI conoce.
        if alias not in modulo_org.alias_disponibles():
            raise ValueError("La CLI no tiene autenticado el org {!r}.".format(alias))
        fichero = self.documentator / "_vivo" / "{}-{}.json".format(pagina["id"], re.sub(r"[^A-Za-z0-9_.-]", "_", alias))
        if (consulta.get("forzar") or [""])[0] != "1" and fichero.is_file():
            return json.loads(fichero.read_text(encoding="utf-8"))

        print("  [{}] {} · {} (se guarda)".format(_ahora(), alias, pagina["id"]))
        datos = recoger_datos(alias, pagina)
        fichero.parent.mkdir(exist_ok=True)
        fichero.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
        return datos

    def _detalle(self, consulta):
        alias = (consulta.get("org") or [self.org_por_defecto])[0]
        cual = (consulta.get("cual") or [""])[0]
        seccion = (consulta.get("seccion") or [""])[0]
        print("  [{}] {} · detalle {}/{}".format(_ahora(), alias, seccion, cual or "(sin subconjunto)"))
        return secciones.con_detalle(seccion).detalle(modulo_org.Org(alias), cual)

    def _objetos(self, consulta):
        """
        Los objetos que se pueden documentar, para el «Documentar objeto» del índice.
        Solo los que tienen formato de página (layoutable), que deja fuera History,
        Share, Feed o ChangeEvent; los custom metadata (__mdt) sí lo tienen, así
        que ellos y el resto de técnicos se quitan por sufijo, y las custom settings
        por su marca.
        """
        alias = (consulta.get("org") or [self.org_por_defecto])[0]
        print("  [{}] {} · lista de objetos".format(_ahora(), alias))
        objetos = [
            {"nombre": o["name"], "etiqueta": o.get("label") or o["name"], "custom": bool(o.get("custom"))}
            for o in modulo_org.Org(alias).objetos()
            if o.get("layoutable") and o.get("queryable") and not o.get("deprecatedAndHidden")
            and not o.get("customSetting") and not o["name"].endswith(SUFIJOS_TECNICOS)
        ]
        objetos.sort(key=lambda o: o["etiqueta"].lower())
        manifiesto = self.documentator / "manifest.json"
        documentados = [e["object"] for e in json.loads(manifiesto.read_text(encoding="utf-8"))] if manifiesto.is_file() else []
        return {"org": alias, "objetos": objetos, "documentados": documentados}

    def _actualizar(self, objeto, alias):
        if not NOMBRE_OBJETO.match(objeto):
            raise ValueError("Nombre de objeto no válido: {!r}.".format(objeto))
        # Solo orgs que la CLI tiene autenticados: el alias acaba dentro del prompt.
        if alias not in modulo_org.alias_disponibles():
            raise ValueError("La CLI no tiene autenticado el org {!r}.".format(alias))
        return self.actualizaciones.pedir(objeto, alias)

    def _responder_json(self, cuerpo, codigo=200):
        self._responder(
            json.dumps(cuerpo, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
            codigo,
        )

    def _responder(self, cuerpo, tipo, codigo):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        # Se consulta en vivo y las paginas se regeneran: nada de versiones cacheadas.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def log_message(self, formato, *argumentos):
        pass  # El acceso HTTP no aporta nada; ya se traza cada consulta al org.


def main():
    parser = argparse.ArgumentParser(
        description="Sirve en local la documentación de documentar-objeto.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--org", default="", help="Alias del org que se abre por defecto. Sin él, el primero de la CLI.")
    parser.add_argument("--puerto", type=int, default=PUERTO_POR_DEFECTO, help="Puerto local.")
    parser.add_argument("--raiz", default=".", help="Carpeta que contiene documentator/: la raíz del proyecto.")
    parser.add_argument("--abrir", default="", help="Ruta que abrir en el navegador, p. ej. vivo/usuarios.")
    parser.add_argument("--sin-abrir", action="store_true", help="No abrir el navegador al arrancar.")
    argumentos = parser.parse_args()

    raiz = pathlib.Path(argumentos.raiz).resolve()
    (raiz / "documentator").mkdir(exist_ok=True)

    # El indice solo se reescribe al documentar un objeto: sin esto, una pagina en
    # vivo nueva no tendria enlace hasta la siguiente vez.
    if (raiz / "documentator" / "manifest.json").is_file():
        sf_doc_generator.write_index(str(raiz / "documentator"))

    print("Documentator")
    print("  Preparando sesión…", end=" ", flush=True)
    alias = modulo_org.alias_disponibles()
    org_por_defecto = argumentos.org or (alias[0] if alias else "")
    try:
        if org_por_defecto:
            modulo_org.Org(org_por_defecto).url_instancia()
        print("lista.")
    except modulo_org.ErrorCli as error:
        print("\n  Aviso: no se pudo abrir sesión con {} ({}).".format(org_por_defecto, error))

    Manejador.org_por_defecto = org_por_defecto
    Manejador.raiz = raiz
    Manejador.actualizaciones = Actualizaciones(raiz)

    servidor = http.server.ThreadingHTTPServer(("127.0.0.1", argumentos.puerto), Manejador)
    url = "http://127.0.0.1:{}/".format(argumentos.puerto)

    print("  {}".format(url))
    print("  Org por defecto: {}".format(org_por_defecto or "(ninguno)"))
    print("  Ctrl+C para parar.\n")

    if not argumentos.sin_abrir:
        threading.Timer(0.5, lambda: webbrowser.open(url + argumentos.abrir.lstrip("/"))).start()

    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nParado.")
        servidor.shutdown()


if __name__ == "__main__":
    main()
