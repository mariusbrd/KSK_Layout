"""Keep visible metric tables aligned with the global sidebar unit."""


def selected_unit_columns(frame, unit):
    if unit not in {"Köpfe", "MAK"}:
        raise ValueError("Ungültige Kennzahl für die Ansicht")
    other = "Köpfe" if unit == "MAK" else "MAK"
    prefixes = (f"{other}-Veränderung", f"Bestand-{other}", f"Abgehende {other}")
    hidden = [column for column in frame.columns
              if isinstance(column, str) and column.startswith(prefixes)]
    return frame.drop(columns=hidden)
