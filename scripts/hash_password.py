from __future__ import annotations

from getpass import getpass
from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from auth import hash_password  # noqa: E402


password = getpass("Digite a senha que deseja cadastrar: ")
confirmation = getpass("Confirme a senha: ")
if password != confirmation:
    raise SystemExit("As senhas informadas nao coincidem.")
print(hash_password(password))
