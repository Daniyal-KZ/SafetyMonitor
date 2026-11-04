"""Display names shared by the journal and exports; recognition depends on the model."""

VIOLATION_LABELS = {
    'no_helmet': 'Отсутствие каски',
    'no_vest': 'Отсутствие сигнального жилета',
    'no_glasses': 'Отсутствие защитных очков',
    'no_gloves': 'Отсутствие перчаток',
    'no_boots': 'Отсутствие защитной обуви',
    'no_respirator': 'Отсутствие респиратора',
    'no_ear_protection': 'Отсутствие защиты слуха',
}


def violation_label(kind):
    return VIOLATION_LABELS.get(kind, kind)
