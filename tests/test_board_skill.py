"""Exercise a freshly installed public skill against synthetic IP HTTPS."""
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Handler(BaseHTTPRequestHandler):
    board = {}
    concurrent_edit = None  # fields another writer changes between the CLI's read and its write

    def reply(self, status, payload):
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def authorized(self):
        if self.headers.get("Authorization") == "Bearer synthetic-test-token":
            return True
        self.reply(401, {"error": "invalid token"})
        return False

    def do_GET(self):
        if self.authorized():
            self.reply(200, self.board)

    def do_PUT(self):
        # Whole-board uploads are gone (docs/OPS.md); a client that still sends one must fail.
        if self.authorized():
            self.reply(410, {"error": "gone", "detail": "use POST /api/ops"})

    def do_POST(self):
        if not self.authorized():
            return
        if self.path != "/api/ops":
            return self.reply(404, {"error": "not_found", "detail": self.path})
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        card = self.board["threads"][0]
        if self.concurrent_edit:
            card.update(self.concurrent_edit)
        args = request["args"]
        if request.get("api") != 1 or args.get("id") != card["id"]:
            return self.reply(400, {"error": "bad_request", "detail": "synthetic server"})
        if request["op"] == "note.add":
            card["events"].append({"at": 1780000000001, "type": "note", "text": args["text"]})
        elif request["op"] == "card.update":
            if any(card.get(k) != v for k, v in args.get("if", {}).items()):
                return self.reply(409, {"error": "stale", "detail": "fields changed", "card": card})
            card.update(args["set"])
        else:
            return self.reply(400, {"error": "unknown_op", "detail": request["op"]})
        self.board["rev"] += 1
        self.reply(200, {"ok": True, "rev": self.board["rev"], "changed": {"cards": [card]},
                         "duplicate": False})

    def log_message(self, *args):
        pass


class PublicSkillTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="devkit-board-")
        cls.root = Path(cls.temp.name)

        def openssl(*args):
            subprocess.run(["openssl", *args], cwd=cls.root, check=True, capture_output=True)

        openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj",
                "/CN=Synthetic Test CA", "-keyout", "ca.key", "-out", "ca.crt",
                "-addext", "basicConstraints=critical,CA:TRUE",
                "-addext", "keyUsage=critical,keyCertSign,cRLSign")
        openssl("req", "-new", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=127.0.0.1",
                "-keyout", "leaf.key", "-out", "leaf.csr")
        (cls.root / "leaf.ext").write_text("subjectAltName=IP:127.0.0.1\nextendedKeyUsage=serverAuth\n"
            "basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\n"
            "subjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\n")
        openssl("x509", "-req", "-in", "leaf.csr", "-CA", "ca.crt", "-CAkey", "ca.key",
                "-CAcreateserial", "-days", "1", "-extfile", "leaf.ext", "-out", "leaf.crt")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cls.root / "leaf.crt", cls.root / "leaf.key")
        cls.server.socket = context.wrap_socket(cls.server.socket, server_side=True)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"https://127.0.0.1:{cls.server.server_port}"
        cls.installed = cls.root / "installed-board"
        cls.install(cls.installed)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)
        cls.temp.cleanup()

    @classmethod
    def install(cls, destination, check=True):
        return subprocess.run([sys.executable, str(ROOT / "install-skill.py"), "board",
                               "--dest", str(destination)], check=check, capture_output=True, text=True)

    def setUp(self):
        Handler.board = {"version": 2, "rev": 7, "savedAt": 123, "inbox": [], "threads": [
            {"id": "synthetic-card", "title": "Synthetic task", "state": "ready", "priority": 1,
             "lane": "errand", "createdAt": 1780000000000, "touchedAt": 1780000000000, "events": []}]}
        Handler.concurrent_edit = None
        self.config = self.root / "client.json"
        self.config.write_text(json.dumps({"url": self.url, "token": "synthetic-test-token",
                                          "ca_file": str(self.root / "ca.crt")}))

    def cli(self, *args, **extra):
        env = {k: v for k, v in os.environ.items() if not k.startswith("BOARD_")}
        env.update(BOARD_CLIENT_CONFIG=str(self.config), **extra)
        return subprocess.run([sys.executable, str(self.installed / "scripts/board.py"), *args],
                              env=env, cwd=self.installed, capture_output=True, text=True)

    def test_installed_skill_reads_and_writes_without_private_repository(self):
        result = self.cli("list", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["title"], "Synthetic task")
        literal = "测试 ' \" ; $(touch should-not-exist)\n第二行"
        result = self.cli("note", "synthetic-card", literal)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Handler.board["threads"][0]["events"][-1]["text"], literal)
        self.assertFalse((self.installed / "should-not-exist").exists())
        self.assertFalse((self.installed / "board.json").exists())

    def test_stale_field_is_not_overwritten(self):
        Handler.concurrent_edit = {"title": "Renamed elsewhere"}
        expected = copy.deepcopy(Handler.board)
        expected["threads"][0]["title"] = "Renamed elsewhere"
        result = self.cli("set", "synthetic-card", "title=Renamed here")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("刚被别处改过", result.stderr)
        self.assertEqual(Handler.board, expected)

    def test_invalid_trust_hostname_and_public_http_are_rejected(self):
        config = json.loads(self.config.read_text())
        config.pop("ca_file")
        self.config.write_text(json.dumps(config))
        self.assertIn("CERTIFICATE_VERIFY_FAILED", self.cli("list").stderr)
        mismatch = self.cli("list", BOARD_URL=self.url.replace("127.0.0.1", "localhost"),
                            BOARD_CA_FILE=str(self.root / "ca.crt"))
        self.assertNotEqual(mismatch.returncode, 0)
        self.assertIn("mismatch", mismatch.stderr)
        self.assertIn("必须使用 HTTPS", self.cli("list", BOARD_URL="http://192.0.2.1:8765").stderr)

    def test_update_preserves_config_and_refuses_unmanaged_paths(self):
        destination = self.root / "update-test"
        self.install(destination)
        (destination / "SKILL.md").write_text("Old installed version")
        before = self.config.read_bytes()
        self.install(destination)
        self.assertEqual((destination / "SKILL.md").read_bytes(),
                         (ROOT / "skills/board/SKILL.md").read_bytes())
        self.assertEqual(self.config.read_bytes(), before)
        unmanaged = self.root / "unmanaged"
        unmanaged.mkdir()
        (unmanaged / "keep.txt").write_text("Keep this")
        self.assertNotEqual(self.install(unmanaged, check=False).returncode, 0)
        link = self.root / "existing-link"
        link.symlink_to(unmanaged, target_is_directory=True)
        self.assertNotEqual(self.install(link, check=False).returncode, 0)
        self.assertTrue(link.is_symlink())
        self.assertEqual((unmanaged / "keep.txt").read_text(), "Keep this")


if __name__ == "__main__":
    unittest.main()
