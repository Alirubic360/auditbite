from .api import Deps, create_app
from .worker import build_deps

storage, ai, sender = build_deps()
from .payments import build_provider

app = create_app(Deps(storage, ai, sender, build_provider()))
