# Audio Converter to MP3 (Azure Function)

A robust and scalable Azure Function written in Python that converts various audio formats to MP3, splits the output into configurable time chunks, and securely uploads the results to Azure Blob Storage.

---

## 🇬🇧 English

### ✨ Features
- **Format Agnostic Input**: Automatically detects input audio formats using the `filetype` library.
- **High-Performance Conversion**: Utilizes a bundled `ffmpeg` binary for reliable, dependency-free audio processing in serverless environments.
- **Audio Chunking**: Splits the converted MP3 into smaller, manageable chunks (default: 60 seconds).
- **Secure Storage**: Uploads files directly to Azure Blob Storage and generates time-limited SAS (Shared Access Signature) URLs for secure access.
- **Customizable Output**: Configure bitrate, channels, chunk duration, and SAS expiry dynamically via request parameters.
- **Automatic Cleanup**: Safely removes temporary files and directories after processing to optimize serverless resource usage and prevent disk overflow.

### 📋 Prerequisites
- Python 3.9+ (compatible with Azure Functions V4 programming model)
- Azure Functions Core Tools
- An active Azure Storage Account
- **FFmpeg Binary**: This project relies on a pre-compiled `ffmpeg` binary. You must place the executable in a folder named `ffmpeg-bin` at the root of the project.

### ⚙️ Environment Variables
Configure the following settings in your `local.settings.json` (for local development) or in the Azure Function App Configuration (for production):

| Variable | Required | Description | Default |
|---|---|---|---|
| `AZURE_STORAGE_CONNECTION_STRING` | Yes | The connection string for your Azure Storage Account. | - |
| `BLOB_CONTAINER_NAME` | No | The name of the container where files will be stored. | `audio-output` |

### 🚀 API Usage

**Endpoint:** `POST /api/AudioToMP3Converter`

#### Request
You can send the audio file either as a **raw binary body** with query parameters, or as a **JSON payload**.

**Option 1: JSON Payload (Recommended)**
```json
{
  "content_base64": "<base64_encoded_audio_string>",
  "chunk_duration_seconds": 60,
  "audio_bitrate": "64k",
  "audio_channels": 1,
  "sas_expiry_minutes": 30,
  "return_private_urls": false
}
```

**Option 2: Binary Body with Query Parameters**
```http
POST /api/AudioToMP3Converter?chunk_duration_seconds=60&audio_bitrate=64k&audio_channels=1&sas_expiry_minutes=30&return_private_urls=false
Content-Type: audio/wav

<raw_audio_bytes>
```

#### Response
```json
{
  "request_id": "123e4567-e89b-12d3-a456-426614174000",
  "detected_input_mime": "audio/wav",
  "detected_input_extension": "wav",
  "output_mime": "audio/mpeg",
  "output_extension": "mp3",
  "audio_bitrate": "64k",
  "audio_channels": 1,
  "chunk_duration_seconds": 60,
  "sas_expiry_minutes": 30,
  "chunk_count": 3,
  "full_file": {
    "blob_name": "123e4567-e89b-12d3-a456-426614174000/full.mp3",
    "file_size_bytes": 102400,
    "download_url": "https://<storage-account>.blob.core.windows.net/audio-output/...?<sas-token>"
  },
  "chunks": [
    {
      "index": 1,
      "file_name": "chunk_001.mp3",
      "blob_name": "123e4567-e89b-12d3-a456-426614174000/chunk_001.mp3",
      "output_mime": "audio/mpeg",
      "output_extension": "mp3",
      "file_size_bytes": 34000,
      "download_url": "https://<storage-account>.blob.core.windows.net/audio-output/...?<sas-token>"
    }
  ]
}
```

### 🛠️ Important: Configuring `ffmpeg-bin`
The function is designed to be self-contained. On startup, it copies the `ffmpeg` binary from the `ffmpeg-bin` directory to `/tmp/ffmpeg` and grants it execution permissions (`chmod +x`). 

**Setup Steps:**
1. Download a static build of FFmpeg for Linux (e.g., from [John Van Sickle's static builds](https://johnvansickle.com/ffmpeg/) or a similar trusted source).
2. Extract the `ffmpeg` executable.
3. Create a folder named `ffmpeg-bin` in the root of this repository.
4. Place the `ffmpeg` executable inside this folder (ensure it is named exactly `ffmpeg`).
5. Commit the binary to your repository. If `.gitignore` blocks it, you can force add it:  
   `git add --force ffmpeg-bin/ffmpeg`

> **Note:** The current implementation targets Linux environments (copying to `/tmp/ffmpeg`). If you plan to deploy to a Windows-based Azure Function App, you must update the `TARGET_FFMPEG` path in `function_app.py` to a Windows-compatible path (e.g., `D:\\home\\site\\wwwroot\\ffmpeg.exe`).

---

## 🇪🇸 Español

### ✨ Características
- **Entrada Agnóstica al Formato**: Detecta automáticamente el formato de audio de entrada utilizando la librería `filetype`.
- **Conversión de Alto Rendimiento**: Utiliza un binario `ffmpeg` incluido para un procesamiento de audio fiable y sin dependencias externas en entornos serverless.
- **Segmentación de Audio**: Divide el MP3 convertido en fragmentos más pequeños y manejables (por defecto: 60 segundos).
- **Almacenamiento Seguro**: Sube los archivos directamente a Azure Blob Storage y genera URLs con SAS (Firma de Acceso Compartido) con tiempo de expiración para un acceso seguro.
- **Salida Personalizable**: Configura el bitrate, los canales, la duración de los fragmentos y la expiración del SAS dinámicamente a través de los parámetros de la solicitud.
- **Limpieza Automática**: Elimina de forma segura los archivos y directorios temporales después del procesamiento para optimizar el uso de recursos serverless y evitar el desbordamiento del disco.

### 📋 Requisitos Previos
- Python 3.9+ (compatible con el modelo de programación V4 de Azure Functions)
- Azure Functions Core Tools
- Una cuenta de Azure Storage activa
- **Binario FFmpeg**: Este proyecto depende de un binario `ffmpeg` precompilado. Debes colocar el ejecutable en una carpeta llamada `ffmpeg-bin` en la raíz del proyecto.

### ⚙️ Variables de Entorno
Configura los siguientes ajustes en tu `local.settings.json` (para desarrollo local) o en la Configuración de la Function App en Azure (para producción):

| Variable | Requerida | Descripción | Valor por defecto |
|---|---|---|---|
| `AZURE_STORAGE_CONNECTION_STRING` | Sí | La cadena de conexión de tu cuenta de Azure Storage. | - |
| `BLOB_CONTAINER_NAME` | No | El nombre del contenedor donde se almacenarán los archivos. | `audio-output` |

### 🚀 Uso de la API

**Endpoint:** `POST /api/AudioToMP3Converter`

#### Solicitud
Puedes enviar el archivo de audio ya sea como un **cuerpo binario sin formato** con parámetros de consulta, o como una **carga útil JSON**.

**Opción 1: Carga útil JSON (Recomendado)**
```json
{
  "content_base64": "<cadena_de_audio_en_base64>",
  "chunk_duration_seconds": 60,
  "audio_bitrate": "64k",
  "audio_channels": 1,
  "sas_expiry_minutes": 30,
  "return_private_urls": false
}
```

**Opción 2: Cuerpo Binario con Parámetros de Consulta**
```http
POST /api/AudioToMP3Converter?chunk_duration_seconds=60&audio_bitrate=64k&audio_channels=1&sas_expiry_minutes=30&return_private_urls=false
Content-Type: audio/wav

<bytes_de_audio_sin_formato>
```

#### Respuesta
```json
{
  "request_id": "123e4567-e89b-12d3-a456-426614174000",
  "detected_input_mime": "audio/wav",
  "detected_input_extension": "wav",
  "output_mime": "audio/mpeg",
  "output_extension": "mp3",
  "audio_bitrate": "64k",
  "audio_channels": 1,
  "chunk_duration_seconds": 60,
  "sas_expiry_minutes": 30,
  "chunk_count": 3,
  "full_file": {
    "blob_name": "123e4567-e89b-12d3-a456-426614174000/full.mp3",
    "file_size_bytes": 102400,
    "download_url": "https://<storage-account>.blob.core.windows.net/audio-output/...?<sas-token>"
  },
  "chunks": [
    {
      "index": 1,
      "file_name": "chunk_001.mp3",
      "blob_name": "123e4567-e89b-12d3-a456-426614174000/chunk_001.mp3",
      "output_mime": "audio/mpeg",
      "output_extension": "mp3",
      "file_size_bytes": 34000,
      "download_url": "https://<storage-account>.blob.core.windows.net/audio-output/...?<sas-token>"
    }
  ]
}
```

### 🛠️ Importante: Configuración de `ffmpeg-bin`
La función está diseñada para ser autosuficiente. Al iniciarse, copia el binario `ffmpeg` desde el directorio `ffmpeg-bin` a `/tmp/ffmpeg` y le otorga permisos de ejecución (`chmod +x`).

**Pasos de Configuración:**
1. Descarga una compilación estática de FFmpeg para Linux (por ejemplo, desde [John Van Sickle's static builds](https://johnvansickle.com/ffmpeg/) o una fuente confiable similar).
2. Extrae el ejecutable `ffmpeg`.
3. Crea una carpeta llamada `ffmpeg-bin` en la raíz de este repositorio.
4. Coloca el ejecutable `ffmpeg` dentro de esta carpeta (asegúrate de que se llame exactamente `ffmpeg`).
5. Confirma el binario en tu repositorio. Si `.gitignore` lo bloquea, puedes forzar su agregado:  
   `git add --force ffmpeg-bin/ffmpeg`

> **Nota:** La implementación actual está orientada a entornos Linux (copiando a `/tmp/ffmpeg`). Si planeas desplegar en una Function App de Azure basada en Windows, debes actualizar la ruta `TARGET_FFMPEG` en `function_app.py` a una ruta compatible con Windows (por ejemplo, `D:\\home\\site\\wwwroot\\ffmpeg.exe`).

---

## 📄 License
This project is licensed under the MIT License.

## 👤 Author
**Jean Paul Cardozo**  
[GitHub Profile](https://github.com/JeanPaulCardozo)
