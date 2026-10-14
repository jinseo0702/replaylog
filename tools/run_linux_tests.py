"""Run the same tests in WSL even when Windows drives are not mounted."""

import argparse
import base64
import io
import json
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
RUNNER = r'''
import base64,io,json,os,platform,sqlite3,subprocess,sys,tempfile,zipfile
payload=json.load(sys.stdin)
with tempfile.TemporaryDirectory(prefix="replaylog-test-") as directory:
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(payload["archive"]))) as archive:
        for name in archive.namelist():
            if name.startswith("/") or ".." in name.split("/"):
                raise ValueError("invalid archive member")
        archive.extractall(directory)
    env=os.environ.copy()
    env.update(PYTHONUTF8="1",PYTHONIOENCODING="utf-8")
    result=subprocess.run([sys.executable,"-m","unittest","discover","-s","tests","-v"],
        cwd=directory,env=env,capture_output=True,text=True,encoding="utf-8",timeout=120)
    print(json.dumps({"returncode":result.returncode,"stdout":result.stdout,"stderr":result.stderr,
        "environment":{"platform":platform.platform(),"python":platform.python_version(),
        "sqlite":sqlite3.sqlite_version,"execution":"WSL Ubuntu-22.04 temporary source copy"}}))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=["red", "green", "safety-red", "final"], required=True)
    args = parser.parse_args()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for folder in ["tests", "fixtures", "replaylog"]:
            for path in sorted((ROOT / folder).rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts:
                    archive.write(path, path.relative_to(ROOT).as_posix())
    payload = json.dumps({"archive": base64.b64encode(buffer.getvalue()).decode("ascii")})
    result = subprocess.run(
        ["wsl", "--distribution", "Ubuntu-22.04", "--exec", "python3", "-c", RUNNER],
        input=payload, capture_output=True, text=True, encoding="utf-8", timeout=150,
    )
    if result.returncode:
        print(result.stderr, file=sys.stderr)
        return result.returncode
    data = json.loads(result.stdout)
    names = {"red": "01-red-linux.txt", "green": "02-green-linux.txt", "safety-red": "03-safety-red-linux.txt", "final": "04-final-linux.txt"}
    destination = ROOT / "evidence" / names[args.stage]
    log = "Environment: " + json.dumps(data["environment"], ensure_ascii=False) + "\n"
    log += data["stdout"] + data["stderr"]
    log += "\nExit code: " + str(data["returncode"]) + "\n"
    destination.write_text(log, encoding="utf-8")
    print(log)
    return data["returncode"]


if __name__ == "__main__":
    raise SystemExit(main())
