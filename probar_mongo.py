import os
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import PyMongoError


def main():
    load_dotenv(
        Path(__file__).resolve().parent / ".env",
        override=True,
    )

    uri = os.getenv("MONGO_URI", "").strip()

    if not uri or "TU_CONTRASEÑA_AQUI" in uri or "<db_password>" in uri:
        print("Configura MONGO_URI en .env con la contraseña correcta.")
        return

    try:
        with MongoClient(
            uri,
            serverSelectionTimeoutMS=10000,
            connectTimeoutMS=10000,
        ) as cliente:
            cliente.admin.command("ping")
            print("Conexión con MongoDB Atlas exitosa.")

    except PyMongoError as error:
        print(
            "No se pudo conectar. "
            f"Tipo de error: {type(error).__name__}"
        )

    except ValueError:
        print("La configuración de la conexión no es válida.")


if __name__ == "__main__":
    main()