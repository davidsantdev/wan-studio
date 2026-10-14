"""`python -m modal ...` de mentira: os dois comandos que nuvem/ligar.py chama.

  token new       grava a autorizacao (como o navegador faria); com FAKE_MODAL_DENY, falha
  deploy ARQUIVO  importa o app (o que pega erro de definicao) e marca as funcoes como publicadas
"""

import importlib.util
import json
import os
import sys
from pathlib import Path

import modal


def main(args):
    with modal.locked():
        modal.save("cli", (modal.load("cli", []) or []) + [args])
    if args[:2] == ["token", "new"]:
        if os.environ.get("FAKE_MODAL_DENY"):
            print("Web authentication was not completed.")
            return 1
        (modal.HOME / "token.json").write_text(json.dumps({"token_id": "ak-teste", "token_secret": "as-teste"}),
                                               encoding="utf-8")
        print("Web authentication finished successfully!")
        return 0
    if args[:1] == ["deploy"]:
        path = Path(args[1])
        sys.path.insert(0, str(path.parent))
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with modal.locked():
            deployed = modal.load("deployed", {}) or {}
            deployed[module.app.name] = sorted(module.app.registered_functions)
            modal.save("deployed", deployed)
            modal.save("image", module.image.steps)
        print(f"✓ App deployed! ({', '.join(deployed[module.app.name])})")
        return 0
    print(f"comando de teste desconhecido: {args}")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
