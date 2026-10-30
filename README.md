# Mini IDE · Base de Datos 1

Aplicación web tipo **mini IDE** para ejecutar consultas **SQL (PostgreSQL)** y comandos **NoSQL (MongoDB)** desde el navegador. Está pensada como proyecto académico para la asignatura **Base de Datos 1**: permite practicar DDL, DML, consultas y operaciones CRUD sin instalar un cliente de base de datos, con resultados en tabla, visor JSON y consola de errores.

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-psycopg2-4169E1?logo=postgresql&logoColor=white)
![MongoDB](https://img.shields.io/badge/MongoDB-PyMongo-47A248?logo=mongodb&logoColor=white)

---

## 1. Funcionalidad

### Conexiones

- **PostgreSQL** mediante host, puerto, usuario, contraseña y base de datos.
- **MongoDB** mediante URI (`mongodb://...` o `mongodb+srv://...`) y una base de datos opcional.
- Indicadores de estado en la cabecera, las pestañas y las tarjetas de conexión:
  - **verde** = conectado,
  - **rojo** = desconectado.
- Botones para **conectar, reconectar y desconectar**.
- Las credenciales se mantienen únicamente en la memoria del proceso del servidor; no se escriben en archivos ni se devuelven al navegador.
- El navegador recuerda solo datos no sensibles (host, puerto, usuario, base de datos y las consultas escritas). **No guarda contraseñas ni URIs de conexión.**

### Editor

- Editor **CodeMirror** con resaltado de sintaxis SQL o JavaScript (según la pestaña activa), numeración de líneas, autocompletado de paréntesis, coincidencia de brackets y línea activa.
- Pestañas para alternar entre **PostgreSQL** y **MongoDB**, conservando el texto de cada una.
- Atajo **Ctrl + Enter** / **⌘ + Enter** para ejecutar.
- Plantillas de ejemplo para SQL y MongoDB.
- Si el CDN de CodeMirror no está disponible, la aplicación usa un editor de texto básico como respaldo.

### Ejecución SQL

- Acepta scripts con **una o varias sentencias** separadas por `;`.
- Soporta `CREATE`, `ALTER`, `DROP`, `TRUNCATE`, `INSERT`, `UPDATE`, `DELETE`, `SELECT`, `WITH`, `EXPLAIN`, funciones, transacciones explícitas, etc.
- Para `SELECT` devuelve columnas, tipos, filas y número de filas.
- Para DDL/DML devuelve el comando, las filas afectadas y un mensaje de confirmación en español.
- Las sentencias se ejecutan con `autocommit`; si una sentencia falla, se hace `ROLLBACK` y se informa el error con código SQLSTATE, detalle, sugerencia y posición.
- Conversión segura a JSON de tipos como `numeric`, `date`, `timestamp`, `timestamptz`, `uuid`, `json/jsonb` y arreglos.
- Límite de visualización configurable (1000 filas por defecto) para no bloquear el navegador.

### Ejecución MongoDB

Interpreta una sintaxis reducida del shell de Mongo:

```javascript
db.coleccion.insertOne({ nombre: "Ana", nota: 18.5 });
db.coleccion.insertMany([{ nombre: "Luis" }, { nombre: "María" }]);
db.coleccion.find({ nota: { $gte: 16 } }).sort({ nota: -1 }).skip(0).limit(10);
db.coleccion.findOne({ correo: "ana@uni.edu" });
db.coleccion.updateOne({ correo: "ana@uni.edu" }, { $set: { nota: 19 } });
db.coleccion.updateMany({ activo: false }, { $set: { activo: true } });
db.coleccion.replaceOne({ _id: ObjectId("...") }, { nombre: "Nuevo" });
db.coleccion.deleteOne({ correo: "ana@uni.edu" });
db.coleccion.deleteMany({ activo: false });
db.coleccion.countDocuments({ activo: true });
db.coleccion.distinct("carrera", { activo: true });
db.coleccion.aggregate([{ $group: { _id: "$carrera", total: { $sum: 1 } } }]);
db.coleccion.createIndex({ correo: 1 }, { unique: true });
db.createCollection("nueva_coleccion", { validator: { $jsonSchema: { bsonType: "object" } } });
db.getCollectionNames();
db.coleccion.drop();
db.dropDatabase();
```

También acepta **comandos en JSON**, útil para generar operaciones desde otros programas o para evitar errores de comillas:

```json
{
  "collection": "estudiantes",
  "action": "find",
  "filter": { "nota": { "$gte": 16 } },
  "projection": { "nombre": 1, "nota": 1, "_id": 0 },
  "sort": { "nota": -1 },
  "limit": 10
}
```

Se soportan valores BSON habituales: `ObjectId(...)`, `ISODate(...)`, `new Date(...)`, `NumberInt(...)`, `NumberLong(...)`, `NumberDecimal(...)`, `Timestamp(t, i)`, `MinKey()`, `MaxKey()` y expresiones regulares `/patrón/flags`.

Para datos devuelve los documentos y la unión de columnas; para escrituras devuelve estadísticas (`inserted_id`, `matched_count`, `modified_count`, `deleted_count`, `upserted_id`, etc.). El límite por defecto es de 100 documentos y el máximo absoluto de 1000.

### Resultados y consola

- Tabla HTML con encabezados, tipos de columna, numeración de filas y celdas especiales (`NULL`, números, booleanos, objetos y fechas).
- Visor **JSON** con resaltado de sintaxis, copia al portapapeles y descarga del resultado.
- Tarjetas de confirmación para DDL/DML con filas afectadas y comando ejecutado.
- Vista de valores escalares (por ejemplo `countDocuments` o `distinct`).
- Panel de **Resultados** redimensionable (arrastrando el separador) y **consola de logs** inferior contraíble.
- Logs con marca de tiempo y niveles: `INFO`, `OK`, `ERROR`, `WARN` y `RUN`.
- Manejo estructurado de errores de conexión, autenticación, sintaxis, esquema, permisos y conflictos de integridad, con mensajes claros para la interfaz.

---

## 2. Tecnologías

| Capa | Tecnología |
|---|---|
| Backend | Python 3.10+, FastAPI, Uvicorn |
| PostgreSQL | `psycopg2-binary` |
| MongoDB | `pymongo` |
| Configuración | `python-dotenv` |
| Frontend | HTML5, CSS3, JavaScript nativo (`fetch`) |
| Estilos | Tailwind CSS (CDN) + CSS propio |
| Editor | CodeMirror 5 (CDN) |

---

## 3. Estructura del proyecto

```text
Proyecto_Final_Base_de_Datos_1/
├── static/
│   ├── index.html          # Interfaz del IDE
│   ├── styles.css          # Tema oscuro y componentes
│   └── app.js              # Estado, fetch, editor y renderizado
├── database/
│   ├── __init__.py
│   ├── sql_driver.py       # Conexión y ejecución PostgreSQL
│   └── nosql_driver.py     # Parser shell/JSON y ejecución MongoDB
├── routers/
│   ├── __init__.py
│   ├── sql_router.py       # Endpoints /api/sql/*
│   └── nosql_router.py     # Endpoints /api/nosql/*
├── main.py                 # FastAPI, archivos estáticos y /api/status
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

---

## 4. Requisitos previos

- Python **3.10 o superior**.
- PostgreSQL en ejecución (local o remoto) con un usuario y base de datos accesibles.
- MongoDB en ejecución (local, Docker o MongoDB Atlas) si se usará la pestaña NoSQL.
- Conexión a Internet para cargar Tailwind CSS, CodeMirror y las fuentes desde CDN.

### Levantar motores de prueba con Docker (opcional)

```bash
# PostgreSQL
docker run --name pg-bd1 \
  -e POSTGRES_PASSWORD=postgres \
  -p 5432:5432 \
  -d postgres:16

# MongoDB
docker run --name mongo-bd1 \
  -p 27017:27017 \
  -d mongo:7
```

Con estos contenedores, los valores de ejemplo son:

- PostgreSQL: `localhost`, puerto `5432`, usuario `postgres`, contraseña `postgres`, base `postgres`.
- MongoDB: `mongodb://localhost:27017`, base `mini_ide_bd1`.

---

## 5. Instalación y arranque

### Windows (PowerShell)

```powershell
git clone https://github.com/STRYKER-LUIS/Proyecto_Final_Base_de_Datos_1.git
cd Proyecto_Final_Base_de_Datos_1
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt

Copy-Item .env.example .env
uvicorn main:app --reload
```

> Si PowerShell bloquea la activación del entorno virtual:
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`

### Linux / macOS

```bash
git clone https://github.com/STRYKER-LUIS/Proyecto_Final_Base_de_Datos_1.git
cd Proyecto_Final_Base_de_Datos_1
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt

cp .env.example .env
uvicorn main:app --reload
```

Abrir en el navegador:

```text
http://127.0.0.1:8000
```

También se puede arrancar directamente:

```bash
python main.py
```

La documentación interactiva de la API queda disponible en:

- Swagger UI: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`

### Variables de entorno

Copiar `.env.example` a `.env` y ajustar si es necesario:

| Variable | Descripción | Valor por defecto |
|---|---|---|
| `APP_HOST` | Host de Uvicorn al usar `python main.py` | `127.0.0.1` |
| `APP_PORT` | Puerto de Uvicorn al usar `python main.py` | `8000` |
| `APP_RELOAD` | Recarga automática (`true`/`false`) | `false` |
| `CORS_ORIGINS` | Orígenes permitidos, separados por coma | `*` |
| `SQL_DEFAULT_HOST` | Host precargado para PostgreSQL | `localhost` |
| `SQL_DEFAULT_PORT` | Puerto precargado para PostgreSQL | `5432` |
| `SQL_DEFAULT_USER` | Usuario precargado para PostgreSQL | `postgres` |
| `SQL_DEFAULT_DB` | Base de datos precargada para PostgreSQL | `postgres` |
| `MONGO_DEFAULT_URI` | URI precargada para MongoDB | `mongodb://localhost:27017` |
| `MONGO_DEFAULT_DB` | Base de datos de respaldo para MongoDB | `mini_ide_bd1` |
| `SQL_RESULT_LIMIT` | Máximo de filas devueltas por consulta | `1000` |
| `MONGO_RESULT_LIMIT` | Documentos devueltos por `find` por defecto | `100` |

> Las contraseñas y las URIs **no** se envían desde el `.env` al navegador. El formulario solo se precarga con datos no sensibles.

---

## 6. Uso rápido

1. Abrir `http://127.0.0.1:8000`.
2. En la tarjeta **PostgreSQL**, completar host, puerto, usuario, contraseña y base de datos; pulsar **Conectar**. El indicador debe ponerse verde.
3. En la tarjeta **MongoDB**, escribir la URI y la base de datos; pulsar **Conectar**.
4. Elegir la pestaña del motor y escribir la consulta.
5. Pulsar **Ejecutar consulta** o `Ctrl + Enter`.
6. Revisar la tabla/JSON en **Resultados** y los mensajes en la **Consola** inferior.

### Ejemplo PostgreSQL

```sql
CREATE TABLE IF NOT EXISTS estudiantes (
    id       SERIAL PRIMARY KEY,
    nombre   VARCHAR(100) NOT NULL,
    correo   VARCHAR(150) UNIQUE,
    nota     NUMERIC(4,2) DEFAULT 0
);

INSERT INTO estudiantes (nombre, correo, nota) VALUES
    ('Ana Torres',  'ana@universidad.edu',  18.5),
    ('Luis Pérez',  'luis@universidad.edu', 15.0);

SELECT * FROM estudiantes ORDER BY nota DESC;
```

La tabla de resultados permite alternar a **JSON** y copiar o descargar la respuesta completa.

### Ejemplo MongoDB

```javascript
db.estudiantes.insertMany([
  { nombre: "Ana",  nota: 18.5, activo: true,  cursos: ["BD1", "Prog II"] },
  { nombre: "Luis", nota: 15.0, activo: true,  cursos: ["BD1"] }
]);

db.estudiantes.updateOne(
  { nombre: "Luis" },
  { $set: { nota: 16.0 } }
);

db.estudiantes.find({ activo: true }).sort({ nota: -1 }).limit(10);
```

---

## 7. API REST

### `POST /api/sql/connect`

```json
{
  "host": "localhost",
  "port": 5432,
  "user": "postgres",
  "password": "postgres",
  "dbname": "postgres"
}
```

Respuesta correcta:

```json
{
  "success": true,
  "message": "Conexión establecida con PostgreSQL.",
  "connection": {
    "connected": true,
    "host": "localhost",
    "port": 5432,
    "user": "postgres",
    "database": "postgres",
    "server_version": "PostgreSQL 17.5",
    "connected_at": "2026-10-30T12:00:00-06:00"
  }
}
```

### `POST /api/sql/execute`

```json
{ "query": "SELECT * FROM estudiantes;" }
```

Respuesta (resumida):

```json
{
  "success": true,
  "engine": "postgresql",
  "duration_ms": 4.2,
  "statement_count": 1,
  "results": [
    {
      "type": "query",
      "kind": "SELECT",
      "statement": "SELECT * FROM estudiantes",
      "columns": [
        { "name": "id", "type": "int4" },
        { "name": "nombre", "type": "varchar" }
      ],
      "rows": [[1, "Ana Torres"]],
      "row_count": 1,
      "truncated": false,
      "max_rows": 1000
    }
  ]
}
```

Para DDL/DML:

```json
{
  "type": "command",
  "kind": "INSERT",
  "statement": "INSERT INTO ...",
  "command": "INSERT 0 2",
  "affected_rows": 2,
  "message": "2 fila(s) afectada(s) por INSERT."
}
```

### `POST /api/nosql/connect`

```json
{
  "uri": "mongodb://localhost:27017",
  "database": "mini_ide_bd1"
}
```

> Si la URI incluye una base de datos (`mongodb://localhost:27017/mi_bd`), esa base tiene prioridad. El campo `database` se usa cuando la URI no la indica.

### `POST /api/nosql/execute`

```json
{ "command": "db.estudiantes.find({ activo: true }).limit(5)" }
```

### Otros endpoints

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/api/status` | Estado de PostgreSQL y MongoDB |
| `GET` | `/api/config` | Valores por defecto y límites (sin secretos) |
| `GET` | `/api/health` | Comprobación básica del servidor |
| `POST` | `/api/sql/disconnect` | Cierra la conexión PostgreSQL |
| `POST` | `/api/nosql/disconnect` | Cierra el cliente MongoDB |
| `GET` | `/api/sql/status` | Estado de PostgreSQL |
| `GET` | `/api/nosql/status` | Estado de MongoDB |

---

## 8. Formato de errores

Todos los errores controlados usan la misma estructura:

```json
{
  "success": false,
  "error": {
    "type": "syntax",
    "message": "Error de sintaxis SQL.",
    "detail": "error de sintaxis en o cerca de \"SELEC\"",
    "hint": "Revisa comillas, paréntesis, comas y palabras clave...",
    "code": "42601",
    "position": "1",
    "statement": "SELEC * FROM estudiantes"
  }
}
```

Tipos posibles: `connection`, `auth`, `permission`, `syntax`, `schema`, `conflict`, `validation`, `timeout`, `query`, `config`, `database`, `server` y `http`.

Códigos HTTP usados: `400` (consulta inválida), `401` (autenticación), `403` (permisos), `409` (conflicto o sin conexión), `422` (validación), `500` (error inesperado) y `503` (servidor de base de datos no disponible).

---

## 9. Alcance y limitaciones

Este proyecto es una herramienta **académica y local**, no un producto multiusuario:

- **No incluye autenticación ni autorización de la aplicación.** Cualquier persona con acceso a la URL puede ejecutar SQL/MongoDB con las credenciales activas. No exponer en Internet sin protegerlo.
- La ejecución de SQL es deliberadamente libre (DDL/DML), porque el objetivo es practicar. No hay lista blanca de sentencias.
- Las conexiones son **singleton por proceso**: una conexión PostgreSQL y una MongoDB compartidas por todas las pestañas del navegador. Reiniciar Uvicorn las borra.
- El driver SQL usa `autocommit`. Si un script de varias sentencias falla en la sentencia N, las anteriores ya se confirmaron, salvo que se usen transacciones explícitas (`BEGIN` / `COMMIT` / `ROLLBACK`).
- Los resultados se limitan para proteger la interfaz: 1000 filas en SQL y 100 documentos por defecto en MongoDB (máximo 1000). El límite se puede ajustar con variables de entorno.
- El parser de MongoDB implementa un **subconjunto** del shell (ver sección 1). Operaciones como `mapReduce`, `findOneAndUpdate`, transacciones, `bulkWrite` o *change streams* no están soportadas.
- La interfaz depende de CDN para Tailwind, CodeMirror y fuentes. Sin Internet, el editor básico funciona, pero el aspecto visual puede degradarse.
- Los datos de conexión viven en memoria; no se persisten contraseñas en disco.

---

## 10. Solución de problemas

| Problema | Posible causa / solución |
|---|---|
| `password authentication failed` | Usuario/contraseña incorrectos, o `pg_hba.conf` no permite el acceso. |
| `No se pudo conectar a PostgreSQL` | Servicio detenido, host/puerto incorrectos o firewall. Comprobar con `psql -h localhost -U postgres`. |
| `No se pudo conectar a MongoDB` | `mongod` no está corriendo, URI incorrecta o el puerto no es 27017. Probar `mongosh "mongodb://localhost:27017"`. |
| MongoDB Atlas falla la autenticación | Agregar `?authSource=admin` si corresponde y habilitar la IP en *Network Access*. |
| `ModuleNotFoundError` | Activar el entorno virtual e instalar `pip install -r requirements.txt`. |
| Error al instalar `psycopg2-binary` | Usar una versión de Python soportada o instalar los binarios de PostgreSQL. |
| `Address already in use` | Cambiar el puerto: `uvicorn main:app --port 8001` o `APP_PORT=8001`. |
| La interfaz se ve sin estilos | Verificar acceso a los CDN de Tailwind/CodeMirror o la conexión a Internet. |
| Se pierde la conexión tras reiniciar el servidor | Es esperado: las conexiones viven en memoria. Volver a pulsar **Conectar**. |
| Consulta SQL muy grande | Aumentar `SQL_RESULT_LIMIT` o agregar `LIMIT`/filtros a la consulta. |

---

## 11. Checklist de alcance esperado

- [x] Backend FastAPI con routers separados para SQL y NoSQL.
- [x] Conexión PostgreSQL con prueba de credenciales y estado en memoria.
- [x] Conexión MongoDB mediante URI y base de datos.
- [x] Ejecución de DDL, DML y `SELECT` con metadatos de columnas.
- [x] Ejecución de comandos de colección, `insertOne`, `insertMany`, `find`, `updateOne`, `deleteOne`, etc.
- [x] Manejo estructurado de errores con códigos HTTP adecuados.
- [x] Interfaz con pestañas PostgreSQL/MongoDB, estados verde/rojo, editor, resultados y logs.
- [x] Tabla HTML y visor JSON para resultados.
- [x] Consola de logs/errores en la parte inferior.
- [x] README con alcance, instalación, ejemplos y API.

---

**Autor:** Proyecto académico de Base de Datos 1.
**Stack:** FastAPI + psycopg2 + PyMongo + Tailwind CSS + CodeMirror.
