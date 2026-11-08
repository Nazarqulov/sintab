def hour_in_window(hour: int, start: int, end: int) -> bool:
    """[start, end) soat oralig'i; yarim tundan o'tadigan oraliqlar (22→6) ham to'g'ri."""
    if start == end:
        return False
    return start <= hour < end if start < end else hour >= start or hour < end
