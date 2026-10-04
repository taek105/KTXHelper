"""Control a regular macOS Chrome window through Accessibility and keyboard input."""

import fcntl
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from service.exceptions import (
    BrowserWindowClosedError,
    KorailAccessBlockedError,
    RefreshButtonNotFoundError,
)

BRIDGE = Path(__file__).with_suffix(".swift")
CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
SDK_14 = Path("/Library/Developer/CommandLineTools/SDKs/MacOSX14.5.sdk")
BLOCK_MARKERS = ("미허가도구사용시이용이제한될수있습니다", "CODE:-4003")
QUEUE_MARKERS = ("현재접속자가많아", "접속대기중", "예상대기시간", "대기자수")


class StaleBrowserControlError(RuntimeError):
    """A control moved or changed between the accessibility snapshot and action."""


def _bridge_command():
    if platform.system() != "Darwin":
        raise RuntimeError("일반 Chrome 입력 방식은 macOS에서만 사용할 수 있습니다.")
    sdk = str(SDK_14) if SDK_14.exists() else subprocess.check_output(
        ["xcrun", "--show-sdk-path"], text=True
    ).strip()
    cache = Path(tempfile.gettempdir()) / "ktxhelper-swift-cache"
    cache.mkdir(exist_ok=True)
    target = f"{platform.machine()}-apple-macosx14.5"
    digest = hashlib.sha256(
        BRIDGE.read_bytes() + sdk.encode() + target.encode()
    ).hexdigest()[:16]
    executable = cache / f"native-browser-{digest}"
    if not executable.exists():
        with (cache / "build.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if not executable.exists():
                temporary = cache / f"native-browser-{digest}-{os.getpid()}.tmp"
                try:
                    subprocess.run(
                        ["swiftc", "-sdk", sdk, "-module-cache-path", str(cache),
                         "-target", target, str(BRIDGE), "-o", str(temporary)],
                        capture_output=True, text=True, check=True, timeout=60,
                    )
                    os.replace(temporary, executable)
                finally:
                    temporary.unlink(missing_ok=True)
    return [str(executable)]


class NativeChrome:
    def __init__(self, url):
        if not CHROME.exists():
            raise RuntimeError("Google Chrome을 /Applications에 설치해주세요.")
        # Keep the profile while the payment window remains open after booking.
        self.profile = tempfile.mkdtemp(prefix="ktxhelper-chrome-")
        self.process = subprocess.Popen(
            [
                str(CHROME), f"--user-data-dir={self.profile}",
                "--no-first-run", "--no-default-browser-check",
                "--force-renderer-accessibility", "--new-window", url,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.pid = self.process.pid
        try:
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                try:
                    self.snapshot()
                    break
                except BrowserWindowClosedError:
                    if self.process.poll() is not None:
                        raise
                    time.sleep(0.5)
            else:
                raise TimeoutError("Chrome 창이 열리지 않았습니다.")
        except Exception:
            if self.process.poll() is None:
                self.process.terminate()
                self.process.wait(timeout=5)
            shutil.rmtree(self.profile, ignore_errors=True)
            raise
        threading.Thread(
            target=self._cleanup_after_exit,
            name=f"ktx-chrome-{self.pid}",
            daemon=True,
        ).start()

    def _cleanup_after_exit(self):
        self.process.wait()
        shutil.rmtree(self.profile, ignore_errors=True)

    def _call(self, action, *arguments, input_text=None):
        if self.process.poll() is not None:
            raise BrowserWindowClosedError("Chrome 창이 닫혔습니다.")
        command = [*_bridge_command(), action, str(self.pid), *map(str, arguments)]
        result = subprocess.run(
            command, capture_output=True, text=True, input=input_text, timeout=30
        )
        if result.returncode:
            message = result.stderr.strip()
            if result.returncode == 4 or "Chrome window closed" in message:
                raise BrowserWindowClosedError("Chrome 창이 닫혔습니다.")
            if result.returncode == 3:
                raise RuntimeError(
                    "macOS 시스템 설정 > 개인정보 보호 및 보안 > 손쉬운 사용에서 "
                    "이 앱의 제어 권한을 켜주세요."
                )
            if result.returncode in (5, 6):
                raise StaleBrowserControlError("브라우저 입력칸이 다시 그려졌습니다.")
            if action == "refresh" and "Chrome refresh button not found" in message:
                raise RefreshButtonNotFoundError("Chrome 새로고침 버튼을 찾지 못했습니다.")
            raise RuntimeError(f"Chrome 조작 실패: {message or action}")
        return result.stdout

    def snapshot(self):
        return json.loads(self._call("snapshot"))

    def navigate(self, url):
        self._call("navigate", url)

    def refresh(self):
        self._call("refresh")

    def press(self, node):
        self._call("press", node["path"], node["role"], node["title"])

    def click(self, node, *, fast=False):
        self._call("click_fast" if fast else "click", node["path"], node["role"], node["title"])

    def type_into(self, node, value, *, careful=False, paste=False):
        self._call(
            "type_paste" if paste else ("type_careful" if careful else "type"),
            node["path"], node["role"], node["title"],
            input_text=value,
        )

    def close(self):
        if self.process.poll() is None:
            try:
                self._call("close")
                self.process.wait(timeout=5)
            except (subprocess.TimeoutExpired, RuntimeError):
                self.process.terminate()
                self.process.wait(timeout=5)
        shutil.rmtree(self.profile, ignore_errors=True)


def address(nodes):
    for node in nodes:
        if node["role"] == "AXTextField" and node["title"] == "주소창 및 검색창":
            return node["value"]
    return ""


def logged_in(nodes):
    return any(node["role"] == "AXLink" and node["title"] == "로그아웃" for node in nodes)


def page_state(nodes):
    text = "".join(
        str(node.get("title", "")) + str(node.get("value", ""))
        for node in nodes if node["role"] in ("AXStaticText", "AXHeading", "AXLink")
    )
    normalized = "".join(text.split())
    if any(marker in normalized for marker in BLOCK_MARKERS):
        raise KorailAccessBlockedError("코레일이 현재 브라우저 접근을 제한했습니다(CODE -4003).")
    return "queue" if any(marker in normalized for marker in QUEUE_MARKERS) else "normal"


def schedule_rows(nodes):
    """Read visible KTX rows. The first seat link is the site's general-seat box."""
    by_path = {node["path"]: node for node in nodes}
    rows = []
    for heading in nodes:
        if heading["role"] != "AXHeading":
            continue
        times = re.findall(r"\d{2}:\d{2}", heading["title"])
        if len(times) < 2 or "→" not in heading["title"]:
            continue
        row_path = heading["path"].rsplit(".", 2)[0]
        number_node = by_path.get(f"{row_path}.0.1")
        seat_node = by_path.get(f"{row_path}.1")
        if not number_node or not seat_node or seat_node["role"] not in ("AXLink", "AXButton"):
            continue
        if not number_node["value"].isdigit():
            continue
        seat_title = seat_node["title"].strip()
        if "예약대기" in seat_title or "대기신청" in seat_title:
            status = "대기신청"
        elif "매진" in seat_title or "없음" in seat_title:
            status = "매진"
        elif (
            ("일반실" in seat_title and "원" in seat_title)
            or "예매" in seat_title
            or "예약가능" in seat_title
        ):
            status = "예약가능"
        else:
            status = "매진"
        rows.append({
            "train": int(number_node["value"]),
            "depart": times[0], "arrive": times[1],
            "status": status, "seat": seat_node,
        })
    return rows


def wait_for_results(browser, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        nodes = browser.snapshot()
        page_state(nodes)
        if "/ticket/search/list" in address(nodes):
            rows = schedule_rows(nodes)
            if rows:
                return nodes, rows
            if any("조회된 열차가 없습니다" in node["title"] + node["value"] for node in nodes):
                return nodes, []
        time.sleep(0.2)
    raise TimeoutError("코레일 열차 조회 결과를 기다리다 시간이 초과되었습니다.")
