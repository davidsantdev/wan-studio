"""Um `modal` de mentira, para testar nuvem/ sem conta nem internet (tests/nuvem_test.py).

Imita so o que nuvem/ usa do cliente de verdade (conferido contra o modal 1.6.1): App, Image,
Volume, Dict, Function, FunctionCall, forward, asgi_app, is_local, config e exception. O que
na nuvem ficaria guardado (dicionarios, volumes, o que foi publicado, as chamadas feitas) fica
em arquivos na pasta FAKE_MODAL_HOME, que e o que liga os processos do teste entre si: o atalho
"pede" uma funcao gravando a chamada, e tests/nuvem_cloud.py faz o papel do Modal e a executa.
"""

import contextlib
import copy
import json
import os
import time
import uuid
from pathlib import Path

from . import config, exception  # noqa: F401

HOME = Path(os.environ.get("FAKE_MODAL_HOME", "."))


def load(name, default=None):
    """O arquivo `name`.json da pasta; outro processo pode estar no meio de uma gravacao."""
    path = HOME / f"{name}.json"
    for _ in range(50):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return copy.deepcopy(default)
        except (ValueError, PermissionError):
            time.sleep(0.02)
    return copy.deepcopy(default)


def save(name, data):
    path = HOME / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{uuid.uuid4().hex[:6]}.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    for _ in range(100):  # no Windows a troca falha se outro processo esta lendo
        try:
            tmp.replace(path)
            return
        except PermissionError:
            time.sleep(0.02)
    raise OSError(f"não consegui gravar {path}")


@contextlib.contextmanager
def locked():
    """Uma secao critica entre processos: ler, mudar e gravar um arquivo sem perder a mudanca
    de outro."""
    lock = HOME / "trava"
    HOME.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + 10
    while True:
        try:
            handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.time() > deadline:  # sobrou de um processo morto
                with contextlib.suppress(OSError):
                    lock.unlink()
            time.sleep(0.01)
    try:
        yield
    finally:
        os.close(handle)
        with contextlib.suppress(OSError):
            lock.unlink()


def is_local():
    return not os.environ.get("FAKE_MODAL_REMOTE")


class Image:
    """Guarda as etapas pedidas, na ordem, para o teste conferir o que iria para a imagem."""

    def __init__(self, steps=()):
        self.steps = list(steps)

    @staticmethod
    def debian_slim(python_version=None):
        return Image([("debian_slim", python_version)])

    def _with(self, *step):
        return Image(self.steps + [step])

    def apt_install(self, *packages):
        return self._with("apt_install", packages)

    def pip_install(self, *packages, index_url=None):
        return self._with("pip_install", packages, index_url)

    def run_commands(self, *commands):
        return self._with("run_commands", commands)

    def add_local_file(self, local_path, remote_path, *, copy=False):
        if not Path(local_path).is_file():
            raise FileNotFoundError(local_path)
        return self._with("add_local_file", str(local_path), remote_path, copy)

    def add_local_dir(self, local_path, remote_path, *, copy=False, ignore=()):
        if not Path(local_path).is_dir():
            raise FileNotFoundError(local_path)
        return self._with("add_local_dir", str(local_path), remote_path, copy)


class FileEntry:
    def __init__(self, path):
        self.path = path


class Volume:
    def __init__(self, name):
        self.name = name
        self.root = HOME / "volumes" / name

    @staticmethod
    def from_name(name, create_if_missing=False):
        volume = Volume(name)
        if create_if_missing:
            volume.root.mkdir(parents=True, exist_ok=True)
        return volume

    def listdir(self, path, *, recursive=False):
        folder = self.root / path.strip("/")
        if not folder.is_dir():
            raise exception.NotFoundError(f"No such file or directory: {path}")
        found = folder.rglob("*") if recursive else folder.iterdir()
        return [FileEntry(item.relative_to(self.root).as_posix()) for item in found]

    def commit(self):
        pass


class Dict:
    def __init__(self, name):
        self.file = f"dicts/{name}"

    @staticmethod
    def from_name(name, create_if_missing=False):
        return Dict(name)

    def get(self, key, default=None):
        return load(self.file, {}).get(key, default)

    def __getitem__(self, key):
        return load(self.file, {})[key]

    def __setitem__(self, key, value):
        with locked():
            data = load(self.file, {})
            data[key] = value
            save(self.file, data)


class LogEntry:
    def __init__(self, message):
        self.message = message


class Logs:
    def __init__(self, name, done=None):
        self.path = HOME / "logs" / f"{name}.txt"
        self.done = done

    def lines(self):
        try:
            return self.path.read_text(encoding="utf-8").splitlines(keepends=True)
        except OSError:
            return []

    def tail(self, entries=100):
        return [LogEntry(line) for line in self.lines()[-entries:]]

    def stream(self, timeout=None):
        """As linhas conforme chegam, ate a chamada terminar."""
        sent, deadline = 0, time.time() + (timeout or 3600)
        while time.time() < deadline:
            finished = self.done() if self.done else True
            lines = self.lines()
            for line in lines[sent:]:
                yield LogEntry(line)
            sent = len(lines)
            if finished:
                return
            time.sleep(0.05)


def calls():
    return load("calls", [])


def find_call(call_id):
    return next((call for call in calls() if call["id"] == call_id), None)


def update_call(call_id, **fields):
    with locked():
        every = calls()
        for call in every:
            if call["id"] == call_id:
                call.update(fields)
        save("calls", every)


class FunctionCall:
    def __init__(self, object_id):
        self.object_id = object_id
        self.logs = Logs(object_id, done=lambda: (find_call(object_id) or {}).get("status") in ("done", "failed", "cancelled"))

    @staticmethod
    def from_id(object_id):
        return FunctionCall(object_id)

    def get(self, timeout=None):
        deadline = time.time() + (3600 if timeout is None else timeout)
        while True:
            call = find_call(self.object_id) or {}
            if call.get("status") == "done":
                return call.get("result")
            if call.get("status") in ("failed", "cancelled"):
                raise exception.Error(call.get("error") or call["status"])
            if time.time() >= deadline:
                raise TimeoutError()
            time.sleep(0.05)

    def cancel(self, terminate_containers=False):
        update_call(self.object_id, cancel=True, terminate=terminate_containers)


class Function:
    def __init__(self, app_name, name, raw=None, options=None):
        self.app_name, self.name, self.raw = app_name, name, raw
        self.options = dict(options or {})
        self.logs = Logs(name)

    @staticmethod
    def from_name(app_name, name):
        return Function(app_name, name)

    def hydrate(self):
        if self.name not in load("deployed", {}).get(self.app_name, []):
            raise exception.NotFoundError(f"Lookup failed for Function '{self.name}' from the '{self.app_name}' app: "
                                          "App not found")
        return self

    def with_options(self, **options):
        return Function(self.app_name, self.name, self.raw, {**self.options, **options})

    def get_web_url(self):
        self.hydrate()
        return f"https://conta-de-teste--{self.app_name}-{self.name}.modal.run"

    def spawn(self, *args):
        self.hydrate()
        failure = load("refuse")  # o teste arma uma recusa do Modal (conta sem cartao...)
        if failure:
            raise exception.Error(failure)
        call_id = "fc-" + uuid.uuid4().hex[:10]
        with locked():
            save("calls", calls() + [{"id": call_id, "app": self.app_name, "function": self.name, "args": list(args),
                                      "options": self.options, "status": "pending"}])
        return FunctionCall(call_id)


class App:
    def __init__(self, name):
        self.name = name
        self.registered_functions = {}
        self.registered_web_endpoints = []

    def function(self, **options):
        def register(raw):
            web = getattr(raw, "fake_web", False)
            function = Function(self.name, raw.__name__, raw, options)
            self.registered_functions[raw.__name__] = function
            if web:
                self.registered_web_endpoints.append(raw.__name__)
            return function
        return register


def asgi_app(*, label=None):
    def mark(raw):
        raw.fake_web = True
        return raw
    return mark


class Tunnel:
    def __init__(self, port):
        self.url = f"http://127.0.0.1:{port}"


@contextlib.contextmanager
def forward(port):
    yield Tunnel(port)
