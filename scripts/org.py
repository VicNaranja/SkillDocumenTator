"""
Acceso a un org de Salesforce para las paginas en vivo del documentator.

Casi todo es lectura. Las unicas escrituras son `Org.crear_registros` y
`Org.borrar_registros`, con las que la gestion de usuarios asigna y quita
permisos. La Metadata API se usa solo para leer (`Org.leer_metadata`), con lo
que la API REST no da.

La CLI `sf` se usa una unica vez por org, para tomar prestado el token de la
sesion que ya tiene abierta; a partir de ahi las consultas van directas a la
API REST por HTTP. El motivo es de latencia: cada invocacion de `sf` arranca un
proceso Node completo (~3 s), y una pagina lanza decenas de consultas.

No se gestionan credenciales propias: si la CLI no tiene sesion con el org, aqui
no hay nada que hacer.
"""

import json
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

API_POR_DEFECTO = "v62.0"
TIMEOUT = 120

# readMetadata no admite mas nombres por llamada.
METADATA_POR_LLAMADA = 10

SOBRE_METADATA = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
    'xmlns:met="http://soap.sforce.com/2006/04/metadata">'
    "<soapenv:Header><met:SessionHeader><met:sessionId>{token}</met:sessionId></met:SessionHeader></soapenv:Header>"
    "<soapenv:Body><met:readMetadata><met:type>{tipo}</met:type>{nombres}</met:readMetadata></soapenv:Body>"
    "</soapenv:Envelope>"
)


def _sin_espacio(etiqueta):
    return etiqueta.rsplit("}", 1)[-1]


def _a_dict(nodo):
    """
    Un elemento XML de la Metadata API como dict: los hijos que se repiten son
    listas y las hojas, su texto. Los tipos no se interpretan: todo es texto.
    """
    hijos = list(nodo)
    if not hijos:
        return nodo.text or ""
    resultado = {}
    for hijo in hijos:
        clave = _sin_espacio(hijo.tag)
        valor = _a_dict(hijo)
        if clave in resultado:
            if not isinstance(resultado[clave], list):
                resultado[clave] = [resultado[clave]]
            resultado[clave].append(valor)
        else:
            resultado[clave] = valor
    return resultado

# Lo que no cambia entre peticiones se guarda mientras viva el servidor: la
# sesion del org y el describe de cada objeto (que es un payload grande y caro).
_sesiones = {}
_describes = {}
_catalogo = {}
_produccion = {}


class ErrorCli(Exception):
    """Fallo al hablar con la CLI `sf` o con la API del org."""


def _sf(argumentos):
    # En Windows `sf` es un .cmd, que subprocess no resuelve sin ayuda.
    ruta = shutil.which("sf") or shutil.which("sf.cmd")
    if not ruta:
        raise ErrorCli("No se encuentra la CLI `sf` en el PATH.")

    proceso = subprocess.run(
        [ruta] + argumentos + ["--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    try:
        respuesta = json.loads(proceso.stdout)
    except json.JSONDecodeError:
        raise ErrorCli(
            "Respuesta no interpretable de `sf {}`:\n{}".format(
                " ".join(argumentos[:3]), (proceso.stderr or proceso.stdout)[:800]
            )
        )

    if respuesta.get("status") != 0:
        raise ErrorCli(
            "`sf {}` falló: {}".format(
                " ".join(argumentos[:3]), respuesta.get("message", "sin mensaje")
            )
        )

    return respuesta["result"]


def alias_disponibles():
    """
    Alias de orgs que la CLI tiene autenticados, para el selector de org.

    Se cachea porque `sf org list` es de las llamadas mas lentas (arranca Node y
    contacta con cada org) y la lista no cambia mientras el servidor vive. Sin
    cache, esos segundos los paga cada carga de la pagina.
    """
    if "alias" not in _catalogo:
        try:
            resultado = _sf(["org", "list"])
        except ErrorCli:
            return []

        alias = []
        for grupo in ("nonScratchOrgs", "scratchOrgs", "sandboxes", "other"):
            for entrada in resultado.get(grupo, []) or []:
                nombre = entrada.get("alias") or entrada.get("username")
                if nombre and nombre not in alias:
                    alias.append(nombre)
        _catalogo["alias"] = alias

    return _catalogo["alias"]


class Org:
    """
    Un org concreto. Es lo unico que reciben los modulos de seccion, para que no
    dependan de como se accede al org.
    """

    def __init__(self, alias):
        self.alias = alias

    # ------------------------------------------------------------------ #

    def _sesion(self, renovar=False):
        if renovar or self.alias not in _sesiones:
            datos = _sf(["org", "display", "--target-org", self.alias])

            # `org display` devuelve el token censurado ("[REDACTED] Use 'sf org
            # auth show-access-token' to view"), incluso con --verbose. Ese literal
            # llega al org como cabecera y lo rechaza con INVALID_AUTH_HEADER, asi
            # que el token hay que pedirlo por el comando que indica el propio aviso.
            token = _sf(["org", "auth", "show-access-token", "--target-org", self.alias])
            token = (token.get("accessToken") if isinstance(token, dict) else token) or ""

            if not token or token.startswith("[REDACTED]"):
                raise ErrorCli(
                    "No se pudo obtener el token de «{}». Comprueba que la CLI tiene "
                    "sesión activa (`sf org login web --alias {}`).".format(self.alias, self.alias)
                )

            _sesiones[self.alias] = (
                datos["instanceUrl"].rstrip("/"),
                token,
                "v" + str(datos.get("apiVersion") or API_POR_DEFECTO).lstrip("v"),
            )
        return _sesiones[self.alias]

    def _get(self, ruta, reintentado=False, cabeceras=None):
        """
        GET contra la API del org. `ruta` puede ser absoluta (la que devuelve
        `nextRecordsUrl` al paginar) o relativa a /services/data/<API>/.
        """
        url_base, token, api = self._sesion()
        url = url_base + (ruta if ruta.startswith("/") else "/services/data/{}/{}".format(api, ruta))

        peticion = urllib.request.Request(
            url, headers=dict(cabeceras or {}, Authorization="Bearer " + token)
        )

        try:
            with urllib.request.urlopen(peticion, timeout=TIMEOUT) as respuesta:
                return json.loads(respuesta.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            # El token cacheado puede haber caducado: se renueva y se reintenta una vez.
            if error.code == 401 and not reintentado:
                self._sesion(renovar=True)
                return self._get(ruta, reintentado=True, cabeceras=cabeceras)

            detalle = error.read().decode("utf-8", "replace")[:600]
            raise ErrorCli("La API del org respondió {} en {}:\n{}".format(error.code, ruta, detalle))

    def _enviar(self, metodo, ruta, cuerpo=None, reintentado=False):
        """
        Peticion con otro metodo que GET, contra /services/data/<API>/<ruta>, con
        cuerpo JSON opcional.
        """
        url_base, token, api = self._sesion()
        cabeceras = {"Authorization": "Bearer " + token}
        if cuerpo is not None:
            cabeceras["Content-Type"] = "application/json"
        peticion = urllib.request.Request(
            url_base + "/services/data/{}/{}".format(api, ruta),
            data=None if cuerpo is None else json.dumps(cuerpo).encode("utf-8"),
            headers=cabeceras,
            method=metodo,
        )
        try:
            with urllib.request.urlopen(peticion, timeout=TIMEOUT) as respuesta:
                contenido = respuesta.read().decode("utf-8")
                return json.loads(contenido) if contenido else None
        except urllib.error.HTTPError as error:
            if error.code == 401 and not reintentado:
                self._sesion(renovar=True)
                return self._enviar(metodo, ruta, cuerpo=cuerpo, reintentado=True)

            detalle = error.read().decode("utf-8", "replace")[:600]
            raise ErrorCli("La API del org respondió {} en {}:\n{}".format(error.code, ruta, detalle))

    # ------------------------------------------------------------------ #

    def consultar(self, soql, tooling=False):
        """
        Lanza una SOQL y devuelve todos los registros, paginando si hace falta.

        Ojo: las consultas agregadas (GROUP BY) no paginan — no traen
        `nextRecordsUrl` —, y en cuanto los grupos no caben en un lote el org
        corta con error ("Aggregate query does not support queryMore()"). Por
        eso hay que agrupar por dimensiones de pocos valores, nunca por Id.
        """
        camino = "tooling/query" if tooling else "query"
        respuesta = self._get("{}?q={}".format(camino, urllib.parse.quote(soql)))

        registros = respuesta.get("records", [])
        while not respuesta.get("done", True) and respuesta.get("nextRecordsUrl"):
            respuesta = self._get(respuesta["nextRecordsUrl"])
            registros.extend(respuesta.get("records", []))

        return registros

    def consultar_pagina(self, soql=None, cursor=None, tamano=2000):
        """
        Una sola pagina de una SOQL, para listados que no se pueden traer enteros.

        Sin `cursor` lanza la consulta; con el `siguiente` de una pagina anterior,
        trae la que va detras. El cursor es el `nextRecordsUrl` del org: un
        localizador que caduca a los 15 minutos sin usarse, asi que una pagina
        pedida mas tarde exige volver a empezar.

        OFFSET no sirve para esto: SOQL lo topa en 2.000 filas.
        """
        if cursor:
            respuesta = self._get(cursor)
        else:
            respuesta = self._get(
                "query?q={}".format(urllib.parse.quote(soql)),
                cabeceras={"Sforce-Query-Options": "batchSize={}".format(tamano)},
            )

        return {
            "registros": respuesta.get("records", []),
            "total": respuesta.get("totalSize", 0),
            "siguiente": None if respuesta.get("done", True) else respuesta.get("nextRecordsUrl"),
        }

    def crear_registros(self, registros):
        """
        Crea registros con la API de colecciones, en lotes de 200 y sin
        todo-o-nada: un registro que falla no tumba a los demas.

        Devuelve un resultado por registro, en el mismo orden: {id, success,
        errors}.
        """
        resultados = []
        for inicio in range(0, len(registros), 200):
            resultados.extend(self._enviar("POST", "composite/sobjects", cuerpo={
                "allOrNone": False,
                "records": registros[inicio:inicio + 200],
            }))
        return resultados

    def borrar_registros(self, ids):
        """
        Borra registros por Id con la API de colecciones, en lotes de 200 y sin
        todo-o-nada. Devuelve un resultado por Id, en el mismo orden.
        """
        resultados = []
        for inicio in range(0, len(ids), 200):
            ruta = "composite/sobjects?allOrNone=false&ids=" + ",".join(ids[inicio:inicio + 200])
            resultados.extend(self._enviar("DELETE", ruta))
        return resultados

    def leer_metadata(self, tipo, nombres, reintentado=False):
        """
        Componentes de metadata por su fullName, con readMetadata de la Metadata
        API (SOAP), en lotes de 10. Devuelve un dict por componente que exista;
        los que no existen no salen.

        Es para lo que no esta en ningun sObject, como los ajustes de un org o la
        gestion de sesiones de un canal de mensajeria.
        """
        url_base, token, api = self._sesion()
        registros = []
        for inicio in range(0, len(nombres), METADATA_POR_LLAMADA):
            lote = nombres[inicio:inicio + METADATA_POR_LLAMADA]
            sobre = SOBRE_METADATA.format(
                token=escape(token),
                tipo=escape(tipo),
                nombres="".join("<met:fullNames>{}</met:fullNames>".format(escape(n)) for n in lote),
            )
            peticion = urllib.request.Request(
                url_base + "/services/Soap/m/" + api.lstrip("v"),
                data=sobre.encode("utf-8"),
                headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": '""'},
            )
            try:
                with urllib.request.urlopen(peticion, timeout=TIMEOUT) as respuesta:
                    raiz = ET.fromstring(respuesta.read())
            except urllib.error.HTTPError as error:
                detalle = error.read().decode("utf-8", "replace")
                # La sesion caducada llega como fallo SOAP, no como 401.
                if "INVALID_SESSION_ID" in detalle and not reintentado:
                    self._sesion(renovar=True)
                    return self.leer_metadata(tipo, nombres, reintentado=True)
                raise ErrorCli("La Metadata API respondió {} al leer {}:\n{}".format(error.code, tipo, detalle[:600]))

            for nodo in raiz.iter():
                # Un componente que no existe vuelve como <records xsi:nil="true"/>, vacio.
                if _sin_espacio(nodo.tag) == "records" and len(nodo):
                    registros.append(_a_dict(nodo))
        return registros

    def _describe(self, objeto):
        clave = (self.alias, objeto)
        if clave not in _describes:
            _describes[clave] = self._get("sobjects/{}/describe".format(objeto))
        return _describes[clave]

    def describir(self, objeto):
        """Definicion de cada campo del objeto, tal como la da el describe del org."""
        return self._describe(objeto).get("fields", [])

    def etiquetas_objetos(self):
        """{nombre API: etiqueta} de todos los objetos del org, del describe global."""
        clave = (self.alias, "__global__")
        if clave not in _describes:
            _describes[clave] = {
                o["name"]: o.get("label") or o["name"] for o in self._get("sobjects").get("sobjects", [])
            }
        return _describes[clave]

    def objetos(self):
        """Los objetos del describe global del org, tal como los devuelve."""
        return self._get("sobjects").get("sobjects", [])

    def picklist(self, objeto, campo):
        """Devuelve {valor: etiqueta} de un picklist, leido del describe del org."""
        for definicion in self._describe(objeto).get("fields", []):
            if definicion.get("name") == campo:
                return {
                    valor["value"]: (valor.get("label") or valor["value"])
                    for valor in definicion.get("picklistValues", [])
                }

        return {}

    def es_produccion(self):
        """
        True si el org no es un sandbox.

        Se pregunta al org y no se deduce del alias: el alias es una etiqueta
        local que cada maquina pone como quiere, y fiarse de el es lo que hace que
        alguien lea o escriba datos reales creyendo que mira un sandbox.

        Se cachea porque para un alias dado no cambia nunca.
        """
        if self.alias not in _produccion:
            registros = self.consultar("SELECT IsSandbox FROM Organization LIMIT 1")
            _produccion[self.alias] = bool(registros) and not registros[0]["IsSandbox"]

        return _produccion[self.alias]

    def url_instancia(self):
        """URL base del org, para poder enlazar cada registro desde la pagina."""
        return self._sesion()[0]
