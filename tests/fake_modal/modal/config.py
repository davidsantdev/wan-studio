"""A autorizacao guardada: no cliente de verdade, ~/.modal.toml; aqui, um arquivo da pasta do
teste, que o `modal token new` de mentira escreve."""

import json
import os
from pathlib import Path


class Config:
    def get(self, key):
        try:
            return json.loads((Path(os.environ.get("FAKE_MODAL_HOME", ".")) / "token.json").read_text(encoding="utf-8")).get(key)
        except (OSError, ValueError):
            return None


config = Config()
