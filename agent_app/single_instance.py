"""One resident companion per user, with activation of an existing instance."""
import ctypes
import hashlib
import os
from pathlib import Path
from PySide6.QtCore import QObject, QLockFile, QStandardPaths, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


class SingleInstance(QObject):
    activated = Signal()
    full_scan_requested = Signal()

    def __init__(self, name="code-companion", parent=None):
        super().__init__(parent)
        user = hashlib.sha256(str(Path.home()).casefold().encode()).hexdigest()[:16]
        self.name = name + "-" + user
        self.handle, self.lock, self.server = None, None, None
        if os.name == "nt":
            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
            self.kernel.CreateMutexW.restype = ctypes.c_void_p
            self.kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            self.kernel.CloseHandle.restype = ctypes.c_int
            self.handle = self.kernel.CreateMutexW(None, False, "Local\\" + self.name)
            if not self.handle: raise OSError("비서 실행 잠금을 만들지 못했어요.")
            self.primary = ctypes.get_last_error() != 183
        else:
            folder = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.TempLocation))
            self.lock = QLockFile(str(folder / (self.name + ".lock")))
            self.lock.setStaleLockTime(0)
            self.primary = self.lock.tryLock(0)
        if self.primary:
            QLocalServer.removeServer(self.name)
            self.server = QLocalServer(self)
            self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
            if not self.server.listen(self.name):
                self.close()
                raise OSError("기존 비서로 돌아오는 연결을 열지 못했어요.")
            self.server.newConnection.connect(self.accept)

    def accept(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            def receive(s=socket):
                if not s.canReadLine() and s.bytesAvailable() < 32: return
                message = bytes(s.read(32))
                if message == b"show\n": self.activated.emit()
                elif message == b"scan-all\n": self.full_scan_requested.emit()
                s.disconnectFromServer()
            socket.readyRead.connect(receive)
            socket.disconnected.connect(socket.deleteLater)
            if socket.bytesAvailable(): receive()

    def notify(self, command="show"):
        if command not in {"show", "scan-all"}: raise ValueError("지원하지 않는 비서 명령이에요.")
        for _ in range(15):
            socket = QLocalSocket()
            socket.connectToServer(self.name)
            if socket.waitForConnected(150):
                socket.write((command + "\n").encode())
                socket.waitForBytesWritten(500)
                socket.disconnectFromServer()
                return True
        return False

    def close(self):
        if self.server:
            self.server.close()
            self.server = None
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
        if self.lock and self.primary: self.lock.unlock()
