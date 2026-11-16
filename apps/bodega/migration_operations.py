from django.db import migrations
from django.db.migrations.exceptions import IrreversibleError


class AddFieldIfMissing(migrations.AddField):
    """
    Registra el campo en el historial de Django.

    En la base de datos, agrega la columna únicamente si no existe.
    Se utiliza para reconciliar las columnas verificadas en Neon.
    """

    def database_forwards(
        self,
        app_label,
        schema_editor,
        from_state,
        to_state,
    ):
        modelo = to_state.apps.get_model(
            app_label,
            self.model_name,
        )

        if not self.allow_migrate_model(
            schema_editor.connection.alias,
            modelo,
        ):
            return

        campo = modelo._meta.get_field(self.name)
        tabla = modelo._meta.db_table

        # Consulta las columnas sin modificar los datos existentes.
        with schema_editor.connection.cursor() as cursor:
            descripcion = (
                schema_editor.connection.introspection
                .get_table_description(cursor, tabla)
            )

        columnas = {
            columna.name
            for columna in descripcion
        }

        # Conserva la columna existente y sus valores.
        if campo.column in columnas:
            return

        # En una base nueva, crea el campo normalmente.
        super().database_forwards(
            app_label,
            schema_editor,
            from_state,
            to_state,
        )

    def database_backwards(
        self,
        app_label,
        schema_editor,
        from_state,
        to_state,
    ):
        # Impide borrar por reversión una columna que podía
        # existir antes de ejecutar esta migración.
        raise IrreversibleError(
            "Esta operación de reconciliación no permite "
            "eliminar automáticamente columnas existentes."
        )