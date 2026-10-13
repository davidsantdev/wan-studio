"""Servidor do Wan Studio com um melhorador de prompt de mentira, para testar a interface
do melhorador sem carregar um LLM. O codigo de producao nao e alterado: so trocamos o objeto.
"""
import asyncio
import os
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"
sys.path.insert(0, str(APP))
os.chdir(APP)
import server  # noqa: E402
from aiohttp import web  # noqa: E402


class FakeEnhancer:
    """Tem os mesmos estados do de verdade, inclusive ceder a GPU e voltar."""
    state = "ready"
    detail = ""

    def start(self):
        pass

    async def enhance(self, prompt, mode="t2v", camera_fixed=False):
        await asyncio.sleep(0.4)
        return f"[{mode}{' cam' if camera_fixed else ''}] A cinematic shot of: {prompt}"

    async def sleep(self):
        self.state, self.detail = "sleeping", "a segunda GPU está gerando vídeo"
        return True

    def wake(self):
        self.state, self.detail = "ready", ""


server.enhancer = FakeEnhancer()
web.run_app(server.make_app(), host="127.0.0.1", port=server.PORT, print=None)
