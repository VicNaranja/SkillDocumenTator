"""
Seccion "Licencias": estado de las licencias del org — de usuario, de permission
set y de paquetes instalados — y como se reparten los usuarios activos.

Todo son consultas de lectura que se lanzan en paralelo al abrir la pagina.

Gotchas de los objetos, comprobados contra un org:

- `UserLicense.UsedLicenses` no se puede filtrar desde la API 64, un ORDER BY
  sobre el se ignora sin avisar y los agregados fallan. Se traen las filas y se
  ordena aqui. En `PermissionSetLicense`, en cambio, si funciona todo.
- `UsedLicenses` solo cuenta usuarios activos, en las dos.
- Agrupar `User` por `Profile.UserLicense.Name` descarta sin avisar a los
  usuarios sin perfil visible (Automated Process, Cloud Integration User). Por
  `Profile.UserLicenseId` salen como grupo nulo, y se cuentan aparte.
- `PackageLicense.AllowedLicenses = -1` no esta documentado; se observa en los
  paquetes con licencia de sitio o gratuitos. No trae el nombre del paquete,
  solo el namespace, que ademas se repite.
"""

import concurrent.futures

ID = "licencias"
TITULO = "Licencias"

# Sin login en este numero de dias, o nunca, cuenta como "sin entrar".
DIAS_SIN_LOGIN = 90

SIN_LOGIN = "(LastLoginDate < LAST_N_DAYS:{} OR LastLoginDate = null)".format(DIAS_SIN_LOGIN)


def _licencias_usuario(org):
    filas = org.consultar(
        "SELECT Id, Name, MasterLabel, Status, TotalLicenses, UsedLicenses, UsedLicensesLastUpdated "
        "FROM UserLicense"
    )
    return [
        {
            "id": f["Id"],
            "nombre": f.get("MasterLabel") or f.get("Name"),
            "estado": f.get("Status"),
            "total": f.get("TotalLicenses") or 0,
            "usadas": f.get("UsedLicenses") or 0,
            "actualizado": f.get("UsedLicensesLastUpdated"),
        }
        for f in filas
    ]


def _licencias_permission_set(org):
    filas = org.consultar(
        "SELECT MasterLabel, DeveloperName, Status, TotalLicenses, UsedLicenses, ExpirationDate "
        "FROM PermissionSetLicense ORDER BY MasterLabel"
    )
    return [
        {
            "nombre": f.get("MasterLabel") or f.get("DeveloperName"),
            "estado": f.get("Status"),
            "total": f.get("TotalLicenses") or 0,
            "usadas": f.get("UsedLicenses") or 0,
            "caduca": f.get("ExpirationDate"),
        }
        for f in filas
    ]


def _licencias_paquete(org):
    filas = org.consultar(
        "SELECT NamespacePrefix, Status, AllowedLicenses, UsedLicenses, ExpirationDate "
        "FROM PackageLicense ORDER BY NamespacePrefix"
    )
    return [
        {
            "namespace": f.get("NamespacePrefix") or "",
            "estado": f.get("Status"),
            "permitidas": f.get("AllowedLicenses"),
            "usadas": f.get("UsedLicenses") or 0,
            "caduca": f.get("ExpirationDate"),
        }
        for f in filas
    ]


def _por_licencia(org, filtro):
    """Usuarios activos por Id de licencia del perfil; la clave None son los sin perfil visible."""
    filas = org.consultar(
        "SELECT Profile.UserLicenseId lic, COUNT(Id) n FROM User WHERE IsActive = true{} "
        "GROUP BY Profile.UserLicenseId".format(filtro)
    )
    return {f.get("lic"): f.get("n") or 0 for f in filas}


def _por_perfil(org, filtro):
    filas = org.consultar(
        "SELECT Profile.Name perfil, Profile.UserLicense.MasterLabel lic, COUNT(Id) n FROM User "
        "WHERE IsActive = true{} GROUP BY Profile.Name, Profile.UserLicense.MasterLabel".format(filtro)
    )
    return {(f.get("perfil"), f.get("lic")): f.get("n") or 0 for f in filas}


def recoger(org):
    sin_login = " AND " + SIN_LOGIN

    tareas = {
        "usuario": lambda: _licencias_usuario(org),
        "permissionSet": lambda: _licencias_permission_set(org),
        "paquete": lambda: _licencias_paquete(org),
        "activosPorLicencia": lambda: _por_licencia(org, ""),
        "sinLoginPorLicencia": lambda: _por_licencia(org, sin_login),
        "activosPorPerfil": lambda: _por_perfil(org, ""),
        "sinLoginPorPerfil": lambda: _por_perfil(org, sin_login),
    }
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(tareas)) as ejecutor:
        pendientes = {clave: ejecutor.submit(tarea) for clave, tarea in tareas.items()}
        r = {clave: pendiente.result() for clave, pendiente in pendientes.items()}

    nombres = {licencia["id"]: licencia["nombre"] for licencia in r["usuario"]}
    por_licencia = sorted(
        (
            {
                "licencia": nombres.get(lic, "(sin licencia de perfil)") if lic else "(sin licencia de perfil)",
                "activos": n,
                "sinLogin": r["sinLoginPorLicencia"].get(lic, 0),
            }
            for lic, n in r["activosPorLicencia"].items()
        ),
        key=lambda fila: -fila["activos"],
    )
    por_perfil = sorted(
        (
            {
                "perfil": perfil or "(sin perfil visible)",
                "licencia": licencia or "",
                "activos": n,
                "sinLogin": r["sinLoginPorPerfil"].get((perfil, licencia), 0),
            }
            for (perfil, licencia), n in r["activosPorPerfil"].items()
        ),
        key=lambda fila: -fila["activos"],
    )

    usuario = sorted(r["usuario"], key=lambda f: (-f["usadas"], -f["total"], f["nombre"] or ""))
    for licencia in usuario:
        del licencia["id"]

    return {
        "id": ID,
        "titulo": TITULO,
        "descripcion": (
            "Licencias del org y cómo se reparten los usuarios activos. Las usadas son las que cuenta "
            "Salesforce, que solo incluye usuarios activos; las disponibles, el total menos las usadas, y pueden "
            "salir negativas si el org va por encima de lo contratado."
        ),
        "diasSinLogin": DIAS_SIN_LOGIN,
        "usuario": usuario,
        "permissionSet": sorted(r["permissionSet"], key=lambda f: (-f["usadas"], -f["total"], f["nombre"] or "")),
        "paquete": r["paquete"],
        "porLicencia": por_licencia,
        "porPerfil": por_perfil,
    }
